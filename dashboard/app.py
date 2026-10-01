import streamlit as st
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.config import settings
from backend.models import Failure, Metrics, Scenario, SimulationRun
from dashboard.components import render_failure_list, render_metric_charts, render_run_table

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


st.title("OpenDriveLab - Simulation Dashboard")

runs = load_runs()
scenario_map = load_scenarios()

if not runs:
    st.info("No simulation runs yet. Generate scenarios and start workers.")
    st.stop()

run_ids = [r.id for r in runs]
metrics_map = load_metrics(run_ids)
failures_map = load_failures(run_ids)

# Build runs map for failure list
runs_map = {r.id: r for r in runs}

# Render components
render_run_table(runs, metrics_map, failures_map, scenario_map)

st.markdown("---")
render_metric_charts(runs, metrics_map, scenario_map)

st.markdown("---")
render_failure_list(failures_map, runs_map)
