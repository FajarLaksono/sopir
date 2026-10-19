# TORCS + RL on Windows (WSL2): Setup Notes

A record of what worked, what failed, and how each problem was fixed, so the setup can be repeated without confusion.

## 1. Summary

- **Goal:** run the TORCS racing simulator with an external program (Python) reading sensors and sending controls, for reinforcement learning and data logging.
- **Environment:** Windows 10/11, WSL2 with Ubuntu, logged in as `root`.
- **Source used:** the `vtorcs-RL-color` folder inside the gym\_torcs repo (https://github.com/ugo-nama-kun/gym\_torcs).
- **Result:** TORCS compiled, installed and runs, with `scr_server` available as a driver.

## 2. Why this route

| Option | Verdict |
| --- | --- |
| Official TORCS 1.3.7 win32 | Works, but has no `scr_server`. You must find and apply the SCR patch yourself. |
| Unofficial win64 builds | Do not include `scr_server`, so they are useless for RL. This was the original problem. |
| Compile win64 yourself | Painful because of old dependencies. Not worth it. |
| **WSL2 + gym\_torcs `vtorcs-RL-color`** | **Chosen.** Already contains `scr_server`, and most RL tooling for TORCS assumes Linux. |

Key fact: `scr_server` is not part of stock TORCS. It is a separate bot that opens a UDP port (3001 for the first bot) so an outside program can drive the car.

## 3. Steps that worked

### 3.1 Install WSL2 + Ubuntu

In PowerShell as Administrator:

```
wsl --install -d Ubuntu
```

Reboot, open Ubuntu from the Start menu and create a user. Guide: https://learn.microsoft.com/windows/wsl/install

GUI test: `sudo apt install -y x11-apps` then `xeyes`. A window with eyes means the display works. If not, run `wsl --update` in PowerShell.

### 3.2 Install dependencies

```
sudo apt update
sudo apt install -y build-essential git xautomation \
  libglib2.0-dev libgl1-mesa-dev libglu1-mesa-dev freeglut3-dev \
  libplib-dev libopenal-dev libalut-dev libxi-dev libxmu-dev \
  libxrender-dev libxrandr-dev libpng-dev libvorbis-dev zlib1g-dev \
  libxxf86vm-dev libxxf86dga-dev libxext-dev libx11-dev libxt-dev \
  libsm-dev libice-dev python3 python3-pip python3-venv
```

The last line of X11 packages includes `libxxf86vm-dev`, which was missing from the first list (see problem 1 below).

### 3.3 Download the source

```
cd ~
git clone https://github.com/ugo-nama-kun/gym_torcs.git
```

### 3.4 Configure, patch and build

```
cd ~/gym_torcs/vtorcs-RL-color
CXXFLAGS="-fpermissive -w -O2" CFLAGS="-w -O2" ./configure
```

Before building, fix the one known compile error in the `olethros` bot (see problem 3):

```
cd ~/gym_torcs/vtorcs-RL-color/src/drivers/olethros
sed -i 's/\bisnan(/std::isnan(/g' geometry.cpp
cd ~/gym_torcs/vtorcs-RL-color
make 2>&1 | tee build.log | tail -30
```

The build takes several minutes. A successful build ends with `Leaving directory` lines and no `Error`.

### 3.5 Install and run

```
make install
make datainstall
torcs
```

If the window is black or shows an OpenGL error, try `LIBGL_ALWAYS_SOFTWARE=1 torcs`.

### 3.6 Set up a race with scr\_server

1. Race, then Practice, then Configure Race.
2. Pick a track and continue to Select Drivers.
3. Remove other drivers and add **scr\_server 1**.
4. Accept, then New Race. The game waits for a Python client on UDP port 3001.

## 4. Problems and fixes

### Problem 1: `configure: error: Can't find libXxf86vm`

- **Cause:** a missing X11 development package.
- **Fix:** `sudo apt install -y libxxf86vm-dev`.
- **General rule:** `Can't find libFoo` usually means installing `libfoo-dev`.

### Problem 2: thousands of warning lines during `make`

- Examples: `_BSD_SOURCE and _SVID_SOURCE are deprecated`, `ISO C++ forbids converting a string constant to 'char*'`.
- **Cause:** 2000s-era code compiled with a modern GCC.
- **Fix:** none needed. Warnings do not stop the build. Only a line containing `error:` followed by `make: *** ... Error 1` is a real failure. The `-w` flag in step 3.4 silences most warnings.

### Problem 3: build stops in `src/drivers/olethros` with `'isnan' was not declared in this scope`

- **Cause:** modern C++ only declares `std::isnan`, not plain `isnan`.
- **Fix:** `sed -i 's/\bisnan(/std::isnan(/g' geometry.cpp` in that folder, then run `make` again.
- **Note:** the `-fpermissive` flag alone did not fix this one. Only the source edit did.
- **If another bot hits the same issue:** apply the same `sed`, adding `s/\bisinf(/std::isinf(/g`, in the folder named in the error path.

### Tip: reading build output

- Save the log: `make 2>&1 | tee build.log | tail -30`.
- Find real errors only: `grep -n "error:" build.log | head`.
- After a failure, `make clean` is optional. `make` resumes where it stopped.

## 5. Ideas that were suggested but not needed

- Removing broken bots from `src/drivers/Makefile` as a fallback. Not needed once the `isnan` patch was applied.
- Win32 TORCS with an SCR patch. Still valid as a native-Windows alternative, but not used.

## 6. Not yet confirmed

TORCS launching is confirmed. The following steps from the original plan have not been verified yet:

- `scr_server 1` waiting for a client after New Race.
- The Python connection. The original gym\_torcs scripts (`snakeoil3_gym.py`, `example_experiment.py`) are Python 2 era and may need `print` and syntax fixes.
- Python environment: `python3 -m venv ~/torcs-env`, `source ~/torcs-env/bin/activate`, `pip install numpy`.

## 7. How the control loop works

1. TORCS runs the race with `scr_server` as the driver and waits.
2. The Python client sends `SCR(init ...)` to UDP `localhost:3001` and waits for `***identified***`.
3. Every tick (about 50 Hz) TORCS sends sensors: `angle`, `trackPos`, `speedX/Y/Z`, `track` (19 range finders), `rpm`, `damage`, `distRaced`, `opponents`.
4. The client replies with `steer`, `accel`, `brake`, `gear`, `clutch`, and `meta=1` to restart the race.

## 8. Next steps

1. Get a Python client connecting and the car moving.
2. Log `(state, action)` to CSV or Parquet for imitation learning or offline RL.
3. Wrap the loop in a Gymnasium environment and train with Stable-Baselines3 (PPO or SAC).
4. Optional: headless training with `xvfb-run`, or several TORCS instances on different ports.

## 9. Quick checklist for a fresh rebuild

1. Install WSL2 + Ubuntu.
2. Install the full dependency list (3.2).
3. Clone gym\_torcs.
4. Run `configure` with the permissive flags.
5. Apply the `isnan` patch to `olethros/geometry.cpp`.
6. Run `make`, then `make install` and `make datainstall`.
7. Run `torcs` and configure a race with `scr_server 1`.

## 10. Addendum: image and texture warnings at startup

### Problem 4: `Can't open file tracks/oval/backyard4/backyard4.png` and `Failed to open shadow2.rgb`

Messages seen when running `torcs`:

```
Compatibility mode, properties unknown.
Can't open file tracks/oval/backyard4/backyard4.png
WARNING: grscene:initBackground Failed to open shadow2.rgb for reading
```

- **Cause:** the `backyard4` track does not exist in this repo. `find ~/gym_torcs -name "backyard4.png"` returned nothing. TORCS was loading a default or stale race config that pointed to that missing track. The `shadow2.rgb` file only exists inside each track's own folder, so it was missing for the same reason.
- **Not a broken install.** The repo's other tracks do contain `shadow2.rgb`.
- **Fix:** choose a track that exists in Race, then Practice, then Configure Race. Tracks present in this repo include `oval/michigan`, `road/alpine-1`, `road/g-track-2`, `road/street-1` and `aalborg`. An oval track is a good first choice for testing a simple agent.
- **Result:** after choosing a valid track, most images appeared and the warnings were no longer a problem.

### Which messages can be ignored

| Message | Meaning | Action |
| --- | --- | --- |
| `Compatibility mode, properties unknown` | OpenGL under WSLg cannot detect GPU features | Ignore, normal under WSL |
| `Can't open file ... backyard4.png` | Menu preview image of a track that is not installed | Pick an installed track |
| `Failed to open shadow2.rgb` | Car shadow texture for the missing track | Cosmetic, ignore |

None of these affect `scr_server` or RL. The only thing that matters for RL is that `scr_server` is installed.

### Useful checks

```
find ~/gym_torcs -name "backyard4.png"          # empty = track not in repo
ls /usr/local/share/games/torcs/tracks/        # installed tracks
ls /usr/local/lib/torcs/drivers/               # should list scr_server
grep -rl backyard4 ~/.torcs                    # config still pointing at the missing track
```

If a stale config keeps pointing at a missing track, reset it with `mv ~/.torcs ~/.torcs.bak` and relaunch `torcs`. TORCS recreates the config, and the race and `scr_server 1` driver must be set up again.

### Status after this fix

- TORCS builds, installs and runs under WSL2.
- Races start on valid tracks and menu images mostly load.
- Still to do: connect the Python client (section 6) and begin logging data or training.

## 11. Addendum: scr\_server not appearing (car vanishing)

### Problem 5: `scr_server` disappears in Select Drivers, and some bots vanish

Symptoms:

- `car7-trb1` appeared for the Player but not for bots.
- `car1-trb1` appeared for bots but `scr_server` was missing when it was used.

What was checked:

- The installed `car1-trb1` folder has all the files a car needs (`.xml`, `.acc`, texture `.rgb`, `shadow.rgb`, `tex-wheel.rgb`). The files only in the source folder (`CVS`, `Makefile`, `-src.ac`, `.xcf`) are editing files that `datainstall` skips on purpose, so nothing was missing.
- `scr_server.xml` had all 10 entries (`scr_server 1` to `scr_server 10`) set to `car name = car1-trb1`.
- The exact reason `car1-trb1` hid `scr_server` was not confirmed. A category filter in Select Drivers was suspected but not verified.

### Fix that worked

Set every `scr_server` entry to use `car7-trb1`, the car that works.

```
cd /usr/local/share/games/torcs/drivers/scr_server
cp scr_server.xml scr_server.xml.bak
sed -i 's/\(name="car name" val="\)[^"]*"/\1car7-trb1"/' scr_server.xml
grep -n "car name" scr_server.xml
```

All 10 lines should show `car7-trb1`. Apply the same edit to the source copy so a rebuild does not undo it (`find ~/gym_torcs -name scr_server.xml`), then reset the cached race config and relaunch:

```
mv ~/.torcs ~/.torcs.bak2
torcs
```

After this, `scr_server 1` appears in Select Drivers and the race can be started.

### Notes

- **Pick one car for RL and keep it.** `car7-trb1` handles differently from `car1-trb1`, so a policy trained on one will not carry over to the other.
- **Bots do not matter for RL.** Only `scr_server` with a working car is needed, so it is fine if some bots still vanish.
- **To undo:** copy `scr_server.xml.bak` back over `scr_server.xml`.
- **If it breaks again:** check which car each driver uses with `grep -H "car name" /usr/local/share/games/torcs/drivers/*/*.xml`.
