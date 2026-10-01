"""Metric charts component for dashboard."""

import pandas as pd
import streamlit as st

from backend.models import SimulationRun


def render_metric_charts(
    runs: list[SimulationRun],
    metrics_map: dict,
    scenario_map: dict,
) -> None:
    """Render metric charts using native Streamlit charts."""
    st.subheader("Metric Charts")

    completed_runs = [r for r in runs if r.status == "completed" and r.id in metrics_map]
    if not completed_runs:
        st.info("No completed runs with metrics to display.")
        return

    # Prepare data for charts
    chart_data = []
    for r in completed_runs:
        scenario = scenario_map.get(r.scenario_id)
        m = metrics_map.get(r.id)
        if m:
            chart_data.append(
                {
                    "Run ID": str(r.id)[:8],
                    "Scenario": scenario.type if scenario else "Unknown",
                    "Collisions": m.collision_count,
                    "Min TTC": m.min_ttc if m.min_ttc else 0,
                    "Avg Speed": m.avg_speed,
                    "Speed Violations": m.speed_violations,
                    "Lane Deviations": m.lane_deviations,
                    "TTC Per Step": m.ttc_per_step if m.ttc_per_step else [],
                }
            )

    if not chart_data:
        st.info("No metric data available.")
        return

    df = pd.DataFrame(chart_data)

    # Bar charts
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Collisions per Scenario**")
        if len(df) > 1:
            collision_by_scenario = df.groupby("Scenario")["Collisions"].sum().reset_index()
            st.bar_chart(collision_by_scenario.set_index("Scenario"))
        else:
            st.bar_chart(df.set_index("Run ID")["Collisions"])

    with col2:
        st.markdown("**Min TTC per Run**")
        ttc_df = df[["Run ID", "Min TTC"]].copy()
        ttc_df = ttc_df[ttc_df["Min TTC"] > 0]
        if not ttc_df.empty:
            st.bar_chart(ttc_df.set_index("Run ID"))

    col3, col4 = st.columns(2)

    with col3:
        st.markdown("**Avg Speed per Scenario**")
        if len(df) > 1:
            speed_by_scenario = df.groupby("Scenario")["Avg Speed"].mean().reset_index()
            st.bar_chart(speed_by_scenario.set_index("Scenario"))
        else:
            st.bar_chart(df.set_index("Run ID")["Avg Speed"])

    with col4:
        st.markdown("**Speed Violations per Run**")
        st.bar_chart(df.set_index("Run ID")["Speed Violations"])

    col5, col6 = st.columns(2)

    with col5:
        st.markdown("**Lane Deviations per Run**")
        st.bar_chart(df.set_index("Run ID")["Lane Deviations"])

    with col6:
        st.markdown("**Failure Count per Scenario**")
        if len(df) > 1:
            # We don't have failures in this component, skip or add if needed
            st.info("Failure counts shown in Runs table")

    # TTC Trend Line - allow selecting a run
    st.markdown("---")
    st.markdown("**TTC Trend (Time-to-Collision per Step)**")
    run_options = {
        f"{row['Run ID']} ({row['Scenario']})": row["Run ID"] for _, row in df.iterrows()
    }
    if run_options:
        selected_run_display = st.selectbox("Select run for TTC trend", list(run_options.keys()))
        selected_run_id = run_options[selected_run_display]
        selected_row = df[df["Run ID"] == selected_run_id].iloc[0]
        ttc_per_step = selected_row.get("TTC Per Step", [])
        if ttc_per_step:
            ttc_df = pd.DataFrame(
                {
                    "Step": range(len(ttc_per_step)),
                    "TTC (s)": [v if v is not None else 0 for v in ttc_per_step],
                }
            )
            st.line_chart(ttc_df.set_index("Step"))
        else:
            st.info("No TTC per-step data for this run")
