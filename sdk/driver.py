import json
import os
import time
import uuid
import sys

import gymnasium as gym
import gym_donkeycar  # noqa: F401  (registers the donkey-* environments)
from PIL import Image
from confluent_kafka import Producer

def flatten(info: dict) -> dict:
    """Turn tuples like pos=(x, y, z) into pos_0, pos_1, pos_2."""
    out = {}
    for key, value in info.items():
        if isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                out[f"{key}_{i}"] = item
        else:
            out[key] = value
    return out

class SeasonDriver:
    def __init__(
        self, 
        season_id: str,
        driver: str = "random",
        track_id: str = "donkey-generated-track-v0",
        sim_host: str = "127.0.0.1",
        sim_port: int = 9091,
        data_dir: str = "data",
        producer=None, # a confluent_kafka.Producer, or None = file only
    ):
        self.season_id = season_id
        self.driver = driver
        self.track_id = track_id
        self.sim_host = sim_host
        self.sim_port = sim_port
        self.data_dir = data_dir
        self.producer = producer

        self.run_id: str | None = None
        self.tick = 0
        self.buffer: list[dict] = []
        self.produce_errors = 0


    def _start_run(self) -> None:
        self.run_id = str(uuid.uuid4())
        self.tick = 0
        self.buffer = []
        print(f"[season {self.season_id[:8]}] run {self.run_id[:8]} started")
        self._send_marker("run_start", {"driver": self.driver})

    def _send_marker(self, msg_type: str, extra: dict) -> None:
        if self.producer is None:
            return
        msg = {"type": msg_type, "season_id": self.season_id, "run_id": self.run_id, **extra}
        self.producer.produce(topic="telemetry", key=self.season_id, value=json.dumps(msg))
        self.producer.poll(0)


    def _build_record(self, action, reward, terminated, info) -> dict:
        """One telemetry row for one tick (fields per tech spec §5.2)."""
        flat = flatten(info)
        return {
            "type": "telemetry",
            "season_id": self.season_id,
            "run_id": self.run_id,
            "tick": self.tick,
            "ts": time.time(),
            "steering": float(action[0]),
            "throttle": float(action[1]),
            "speed": float(flat.get("speed", 0.0)),
            "forward_vel": float(flat.get("forward_vel", 0.0)),
            "cte": float(flat.get("cte", 0.0)),          # lane deviation — the key signal
            "pos_x": float(flat.get("pos_0", 0.0)),
            "pos_y": float(flat.get("pos_1", 0.0)),
            "pos_z": float(flat.get("pos_2", 0.0)),
            "vel_x": float(flat.get("vel_0", 0.0)),
            "vel_y": float(flat.get("vel_1", 0.0)),
            "vel_z": float(flat.get("vel_2", 0.0)),
            "accel_x": float(flat.get("accel_0", 0.0)),
            "accel_y": float(flat.get("accel_1", 0.0)),
            "accel_z": float(flat.get("accel_2", 0.0)),
            "gyro_x": float(flat.get("gyro_0", 0.0)),
            "gyro_y": float(flat.get("gyro_1", 0.0)),
            "gyro_z": float(flat.get("gyro_2", 0.0)),
            "hit": str(flat.get("hit", "none")),
            "reward": float(reward),
            "lap_count": int(flat.get("lap_count", 0)),
            "last_lap_time": float(flat.get("last_lap_time", 0.0)),
            "terminated": bool(terminated),
        }


    def _save_frame(self, obs) -> None:
        path = os.path.join(self.data_dir, "seasons", self.season_id, "runs", self.run_id, "frames")
        os.makedirs(path, exist_ok=True)
        Image.fromarray(obs).save(os.path.join(path, f"{self.tick:06d}.jpg"), quality=85)

    def _flush(self) -> None:
        if not self.buffer:
            return
        # 1) always: the raw zone (source of truth)
        path = os.path.join(self.data_dir, "seasons", self.season_id, "raw.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for rec in self.buffer:
                f.write(json.dumps(rec) + "\n")

        # 2) best effort: the stream
        if self.producer is not None:
            for rec in self.buffer:
                self.producer.produce(
                    topic="telemetry",
                    key=self.season_id,           # keeps one season in order
                    value=json.dumps(rec),
                    on_delivery=self._on_delivery,
                )
            self.producer.poll(0)                 # let the client send + fire callbacks
            print(f"[flush] {len(self.buffer)} rows -> raw.jsonl + topic (errors so far: {self.produce_errors})")
        else:
            print(f"[flush] {len(self.buffer)} rows -> raw.jsonl")
        self.buffer = []

    def _on_delivery(self, err, _msg) -> None:
        if err is not None:
            self.produce_errors += 1
            # print the first few, then every 100th — don't drown the log
            if self.produce_errors <= 5 or self.produce_errors % 100 == 0:
                print(f"[kafka] produce failed ({self.produce_errors} so far): {err}")
                

    def run(self) -> None:
        conf = {"exe_path": "remote", "host": self.sim_host, "port": self.sim_port}
        env = gym.make(self.track_id, conf=conf)
        obs, info = env.reset()
        print(f"[season {self.season_id[:8]}] connected to sim on {self.sim_host}:{self.sim_port}")
        self._start_run()

        try:
            while True:
                started = time.time()

                # 1) brain decides — random for now
                action = env.action_space.sample()

                # 2) sim moves and answers with sensors
                obs, reward, terminated, truncated, info = env.step(action)

                # 3) record this tick
                self.buffer.append(self._build_record(action, reward, terminated, info))
                if self.tick % 5 == 0:
                    self._save_frame(obs)

                self.tick += 1
                if self.tick % 50 == 0:
                    self._flush()

                # 4) crash? close the run, open the next one
                if terminated or truncated:
                    print(f"[crash] run {self.run_id[:8]} ended at tick {self.tick}")
                    self._flush()
                    self._send_marker("run_end", {"status": "crashed", "last_tick": self.tick})
                    self._start_run()
                    obs, info = env.reset()

                # hold roughly 20 ticks per second
                time.sleep(max(0.0, 0.05 - (time.time() - started)))
        except KeyboardInterrupt:
            print("\n[stop] Ctrl+C — flushing and closing the run")
            self._flush()
            self._send_marker("run_end", {"status": "completed", "last_tick": self.tick})
        finally:
            if self.producer is not None:
                self.producer.flush(timeout=5)    # give in-flight messages up to 5s to leave
            env.close()
            print(f"[season {self.season_id[:8]}] sim closed")
            print(f"[season {self.season_id[:8]}] data: {os.path.join(self.data_dir, 'seasons', self.season_id)}")


if __name__ == "__main__":
    track = sys.argv[1] if len(sys.argv) > 1 else "donkey-generated-track-v0"
    producer = Producer({
        "bootstrap.servers": "localhost:19092",
        "client.id": "sopir-smoke"
    })
    SeasonDriver(
        # season_id=f"smoke-{uuid.uuid4().hex[:8]}",
        season_id=str(uuid.uuid4()),
        track_id=track,
        producer=producer,
    ).run()