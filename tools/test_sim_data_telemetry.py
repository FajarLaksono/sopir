import csv
import time
import os
from datetime import datetime
import gymnasium as gym
import gym_donkeycar

conf = {"exe_path": "remote", "host": "127.0.0.1", "port": 9091}
env = gym.make("donkey-generated-track-v0", conf=conf)
obs, info = env.reset()
print("Available info keys:", list(info.keys()))


def flatten(d):
    """Turn tuples/lists like pos=(x, y, z) into pos_0, pos_1, pos_2."""
    out = {}
    for k, v in d.items():
        if isinstance(v, (list, tuple)):
            for j, item in enumerate(v):
                out[f"{k}_{j}"] = item
        else:
            out[k] = v
    return out


rows = []
for i in range(200):
    action = env.action_space.sample()
    obs, reward, terminated, truncated, info = env.step(action)

    row = {"step": i, "time": time.time(),
           "steering": action[0], "throttle": action[1],
           "reward": reward, "terminated": terminated}
    row.update(flatten(info))
    rows.append(row)
    print(i, info.get("speed"), info.get("cte"))

    if terminated or truncated:
        obs, info = env.reset()

env.close()

# Write after the loop so every row has the same columns
os.makedirs("data", exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
filename = os.path.join("data", f"telemetry_{stamp}.csv")

fieldnames = sorted({k for r in rows for k in r})
with open(filename, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)

print(f"Saved {len(rows)} rows to {filename}")