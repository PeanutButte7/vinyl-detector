import json
import threading
import urllib.request
from unittest.mock import MagicMock

import pytest

import app


@pytest.fixture(autouse=True)
def tmp_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAM_MODE_FILE", str(tmp_path / "mode.json"))


def test_mode_default_and_persist():
    assert app.load_mode() == "tram"
    app.save_mode("vinyl")
    assert app.load_mode() == "vinyl"
    with pytest.raises(ValueError):
        app.save_mode("disco")


def _fake_runners(log):
    def mk(name):
        def run(stop):
            log.append(f"start {name}")
            stop.wait()
            log.append(f"stop {name}")
        return run
    return {"tram": mk("tram"), "vinyl": mk("vinyl")}


def test_switch_stops_previous_cleanly():
    log = []
    m = app.Manager(MagicMock(), runners=_fake_runners(log))
    m.set_mode("tram")
    m.set_mode("vinyl")
    m.set_mode("off")
    assert log == ["start tram", "stop tram", "start vinyl", "stop vinyl"]
    assert app.load_mode() == "off"


def test_vinyl_not_imported_in_tram_mode(monkeypatch):
    import sys
    sys.modules.pop("vinyl_detector", None)
    started = threading.Event()
    m = app.Manager(MagicMock(), runners={"tram": lambda s: (started.set(), s.wait()), "vinyl": None})
    m.set_mode("tram")
    started.wait(2)
    assert "vinyl_detector" not in sys.modules
    m.shutdown()


def test_portal_status_and_switch():
    log = []
    m = app.Manager(MagicMock(), runners=_fake_runners(log))
    srv = app.start_portal(m, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert b"Tuneshine mode" in urllib.request.urlopen(base + "/").read()
        req = urllib.request.Request(base + "/api/mode", data=json.dumps({"mode": "tram"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        assert json.load(urllib.request.urlopen(req))["mode"] == "tram"
        assert json.load(urllib.request.urlopen(base + "/api/status"))["mode"] == "tram"
        bad = urllib.request.Request(base + "/api/mode", data=b'{"mode":"x"}', method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad)
        assert e.value.code == 400
    finally:
        srv.shutdown()
        m.shutdown()
