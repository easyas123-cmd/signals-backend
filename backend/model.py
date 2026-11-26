"""In-memory world model for simulation mode.

Holds the state the collectors would otherwise fill, builds the console's JSON feeds from it,
and evolves it on a tick so the dashboard is live without any radios. The feed shapes match the
console's data contract (see ../docs or the signals-console README).
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

ISM_CARRIERS = [433.9, 915.0, 315.0, 345.0, 1090.0]


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + "Z"


def _mac(rng: random.Random) -> str:
    # locally-administered MAC (U/L bit set) so it never collides with a real OUI
    return "02:" + ":".join(f"{rng.randint(0,255):02X}" for _ in range(5))


RF_MODELS = [
    ("Acurite-Tower", "Backyard weather", "thermometer"),
    ("Prologue-TH", "Garage temp/humidity", "thermometer"),
    ("LaCrosse-TX141", "Attic sensor", "thermometer"),
    ("TPMS-Generic", "Front-left tire", "car"),
    ("TPMS-Generic", "Front-right tire", "car"),
    ("Honeywell-Door", "Side door contact", "contact"),
    ("Oregon-Rain", "Rain gauge", "rain"),
    ("Nexus-TH", "Fridge probe", "thermometer"),
    ("SoilMoisture", "Garden bed", "soil"),
    ("EcoWitt-WH51", "Greenhouse", "soil"),
]
VENDORS = ["Apple", "Samsung", "Google", "Espressif", "Amazon", "Ubiquiti", "TP-Link", "Unknown"]


@dataclass
class World:
    seed: int = 1
    rng: random.Random = field(default_factory=lambda: random.Random(1))
    started: float = field(default_factory=time.time)
    devices: list = field(default_factory=list)
    aps: list = field(default_factory=list)
    clients: list = field(default_factory=list)
    ble: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    alerts: list = field(default_factory=list)
    noise_recent: list = field(default_factory=list)
    noise_hourly: dict = field(default_factory=dict)
    aircraft: list = field(default_factory=list)
    sweeps: list = field(default_factory=list)
    tick_n: int = 0

    def __post_init__(self):
        self.rng = random.Random(self.seed)
        now = time.time()
        for i, (model, label, kind) in enumerate(RF_MODELS):
            age = self.rng.randint(5, 4000)
            self.devices.append({
                "key": f"rf|{model}-{100+i}", "slug": f"{model.lower()}-{100+i}",
                "label": label, "model": model, "type": "sensor",
                "first_seen": _iso(now - self.rng.randint(10, 60) * 86400),
                "last_seen": _iso(now - age), "age_seconds": age,
                "active": age < 120, "count": self.rng.randint(40, 9000),
                "present": (i % 2 == 0) if kind == "car" else None,
                "known": {"kind": kind, "label": label},
            })
        for i in range(6):
            self.aps.append({
                "mac": _mac(self.rng), "first_seen": _iso(now - self.rng.randint(2, 40) * 86400),
                "last_seen": _iso(now - self.rng.randint(1, 300)),
                "ssids": ["Home-WiFi"] if i < 2 else (["Neighbor-5G"] if i < 4 else []),
                "channel": self.rng.choice([1, 6, 11, 36, 44, 149]), "crypt": "WPA2", "hidden": False,
                "signal": self.rng.randint(-82, -42), "manuf": self.rng.choice(VENDORS), "trusted": i < 2,
            })
        for _ in range(9):
            self.clients.append({
                "mac": _mac(self.rng), "first_seen": _iso(now - self.rng.randint(1, 30) * 86400),
                "last_seen": _iso(now - self.rng.randint(1, 600)),
                "client_of": [self.rng.choice(self.aps)["mac"]],
                "signal": self.rng.randint(-85, -45), "manuf": self.rng.choice(VENDORS),
            })
        for i in range(7):
            self.ble.append({
                "mac": _mac(self.rng), "first_seen": _iso(now - self.rng.randint(1, 20) * 86400),
                "last_seen": _iso(now - self.rng.randint(1, 200)), "entity_id": i if i < 4 else None,
                "signal": self.rng.randint(-90, -50), "manuf": self.rng.choice(VENDORS),
                "name": self.rng.choice(["", "Fitness band", "Earbuds", "Beacon", "Smartwatch"]),
            })
        for i in range(4):
            self.entities.append({
                "id": i, "label": ["Phone A", "Phone B", "Watch", "Earbuds"][i],
                "present": i < 3, "signal": self.rng.randint(-80, -48),
                "kind": self.rng.choice(["phone", "wearable", "beacon"]),
            })
        for i in range(30):
            self.noise_recent.append([_iso(now - (30 - i) * 10), round(self.rng.uniform(-80, -68), 1)])
        for h in range(167, -1, -1):
            self.noise_hourly[_iso(now - h * 3600)[:13]] = round(self.rng.uniform(-80, -70), 1)
        self._init_aircraft(now)
        self.sweeps = [self._sweep(now - 9 * 3600), self._sweep(now - 3600)]

    # ---- sweeps ----
    def _sweep(self, end):
        n, f0, f1 = 220, 24.0, 1750.0
        bins = [round(f0 + (f1 - f0) * i / (n - 1), 2) for i in range(n)]
        db = []
        for f in bins:
            v = self.rng.uniform(-44, -38)
            for c in ISM_CARRIERS:
                if abs(f - c) < 6:
                    v = self.rng.uniform(-18, -6)
            db.append(round(v, 1))
        return {"end": _iso(end), "db": db, "bins": bins}

    # ---- aircraft ----
    def _init_aircraft(self, now):
        cx, cy = 39.3, -92.3
        for i in range(8):
            lat = round(cx + self.rng.uniform(-1.3, 1.3), 4)
            lon = round(cy + self.rng.uniform(-2.0, 2.0), 4)
            self.aircraft.append({
                "hex": format(0xA00000 + self.rng.randint(1, 0xFFFFF), "x"), "flight": f"SMPL{100+i}",
                "lat": lat, "lon": lon, "alt": self.rng.randint(5, 390) * 100,
                "track_deg": self.rng.randint(0, 359), "speed": self.rng.randint(180, 520),
                "trail": [], "ts": now - self.rng.randint(2, 120),
            })

    # ---------- evolve ----------
    def tick(self, now: float) -> list:
        """Advance the world one step. Returns a list of events for the correlator / SSE."""
        self.tick_n += 1
        events = []
        # noise floor wiggle
        cur = round(self.rng.uniform(-80, -68), 1)
        self.noise_recent.append([_iso(now), cur])
        self.noise_recent = self.noise_recent[-360:]
        if cur > -62:
            events.append({"at": _iso(now), "category": "rf", "type": "noise-high",
                           "severity": 2, "message": f"Noise floor {cur} dB on 433.92",
                           "device_key": "rf|noise"})
        # a device re-decodes
        d = self.rng.choice(self.devices)
        d["last_seen"] = _iso(now); d["age_seconds"] = 0; d["active"] = True; d["count"] += 1
        # aircraft move
        for a in self.aircraft:
            rad = a["track_deg"] * 3.14159 / 180
            a["trail"] = (a["trail"] + [[a["lat"], a["lon"]]])[-8:]
            a["lat"] = round(a["lat"] - 0.02 * math.cos(rad), 4)
            a["lon"] = round(a["lon"] + 0.02 * math.sin(rad), 4)
            a["ts"] = now
        # occasionally a new wifi client appears -> event the correlator can open an incident on
        if self.tick_n % 6 == 0:
            m = _mac(self.rng)
            self.clients.append({"mac": m, "first_seen": _iso(now), "last_seen": _iso(now),
                                 "client_of": [self.aps[0]["mac"]], "signal": self.rng.randint(-80, -50),
                                 "manuf": self.rng.choice(VENDORS)})
            self.clients = self.clients[-40:]
            ev = {"at": _iso(now), "category": "wifi", "type": "WF-NEW",
                  "severity": 2, "message": f"New client {m} on Home-WiFi", "device_key": f"client|{m}"}
            self.alerts.insert(0, {"type": "WF-NEW", "at": _iso(now), "category": "wifi",
                                   "severity": 2, "message": ev["message"], "actor": {"mac": m}})
            self.alerts = self.alerts[:40]
            events.append(ev)
        # fresh sweep every ~20 ticks
        if self.tick_n % 20 == 0:
            self.sweeps = (self.sweeps + [self._sweep(now)])[-48:]
        return events

    # ---------- feeds ----------
    def summary(self, now):
        return {
            "generated_at": _iso(now),
            "receiver": {"status": "running", "model": "rtl_433", "freq_mhz": 433.92},
            "noise": {"current_db": self.noise_recent[-1][1], "baseline_db": -77.9,
                      "recent": self.noise_recent[-30:], "hourly": self.noise_hourly},
            "weather": {"temp_f": 54, "temp_c": 12, "humidity": 71, "wind_mph": 6,
                        "desc": "Overcast", "as_of": _iso(now - 600)},
            "devices": self.devices, "devices_total": len(self.devices),
        }

    def wifi(self):
        present = [e for e in self.entities if e["present"]]
        return {
            "counts": {"phones_present": sum(1 for e in present if e["kind"] == "phone"),
                       "trackers_present": 0, "aps_total": len(self.aps), "clients_total": len(self.clients)},
            "sensor": {"status": "running", "iface": "wlan0"}, "ble_sensor": {"status": "running"},
            "aps": self.aps, "clients": self.clients, "ble": self.ble, "entities": self.entities,
            "alerts": self.alerts,
            "known": {"ssids": ["Home-WiFi"],
                      "bssids": {self.aps[0]["mac"].lower(): "Living-room AP",
                                 self.aps[1]["mac"].lower(): "Office AP"}},
        }

    def jobs(self, now):
        return {
            "schedule": [
                {"job": "sweep", "when": "continuous (dedicated SDR)", "minutes": 0, "what": "rtl_power 24-1750 MHz occupancy"},
                {"job": "rf433", "when": "continuous (dedicated SDR)", "minutes": 0, "what": "rtl_433 on 433.92 MHz"},
                {"job": "adsb", "when": "continuous (dedicated SDR)", "minutes": 0, "what": "dump1090 on 1090 MHz"},
                {"job": "meters", "when": "shared, hourly", "minutes": 6, "what": "rtlamr utility meters (900 MHz)"},
            ],
            "history": [{"job": self.rng.choice(["sweep", "rf433", "adsb", "meters"]),
                         "started": _iso(now - (i + 1) * 1800), "ended": _iso(now - (i + 1) * 1800 + 300),
                         "rc": 0, "output": f"{self.rng.randint(3, 90)} decodes"} for i in range(10)],
        }

    def sweep(self, now):
        last = self.sweeps[-1]
        return {"sweeps": self.sweeps,
                "anomalies": [{"at": last["end"], "start_mhz": 430, "end_mhz": 435,
                               "peak_db": -6.2, "above_db": 9}]}

    def ham(self, now):
        return {"survey": {"latest": {"at": _iso(now - 3600), "health": "ok",
                                      "heard": self.rng.randint(2, 14), "bands": ["2m", "70cm"]}}}

    def adsb(self, now):
        acs = []
        for a in self.aircraft:
            acs.append({"hex": a["hex"], "flight": a["flight"], "track": a["trail"] + [[a["lat"], a["lon"]]],
                        "last": {"lat": a["lat"], "lon": a["lon"], "alt": a["alt"],
                                 "speed": a["speed"], "track": a["track_deg"], "ts": int(a["ts"])}})
        return {"generated_at": _iso(now), "receiver": {"lat": 39.3, "lon": -92.3}, "aircraft": acs}
