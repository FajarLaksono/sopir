"""Pipeline reconciliation panel.

Answers one question: did every record a producer published end up somewhere
accounted for? The four figures are produced, landed, processed and
dead-lettered, and the identity that has to hold is::

    produced == landed + dead_lettered

That identity is only expected to hold against a *quiesced* pipeline. A replay
re-writes records into an append-only lake, so ``landed`` can legitimately sit
above ``produced``; that direction is reported as benign, while the opposite
direction (records short) is reported as a real gap. The renderer distinguishes
them deliberately -- see :func:`_render_balance`.

Pure renderer. Takes the figures, renders them; touches no database, no broker
and no object store. Fetching lives in ``dashboard/pipeline_stats.py``.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

from dashboard.pipeline_stats import Reconciliation

_SOURCE_LABELS = {
    "sopir.sim.telemetry.v1": "SUMO simulation",
    "sopir.veh.can.v1": "Vehicle CAN bus",
    "sopir.veh.gnss.v1": "Vehicle GNSS",
    "sopir.veh.events.v1": "Vehicle events",
}


def render_reconciliation(
    stream: Optional[Reconciliation],
    processed: Optional[int],
    windows: Optional[int],
    failures: Optional[int],
) -> None:
    """Render the four-way accounting.

    ``stream`` is None when the snapshot could not be gathered at all;
    ``processed``/``windows``/``failures`` are None when the database could not
    be read. Each is reported as unavailable rather than as zero, because "the
    broker is down" and "the pipeline produced nothing" are very different
    statements and only one of them is a good thing to show.
    """
    st.subheader("Pipeline reconciliation")

    if stream is None:
        st.info("Pipeline figures are unavailable. Check that Redpanda and LocalStack are running.")
        return

    cols = st.columns(4)
    _figure(
        cols[0],
        "Produced",
        stream.produced,
        "Records published by producers, from Kafka high watermarks",
    )
    _figure(cols[1], "Landed", stream.landed, "Rows written to the raw lake as Parquet")
    _figure(cols[2], "Processed", processed, "Rows in stream_events, the silver landing zone")
    _figure(cols[3], "Dead-lettered", stream.dead_lettered, "Distinct keys on sopir.dlq.v1")

    for name, reason in sorted(stream.unavailable.items()):
        st.warning(f"{name.capitalize()}: {reason}")

    st.markdown("---")
    _render_balance(stream, processed)
    _render_breakdown(stream, windows, failures)


def _figure(col: object, label: str, value: Optional[int], help_text: str) -> None:
    """One metric tile. Shows 'n/a' rather than 0 when the figure is unreadable."""
    if value is None:
        col.metric(label, "n/a", help=help_text)
    else:
        col.metric(label, f"{value:,}", help=help_text)


def _render_balance(stream: Reconciliation, processed: Optional[int]) -> None:
    """State the identity, and separate a real gap from a benign replay surplus.

    The distinction is the whole point of this block. A *short* balance means
    records are unaccounted for and someone should go looking. A *surplus* means
    the lake holds more rows than were produced, which is exactly what an
    append-only lake looks like after a replay and needs no action. Reporting
    both as "does not balance" would train people to ignore the panel.
    """
    gap = stream.gap

    if gap is None:
        st.info("Balance cannot be asserted: one or more figures could not be read.")
    elif gap > 0:
        st.error(
            f"Does not balance: {stream.produced:,} produced vs "
            f"{stream.landed:,} landed + {stream.dead_lettered:,} dead-lettered "
            f"({gap:,} records short). Investigate before trusting anything "
            "downstream of the gap."
        )
    elif gap < 0:
        # Expected after a replay; bronze is append-only. Silver dedupes on
        # event_id, so the duplicate rows collapse there rather than propagating.
        st.info(
            f"Lake holds {abs(gap):,} more rows than were produced "
            f"({stream.landed:,} landed vs {stream.produced:,} produced). This is "
            "the expected signature of a replay against an append-only raw lake, "
            "not data loss: every distinct event_id and payload is still intact, "
            "and silver collapses the duplicates on event_id."
        )
    else:
        st.success(
            f"Balanced: {stream.produced:,} produced = {stream.landed:,} landed "
            f"+ {stream.dead_lettered:,} dead-lettered. Nothing was lost."
        )

    if processed is not None and stream.landed is not None:
        in_flight = stream.landed - processed
        if in_flight > 0:
            st.info(
                f"{in_flight:,} landed records are not in silver yet. That is the "
                f"normal steady state while the processor catches up."
            )
        elif in_flight < 0:
            st.warning(
                f"Silver holds {abs(in_flight):,} more events than the lake holds "
                f"records. Replayed silver is retained after a lake expiry, so "
                f"this is expected once the 30-day lifecycle has removed anything."
            )


def _render_breakdown(
    stream: Reconciliation,
    windows: Optional[int],
    failures: Optional[int],
) -> None:
    """Per-source production, and what silver made of it."""
    if stream.produced_by_topic:
        st.markdown("**Produced per source**")
        st.dataframe(
            [
                {
                    "Source": _SOURCE_LABELS.get(topic, topic),
                    "Topic": topic,
                    "Records": count,
                }
                for topic, count in sorted(stream.produced_by_topic.items())
            ],
            hide_index=True,
            use_container_width=True,
            column_config={"Records": st.column_config.NumberColumn(format="%d")},
        )

    detail = []
    if stream.lake_objects is not None:
        detail.append(f"{stream.lake_objects:,} Parquet objects in the raw lake")
    if windows is not None:
        detail.append(f"{windows:,} windows evaluated")
    if failures is not None:
        detail.append(f"{failures:,} failures detected")

    if detail:
        st.caption(" - ".join(detail))
