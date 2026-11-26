#!/usr/bin/env python3
"""sweep collector: rtl_power occupancy sweep -> sweep.json.

Hardware path. Runs `rtl_power` over a frequency range, parses its CSV into a single sweep
(frequency bins + power), keeps a ring of recent sweeps, and flags bins well above the recent
median as anomalies. Needs an RTL-SDR dongle and rtl_power on PATH.

  python3 -m backend.collectors.sweep --serial SCAN --range 24M:1750M:1M --data ./data
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time

from ..feeds import iso, write_feed, emit_event

KEEP = 48
ANOMALY_DB = 12.0
MIN_HISTORY = 5


def parse_csv(text):
    """rtl_power CSV rows: date,time,hz_low,hz_high,hz_step,samples,db,db,db,... -> (bins_mhz, db)."""
    bins, db = [], []
    for row in text.splitlines():
        parts = [p.strip() for p in row.split(",")]
        if len(parts) < 7:
            continue
        try:
            low, step = float(parts[2]), float(parts[4])
            vals = [float(x) for x in parts[6:]]
        except ValueError:
            continue
        for i, v in enumerate(vals):
            bins.append(round((low + i * step) / 1e6, 3))
            db.append(round(v, 1))
    return bins, db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default="")
    ap.add_argument("--range", default="24M:1750M:1M")
    ap.add_argument("--interval", type=float, default=60.0, help="seconds between sweeps")
    ap.add_argument("--integration", default="20", help="rtl_power -i integration seconds")
    ap.add_argument("--data", default="./data")
    ap.add_argument("--rtl_power", default="rtl_power")
    a = ap.parse_args()

    sweeps = []
    anomalies = []
    while True:
        cmd = [a.rtl_power, "-f", a.range, "-i", a.integration, "-1"]
        if a.serial:
            cmd += ["-d", f":{a.serial}"]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=600).stdout
        except (OSError, subprocess.TimeoutExpired):
            time.sleep(a.interval)
            continue
        bins, db = parse_csv(out)
        if not bins:
            time.sleep(a.interval)
            continue
        sweeps.append({"end": iso(), "db": db, "bins": bins})
        sweeps[:] = sweeps[-KEEP:]
        if len(sweeps) >= MIN_HISTORY:
            for i, v in enumerate(db):
                hist = [s["db"][i] for s in sweeps[:-1] if i < len(s["db"])]
                if not hist:
                    continue
                med = statistics.median(hist)
                if v - med >= ANOMALY_DB and v > -25:
                    anomalies.append({"at": iso(), "start_mhz": bins[i], "end_mhz": bins[i],
                                      "peak_db": v, "above_db": round(v - med, 1)})
                    emit_event(a.data, {"category": "rf", "type": "rf-new-carrier", "severity": 2,
                                        "message": f"New carrier near {bins[i]} MHz (+{round(v-med,1)} dB)",
                                        "device_key": "rf|sweep"})
            anomalies[:] = anomalies[-50:]
        write_feed(a.data, "sweep.json", {"sweeps": sweeps, "anomalies": list(reversed(anomalies[-20:]))})
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
