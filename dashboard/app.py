from dataclasses import replace

import streamlit as st
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from backend.config import settings
from backend.models import (
    Failure,
    Metrics,
    Scenario,
    SimulationRun,
    StreamEvent,
    StreamFailure,
    StreamMetrics,
)
from dashboard.components import (
    render_failure_list,
    render_metric_charts,
    render_reconciliation,
    render_run_table,
)
from dashboard.pipeline_stats import collect as collect_reconciliation

st.set_page_config(page_title="OpenDriveLab Dashboard", layout="wide")

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@st.cache_data(ttl=30)
def load_runs():
    db = SessionLocal()
    try:
        runs = (
            db.execute(select(SimulationRun).order_by(SimulationRun.created_at.desc()))
            .scalars()
            .all()
        )
        return runs
    finally:
        db.close()


@st.cache_data(ttl=30)
def load_metrics(run_ids):
    if not run_ids:
        return {}
    db = SessionLocal()
    try:
        metrics = db.execute(select(Metrics).where(Metrics.run_id.in_(run_ids))).scalars().all()
        return {m.run_id: m for m in metrics}
    finally:
        db.close()


@st.cache_data(ttl=30)
def load_failures(run_ids):
    if not run_ids:
        return {}
    db = SessionLocal()
    try:
        failures = db.execute(select(Failure).where(Failure.run_id.in_(run_ids))).scalars().all()
        result = {}
        for f in failures:
            result.setdefault(f.run_id, []).append(f)
        return result
    finally:
        db.close()


@st.cache_data(ttl=30)
def load_scenarios():
    db = SessionLocal()
    try:
        scenarios = db.execute(select(Scenario)).scalars().all()
        return {s.id: s for s in scenarios}
    finally:
        db.close()


@st.cache_data(ttl=30)
def load_silver_counts() -> tuple[dict[str, int | None], str | None]:
    """Row counts from the silver tables, plus a reason if the read failed.

    Streamlit renders both tab bodies on every rerun, so an exception here
    would blank the whole page rather than just this panel. Counts and the
    failure reason are returned instead of raised.
    """
    try:
        db = SessionLocal()
        try:
            return (
                {
                    "processed": db.execute(
                        select(func.count()).select_from(StreamEvent)
                    ).scalar_one(),
                    "windows": db.execute(
                        select(func.count()).select_from(StreamMetrics)
                    ).scalar_one(),
                    "failures": db.execute(
                        select(func.count()).select_from(StreamFailure)
                    ).scalar_one(),
                },
                None,
            )
        finally:
            db.close()
    except SQLAlchemyError as exc:
        return {"processed": None, "windows": None, "failures": None}, str(exc)


@st.cache_data(ttl=30)
def load_reconciliation():
    return collect_reconciliation()


st.title("OpenDriveLab - Simulation Dashboard")

# Two audiences, two tabs. Validation results and pipeline health answer
# different questions and are read at different times; the split also means the
# pipeline panel is reachable on a fresh stack where no simulation run exists
# yet. Streamlit runs both bodies regardless of which tab is selected, so each
# side guards its own empty case rather than the page stopping early.
validation_tab, pipeline_tab = st.tabs(["Validation", "Pipeline"])

runs = load_runs()
scenario_map = load_scenarios()
run_ids = [r.id for r in runs]

with validation_tab:
    if not runs:
        st.info("No simulation runs yet. Generate scenarios and start workers.")
    else:
        metrics_map = load_metrics(run_ids)
        failures_map = load_failures(run_ids)
        runs_map = {r.id: r for r in runs}

        render_run_table(runs, metrics_map, failures_map, scenario_map)

        st.markdown("---")
        render_metric_charts(runs, metrics_map, scenario_map)

        st.markdown("---")
        render_failure_list(failures_map, runs_map)

with pipeline_tab:
    reconciliation = load_reconciliation()
    counts, silver_error = load_silver_counts()
    if silver_error and reconciliation is not None:
        reconciliation = replace(
            reconciliation,
            unavailable={**reconciliation.unavailable, "silver": silver_error},
        )

    render_reconciliation(
        reconciliation,
        counts["processed"],
        counts["windows"],
        counts["failures"],
    )
