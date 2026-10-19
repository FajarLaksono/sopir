#!/usr/bin/env python3
"""Cross-platform helper for the Makefile (Windows + Linux). Standard library only."""
import argparse
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

REPO = "tawnkramer/gym-donkeycar"
IS_WIN = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
EXE_NAMES = {"donkey_sim.exe"} if IS_WIN else {"donkey_sim", "donkey_sim.x86_64"}


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def find_exe(sim_dir):
    for p in sorted(Path(sim_dir).rglob("*")):
        if p.is_file() and p.name in EXE_NAMES:
            return p
    return None


SKIP_DIRS = {"venv", ".venv", ".git", "downloads", "node_modules", "__pycache__"}


def locate_exe(sim_dir):
    """Look in sim_dir first, then anywhere else in the project (cwd)."""
    exe = find_exe(sim_dir) if Path(sim_dir).is_dir() else None
    if exe:
        return exe
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f in EXE_NAMES:
                return Path(root) / f
    return None


def ensure_executable(exe):
    if not IS_WIN:  # zipfile drops the executable bit
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def http_get(url):
    headers = {"User-Agent": "donkey-make"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)


def resolve_url():
    print("Looking up latest release...")
    with http_get(f"https://api.github.com/repos/{REPO}/releases/latest") as r:
        release = json.load(r)
    if IS_WIN:
        pattern = re.compile(r"win", re.I)
    else:
        pattern = re.compile(r"linux", re.I)
    for a in release.get("assets", []):
        name = a["name"]
        if name.lower().endswith(".zip") and pattern.search(name) and "darwin" not in name.lower():
            print(f"Release {release.get('tag_name')}: {name}")
            return a["browser_download_url"]
    die("No matching asset in the latest release. Pass SIM_URL=<direct zip link> to make.")


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def download_with_progress(url, dest):
    """Stream url to dest, showing a progress line (in-place on a terminal)."""
    tty = sys.stdout.isatty()
    with http_get(url) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last_pct, last_print = 0, -1, 0.0
        start = time.time()
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            now = time.time()
            if total:
                pct = done * 100 // total
                step = 1 if tty else 10  # fewer lines when output is piped/logged
                if pct == last_pct or (pct % step and pct != 100) or (tty and now - last_print < 0.1 and pct != 100):
                    continue
                last_pct, last_print = pct, now
                speed = done / max(now - start, 0.001)
                bar = "#" * (pct // 4) + "-" * (25 - pct // 4)
                line = f"  [{bar}] {pct:3d}%  {human(done)} / {human(total)}  {human(speed)}/s"
            else:  # server sent no size
                if now - last_print < 1:
                    continue
                last_print = now
                line = f"  Downloaded {human(done)}"
            print("\r" + line + "   " if tty else line, end="" if tty else "\n", flush=True)
        if tty:
            print()


def cmd_download_sim(args):
    if not (IS_WIN or IS_LINUX):
        die("Unsupported OS: this helper handles Windows and Linux only.")
    sim_dir, dl_dir = Path(args.sim_dir), Path(args.dl_dir)
    existing = None if args.force else locate_exe(sim_dir)
    if existing:
        ensure_executable(existing)
        print(f"Simulator already in project, skipping download: {existing}")
        return
    dl_dir.mkdir(parents=True, exist_ok=True)
    url = args.url or resolve_url()
    zip_path = dl_dir / "sim.zip"
    print(f"Downloading {url}")
    download_with_progress(url, zip_path)
    sim_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        members = z.infolist()
        tty = sys.stdout.isatty()
        for i, m in enumerate(members, 1):
            z.extract(m, sim_dir)
            if tty and (i % 20 == 0 or i == len(members)):
                print(f"\r  Unzipping... {i * 100 // len(members):3d}% ({i}/{len(members)} files)", end="", flush=True)
        print() if tty else print(f"Unzipped {len(members)} files.")
    exe = find_exe(sim_dir)
    if not exe:
        die(f"Could not find {' / '.join(sorted(EXE_NAMES))} after unzip.")
    ensure_executable(exe)
    print(f"Simulator ready: {exe}")


def cmd_run_sim(args):
    exe = locate_exe(args.sim_dir)
    if not exe:
        die("Simulator not found. Run: make sim")
    ensure_executable(exe)
    kwargs = {"cwd": str(exe.parent)}
    if IS_WIN:
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([str(exe.resolve())], **kwargs)
    print("Simulator launched. Leave it on the track menu, then run: make test")


def cmd_stop_sim(_args):
    if IS_WIN:
        cmd = ["taskkill", "/F", "/IM", "donkey_sim.exe"]
    else:
        cmd = ["pkill", "-f", r"donkey_sim(\.x86_64)?( |$)"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        die(f"{cmd[0]} not found.")
    print("Stopped donkey_sim." if r.returncode == 0 else "No donkey_sim running.")


def cmd_wait_sim(args):
    try:
        socket.create_connection((args.host, args.port), timeout=2).close()
    except OSError:
        die(f"Simulator is not listening on {args.host}:{args.port}. Run: make run-sim")


def cmd_stamp(args):
    p = Path(args.path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()


def cmd_clean(args):
    for d in args.paths:
        shutil.rmtree(d, ignore_errors=True)


def cmd_check(_args):
    import gymnasium as gym
    import gym_donkeycar

    print(gym_donkeycar.__file__)
    ids = [k for k in gym.registry if "donkey" in k]
    print(ids)
    if not ids:
        die("No donkey envs registered - wrong gym-donkeycar version installed (check pip show gym-donkeycar).")


def cmd_help(args):
    print("Targets:")
    for line in Path(args.makefile).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_-]+):.*##\s*(.*)$", line)
        if m:
            print(f"  {m.group(1):<10} {m.group(2)}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("download-sim")
    p.add_argument("--sim-dir", required=True)
    p.add_argument("--dl-dir", required=True)
    p.add_argument("--url", default="")
    p.add_argument("--force", action="store_true", help="download even if a simulator already exists")
    p.set_defaults(fn=cmd_download_sim)

    p = sub.add_parser("run-sim")
    p.add_argument("--sim-dir", required=True)
    p.set_defaults(fn=cmd_run_sim)

    sub.add_parser("stop-sim").set_defaults(fn=cmd_stop_sim)

    p = sub.add_parser("wait-sim")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9091)
    p.set_defaults(fn=cmd_wait_sim)

    p = sub.add_parser("stamp")
    p.add_argument("path")
    p.set_defaults(fn=cmd_stamp)

    p = sub.add_parser("clean")
    p.add_argument("paths", nargs="+")
    p.set_defaults(fn=cmd_clean)

    sub.add_parser("check").set_defaults(fn=cmd_check)

    p = sub.add_parser("help")
    p.add_argument("makefile")
    p.set_defaults(fn=cmd_help)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()