#!/usr/bin/env python3
"""Tuneshine companion: one process, switchable mode (tram | vinyl | off) + LAN web portal."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tuneshine import TuneshineClient

log = logging.getLogger("app")
HERE = Path(__file__).parent
MODES = ("tram", "vinyl", "off")
DEFAULT_MODE = "tram"


# ── Mode persistence ─────────────────────────────────────────────────────────

def mode_file() -> Path:
    return Path(os.environ.get("TRAM_MODE_FILE", HERE / "mode.json"))


def load_mode() -> str:
    try:
        m = json.loads(mode_file().read_text()).get("mode")
        return m if m in MODES else DEFAULT_MODE
    except (OSError, ValueError):
        return DEFAULT_MODE


def save_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(mode)
    p = mode_file()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"mode": mode}))
    tmp.replace(p)


# ── Runners ──────────────────────────────────────────────────────────────────

class Manager:
    """Owns the single active mode worker thread."""

    def __init__(self, client: TuneshineClient, runners: dict | None = None):
        self.client = client
        self.status: dict = {}
        self.mode = "off"
        self._lock = threading.Lock()
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._runners = runners or {"tram": self._run_tram, "vinyl": self._run_vinyl}

    def _run_tram(self, stop: threading.Event) -> None:
        import tram_mode
        tram_mode.run(self.client, stop, self.status)

    def _run_vinyl(self, stop: threading.Event) -> None:
        # Imported lazily so the mic/audio stack is only touched in vinyl mode.
        import vinyl_detector as vd
        loop = asyncio.new_event_loop()
        task = loop.create_task(vd.run(
            self.client,
            max_misses=vd._DEFAULT_MAX_MISSES, cycle_sleep=vd._DEFAULT_CYCLE_SLEEP,
            idle_sleep=vd._DEFAULT_IDLE_SLEEP, record_duration=vd._DEFAULT_RECORD_DURATION,
            handoff_timeout=vd._DEFAULT_HANDOFF_TIMEOUT, searching_grace=vd._DEFAULT_SEARCHING_GRACE,
            recognition_cooldown=vd._DEFAULT_RECOGNITION_COOLDOWN, preprocess=True,
        ))
        threading.Thread(target=lambda: (stop.wait(), loop.call_soon_threadsafe(task.cancel)),
                         daemon=True).start()
        self.status["vinyl"] = "listening"
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        finally:
            loop.close()
            self.status["vinyl"] = "stopped"
            try:
                self.client.clear()
            except Exception:  # noqa: BLE001
                pass

    def set_mode(self, mode: str, persist: bool = True) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        with self._lock:
            if persist:
                save_mode(mode)
            if mode == self.mode and (mode == "off" or (self._thread and self._thread.is_alive())):
                return
            self._stop_current()
            self.mode = mode
            self.status = {"mode": mode}
            if mode == "off":
                return
            self._stop = threading.Event()
            target = self._runners[mode]
            self._thread = threading.Thread(target=self._guard, args=(target, self._stop), name=mode, daemon=True)
            self._thread.start()
            log.info("Mode -> %s", mode)

    def _guard(self, target, stop):
        try:
            target(stop)
        except Exception as exc:  # noqa: BLE001
            log.exception("Mode %s crashed", self.mode)
            self.status["error"] = f"crashed: {exc}"

    def _stop_current(self, timeout: float = 20.0) -> None:
        if self._stop:
            self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            if self._thread.is_alive():
                log.warning("Previous mode did not stop within %.0fs", timeout)
        self._stop = self._thread = None

    def shutdown(self) -> None:
        with self._lock:
            self._stop_current()

    def snapshot(self) -> dict:
        return {"mode": self.mode, "modes": list(MODES), **{k: v for k, v in self.status.items() if k != "mode"}}


# ── Portal ───────────────────────────────────────────────────────────────────

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Tuneshine</title>
<style>body{font-family:system-ui,sans-serif;background:#0b0b10;color:#eee;margin:0;padding:24px;max-width:480px}
h1{font-size:20px}.modes{display:flex;gap:8px;margin:16px 0}button{flex:1;padding:16px;font-size:17px;border:0;
border-radius:12px;background:#22222c;color:#ddd}button.on{background:#e8e8f0;color:#111;font-weight:600}
pre{background:#16161e;padding:12px;border-radius:10px;font-size:12px;white-space:pre-wrap}</style></head>
<body><h1>Tuneshine mode</h1><div class=modes id=m></div><pre id=s>loading…</pre>
<script>
async function load(){const r=await fetch('/api/status');const j=await r.json();
document.getElementById('m').innerHTML=j.modes.map(x=>`<button class="${x==j.mode?'on':''}" onclick="go('${x}')">${x}</button>`).join('');
document.getElementById('s').textContent=JSON.stringify(j,null,2)}
async function go(m){await fetch('/api/mode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:m})});load()}
load();setInterval(load,5000)</script></body></html>"""


def make_handler(manager: Manager):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if self.path == "/api/status":
                return self._send(200, manager.snapshot())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/api/mode":
                return self._send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                mode = json.loads(self.rfile.read(n) or b"{}").get("mode")
                manager.set_mode(mode)
            except (ValueError, AttributeError) as exc:
                return self._send(400, {"error": str(exc)})
            self._send(200, manager.snapshot())

        def log_message(self, fmt, *args):
            log.debug("portal: " + fmt, *args)
    return H


def start_portal(manager: Manager, host: str, port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), make_handler(manager))
    threading.Thread(target=srv.serve_forever, daemon=True, name="portal").start()
    log.info("Portal on http://%s:%d", host, srv.server_address[1])
    return srv


def main() -> None:
    p = argparse.ArgumentParser(description="Tuneshine companion (tram / vinyl)")
    p.add_argument("--host", default=os.environ.get("TUNESHINE_HOST") or None)
    p.add_argument("--port", type=int, default=int(os.environ.get("PORTAL_PORT", "8080")))
    p.add_argument("--bind", default=os.environ.get("PORTAL_BIND", "0.0.0.0"))
    p.add_argument("--mode", choices=MODES, help="override saved mode at startup")
    p.add_argument("--dry-run", action="store_true", help="render tram preview.png from fake data and exit")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if a.dry_run:
        import tram_mode
        print(f"wrote {tram_mode.dry_run()}")
        return
    mgr = Manager(TuneshineClient(host=a.host))
    srv = start_portal(mgr, a.bind, a.port)
    done = threading.Event()
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: done.set())
    mgr.set_mode(a.mode or load_mode(), persist=bool(a.mode))
    done.wait()
    log.info("Shutting down…")
    srv.shutdown()
    mgr.shutdown()


if __name__ == "__main__":
    main()
