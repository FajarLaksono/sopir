"""Train the Lane Keeping Assist PPO agent.

Training is offline: it talks to the simulator through gym-donkeycar and writes
only model files. It never touches the JSONL file, the Kafka topic, or the
database.

Examples
--------
    python tools/train_lka.py                       # 200k steps, resume if possible
    python tools/train_lka.py --timesteps 2048      # smoke test
    python tools/train_lka.py --fresh               # ignore old checkpoints
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Running a file inside tools/ puts tools/ (not the repo root) on sys.path.
# The next two lines make ``from sdk.features import ...`` work either way.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import gymnasium as gym
import gym_donkeycar  # noqa: F401  (registers donkey-* environments)
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import CheckpointCallback
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv

from sdk.features import DonkeyLKAWrapper

DEFAULT_TRACK = "donkey-warehouse-v0"
DEFAULT_MODEL_DIR = REPO_ROOT / "data" / "models" / "lka_ppo"
FINAL_MODEL = REPO_ROOT / "data" / "models" / "lka_ppo_final.zip"
CHECKPOINT_EVERY = 20_000


def make_env(track: str, host: str, port: int):
    """Return a factory: SB3 builds the env itself (and can reset it for us)."""

    def _init():
        conf = {"exe_path": "remote", "host": host, "port": port}
        env = gym.make(track, conf=conf)
        return Monitor(DonkeyLKAWrapper(env))

    return _init


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the LKA PPO agent")
    parser.add_argument("--track", default=DEFAULT_TRACK, help="gym env id")
    parser.add_argument("--sim-host", default="127.0.0.1", help="simulator host")
    parser.add_argument("--sim-port", type=int, default=9091, help="simulator port")
    parser.add_argument(
        "--timesteps", type=int, default=200_000, help="target total timesteps"
    )
    parser.add_argument(
        "--model-dir", type=Path, default=DEFAULT_MODEL_DIR, help="checkpoint folder"
    )
    parser.add_argument(
        "--fresh", action="store_true", help="ignore existing checkpoints"
    )
    return parser.parse_args()


def newest_checkpoint(model_dir: Path) -> Path | None:
    """Pick the checkpoint with the largest step number (not the newest mtime)."""
    best_path: Path | None = None
    best_step = -1
    for path in model_dir.glob("ckpt_*_steps.zip"):
        match = re.search(r"ckpt_(\d+)_steps\.zip$", path.name)
        if match is None:
            continue
        step = int(match.group(1))
        if step > best_step:
            best_path, best_step = path, step
    return best_path


def main() -> None:
    args = parse_args()
    args.model_dir.mkdir(parents=True, exist_ok=True)

    env = DummyVecEnv([make_env(args.track, args.sim_host, args.sim_port)])

    resume_from = None if args.fresh else newest_checkpoint(args.model_dir)
    if resume_from is not None:
        print(f"[train] resuming from {resume_from}")
        model = PPO.load(resume_from, env=env, device="cpu")
    else:
        print("[train] starting a fresh policy")
        model = PPO(
            "MlpPolicy",
            env,
            n_steps=2048,
            batch_size=64,
            learning_rate=3e-4,
            gamma=0.99,
            policy_kwargs={"net_arch": [64, 64]},
            device="cpu",
            verbose=1,
        )

    remaining = max(0, args.timesteps - model.num_timesteps)
    if remaining == 0:
        model.save(FINAL_MODEL)
        print(f"[train] already at {model.num_timesteps} steps -> {FINAL_MODEL}")
        env.close()
        return

    checkpoint_callback = CheckpointCallback(
        save_freq=CHECKPOINT_EVERY,
        save_path=str(args.model_dir),
        name_prefix="ckpt",
        verbose=1,
    )

    print(f"[train] {model.num_timesteps} -> {args.timesteps} ({remaining} new steps)")
    try:
        model.learn(
            total_timesteps=remaining,
            callback=checkpoint_callback,
            reset_num_timesteps=False,
        )
    except KeyboardInterrupt:
        print("\n[train] interrupted - saving the current state")

    # The callback fires every 20k *new* steps. Saving here means a Ctrl+C at
    # step 27,431 (or at the end) still leaves an exact checkpoint to resume.
    model.save(args.model_dir / f"ckpt_{model.num_timesteps}_steps.zip")
    model.save(FINAL_MODEL)
    env.close()
    print(f"[train] checkpoints -> {args.model_dir}")
    print(f"[train] final model -> {FINAL_MODEL}")


if __name__ == "__main__":
    main()
