"""Tram mode: show recent/next trams on Tuneshine as an idle, overridable image.

Music always wins: we skip while something non-idle plays, and the image is sent with
idle=True + overridable=True so the device itself lets streaming artwork take over.
Outside the active window we clear our own image (never anyone else's).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import tram_display
import tram_golemio
from tuneshine import TuneshineClient, music_playing, ours_on_screen

log = logging.getLogger("tram")
HERE = Path(__file__).parent
SERVICE_NAME = "Tram"


def _list(name: str, default: list[str]) -> list[str]:
    if name not in os.environ:
        return default
    return [x.strip() for x in os.environ[name].split(",") if x.strip()]


def config() -> dict:
    return dict(
        key=os.environ.get("GOLEMIO_API_KEY", ""),
        stops=_list("STOP_NAMES", ["Poštovská"]),
        stop_ids=_list("STOP_IDS", ["U873Z1P"]),   # Poštovská platform A -> Vysočanská
        platforms=_list("PLATFORMS", ["A"]),
        direction=_list("DIRECTION_FILTER", []),
        lines=_list("LINES", []),
        min_minutes=int(os.environ.get("MIN_MINUTES", "0")),
        window=os.environ.get("ACTIVE_WINDOW", "07:00-20:00"),
        tz=ZoneInfo(os.environ.get("TIMEZONE", "Europe/Prague")),
        poll=int(os.environ.get("POLL_SECONDS", "60")),
    )


def in_window(now: dt.datetime, window: str) -> bool:
    a, b = window.split("-")
    s, e = dt.time.fromisoformat(a.strip()), dt.time.fromisoformat(b.strip())
    t = now.time()
    return s <= t < e if s <= e else (t >= s or t < e)


def select(data: dict, now: dt.datetime, cfg: dict):
    """Return (last_departed or None, [upcoming...])."""
    deps = tram_golemio.parse(data, cfg["platforms"], cfg["direction"], cfg["lines"])
    past = [d for d in deps if d.when < now]
    up = [d for d in deps if d.when >= now and d.minutes_from(now) >= cfg["min_minutes"]]
    return (past[-1] if past else None), up


def tick(cfg, client, fetch, now, status: dict | None = None) -> bool:
    """One poll cycle. Returns True if we're inside the active window."""
    status = status if status is not None else {}
    state = client.get_state()
    status["tuneshine"] = summarize_state(state)
    if not in_window(now, cfg["window"]):
        if ours_on_screen(state, SERVICE_NAME):
            log.info("Tram window over, releasing display")
            client.clear(preserve_image=False)
        status["tram"] = "outside window"
        return False
    if music_playing(state, SERVICE_NAME):
        status["tram"] = "paused, music playing"
        return True
    last, deps = select(fetch(), now, cfg)
    status["last_fetch"] = now.isoformat(timespec="seconds")
    status["next"] = [d.when.astimezone(cfg["tz"]).strftime("%H:%M") for d in deps[:2]]
    img = tram_display.render(deps, now, last)
    title = f"Tram {deps[0].when.astimezone(cfg['tz']):%H:%M}" if deps else "No trams"
    client.push_image(tram_display.to_webp(img), track_name=title, animation="none",
                      idle=True, overridable=True, service_name=SERVICE_NAME)
    status["tram"] = "showing"
    return True


def summarize_state(state: dict) -> dict:
    remote = state.get("remoteMetadata") or {}
    local = state.get("localMetadata") or {}
    return {
        "imageSource": state.get("imageSource"),
        "remote": {k: remote.get(k) for k in ("trackName", "artistName", "serviceName", "idle") if k in remote},
        "local": {k: local.get(k) for k in ("trackName", "serviceName", "idle") if k in local},
    }


def run(client: TuneshineClient, stop: threading.Event, status: dict, cfg: dict | None = None) -> None:
    """Loop until `stop` is set. Clears our image on exit."""
    cfg = cfg or config()
    if not cfg["key"]:
        status["tram"] = "error: GOLEMIO_API_KEY missing"
        log.error("GOLEMIO_API_KEY missing (get one at https://api.golemio.cz/api-keys)")
        stop.wait()
        return
    session = requests.Session()
    fetch = lambda: tram_golemio.fetch(cfg["key"], cfg["stops"], session=session, stop_ids=cfg["stop_ids"])
    try:
        while not stop.is_set():
            try:
                tick(cfg, client, fetch, dt.datetime.now(cfg["tz"]), status)
                status.pop("error", None)
            except (requests.RequestException, RuntimeError) as exc:
                log.warning("Tram cycle failed: %s", exc)
                status["error"] = str(exc)
            now = dt.datetime.now().timestamp()
            stop.wait(cfg["poll"] - now % cfg["poll"])
    finally:
        try:
            if ours_on_screen(client.get_state(), SERVICE_NAME):
                client.clear(preserve_image=False)
        except Exception as exc:  # noqa: BLE001
            log.debug("Cleanup failed: %s", exc)


def dry_run(out_dir: Path = HERE) -> Path:
    cfg = config()
    data = json.loads((HERE / "tests" / "fake_departures.json").read_text())
    now = dt.datetime(2026, 10, 16, 8, 10, tzinfo=cfg["tz"])
    last, deps = select(data, now, cfg)
    img = tram_display.render(deps, now, last)
    out = out_dir / "preview.png"
    img.resize((320, 320), 0).save(out)
    img.save(out_dir / "preview_64.png")
    return out
