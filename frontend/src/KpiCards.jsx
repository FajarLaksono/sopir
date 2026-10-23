export default function KpiCards({ run }) {
  const s = run?.summary;
  const cards = [
    { label: "% in lane", value: s ? `${s.pct_in_lane}%` : "—" },
    { label: "Avg speed", value: s ? s.avg_speed : "—" },
    { label: "Collisions", value: s ? s.collisions : "—" },
    { label: "Laps", value: s ? s.laps : "—" },
  ];
  return (
    <div className="cards">
      {cards.map((c) => (
        <div className="card" key={c.label}>
          <div className="card-value">{c.value}</div>
          <div className="card-label">{c.label}</div>
        </div>
      ))}
    </div>
  );
}
