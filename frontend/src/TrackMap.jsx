import { Scatter } from "react-chartjs-2";
import {
  Chart, PointElement, LineElement, LinearScale, Tooltip,
} from "chart.js";

Chart.register(PointElement, LineElement, LinearScale, Tooltip);

export default function TrackMap({ points }) {
  const data = {
    datasets: [
      {
        label: "path",
        data: points.map((p) => ({ x: p.pos_x, y: p.pos_y })),
        showLine: true,
        borderColor: "#4caf50",
        pointRadius: 0,
        tension: 0,
      },
    ],
  };
  const options = {
    responsive: true,
    animation: false,
    plugins: { legend: { display: false } },
    scales: { x: { display: false }, y: { display: false } },
  };
  return <Scatter data={data} options={options} />;
}
