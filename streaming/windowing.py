"""Windowing for the silver layer.

Kept free of Kafka and Postgres so the aggregation rules can be unit tested
directly. The processor is the adapter that feeds it; this module decides *what*
counts as one evaluation unit.

Two aggregation strategies, because the two kinds of stream have genuinely
different shapes:

``run`` windows
    Simulation telemetry belongs to a finite run that ends. Records are grouped
    by ``run_id`` and the window is closed by an explicit end-of-run signal or,
    failing that, by an idle timeout. This is the mode the parity test compares
    against the batch path, because a run is exactly the unit
    ``compute_metrics`` expects.

``tumbling`` windows
    Vehicle logs are unbounded; there is no "end" to wait for. Records are
    grouped by ``vehicle_id`` within fixed-length windows aligned to the epoch,
    and closed when the wall clock passes the boundary.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

WINDOW_IDLE_TIMEOUT_SEC = 30.0


@dataclass
class Window:
    """One aggregation unit: the records that become a single metrics row."""

    key: str
    source: str
    run_id: Optional[str] = None
    vehicle_id: Optional[str] = None
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    records: list[dict[str, Any]] = field(default_factory=list)
    last_offsets: dict[str, int] = field(default_factory=dict)
    last_seen: float = 0.0

    @property
    def count(self) -> int:
        return len(self.records)

    @property
    def is_empty(self) -> bool:
        return not self.records

    def add(
        self,
        record: dict[str, Any],
        now: float,
        topic: str,
        partition: int,
        offset: int,
    ) -> None:
        self.records.append(record)
        self.last_seen = now
        key = f"{topic}:{partition}"
        # Offsets only ever move forward within a window, so this is the high
        # water mark that the replay check compares against.
        self.last_offsets[key] = max(self.last_offsets.get(key, 0), offset)

    def closed(self, now: float, idle_timeout_sec: float = WINDOW_IDLE_TIMEOUT_SEC) -> bool:
        """True when the window has gone quiet long enough to evaluate."""
        if self.is_empty:
            return False
        return (now - self.last_seen) >= idle_timeout_sec


def floor_to_window(ts: datetime, window_sec: int) -> datetime:
    """Align a timestamp to the start of its tumbling window.

    Alignment is on absolute epoch seconds rather than relative to the first
    record seen, so a restart mid-stream produces the same window boundaries as
    a fresh start, and two processors agree on where the edges are.
    """
    epoch_seconds = int(ts.timestamp())
    aligned = epoch_seconds - (epoch_seconds % window_sec)
    return datetime.fromtimestamp(aligned, tz=timezone.utc)


def window_end_for(start: datetime, window_sec: int) -> datetime:
    return start + timedelta(seconds=window_sec)


class WindowManager:
    """Owns the open windows and decides which ones to close."""

    def __init__(
        self,
        strategy: str,
        window_sec: int = 30,
        idle_timeout_sec: float = WINDOW_IDLE_TIMEOUT_SEC,
        max_open_windows: int = 500,
    ):
        if strategy not in ("run", "tumbling"):
            raise ValueError(f"unknown window strategy: {strategy}")
        self.strategy = strategy
        self.window_sec = window_sec
        self.idle_timeout_sec = idle_timeout_sec
        self.max_open_windows = max_open_windows
        # Insertion-ordered so the oldest window is always first, which makes
        # eviction a matter of popping from the front.
        self._windows: "OrderedDict[str, Window]" = OrderedDict()

    @property
    def open_count(self) -> int:
        return len(self._windows)

    def _key_for(self, record: dict[str, Any]) -> str:
        run_id = record.get("run_id")
        vehicle_id = record.get("vehicle_id")

        if self.strategy == "run":
            if not run_id:
                raise ValueError("run_id is required for run-windowed aggregation")
            return f"run:{run_id}"

        if not vehicle_id:
            raise ValueError("vehicle_id is required for tumbling-windowed aggregation")
        if record.get("captured_at") is None:
            raise ValueError("captured_at is required for tumbling-windowed aggregation")
        captured_at = datetime.fromtimestamp(record["captured_at"] / 1000.0, tz=timezone.utc)
        start = floor_to_window(captured_at, self.window_sec)
        return f"veh:{vehicle_id}:{int(start.timestamp())}"

    def add(
        self,
        record: dict[str, Any],
        source: str,
        now: float,
        topic: str,
        partition: int,
        offset: int,
    ) -> Window:
        """Buffer a record, returning the window it landed in."""
        key = self._key_for(record)
        window = self._windows.get(key)

        if window is None:
            window = self._build_window(key, source, record)
            self._windows[key] = window
        elif self.strategy == "run" and window.run_id is None and record.get("run_id"):
            # A record that arrived before its run_id was known.
            window.run_id = record["run_id"]

        window.add(record, now, topic, partition, offset)
        self._windows.move_to_end(key)
        return window

    def _build_window(self, key: str, source: str, record: dict[str, Any]) -> Window:
        window = Window(
            key=key,
            source=source,
            run_id=record.get("run_id"),
            vehicle_id=record.get("vehicle_id"),
        )
        # Run windows are keyed only by run_id, so they never need a timestamp.
        # Only tumbling windows have to locate the record in time.
        if self.strategy == "tumbling" and record.get("captured_at") is not None:
            captured_at = datetime.fromtimestamp(record["captured_at"] / 1000.0, tz=timezone.utc)
            start = floor_to_window(captured_at, self.window_sec)
            window.window_start = start
            window.window_end = window_end_for(start, self.window_sec)
        return window

    def __len__(self) -> int:
        """Number of windows currently open.

        Exposed so a service can publish its buffer size as a gauge: a window
        count that climbs without falling means windows are not being closed.
        """
        return len(self._windows)

    def close_due(self, now: float) -> list[Window]:
        """Remove and return windows that are ready to be evaluated."""
        ready: list[Window] = []
        for key, window in list(self._windows.items()):
            if self.strategy == "run":
                if window.closed(now, self.idle_timeout_sec):
                    ready.append(window)
                    del self._windows[key]
            elif window.window_end is not None and now >= window.window_end.timestamp():
                ready.append(window)
                del self._windows[key]

        # A stuck stream must not grow memory without bound. Dropping the
        # oldest is safe because the raw lake already holds a durable copy.
        while len(self._windows) > self.max_open_windows:
            _, evicted = self._windows.popitem(last=False)
            ready.append(evicted)

        return ready

    def force_close_run(self, run_id: str) -> Optional[Window]:
        """Close one run's window immediately, e.g. on an end-of-run signal."""
        key = f"run:{run_id}"
        return self._windows.pop(key, None)

    def flush_all(self) -> list[Window]:
        """Close every open window. Used on shutdown."""
        windows = list(self._windows.values())
        self._windows.clear()
        return windows
