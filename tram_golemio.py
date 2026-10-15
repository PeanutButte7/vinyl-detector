"""Golemio PID departure boards client."""
from __future__ import annotations
import datetime as dt
from dataclasses import dataclass
import requests

URL = "https://api.golemio.cz/v2/pid/departureboards"


@dataclass
class Departure:
    line: str
    destination: str
    when: dt.datetime        # predicted (live) departure, tz-aware
    delay_min: int | None    # None = no realtime data
    platform: str | None = None

    def minutes_from(self, now: dt.datetime) -> int:
        return max(0, int((self.when - now).total_seconds() // 60))


def _parse_ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse(data: dict, platforms=(), direction_words=(), lines=()) -> list[Departure]:
    out = []
    for d in data.get("departures", []):
        route = d.get("route", {}) or {}
        if route.get("type") not in (None, 0):  # GTFS route_type 0 = tram
            continue
        line = str(route.get("short_name", "?"))
        if lines and line not in lines:
            continue
        stop = d.get("stop", {}) or {}
        plat = stop.get("platform_code")
        if platforms and plat not in platforms:
            continue
        dest = (d.get("trip", {}) or {}).get("headsign", "")
        if direction_words and not any(w.lower() in dest.lower() for w in direction_words):
            continue
        ts = d.get("departure_timestamp", {}) or {}
        when = ts.get("predicted") or ts.get("scheduled")
        if not when:
            continue
        delay = d.get("delay", {}) or {}
        delay_min = delay.get("minutes") if delay.get("is_available") else None
        out.append(Departure(line, dest, _parse_ts(when), delay_min, plat))
    out.sort(key=lambda x: x.when)
    return out


def fetch(api_key: str, stop_names: list[str], limit: int = 20, session=requests, stop_ids=()) -> dict:
    params = ([("ids[]", i) for i in stop_ids] if stop_ids else [("names[]", n) for n in stop_names]) + [
        ("mode", "departures"), ("limit", str(limit)), ("minutesAfter", "90"), ("minutesBefore", "15"),
        ("order", "real"), ("routeTypes", "0"),
    ]
    r = session.get(URL, params=params, headers={"X-Access-Token": api_key}, timeout=10)
    r.raise_for_status()
    return r.json()
