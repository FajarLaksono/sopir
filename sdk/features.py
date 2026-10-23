from __future__ import annotations

import gymnasium as gym
import numpy as np
from gymnasium import spaces

LKA_THROTTLE = 0.3
STATE_DIM = 5
COLLISION_PENALTY = 5.0
CTE_WEIGHT = 0.5
STEERING_PENALTY = 0.01


def _vec3(info: dict, key: str, index: int) -> float:
    """Read one component of an (x, y, z)-shaped info field, safely."""
    value = info.get(key, (0.0, 0.0, 0.0))
    if isinstance(value, dict):
        return float(value.get(("x", "y", "z")[index], 0.0))
    try:
        return float(value[index])
    except (TypeError, IndexError, KeyError):
        return 0.0


def extract_state(info: dict, prev_cte: float, prev_steering: float) -> np.ndarray:
    """The five numbers the policy sees (spec 10.2).

    ``cte_rate`` and ``prev_steering`` are the two "memory" features: they let
    the agent react to how fast it is drifting and how jerky its own commands
    are. That is why the caller must pass the previous tick's values.
    """
    cte = float(info.get("cte", 0.0))
    speed = float(info.get("speed", 0.0))
    yaw_rate = _vec3(info, "gyro", 2)  # gyro_z = turning rate
    return np.array(
        [cte, cte - prev_cte, speed, yaw_rate, prev_steering],
        dtype=np.float32,
    )


def lka_reward(info: dict, steering: float, prev_steering: float) -> float:
    """Shaped reward (spec 10.4): go fast, stay centred, avoid jerky turns."""
    cte = float(info.get("cte", 0.0))
    speed = float(info.get("speed", 0.0))
    collision = 1.0 if str(info.get("hit", "none")) != "none" else 0.0
    return (
        speed
        - CTE_WEIGHT * cte * cte
        - COLLISION_PENALTY * collision
        - STEERING_PENALTY * (steering - prev_steering) ** 2
    )


class DonkeyLKAWrapper(gym.Wrapper):
    """Adapt a DonkeyCar env to the LKA task.

    Upstream (training):

        observation: 5 floats (extract_state)
        action:      [steering] in [-1, 1]
        reward:      lka_reward(...)

    Downstream (the real simulator): the wrapper expands the action to
    ``[steering, 0.3]`` and keeps the previous tick's state for the rate and
    smoothness terms.
    """

    def __init__(self, env: gym.Env, throttle: float = LKA_THROTTLE):
        super().__init__(env)
        self.throttle = throttle
        self.action_space = spaces.Box(
            low=np.array([-1.0], dtype=np.float32),
            high=np.array([1.0], dtype=np.float32),
            dtype=np.float32,
        )
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(STATE_DIM,), dtype=np.float32
        )
        self.prev_cte = 0.0
        self.prev_steering = 0.0

    def _state(self, info: dict) -> np.ndarray:
        return extract_state(info, self.prev_cte, self.prev_steering)

    def reset(self, **kwargs):
        _obs, info = self.env.reset(**kwargs)
        self.prev_cte = float(info.get("cte", 0.0))
        self.prev_steering = 0.0
        return self._state(info), info

    def step(self, action):
        steering = float(
            np.clip(np.asarray(action, dtype=np.float32).reshape(-1)[0], -1.0, 1.0)
        )
        raw_action = np.array([steering, self.throttle], dtype=np.float32)
        _obs, _sim_reward, terminated, truncated, info = self.env.step(raw_action)

        # Build the state BEFORE updating the memory fields: cte_rate and the
        # steering delta must compare "now" with "the previous command".
        state = self._state(info)
        reward = lka_reward(info, steering, self.prev_steering)

        self.prev_cte = float(info.get("cte", 0.0))
        self.prev_steering = steering
        return state, reward, terminated, truncated, info
