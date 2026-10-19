# Donkey Car Simulator on Windows: Install Guide

Written for Fajar Laksono, from the working setup of October 2026.

## Goal

Run the Donkey Car (Unity) simulator and control it from a Python script so telemetry (speed, cross-track error, position, images) can be collected.

## The setup that works

| Part | What was used |
| --- | --- |
| Where everything runs | Native Windows (simulator and Python script both on Windows) |
| Simulator | `donkey_sim.exe` (Windows build from the gym-donkeycar releases page) |
| Python | A virtual environment (`venv`) inside the project folder |
| gym-donkeycar | Installed from GitHub (version 1.3.1 or newer), not from PyPI |
| Gym library | Gymnasium 1.4.0 |
| Connection | Script connects to the already-running sim on `127.0.0.1:9091` |

The laptop is a Lenovo IdeaPad Gaming 3 (Ryzen 7 4800H, 16 GB RAM, 4 GB dedicated GPU plus Radeon integrated graphics). The simulator runs well on it when everything is on Windows.

## Step 1: Download the simulator

1. Open https://github.com/tawnkramer/gym-donkeycar/releases
2. Download the Windows zip from the Assets section of the latest release (the name usually contains `Win`).
3. Unzip it to a simple folder, for example `D:\Project\DonkeySimWin\`.
4. Confirm `donkey_sim.exe` is inside. Double-click it once to check that the track menu appears.

## Step 2: Create the project and virtual environment

In PowerShell:

```powershell
cd D:\Project\Github\FajarLaksono\sopir
python -m venv venv
venv\Scripts\activate
python --version
```

The prompt should start with `(venv)`. Python 3.11 is the safest choice for compatibility. If `py -3.11` is available, you can use `py -3.11 -m venv venv` instead.

Always activate the venv in each new terminal before running scripts.

## Step 3: Install gym-donkeycar from GitHub

Important: do **not** use plain `pip install gym-donkeycar`. PyPI gave version 1.0.13, which uses the old `gym` library and registers no environments in Gymnasium. Install from GitHub instead:

```powershell
pip uninstall -y gym-donkeycar gym
pip install git+https://github.com/tawnkramer/gym-donkeycar
```

If `git` is not recognized, install Git for Windows, or use the zip instead:

```powershell
pip install https://github.com/tawnkramer/gym-donkeycar/archive/refs/heads/master.zip
```

If Gymnasium is missing, install it too:

```powershell
pip install gymnasium
```

## Step 4: Verify the install

Save as `check.py` and run `python check.py`:

```python
import gymnasium as gym
import gym_donkeycar

print(gym_donkeycar.__file__)
print([k for k in gym.registry if "donkey" in k])
```

You should see a non-empty list with names like `donkey-generated-track-v0`. If the list is empty, the wrong gym-donkeycar version is installed. Check with `pip show gym-donkeycar`.

## Step 5: Test script

Save as `test_sim.py`:

```python
import gymnasium as gym
import gym_donkeycar

conf = {"exe_path": "remote", "host": "127.0.0.1", "port": 9091}
env = gym.make("donkey-generated-track-v0", conf=conf)
obs, info = env.reset()

for i in range(200):
    obs, reward, terminated, truncated, info = env.step(env.action_space.sample())
    print(i, info.get("speed"), info.get("cte"))
    if terminated or truncated:
        obs, info = env.reset()

env.close()
```

`"exe_path": "remote"` means the script does not launch the sim. You start it yourself.

## Step 6: Run it

1. Start `donkey_sim.exe` and leave it on the track menu.
2. In PowerShell, with the venv active:

```powershell
python test_sim.py
```

The car should move in the sim window and the terminal should print speed and CTE values.

## Optional: Log telemetry to CSV with images

Save as `log_telemetry.py`. The sim must already be running.

```python
import csv
import os
import gymnasium as gym
import gym_donkeycar
from PIL import Image

conf = {"exe_path": "remote", "host": "127.0.0.1", "port": 9091}
env = gym.make("donkey-generated-track-v0", conf=conf)
obs, info = env.reset()
print("Available info keys:", list(info.keys()))

os.makedirs("data/images", exist_ok=True)
with open("data/telemetry.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["step", "img", "steering", "throttle", "speed", "cte", "x", "y", "z", "hit"])

    for i in range(2000):
        action = env.action_space.sample()  # replace with your own controller
        obs, reward, terminated, truncated, info = env.step(action)

        img_path = f"images/{i}.jpg"
        Image.fromarray(obs).save(f"data/{img_path}")
        x, y, z = info.get("pos", (None, None, None))
        w.writerow([i, img_path, action[0], action[1],
                    info.get("speed"), info.get("cte"), x, y, z, info.get("hit")])

        if terminated or truncated:
            obs, info = env.reset()

env.close()
```

Telemetry fields depend on the simulator build. The official docs list steering angle, throttle, speed, image, hit, accelerometer and gyro values, pitch/roll/yaw, and node info, with position, velocity and CTE marked as training-only. Print the keys once to see what your build provides.

## Troubleshooting log

These are the problems hit during setup and how each was fixed.

| Symptom | Cause | Fix |
| --- | --- | --- |
| `EnvironmentNameNotFound: donkey` | The conda env was never created | Create it, or use a venv instead |
| `donkey: command not found` | Only the gym wrapper was installed, not the full Donkeycar framework | Not needed for gym-only telemetry work |
| `CondaToSNonInteractiveError` | Conda requires accepting channel terms | Run the two `conda tos accept` commands from the error, or use `-c conda-forge --override-channels` |
| `ModuleNotFoundError: gymnasium` (in WSL) | `wsl python3` used the system Python, not the conda env | Run `conda activate donkey` inside WSL first |
| `NameNotFound: donkey-generated-track` | PyPI gym-donkeycar 1.0.13 is too old and does not register in Gymnasium | Install from GitHub (Step 3) |
| `ModuleNotFoundError: gymnasium` (in venv) | Gymnasium was not installed in the venv | `pip install gymnasium` |
| Linux sim in WSL was very slow | WSLg likely used software rendering | Run the sim on Windows instead |
| Laptop crashed when script ran with sim on Windows and script in WSL | Not fully diagnosed; WSL combined with a leftover Linux sim and hybrid GPU are suspects | Running both on Windows was stable |

## Notes and lessons

- Running the sim and script both on Windows was stable and fast. Prefer this setup.
- If you try WSL again, run the sim on Windows with `"exe_path": "remote"` and use the Windows host IP (from `ip route | grep default`) as `"host"`. Make sure no Linux copy of the sim is still running (`pkill -f donkey_sim`).
- The laptop has hybrid graphics. If the sim ever misbehaves, set `donkey_sim.exe` to the high-performance GPU in Windows Settings, Display, Graphics, and keep NVIDIA and AMD drivers updated.
- Only one simulator instance should run at a time, because it listens on port 9091.

## Next steps

- Replace the random actions with a controller (keyboard, joystick or a simple rule-based driver).
- Add timestamps to each logged row for consistent timing.
- If you want the full drive, train and test cycle, install the Donkeycar framework in a separate environment and follow the official guide at https://docs.donkeycar.com/guide/deep\_learning/simulator/
