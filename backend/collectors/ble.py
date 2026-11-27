#!/usr/bin/env python3
"""ble collector: bluetoothctl scan -> the BLE slice of wifi.json.

Hardware path. Drives `bluetoothctl scan on` (BlueZ) and records advertising devices with their
RSSI into the ble/entities part of wifi.json (leaving the APs/clients slice the wifi collector owns
untouched). Needs a Bluetooth adapter and BlueZ (bluetoothctl):

    python3 -m backend.collectors.ble --data ./data

parse_line is pure, so the output parsing can be tested without an adapter.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import threading
import time

from ..feeds import iso, update_feed, emit_event

PRESENT_S = 120
_LINE = re.compile(r"\[(NEW|CHG|DEL)\]\s+Device\s+([0-9A-F:]{17})\s*(.*)", re.I)


def parse_line(line):
    """Return (action, mac, key, value) for a bluetoothctl device line, or None."""
    m = _LINE.search(line)
    if not m:
        return None
    action, mac, rest = m.group(1).upper(), m.group(2).upper(), m.group(3).strip()
    key, val = None, None
    if rest.startswith("RSSI:"):
        key, val = "rssi", rest.split(":", 1)[1].strip()
    elif rest.startswith("Name:"):
        key, val = "name", rest.split(":", 1)[1].strip()
    elif action == "NEW" and rest:
        key, val = "name", rest
    return action, mac, key, val


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="./data")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--bluetoothctl", default="bluetoothctl")
    a = ap.parse_args()

    devices, lock, new_q = {}, threading.Lock(), []

    def reader():
        proc = subprocess.Popen([a.bluetoothctl], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, bufsize=1)
        proc.stdin.write("scan on\n")
        proc.stdin.flush()
        for line in proc.stdout:
            p = parse_line(line)
            if not p:
                continue
            action, mac, key, val = p
            now = time.time()
            with lock:
                if action == "DEL":
                    continue
                d = devices.get(mac)
                if d is None:
                    d = devices[mac] = {"first": now, "name": "", "rssi": None}
                    new_q.append(mac)
                d["last"] = now
                if key == "rssi":
                    try:
                        d["rssi"] = int(val)
                    except (TypeError, ValueError):
                        pass
                elif key == "name" and val:
                    d["name"] = val

    threading.Thread(target=reader, daemon=True).start()

    try:
        while True:
            time.sleep(a.interval)
            now = time.time()
            with lock:
                ble, entities = [], []
                for i, (mac, d) in enumerate(devices.items()):
                    present = (now - d.get("last", 0)) < PRESENT_S
                    ble.append({"mac": mac, "first_seen": iso(d["first"]), "last_seen": iso(d.get("last", now)),
                                "signal": d["rssi"], "manuf": "", "name": d["name"],
                                "entity_id": i if d["name"] else None})
                    if d["name"]:
                        entities.append({"id": i, "label": d["name"], "present": present,
                                         "signal": d["rssi"], "kind": "beacon"})
                flush_new = new_q[:]
                new_q.clear()
            for mac in flush_new:
                emit_event(a.data, {"category": "ble", "type": "BLE-NEW", "severity": 1,
                                    "message": f"New BLE device {mac}", "device_key": f"ble|{mac}"})

            def patch(doc):
                doc["ble"] = ble
                doc["entities"] = entities
                doc.setdefault("aps", [])
                doc.setdefault("clients", [])
                doc.setdefault("known", {"ssids": [], "bssids": {}})
                doc.setdefault("sensor", {"status": "absent"})
                doc["ble_sensor"] = {"status": "running"}
                counts = doc.get("counts", {})
                counts["phones_present"] = sum(1 for e in entities if e["present"])
                doc["counts"] = counts
                return doc

            update_feed(a.data, "wifi.json", patch)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
