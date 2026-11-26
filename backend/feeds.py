"""Small shared helpers for collectors: atomic feed writes and the event log."""

from __future__ import annotations

import json
import os
import time


def iso(ts=None):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts if ts is not None else time.time())) + "Z"


def write_feed(data_dir, name, obj):
    """Atomically write one feed file (summary.json, sweep.json, ...)."""
    os.makedirs(data_dir, exist_ok=True)
    tmp = os.path.join(data_dir, name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, os.path.join(data_dir, name))


def emit_event(data_dir, event):
    """Append one event to data/events.jsonl; the server tails this for /live and the correlator."""
    event.setdefault("at", iso())
    with open(os.path.join(data_dir, "events.jsonl"), "a") as f:
        f.write(json.dumps(event) + "\n")
