"""Tests for the reconciliation arithmetic behind the pipeline panel.

These cover the part of the panel that decides whether a discrepancy is worth
waking someone up for. The distinction is asymmetric and easy to get wrong:
records *short* means something was lost, records *surplus* means a replay hit an
append-only lake. Collapsing both into "does not balance" trains people to
ignore the panel, which is worse than not having one.

The fetch functions are not exercised here -- they need a broker and an object
store, and ``tests/test_pipeline_integration.py`` covers them end to end. What is
testable in isolation is the interpretation of their output, so that is what this
file pins.
"""

from __future__ import annotations

from dashboard.pipeline_stats import Reconciliation


def _snapshot(
    produced: int | None = 1000,
    landed: int | None = 1000,
    dead_lettered: int | None = 0,
) -> Reconciliation:
    return Reconciliation(
        produced=produced,
        landed=landed,
        dead_lettered=dead_lettered,
    )


class TestGap:
    def test_balanced_snapshot_has_no_gap(self):
        assert _snapshot().gap == 0

    def test_gap_is_produced_minus_landed_and_dead_lettered(self):
        assert _snapshot(produced=1000, landed=980, dead_lettered=5).gap == 15

    def test_dead_lettered_records_close_the_gap(self):
        # The identity is produced == landed + dead_lettered, so a routed record
        # counts as accounted for even though it never reached the lake.
        snapshot = _snapshot(produced=1000, landed=990, dead_lettered=10)
        assert snapshot.gap == 0
        assert snapshot.reconciles is True

    def test_surplus_is_negative_gap(self):
        # A replay duplicates rows, so the lake can hold more than was produced.
        # This is the case the old boolean could not represent.
        snapshot = _snapshot(produced=1000, landed=2000)
        assert snapshot.gap == -1000
        assert snapshot.reconciles is False
        assert snapshot.surplus == 1000

    def test_surplus_is_zero_when_balanced(self):
        assert _snapshot().surplus == 0

    def test_surplus_is_zero_when_records_are_short(self):
        # Never negative: a shortfall is a loss, not a surplus, and must not be
        # laundered into the benign branch.
        assert _snapshot(produced=1000, landed=900).surplus == 0

    def test_gap_is_unknown_when_a_figure_is_unreadable(self):
        for missing in ("produced", "landed", "dead_lettered"):
            values = {
                "produced": None,
                "landed": 1000,
                "dead_lettered": 0,
            }
            values[missing] = None
            snapshot = _snapshot(**values)
            assert snapshot.gap is None, f"gap should be unknown without {missing}"
            assert snapshot.reconciles is None
            assert snapshot.surplus is None

    def test_zero_is_distinct_from_unreadable(self):
        # A DLQ count of zero is a real measurement, not a missing one. Reading
        # zero as "unknown" would make the panel refuse to assert balance on a
        # pipeline that has never dead-lettered anything, which is the common
        # case and the one you most want a green check on.
        snapshot = _snapshot(dead_lettered=0)
        assert snapshot.gap == 0
        assert snapshot.reconciles is True
