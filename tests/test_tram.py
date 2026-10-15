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
