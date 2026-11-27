#!/usr/bin/env python3
"""wifi collector: airodump-ng -> the APs/clients slice of wifi.json.

Hardware path. Runs airodump-ng on a monitor-mode interface, re-reads its CSV, and writes the
access points and clients into wifi.json (leaving the BLE slice that the ble collector owns
untouched). Needs the aircrack-ng suite and an adapter in monitor mode:

    sudo airmon-ng start wlan0            # -> wlan0mon
    python3 -m backend.collectors.wifi --iface wlan0mon --data ./data

The CSV parser (parse_airodump) is pure, so it can be tested without hardware.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import tempfile
import time

from ..feeds import iso, update_feed, emit_event


def _t(s):
    s = (s or "").strip()
    return s.replace(" ", "T") if s else iso()


def _int(s, default=None):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return default


def parse_airodump(text):
    """Parse an airodump-ng CSV dump into (aps, clients). Pure, for testing."""
    aps, clients = [], []
    section = None
    for row in text.splitlines():
        line = row.strip()
        if not line:
            continue
        cols = [c.strip() for c in row.split(",")]
        if cols[0] == "BSSID":
            section = "ap"
            continue
        if cols[0] == "Station MAC":
            section = "client"
            continue
        if section == "ap" and len(cols) >= 14 and ":" in cols[0]:
            aps.append({
                "mac": cols[0].upper(), "first_seen": _t(cols[1]), "last_seen": _t(cols[2]),
                "channel": _int(cols[3]), "crypt": cols[5] or "Open", "hidden": not cols[13],
                "signal": _int(cols[8]), "ssids": [cols[13]] if cols[13] else [],
                "manuf": "", "trusted": False,
            })
        elif section == "client" and len(cols) >= 6 and ":" in cols[0]:
            bssid = cols[5].upper()
            probed = [p.strip() for p in cols[6:] if p.strip()] if len(cols) > 6 else []
            clients.append({
                "mac": cols[0].upper(), "first_seen": _t(cols[1]), "last_seen": _t(cols[2]),
                "signal": _int(cols[3]), "client_of": [] if bssid.startswith("(NOT") else [bssid],
                "manuf": "", "probed": probed,
            })
    return aps, clients


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", required=True, help="monitor-mode interface, e.g. wlan0mon")
    ap.add_argument("--data", default="./data")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--airodump", default="airodump-ng")
    a = ap.parse_args()

    workdir = tempfile.mkdtemp(prefix="wifi-")
    prefix = os.path.join(workdir, "scan")
    proc = subprocess.Popen(
        [a.airodump, "-w", prefix, "--output-format", "csv", "--write-interval", "5", a.iface],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    seen = set()
    try:
        while True:
            time.sleep(a.interval)
            files = sorted(glob.glob(prefix + "-*.csv"))
            if not files:
                continue
            with open(files[-1], errors="replace") as f:
                aps, clients = parse_airodump(f.read())
            for c in clients:
                if c["mac"] not in seen:
                    seen.add(c["mac"])
                    emit_event(a.data, {"category": "wifi", "type": "WF-NEW", "severity": 2,
                                        "message": f"New client {c['mac']}",
                                        "device_key": f"client|{c['mac']}"})

            def patch(doc):
                doc["aps"] = aps
                doc["clients"] = clients
                doc.setdefault("known", {"ssids": [], "bssids": {}})
                doc.setdefault("ble", [])
                doc.setdefault("entities", [])
                doc["sensor"] = {"status": "running", "iface": a.iface}
                doc.setdefault("ble_sensor", {"status": "absent"})
                doc["counts"] = {
                    "phones_present": sum(1 for e in doc.get("entities", []) if e.get("present")),
                    "trackers_present": doc.get("counts", {}).get("trackers_present", 0),
                    "aps_total": len(aps), "clients_total": len(clients),
                }
                return doc

            update_feed(a.data, "wifi.json", patch)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
