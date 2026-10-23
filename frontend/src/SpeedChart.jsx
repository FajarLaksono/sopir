import { Line } from "react-chartjs-2";
import {
  Chart, LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend,
} from "chart.js";

Chart.register(LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend);

export default function SpeedChart({ points }) {
  const data = {
    labels: points.map((p) => p.tick),
    datasets: [
      {
        label: "speed",
        data: points.map((p) => p.speed),
        borderColor: "#2f7de1",
        backgroundColor: "rgba(47,125,225,0.15)",
        pointRadius: 0,
        tension: 0.2,
      },
    ],
  };
  const options = {
    responsive: true,
    animation: false,
    plugins: { legend: { display: false } },
    scales: { x: { display: false } },
  };
  return <Line data={data} options={options} />;
}
