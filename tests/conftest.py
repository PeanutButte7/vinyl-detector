"""Allow running tests on machines without the PortAudio system library (CI, dev boxes).
Tests mock sounddevice anyway; this only stubs the import when PortAudio is missing."""
import sys, types

try:
    import sounddevice  # noqa: F401
except OSError:
    stub = types.ModuleType("sounddevice")
    stub.PortAudioError = type("PortAudioError", (Exception,), {})
    stub.rec = stub.wait = lambda *a, **k: None
    sys.modules["sounddevice"] = stub

import os
os.environ.setdefault("TRAM_MODE_FILE", os.path.join(os.path.dirname(__file__), ".mode-test.json"))
