#!/usr/bin/env python3
"""adsb collector: dump1090 / readsb -> adsb.json.

Hardware path. Reads the aircraft.json that dump1090-fa / readsb publishes (or polls its HTTP
endpoint), reshapes it into the console's adsb feed, and keeps a short position trail per aircraft.
Needs a 1090 MHz-capable SDR and a running dump1090/readsb.

  python3 -m backend.collectors.adsb --source /run/dump1090-fa/aircraft.json \
          --lat 39.3 --lon -92.3 --data ./data
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

from ..feeds import iso, write_feed

TRAIL = 8


def read_source(src):
    if src.startswith(("http://", "https://")):
        with urllib.request.urlopen(src, timeout=5) as r:
            return json.load(r)
    with open(src) as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="/run/dump1090-fa/aircraft.json",
                    help="path or URL to dump1090/readsb aircraft.json")
    ap.add_argument("--lat", type=float, default=0.0)
    ap.add_argument("--lon", type=float, default=0.0)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--data", default="./data")
    a = ap.parse_args()

    trails = {}   # hex -> [[lat,lon],...]
    while True:
        try:
            raw = read_source(a.source)
        except (OSError, ValueError):
            time.sleep(a.interval)
            continue
        now = time.time()
        out = []
        seen = set()
        for ac in raw.get("aircraft", []):
            lat, lon = ac.get("lat"), ac.get("lon")
            if lat is None or lon is None:
                continue
            h = ac.get("hex", "")
            seen.add(h)
            trail = trails.setdefault(h, [])
            if not trail or trail[-1] != [lat, lon]:
                trail.append([lat, lon])
                del trail[:-TRAIL]
            out.append({
                "hex": h, "flight": (ac.get("flight") or h).strip(), "track": list(trail),
                "last": {"lat": lat, "lon": lon, "alt": ac.get("alt_baro", ac.get("altitude")),
                         "speed": ac.get("gs", ac.get("speed")), "track": ac.get("track"),
                         "ts": int(now - ac.get("seen", 0))},
            })
        for h in list(trails):
            if h not in seen:
                del trails[h]
        write_feed(a.data, "adsb.json", {
            "generated_at": iso(now), "receiver": {"lat": a.lat, "lon": a.lon}, "aircraft": out,
        })
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
