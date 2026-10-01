---
name: dashboard-components
description: Build and modify Streamlit views in dashboard/. Use when adding a metric chart, changing the runs table or failure list, wiring a filter, or debugging a slow or empty dashboard page.
compatibility: opencode
metadata:
  project: sopir
  layer: dashboard
---

## What I do

```
dashboard/
├── app.py                          # page config, session/engine, @st.cache_data loaders, layout
└── components/
    ├── run_table.py                # render_run_table(runs, metrics_map, failures_map, scenario_map)
    ├── metric_charts.py            # render_metric_charts(metrics_map, ...)
    └── failure_list.py             # render_failure_list(failures, ...)
```

`app.py` sets `st.set_page_config(page_title="OpenDriveLab Dashboard",
layout="wide")` at import time. It must stay the first Streamlit call.

## The two hard rules

1. **Separate fetching from rendering.** All SQL lives in `app.py` behind
   `@st.cache_data(ttl=30)` loaders (`load_runs`, `load_metrics`, and the
   failure/scenario loaders). Components receive plain data.
2. **Components are pure functions of their arguments.** A component takes
   data and returns Streamlit elements. It opens no session, runs no query and
   reads no env var. This is what makes them reviewable in isolation.

Consequence: no business logic in `dashboard/`. If you need a threshold or a
join rule, it belongs in `evaluation/` and the dashboard only displays it.

## Conventions

- Components are `render_*` and return `None`; they emit Streamlit calls.
- Export them from `dashboard/components/__init__.py`.
- Plotly for charts, `st.dataframe` or AgGrid for tables.
- Render an explicit empty state (`st.info("No simulation runs yet.")`) rather
  than an empty table. A blank panel reads as a bug.

## Performance rules

- Every loader keeps `ttl=30`. Do not raise it to "fix" slowness; the fix is
  an indexed, paginated query.
- With 10k+ runs, paginate and cache aggregates server-side. Do not load all
  telemetry into the page.
- Join through `scenario_map` / `metrics_map` / `failures_map` dictionaries
  keyed by id. Do not emit a query per table row (N+1).

## Debugging an empty dashboard

1. Confirm data exists at the API first:
   `curl -s "http://localhost:8000/api/v1/runs?page_size=5"`
   If the API is empty, the dashboard is correct and the problem is upstream.
2. If the API has runs but the page is blank, check that the loader returns
   before rendering and that `load_runs` is actually called after
   `set_page_config`.
3. If the page errors on the database, check `DATABASE_URL` for the dashboard
   service. Do not edit `app.py` to work around a connection string.
4. Streamlit caches by loader arguments. After changing a loader body, hard
   reload the browser rather than assuming a stale cache.

## Port

Dashboard is published on `8501`. The FastAPI reference is on `8000/docs`.
Opening the dashboard from a command must pick the platform opener for macOS,
Linux or Windows rather than assuming one OS.
