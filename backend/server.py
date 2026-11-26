#!/usr/bin/env python3
"""Web layer for the signals backend.

Serves the built console and the data/ feeds, plus the live endpoints the console expects:
  GET  /<feed>.json       collector output (summary, wifi, incidents, jobs, sweep, ham, adsb, ...)
  GET  /annotations       notes/tags/watch/track store
  POST /annotations       store update with optimistic concurrency (409 on a stale rev)
  GET  /live              Server-Sent Events stream of collector events
  GET  /jobs/status       scheduler status
  POST /jobs/trigger      request a manual job run

Run:  python3 -m backend.server --sim            # simulation mode, no radios
      python3 -m backend.server --web ../signals-console/dist   # also serve a built console
Collectors (real mode) write the same data/ files and append events to data/events.jsonl, which
this server tails and broadcasts on /live.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class Hub:
    """Fan-out of collector events to SSE subscribers."""

    def __init__(self):
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self):
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def publish(self, event):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


class App:
    def __init__(self, data_dir, web_dir, hub):
        self.data = data_dir
        self.web = web_dir
        self.hub = hub
        os.makedirs(self.data, exist_ok=True)
        self._annot_lock = threading.Lock()

    # ---- annotations ----
    def _annot_path(self):
        return os.path.join(self.data, "annotations.json")

    def annotations(self):
        try:
            with open(self._annot_path()) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"rev": 0, "notes": {}, "watch": {}, "tags": {}, "track": {}, "trackLog": []}

    def put_annotations(self, body):
        with self._annot_lock:
            cur = self.annotations()
            if int(body.get("rev", 0)) != int(cur.get("rev", 0)):
                return 409, {"current": cur}
            doc = {k: body.get(k, cur.get(k)) for k in ("notes", "watch", "tags", "track", "trackLog")}
            doc["rev"] = int(cur.get("rev", 0)) + 1
            tmp = self._annot_path() + ".tmp"
            with open(tmp, "w") as f:
                json.dump(doc, f)
            os.replace(tmp, self._annot_path())
            return 200, {"rev": doc["rev"]}


class Handler(BaseHTTPRequestHandler):
    app: App = None  # set at startup

    def _send(self, code, body=b"", ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _route(self):
        return self.path.split("?", 1)[0]

    def do_GET(self):
        p = self._route()
        if p == "/annotations":
            return self._send(200, self.app.annotations())
        if p == "/jobs/status":
            return self._send(200, {"state": "idle"})
        if p == "/live":
            return self._sse()
        return self._serve_file(p)

    def do_POST(self):
        p = self._route()
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        if p == "/annotations":
            code, resp = self.app.put_annotations(body)
            return self._send(code, resp)
        if p == "/jobs/trigger":
            return self._send(200, {"ok": True, "job": body.get("job")})
        return self._send(404, {"error": "not found"})

    def _serve_file(self, p):
        rel = p.lstrip("/") or "index.html"
        for base in (self.app.data, self.app.web):
            if not base:
                continue
            fp = os.path.normpath(os.path.join(base, rel))
            if not fp.startswith(os.path.abspath(base)):
                return self._send(403, b"forbidden", "text/plain")
            if os.path.isfile(fp):
                ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
                with open(fp, "rb") as f:
                    return self._send(200, f.read(), ctype)
        # SPA fallback to the console's index.html
        if self.app.web and os.path.isfile(os.path.join(self.app.web, "index.html")):
            with open(os.path.join(self.app.web, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html")
        return self._send(404, {"error": "not found"})

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = self.app.hub.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    self.wfile.write(("data: " + json.dumps(ev) + "\n\n").encode())
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")  # comment frame keeps the connection open
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.hub.unsubscribe(q)

    def log_message(self, *a):
        pass


def sim_loop(app: App, interval=2.0):
    """Drive the world model, write feeds, correlate, and broadcast events."""
    from .model import World
    from .correlate import Correlator
    world, corr = World(), Correlator()

    def dump(name, obj):
        tmp = os.path.join(app.data, name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, os.path.join(app.data, name))

    while True:
        now = time.time()
        events = world.tick(now)
        corr.feed(events, now)
        dump("summary.json", world.summary(now))
        dump("wifi.json", world.wifi())
        dump("incidents.json", corr.snapshot())
        dump("jobs.json", world.jobs(now))
        dump("sweep.json", world.sweep(now))
        dump("ham.json", world.ham(now))
        dump("adsb.json", world.adsb(now))
        for ev in events:
            app.hub.publish(ev)
        time.sleep(interval)


def tail_events(app: App):
    """Real mode: broadcast new lines appended to data/events.jsonl by the collectors."""
    path = os.path.join(app.data, "events.jsonl")
    pos = 0
    while True:
        try:
            if os.path.isfile(path):
                with open(path) as f:
                    f.seek(pos)
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                app.hub.publish(json.loads(line))
                            except ValueError:
                                pass
                    pos = f.tell()
        except OSError:
            pass
        time.sleep(1.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--web", default="", help="path to a built console (dist/) to serve at /")
    ap.add_argument("--sim", action="store_true", help="run the simulation driver (no radios)")
    ap.add_argument("--interval", type=float, default=2.0)
    a = ap.parse_args()

    hub = Hub()
    Handler.app = App(os.path.abspath(a.data), os.path.abspath(a.web) if a.web else "", hub)
    if a.sim:
        threading.Thread(target=sim_loop, args=(Handler.app, a.interval), daemon=True).start()
    else:
        threading.Thread(target=tail_events, args=(Handler.app,), daemon=True).start()

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"signals-backend on http://{a.host}:{a.port}  (sim={a.sim}, data={Handler.app.data})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
