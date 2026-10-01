"""Failure list component for dashboard."""

import pandas as pd
import streamlit as st


def render_failure_list(
    failures_map: dict,
    runs_map: dict,
) -> None:
    """Render filterable failure list."""
    st.subheader("Failure Details")

    all_failures = []
    for f_list in failures_map.values():
        all_failures.extend(f_list)

    if not all_failures:
        st.info("No failures detected yet.")
        return

    # Filters
    col1, col2 = st.columns([1, 2])
    with col1:
        severities = ["All"] + sorted(set(f.severity for f in all_failures))
        selected_severity = st.selectbox("Severity", severities, key="failure_filter_severity")
    with col2:
        rule_search = st.text_input(
            "Search Rule", placeholder="e.g., collision, ttc, speed", key="failure_filter_rule"
        )

    # Apply filters
    filtered_failures = all_failures
    if selected_severity != "All":
        filtered_failures = [f for f in filtered_failures if f.severity == selected_severity]
    if rule_search:
        filtered_failures = [f for f in filtered_failures if rule_search.lower() in f.rule.lower()]

    if not filtered_failures:
        st.info("No failures match the current filters.")
        return

    failure_data = []
    for f in filtered_failures:
        failure_data.append(
            {
                "Run ID": str(f.run_id)[:8],
                "Severity": f.severity,
                "Rule": f.rule,
                "Details": str(f.details),
                "Created": f.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    df = pd.DataFrame(failure_data)

    # Color coding via column config
    column_config = {
        "Run ID": st.column_config.TextColumn("Run ID", width="small"),
        "Severity": st.column_config.SelectboxColumn(
            "Severity",
            options=["critical", "high", "medium"],
            width="small",
        ),
        "Rule": st.column_config.TextColumn("Rule", width="medium"),
        "Details": st.column_config.TextColumn("Details", width="large"),
        "Created": st.column_config.TextColumn("Created", width="medium"),
    }

    st.dataframe(df, use_container_width=True, hide_index=True, column_config=column_config)
