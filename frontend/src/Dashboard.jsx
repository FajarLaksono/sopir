import { useEffect, useState } from "react";
import { api } from "./api";
import KpiCards from "./KpiCards";
import SpeedChart from "./SpeedChart";
import CteChart from "./CteChart";
import TrackMap from "./TrackMap";

const POLL_MS = 2000;

export default function Dashboard() {
  const [seasons, setSeasons] = useState([]);
  const [seasonId, setSeasonId] = useState(null);
  const [runs, setRuns] = useState([]);
  const [runId, setRunId] = useState(null);
  const [points, setPoints] = useState([]);
  const [error, setError] = useState("");

  // 1) seasons once on mount
  useEffect(() => {
    api.seasons().then(setSeasons).catch((e) => setError(e.message));
  }, []);

  // 2) runs whenever the season changes
  useEffect(() => {
    if (!seasonId) return;
    api.runs(seasonId).then((r) => {
      setRuns(r);
      setRunId(r[0]?.id ?? null);   // newest run first
    }).catch((e) => setError(e.message));
  }, [seasonId]);

  // 3) poll telemetry for the selected run (the "live" part)
  useEffect(() => {
    if (!seasonId || !runId) return;
    let alive = true;
    const load = () =>
      api.telemetry(seasonId, runId, 1000)
        .then((p) => alive && setPoints(p))
        .catch((e) => setError(e.message));
    load();
    const timer = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(timer); };
  }, [seasonId, runId]);

  const selectedRun = runs.find((r) => r.id === runId);

  return (
    <div className="dashboard">
      <header>
        <h1>Sopir dashboard</h1>
        <div className="pickers">
          <select value={seasonId ?? ""} onChange={(e) => setSeasonId(e.target.value)}>
            <option value="">Select a season…</option>
            {seasons.map((s) => (
              <option key={s.id} value={s.id}>
                {new Date(s.created_at).toLocaleString()} · {s.track_id}
              </option>
            ))}
          </select>
          <select value={runId ?? ""} onChange={(e) => setRunId(e.target.value)}>
            <option value="">Select a run…</option>
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.driver} · {r.status} · {new Date(r.started_at).toLocaleTimeString()}
              </option>
            ))}
          </select>
        </div>
      </header>

      {error && <p className="error">{error}</p>}

      {selectedRun && <KpiCards run={selectedRun} />}

      {points.length > 0 && (
        <div className="charts">
          <section><h2>Speed</h2><SpeedChart points={points} /></section>
          <section><h2>Lane deviation (cte)</h2><CteChart points={points} /></section>
          <section><h2>Track</h2><TrackMap points={points} /></section>
        </div>
      )}
    </div>
  );
}
