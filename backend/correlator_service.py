#!/usr/bin/env python3
"""Correlator service (real mode): tail data/events.jsonl, write incidents.json.

The collectors append events to events.jsonl; this feeds them through the reference rule engine
and refreshes incidents.json. In simulation mode the server does this in-process instead.

  python3 -m backend.correlator_service --data ./data
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .correlate import Correlator
from .feeds import write_feed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="./data")
    ap.add_argument("--interval", type=float, default=2.0)
    a = ap.parse_args()

    corr = Correlator()
    path = os.path.join(a.data, "events.jsonl")
    pos = 0
    while True:
        batch = []
        try:
            if os.path.isfile(path):
                with open(path) as f:
                    f.seek(pos)
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                batch.append(json.loads(line))
                            except ValueError:
                                pass
                    pos = f.tell()
        except OSError:
            pass
        corr.feed(batch)
        write_feed(a.data, "incidents.json", corr.snapshot())
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
