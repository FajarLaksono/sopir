import { Line } from "react-chartjs-2";
import {
  Chart, LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Filler,
} from "chart.js";

Chart.register(LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Filler);

export default function CteChart({ points }) {
  const labels = points.map((p) => p.tick);
  const data = {
    labels,
    datasets: [
      {
        label: "cte",
        data: points.map((p) => p.cte),
        borderColor: "#e15759",
        pointRadius: 0,
        tension: 0.2,
        order: 0,
      },
      {
        // The safe-zone band: from +0.5 down to -0.5, shaded behind the line.
        label: "safe zone",
        data: labels.map(() => 0.5),
        borderColor: "transparent",
        borderDash: [6, 4],
        pointRadius: 0,
        fill: { target: 1, above: "rgba(76,175,80,0.12)" },
        order: 1,
      },
      {
        label: "",
        data: labels.map(() => -0.5),
        borderColor: "rgba(76,175,80,0.5)",
        borderDash: [6, 4],
        pointRadius: 0,
        order: 1,
      },
    ],
  };
  const options = {
    responsive: true,
    animation: false,
    plugins: { legend: { display: false } },
    scales: { x: { display: false }, y: { suggestedMin: -2, suggestedMax: 2 } },
  };
  return <Line data={data} options={options} />;
}
