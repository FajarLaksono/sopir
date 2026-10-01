---
description: Open the Streamlit dashboard and confirm it is serving data
agent: build
---

Open the Sopir dashboard.

Steps:

1. Make sure the stack is up:
   `docker compose ps dashboard backend db`
2. Wait for Streamlit to bind, then check it responds:
   `curl -s -o /dev/null -w "%{http_code}" http://localhost:8501`
3. Open the browser using the platform appropriate opener:
   - macOS: `open http://localhost:8501`
   - Linux: `xdg-open http://localhost:8501`
   - Windows (PowerShell): `Start-Process "http://localhost:8501"`
   Detect the platform before choosing; never hardcode one command for all three.
4. Tell the user what to look at:
   - `dashboard/components/run_table.py` - run lifecycle table, filter by status
   - `dashboard/components/metric_charts.py` - metric comparison across scenarios
   - `dashboard/components/failure_list.py` - filterable failures by severity
   Also available: `http://localhost:8000/docs` for the FastAPI reference.

Troubleshooting:

- Blank page with no rows usually means the dashboard is connected but no run
  has been evaluated yet. Use `/evaluate-run` before debugging the dashboard.
- If Streamlit reports a database error, check `DATABASE_URL` in the dashboard
  service rather than editing `dashboard/app.py`.
- Do not add business logic to `dashboard/`. Data fetching stays in the
  `@st.cache_data(ttl=30)` layer, components stay pure functions.
