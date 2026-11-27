"""Small shared helpers for collectors: atomic feed writes and the event log."""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager


def iso(ts=None):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts if ts is not None else time.time())) + "Z"


def write_feed(data_dir, name, obj):
    """Atomically write one feed file (summary.json, sweep.json, ...)."""
    os.makedirs(data_dir, exist_ok=True)
    tmp = os.path.join(data_dir, name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, os.path.join(data_dir, name))


@contextmanager
def _lock(data_dir, name):
    os.makedirs(data_dir, exist_ok=True)
    fd = os.open(os.path.join(data_dir, name + ".lock"), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def update_feed(data_dir, name, patch):
    """Locked read-modify-write for a feed several collectors share (e.g. wifi.json).

    `patch(doc)` mutates the current document in place (or returns a replacement). The lock makes
    concurrent updates from the wifi and ble collectors safe.
    """
    with _lock(data_dir, name):
        try:
            with open(os.path.join(data_dir, name)) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = {}
        out = patch(doc)
        write_feed(data_dir, name, out if out is not None else doc)


def emit_event(data_dir, event):
    """Append one event to data/events.jsonl; the server tails this for /live and the correlator."""
    event.setdefault("at", iso())
    with open(os.path.join(data_dir, "events.jsonl"), "a") as f:
        f.write(json.dumps(event) + "\n")
