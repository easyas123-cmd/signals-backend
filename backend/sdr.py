#!/usr/bin/env python3
"""SDR manager: discover radios by serial, map them to roles, and lock one per process.

Addressing by serial (set with `rtl_eeprom -s <name>`) is what makes a multi-dongle setup stable:
USB indices reorder across reboots and hubs, serials don't. A per-serial lock stops two collectors
from grabbing the same radio.

  python3 -m backend.sdr list                 # discovered dongles + serials
  python3 -m backend.sdr plan --config config.json   # role -> serial assignment from config
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

LOCK_DIR = os.environ.get("SDR_LOCK_DIR", os.path.join(tempfile.gettempdir(), "signals-sdr-locks"))


def discover():
    """Return [{index, name, serial}] from `rtl_test -t`. Empty if no rtl tools / no dongles."""
    try:
        out = subprocess.run(["rtl_test", "-t"], capture_output=True, text=True, timeout=10)
        text = out.stdout + out.stderr
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for m in re.finditer(r"^\s*(\d+):\s*(.+?),\s*SN:\s*(\S+)", text, re.M):
        found.append({"index": int(m.group(1)), "name": m.group(2).strip(), "serial": m.group(3)})
    return found


class SdrLock:
    """Per-serial lock. Enter grabs the radio or raises if another process holds it."""

    def __init__(self, serial):
        self.serial = serial
        self.path = os.path.join(LOCK_DIR, f"{serial}.lock")
        self._fd = None

    def __enter__(self):
        os.makedirs(LOCK_DIR, exist_ok=True)
        try:
            self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError(f"SDR {self.serial} is already in use (lock {self.path})")
        os.write(self._fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            os.close(self._fd)
        try:
            os.unlink(self.path)
        except OSError:
            pass


def plan(config):
    """Map configured roles to discovered serials; report which are present vs missing."""
    present = {d["serial"] for d in discover()}
    rows = []
    for sdr in config.get("sdrs", []):
        serial = sdr.get("serial")
        rows.append({"serial": serial, "role": sdr.get("role"),
                     "rotate": sdr.get("rotate"), "present": serial in present})
    return rows


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("plan")
    p.add_argument("--config", default="config.json")
    a = ap.parse_args()

    if a.cmd == "list":
        d = discover()
        print(json.dumps(d, indent=2) if d else "no RTL-SDR dongles found (is rtl_test installed?)")
    elif a.cmd == "plan":
        with open(a.config) as f:
            cfg = json.load(f)
        for r in plan(cfg):
            mark = "ok " if r["present"] else "MISSING"
            extra = f" rotate={r['rotate']}" if r.get("rotate") else ""
            print(f"  [{mark}] {r['role']:8s} <- SDR {r['serial']}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
