"""Microphone capture — records short WAV snippets for music recognition."""

import io
import numpy as np
import sounddevice as sd
from scipy.io import wavfile

SAMPLE_RATE = 44100  # 44.1kHz — matches Shazam mobile app; better fingerprint for obscure tracks
CHANNELS = 1

# Normalize audio to this target RMS level (0–32767 int16 range).
# Weak signals from quiet vinyl give Shazam too little to work with.
_TARGET_RMS = 3000
_MIN_RMS = 50  # below this is effectively silence — don't normalize, let Shazam return None


def record_snippet(duration: float = 6.0) -> bytes:
    """Record `duration` seconds from the default mic, return WAV bytes."""
    try:
        audio = sd.rec(
            int(duration * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
        )
        sd.wait()
    except sd.PortAudioError as exc:
        raise OSError(f"Microphone not available: {exc}") from exc

    rms = np.sqrt(np.mean(audio.astype(np.float32) ** 2))
    if rms >= _MIN_RMS:
        gain = _TARGET_RMS / rms
        audio = np.clip(audio.astype(np.float32) * gain, -32768, 32767).astype(np.int16)

    buf = io.BytesIO()
    wavfile.write(buf, SAMPLE_RATE, audio)
    return buf.getvalue()
