import gymnasium as gym
import numpy as np
import pytest
from gymnasium import spaces

from sdk.features import (
    LKA_THROTTLE,
    DonkeyLKAWrapper,
    extract_state,
    lka_reward,
)


def test_extract_state_uses_gyro_z():
    info = {"cte": 0.4, "speed": 3.0, "gyro": (0.1, 0.2, 0.3)}
    state = extract_state(info, prev_cte=0.1, prev_steering=0.25)
    assert state.dtype == np.float32
    assert np.allclose(state, [0.4, 0.3, 3.0, 0.3, 0.25])


def test_extract_state_handles_missing_and_dict_gyro():
    assert np.allclose(extract_state({}, 0.0, 0.0), [0, 0, 0, 0, 0])
    state = extract_state({"gyro": {"x": 1, "y": 2, "z": 3}}, 0.0, 0.0)
    assert state[3] == 3.0


def test_reward_formula():
    info = {"cte": 2.0, "speed": 5.0, "hit": "none"}
    reward = lka_reward(info, steering=0.5, prev_steering=0.1)
    expected = 5.0 - 0.5 * 4.0 - 0.01 * (0.4**2)
    assert reward == pytest.approx(expected)


def test_reward_collision_penalty():
    info = {"cte": 0.0, "speed": 2.0, "hit": "pole"}
    assert lka_reward(info, 0.0, 0.0) == pytest.approx(2.0 - 5.0)


class FakeDonkeyEnv(gym.Env):
    """Enough of gym-donkeycar to exercise the wrapper without a simulator."""

    def __init__(self):
        self.observation_space = spaces.Box(0, 255, (2, 2, 3), dtype=np.uint8)
        self.action_space = spaces.Box(
            low=np.array([-1.0, 0.0], dtype=np.float32),
            high=np.array([1.0, 1.0], dtype=np.float32),
            dtype=np.float32,
        )
        self.actions = []

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        return np.zeros((2, 2, 3), dtype=np.uint8), {
            "cte": 0.5,
            "speed": 2.0,
            "hit": "none",
            "gyro": (0.0, 0.0, 0.1),
        }

    def step(self, action):
        self.actions.append(np.asarray(action).copy())
        info = {
            "cte": 0.2,
            "speed": 3.0,
            "hit": "none",
            "gyro": (0.0, 0.0, 0.2),
        }
        return np.zeros((2, 2, 3), dtype=np.uint8), 0.0, False, False, info


def test_wrapper_expands_action_and_tracks_state():
    raw = FakeDonkeyEnv()
    env = DonkeyLKAWrapper(raw)

    state, _info = env.reset()
    assert state.shape == (5,)
    assert np.allclose(state, [0.5, 0.0, 2.0, 0.1, 0.0])

    state, reward, terminated, truncated, _info = env.step(np.array([0.25]))
    assert np.allclose(raw.actions[-1], [0.25, LKA_THROTTLE])
    assert np.allclose(state, [0.2, -0.3, 3.0, 0.2, 0.0])
    assert reward == pytest.approx(3.0 - 0.5 * 0.2**2 - 0.01 * 0.25**2)
    assert env.prev_cte == 0.2
    assert env.prev_steering == 0.25
    assert terminated is False and truncated is False


def test_wrapper_clips_steering():
    raw = FakeDonkeyEnv()
    env = DonkeyLKAWrapper(raw)
    env.reset()
    env.step(np.array([2.0]))
    assert np.allclose(raw.actions[-1], [1.0, LKA_THROTTLE])
