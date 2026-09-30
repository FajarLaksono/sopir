import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from backend.models import Scenario, SimulationRun, Metrics, Failure
from backend.config import settings


st.set_page_config(page_title="OpenDriveLab Dashboard", layout="wide")

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@st.cache_data(ttl=30)
def load_runs():
    db = SessionLocal()
    try:
        runs = db.execute(select(SimulationRun).order_by(SimulationRun.created_at.desc())).scalars().all()
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

st.subheader("Runs Overview")

run_data = []
for r in runs:
    scenario = scenario_map.get(r.scenario_id)
    m = metrics_map.get(r.id)
    f = failures_map.get(r.id, [])
    critical_failures = sum(1 for ff in f if ff.severity == "critical")

    run_data.append({
        "Run ID": str(r.id)[:8],
        "Scenario": scenario.type if scenario else "Unknown",
        "Status": r.status,
        "Started": r.started_at.strftime("%H:%M:%S") if r.started_at else "-",
        "Completed": r.completed_at.strftime("%H:%M:%S") if r.completed_at else "-",
        "Collisions": m.collision_count if m else "-",
        "Min TTC (s)": f"{m.min_ttc:.2f}" if m and m.min_ttc else "-",
        "Avg Speed": f"{m.avg_speed:.1f}" if m else "-",
        "Failures": len(f),
        "Critical": critical_failures,
    })

df = pd.DataFrame(run_data)
st.dataframe(df, use_container_width=True, hide_index=True)

st.subheader("Failure Details")
all_failures = []
for f_list in failures_map.values():
    all_failures.extend(f_list)

if all_failures:
    failure_data = []
    for f in all_failures:
        failure_data.append({
            "Run ID": str(f.run_id)[:8],
            "Severity": f.severity,
            "Rule": f.rule,
            "Details": str(f.details),
            "Created": f.created_at.strftime("%H:%M:%S"),
        })
    st.dataframe(pd.DataFrame(failure_data), use_container_width=True, hide_index=True)
else:
    st.info("No failures detected yet.")