#!/usr/bin/env python3
"""rf433 collector: rtl_433 decodes on an ISM band -> summary.json (receiver, noise, devices).

Hardware path. Spawns `rtl_433 -F json` on the assigned SDR and maintains the RF device list.
Needs an RTL-SDR dongle and rtl_433 on PATH (or run it in the sdr-tools container).

  python3 -m backend.collectors.rf433 --serial ISM433 --freq 433.92M --data ./data
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

from ..feeds import iso, write_feed, emit_event

NOISE_KEEP = 360


def dev_key(rec):
    model = rec.get("model", "unknown")
    ident = rec.get("id", rec.get("channel", rec.get("address", "")))
    return f"{model}-{ident}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default="", help="rtl_eeprom serial of the dongle (preferred over index)")
    ap.add_argument("--freq", default="433.92M")
    ap.add_argument("--data", default="./data")
    ap.add_argument("--flush", type=float, default=5.0, help="seconds between summary.json writes")
    ap.add_argument("--rtl433", default="rtl_433")
    a = ap.parse_args()

    cmd = [a.rtl433, "-F", "json", "-M", "level", "-f", a.freq]
    if a.serial:
        cmd += ["-d", f":{a.serial}"]        # address by serial, never a bare index
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    devices = {}            # key -> record
    noise = []              # [[iso, db]]
    last_flush = 0.0
    started = time.time()

    def summary():
        now = time.time()
        devs = []
        for k, d in devices.items():
            age = int(now - d["_last"])
            devs.append({
                "key": "rf|" + k, "slug": k.lower().replace(" ", "-"),
                "label": d.get("label", d["model"]), "model": d["model"], "type": "sensor",
                "first_seen": d["first_seen"], "last_seen": iso(d["_last"]), "age_seconds": age,
                "active": age < 120, "count": d["count"], "known": {"kind": d.get("kind", "sensor")},
            })
        cur = noise[-1][1] if noise else None
        return {
            "generated_at": iso(now),
            "receiver": {"status": "running", "model": "rtl_433", "freq_mhz": float(a.freq.rstrip("M"))},
            "noise": {"current_db": cur, "baseline_db": None, "recent": noise[-30:], "hourly": {}},
            "devices": devs, "devices_total": len(devs),
        }

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            now = time.time()
            if "noise" in rec:
                noise.append([iso(now), round(float(rec["noise"]), 1)])
                noise[:] = noise[-NOISE_KEEP:]
            if "model" in rec:
                k = dev_key(rec)
                d = devices.get(k)
                if d is None:
                    d = devices[k] = {"model": rec["model"], "first_seen": iso(now), "count": 0}
                    emit_event(a.data, {"category": "rf", "type": "rf-new",
                                        "severity": 1, "message": f"New RF device {k}",
                                        "device_key": "rf|" + k})
                d["_last"] = now
                d["count"] += 1
            if now - last_flush >= a.flush:
                write_feed(a.data, "summary.json", summary())
                last_flush = now
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
