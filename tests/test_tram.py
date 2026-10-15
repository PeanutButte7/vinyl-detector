import datetime as dt
import json
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import tram_display
import tram_golemio
import tram_mode as tm
from tuneshine import music_playing

TZ = ZoneInfo("Europe/Prague")
DATA = json.loads((Path(__file__).parent / "fake_departures.json").read_text())
NOW = dt.datetime(2026, 10, 16, 8, 10, tzinfo=TZ)


def cfg(**kw):
    c = dict(platforms=["A"], direction=[], lines=[], min_minutes=0, window="07:00-20:00", tz=TZ)
    c.update(kw)
    return c


def test_parse_skips_bus_and_sorts():
    assert [d.when.minute for d in tram_golemio.parse(DATA)] == [2, 8, 11, 14, 19]


def test_defaults(monkeypatch):
    for k in ("STOP_IDS", "PLATFORMS", "ACTIVE_WINDOW"):
        monkeypatch.delenv(k, raising=False)
    c = tm.config()
    assert c["stop_ids"] == ["U873Z1P"] and c["platforms"] == ["A"] and c["window"] == "07:00-20:00"


def test_select_last_and_upcoming():
    last, up = tm.select(DATA, NOW, cfg())
    assert last.when.minute == 8 and last.delay_min == 1
    assert [d.when.minute for d in up] == [14, 19]
    assert tm.select(DATA, NOW.replace(minute=0), cfg())[0] is None


def test_window():
    assert tm.in_window(NOW, "07:00-20:00")
    assert not tm.in_window(NOW.replace(hour=20), "07:00-20:00")
    assert not tm.in_window(NOW.replace(hour=6, minute=59), "07:00-20:00")


def test_render():
    last, up = tm.select(DATA, NOW, cfg())
    for args in ((up, last), (up[1:], None), ([], None)):
        img = tram_display.render(args[0], NOW, args[1])
        assert img.size == (64, 64)
    assert tram_display.to_webp(img)[:4] == b"RIFF"
    assert tram_display._mins_label(last, NOW) == "-2m"


def test_music_detection():
    assert music_playing({"imageSource": "remote", "remoteMetadata": {"idle": False}}, "Tram")
    assert not music_playing({"imageSource": "remote", "remoteMetadata": {"idle": True}}, "Tram")
    assert music_playing({"imageSource": "local", "localMetadata": {"serviceName": "Vinyl Detector"}}, "Tram")
    assert not music_playing({"imageSource": "local", "localMetadata": {"serviceName": "Tram", "idle": True}}, "Tram")


def _client(state):
    c = MagicMock()
    c.get_state.return_value = state
    return c


def test_tick_pushes_idle_overridable():
    c = _client({"imageSource": "system"})
    status = {}
    assert tm.tick(cfg(), c, lambda: DATA, NOW, status)
    kw = c.push_image.call_args.kwargs
    assert kw["idle"] and kw["overridable"] and kw["service_name"] == "Tram"
    assert status["tram"] == "showing" and status["next"] == ["08:14", "08:19"]


def test_tick_skips_when_music():
    c = _client({"imageSource": "remote", "remoteMetadata": {"idle": False}})
    fetch = MagicMock()
    tm.tick(cfg(), c, fetch, NOW)
    c.push_image.assert_not_called()
    fetch.assert_not_called()


def test_tick_releases_after_window():
    c = _client({"imageSource": "local", "localMetadata": {"serviceName": "Tram", "idle": True}})
    assert not tm.tick(cfg(), c, lambda: DATA, NOW.replace(hour=20, minute=1))
    c.clear.assert_called_once_with(preserve_image=False)


def test_tick_outside_window_leaves_music_alone():
    c = _client({"imageSource": "remote", "remoteMetadata": {"idle": False}})
    tm.tick(cfg(), c, lambda: DATA, NOW.replace(hour=21))
    c.clear.assert_not_called()


def test_fetch_params():
    s = MagicMock()
    s.get.return_value.json.return_value = DATA
    tram_golemio.fetch("KEY", ["Poštovská"], session=s, stop_ids=["U873Z1P"])
    kw = s.get.call_args.kwargs
    assert kw["headers"] == {"X-Access-Token": "KEY"}
    assert ("ids[]", "U873Z1P") in kw["params"] and ("minutesBefore", "15") in kw["params"]


def test_row_fits_64px_worst_case():
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    f = tram_display._font()
    for label, clock, delay in (("-15m", "08:08", "+12"), ("now", "18:59", "+125"), ("88m", "09:38", "+1")):
        assert tram_display._width(d, f, *tram_display._fit(d, f, label, clock, delay)) <= 64
    assert tram_display._fit(d, f, "4m", "08:14", "+2") == ("4m", "08:14", "+2")  # common case untouched


def test_canceled_trip_skipped():
    data = json.loads(json.dumps(DATA))
    for dep in data["departures"]:
        if dep["departure_timestamp"]["predicted"].startswith("2026-10-16T08:14"):
            dep["trip"]["is_canceled"] = True
    assert [x.when.minute for x in tm.select(data, NOW, cfg())[1]] == [19]


def test_window_end_clears_our_image_even_under_music():
    c = _client({"imageSource": "remote", "remoteMetadata": {"idle": False},
                 "localMetadata": {"serviceName": "Tram", "idle": True}})
    tm.tick(cfg(), c, lambda: DATA, NOW.replace(hour=20, minute=1))
    c.clear.assert_called_once_with(preserve_image=False)


def test_unexpected_error_does_not_kill_loop(monkeypatch):
    import threading
    calls = []
    def boom(*a, **k):
        calls.append(1)
        if len(calls) >= 2:
            stop.set()
        raise KeyError("weird data")
    monkeypatch.setattr(tm, "tick", boom)
    stop = threading.Event()
    status = {}
    c = cfg(key="K", stops=[], stop_ids=["U873Z1P"], poll=1)
    c["tz"] = TZ
    tm.run(_client({}), stop, status, c)
    assert len(calls) == 2 and "KeyError" in status["error"]


def test_no_key_waits_quietly():
    import threading
    stop = threading.Event()
    c = _client({})
    t = threading.Thread(target=tm.run, args=(c, stop, {}, cfg(key="")))
    t.start(); stop.set(); t.join(2)
    assert not t.is_alive()
    c.get_state.assert_not_called()
