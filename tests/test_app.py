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
    app.save_vinyl_enabled(True)
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
    app.save_vinyl_enabled(True)
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


def test_crashed_mode_is_retried():
    calls = []
    def flaky(stop):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("No Tuneshine found")
        stop.wait()
    m = app.Manager(MagicMock(), runners={"tram": flaky, "vinyl": None})
    stop = threading.Event()
    t = threading.Thread(target=m._guard, args=(flaky, stop, 0.05))
    t.start()
    import time
    time.sleep(0.3)
    stop.set(); t.join(2)
    assert len(calls) == 2


def test_cold_start_old_entrypoint_without_portaudio(tmp_path):
    """Simulate the Pi's existing unit: `python vinyl_detector.py` (no args), sounddevice broken."""
    import os, socket, subprocess, sys, time, signal as sig
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    (tmp_path / "sitecustomize.py").write_text(
        "import sys\nclass _F:\n    def find_spec(self, name, *a):\n"
        "        if name == 'sounddevice': raise OSError('PortAudio library not found')\n"
        "sys.meta_path.insert(0, _F())\n")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {**os.environ, "PYTHONPATH": str(tmp_path), "TRAM_MODE_FILE": str(tmp_path / "mode.json"),
           "PORTAL_PORT": str(port), "PORTAL_BIND": "127.0.0.1", "TUNESHINE_HOST": "127.0.0.1:9",
           "GOLEMIO_API_KEY": ""}
    p = subprocess.Popen([sys.executable, "vinyl_detector.py"], cwd=repo, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        st = None
        for _ in range(50):
            time.sleep(0.1)
            try:
                st = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1))
                break
            except OSError:
                pass
        assert st and st["mode"] == "tram", p.stdout.read1().decode() if p.poll() is not None else st
        assert "GOLEMIO_API_KEY missing" in st.get("tram", "")
        assert st["vinyl_enabled"] is False
        assert p.poll() is None
    finally:
        p.send_signal(sig.SIGTERM)
        out = p.communicate(timeout=30)[0].decode()
    assert p.returncode == 0, out
    assert "sounddevice" not in out and "Traceback" not in out, out



def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        r = urllib.request.urlopen(req)
        return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_vinyl_listening_default_off(tmp_path):
    assert app.vinyl_enabled() is False
    (tmp_path / "mode.json").write_text('{"mode": "vinyl"}')  # legacy/missing flag
    assert app.vinyl_enabled() is False and app.load_mode() == "tram"


def test_vinyl_rejected_when_off_and_allowed_when_on(monkeypatch):
    import sys
    monkeypatch.delitem(sys.modules, "vinyl_detector", raising=False)
    monkeypatch.delitem(sys.modules, "audio", raising=False)
    log = []
    m = app.Manager(MagicMock(), runners=_fake_runners(log))
    srv = app.start_portal(m, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        code, body = _post(base, "/api/mode", {"mode": "vinyl"})
        assert code == 403 and "turned off" in body["error"] and body["mode"] != "vinyl"
        assert log == [] and "audio" not in sys.modules and "vinyl_detector" not in sys.modules
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "Vinyl listening" in page and "disabled" in page
        assert _post(base, "/api/vinyl", {"enabled": True})[1]["vinyl_enabled"] is True
        code, body = _post(base, "/api/mode", {"mode": "vinyl"})
        assert code == 200 and body["mode"] == "vinyl" and log == ["start vinyl"]
        # turning it off while running stops vinyl and falls back to tram
        code, body = _post(base, "/api/vinyl", {"enabled": False})
        assert body["mode"] == "tram" and log[:2] == ["start vinyl", "stop vinyl"]
        assert _post(base, "/api/vinyl", {"enabled": "yes"})[0] == 400
    finally:
        srv.shutdown(); m.shutdown()


def test_real_vinyl_runner_refuses_when_off(monkeypatch):
    import sys
    monkeypatch.delitem(sys.modules, "vinyl_detector", raising=False)
    monkeypatch.delitem(sys.modules, "audio", raising=False)
    m = app.Manager(MagicMock())
    m._run_vinyl(threading.Event())
    assert "turned off" in m.status["error"]
    assert "audio" not in sys.modules and "vinyl_detector" not in sys.modules
