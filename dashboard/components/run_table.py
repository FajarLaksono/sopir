"""Run table component for dashboard."""

import pandas as pd
import streamlit as st

from backend.models import Scenario, SimulationRun


def render_run_table(
    runs: list[SimulationRun],
    metrics_map: dict,
    failures_map: dict,
    scenario_map: dict,
) -> None:
    """Render the runs overview table with filters."""
    st.subheader("Runs Overview")

    if not runs:
        st.info("No simulation runs yet.")
        return

    # Filters
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        statuses = sorted(set(r.status for r in runs))
        selected_status = st.multiselect(
            "Status", statuses, default=statuses, key="run_filter_status"
        )
    with col2:
        scenario_types = sorted(
            set(scenario_map.get(r.scenario_id, Scenario(type="Unknown")).type for r in runs)
        )
        selected_scenario = st.multiselect(
            "Scenario Type", scenario_types, default=scenario_types, key="run_filter_scenario"
        )
    with col3:
        date_range = st.date_input("Date Range", value=[], key="run_filter_date")

    # Apply filters
    filtered_runs = runs
    if selected_status:
        filtered_runs = [r for r in filtered_runs if r.status in selected_status]
    if selected_scenario:
        filtered_runs = [
            r
            for r in filtered_runs
            if scenario_map.get(r.scenario_id, Scenario(type="Unknown")).type in selected_scenario
        ]
    if date_range and len(date_range) == 2:
        start, end = date_range
        filtered_runs = [
            r for r in filtered_runs if r.created_at and start <= r.created_at.date() <= end
        ]

    run_data = []
    for r in filtered_runs:
        scenario = scenario_map.get(r.scenario_id)
        m = metrics_map.get(r.id)
        f = failures_map.get(r.id, [])

        critical_failures = sum(1 for ff in f if ff.severity == "critical")
        high_failures = sum(1 for ff in f if ff.severity == "high")
        medium_failures = sum(1 for ff in f if ff.severity == "medium")

        # Compute duration
        duration = "-"
        if r.started_at and r.completed_at:
            delta = r.completed_at - r.started_at
            duration = f"{delta.total_seconds():.1f}s"

        run_data.append(
            {
                "Run ID": str(r.id)[:8],
                "Scenario": scenario.type if scenario else "Unknown",
                "Status": r.status,
                "Started": r.started_at.strftime("%H:%M:%S") if r.started_at else "-",
                "Completed": r.completed_at.strftime("%H:%M:%S") if r.completed_at else "-",
                "Duration": duration,
                "Collisions": m.collision_count if m else "-",
                "Min TTC (s)": f"{m.min_ttc:.2f}" if m and m.min_ttc else "-",
                "Avg Speed": f"{m.avg_speed:.1f}" if m else "-",
                "Speed Viol.": m.speed_violations if m else "-",
                "Lane Dev.": m.lane_deviations if m else "-",
                "Failures": len(f),
                "Critical": critical_failures,
                "High": high_failures,
                "Medium": medium_failures,
            }
        )

    if not run_data:
        st.info("No runs match the current filters.")
        return

    df = pd.DataFrame(run_data)

    # Column config for better display
    column_config = {
        "Run ID": st.column_config.TextColumn("Run ID", width="small"),
        "Scenario": st.column_config.TextColumn("Scenario", width="medium"),
        "Status": st.column_config.SelectboxColumn(
            "Status", options=["queued", "running", "completed", "failed"], width="small"
        ),
        "Started": st.column_config.TextColumn("Started", width="small"),
        "Completed": st.column_config.TextColumn("Completed", width="small"),
        "Duration": st.column_config.TextColumn("Duration", width="small"),
        "Collisions": st.column_config.NumberColumn("Collisions", width="small"),
        "Min TTC (s)": st.column_config.TextColumn("Min TTC", width="small"),
        "Avg Speed": st.column_config.TextColumn("Avg Speed", width="small"),
        "Speed Viol.": st.column_config.NumberColumn("Speed Viol.", width="small"),
        "Lane Dev.": st.column_config.NumberColumn("Lane Dev.", width="small"),
        "Failures": st.column_config.NumberColumn("Failures", width="small"),
        "Critical": st.column_config.NumberColumn("Critical", width="small"),
        "High": st.column_config.NumberColumn("High", width="small"),
        "Medium": st.column_config.NumberColumn("Medium", width="small"),
    }

    st.dataframe(df, use_container_width=True, hide_index=True, column_config=column_config)
