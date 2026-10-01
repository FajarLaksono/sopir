import os
import sys
from contextlib import contextmanager

import traci

from backend.config import settings


@contextmanager
def sumo_connection(config_file: str):
    sumo_binary = "sumo"
    if sys.platform == "win32":
        sumo_binary = os.path.join(settings.sumo_home, "bin", "sumo.exe")
    else:
        sumo_binary = "sumo"

    sumo_cmd = [sumo_binary, "-c", config_file, "--no-step-log", "true"]

    try:
        traci.start(sumo_cmd)
        yield
    finally:
        try:
            traci.close()
        except Exception:
            pass
