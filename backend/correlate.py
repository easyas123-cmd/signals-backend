"""Reference correlator.

A deliberately small, stateful rule engine: each rule maps a collector event to an incident
bucket. Events in the same bucket are grouped into one incident; a bucket goes quiet and the
incident auto-closes. This is an EXAMPLE to extend, not a production detection stack. Add rules
to RULES; keep them pure (event -> bucket or None).
"""

from __future__ import annotations

import time


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + "Z"


def rule_new_wifi_client(ev):
    if ev.get("type") == "WF-NEW":
        return {"key": "wifi-new-client", "severity": "medium", "classification": "unknown_device",
                "summary": "Unrecognized client associated with Home-WiFi",
                "finding": {"type": "new-device", "summary": "MAC not seen before"}}
    return None


def rule_noise_floor(ev):
    if ev.get("type") == "noise-high":
        return {"key": "rf-noise", "severity": "low", "classification": "rf_interference",
                "summary": "Elevated RF noise floor on 433.92 MHz",
                "finding": {"type": "noise", "summary": "Noise floor above its baseline"}}
    return None


# add your own rules here; each returns a bucket dict or None
RULES = [rule_new_wifi_client, rule_noise_floor]

CLOSE_AFTER_S = 180   # a bucket with no new events for this long closes
KEEP_CLOSED = 20
_SEV_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


class Correlator:
    def __init__(self):
        self._open = {}       # key -> incident dict
        self._closed = []
        self._next_id = 1

    def feed(self, events, now=None):
        now = now or time.time()
        for ev in events:
            for rule in RULES:
                b = rule(ev)
                if not b:
                    continue
                inc = self._open.get(b["key"])
                if inc is None:
                    inc = self._open[b["key"]] = {
                        "uid": f"INC-{self._next_id:05d}", "id": self._next_id, "status": "open",
                        "severity": b["severity"], "summary": b["summary"],
                        "classification": b["classification"],
                        "opened_at": ev["at"], "updated_at": ev["at"], "confidence": 0.6,
                        "events": [], "findings": [b["finding"]], "_last": now,
                    }
                    self._next_id += 1
                inc["events"].append(ev)
                inc["events"] = inc["events"][-50:]
                inc["updated_at"] = ev["at"]
                inc["_last"] = now
                if _SEV_RANK.get(b["severity"], 0) > _SEV_RANK.get(inc["severity"], 0):
                    inc["severity"] = b["severity"]
        # expire quiet buckets
        for key, inc in list(self._open.items()):
            if now - inc["_last"] > CLOSE_AFTER_S:
                inc["status"] = "closed"
                inc["closed_at"] = _iso(now)
                self._closed.insert(0, inc)
                del self._open[key]
        self._closed = self._closed[:KEEP_CLOSED]

    def snapshot(self):
        open_list = [self._strip(i) for i in self._open.values()]
        high = sum(1 for i in open_list if i["severity"] in ("high", "critical"))
        return {
            "counts": {"open": len(open_list), "open_high": high},
            "open": open_list,
            "recent_closed": [self._strip(i) for i in self._closed],
        }

    @staticmethod
    def _strip(inc):
        return {k: v for k, v in inc.items() if not k.startswith("_")}
