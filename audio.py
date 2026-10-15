"""Microphone capture — records short WAV snippets for music recognition."""

import io
import logging

import numpy as np
import sounddevice as sd
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

log = logging.getLogger(__name__)

SAMPLE_RATE = 44100  # 44.1kHz — matches Shazam mobile app; better fingerprint for obscure tracks
CHANNELS = 1

# Normalize audio to this target RMS level (0–32767 int16 range).
# Weak signals from quiet vinyl give Shazam too little to work with.
_TARGET_RMS = 3000
_MIN_RMS = 50  # below this is effectively silence — don't normalize, let Shazam return None

# ── Vinyl preprocessing ──────────────────────────────────────────────────────
# High-pass filter removes turntable rumble and low-frequency motor noise
_HIGHPASS_FREQ = 80  # Hz
_HIGHPASS_ORDER = 4

# Click/pop removal — transients exceeding this many standard deviations
# are replaced with interpolated values
_CLICK_THRESHOLD_STD = 3.5

# Butterworth high-pass filter coefficients (computed once at import time)
_hp_sos = butter(_HIGHPASS_ORDER, _HIGHPASS_FREQ, btype="high", fs=SAMPLE_RATE, output="sos")


def _remove_clicks(audio: np.ndarray) -> np.ndarray:
    """Replace transient clicks/pops with interpolated values.

    Vinyl pops appear as single-sample or very short spikes far above the
    signal mean.  Detect them via threshold on the absolute sample-to-sample
    difference and linearly interpolate over them.
    """
    samples = audio.astype(np.float32).ravel()
    diff = np.abs(np.diff(samples, prepend=samples[0]))
    threshold = _CLICK_THRESHOLD_STD * np.std(diff)
    clicks = diff > threshold

    if not np.any(clicks):
        return audio

    indices = np.where(clicks)[0]
    cleaned = samples.copy()
    for idx in indices:
        # Find nearest non-click neighbours and interpolate
        left = idx - 1
        while left >= 0 and clicks[left]:
            left -= 1
        right = idx + 1
        while right < len(samples) and right < len(clicks) and clicks[right]:
            right += 1
        left_val = samples[max(left, 0)]
        right_val = samples[min(right, len(samples) - 1)]
        cleaned[idx] = (left_val + right_val) / 2.0

    log.debug("Removed %d click samples", len(indices))
    return np.clip(cleaned, -32768, 32767).astype(np.int16).reshape(audio.shape)


def _preprocess(audio: np.ndarray) -> np.ndarray:
    """Apply vinyl-specific audio cleanup: high-pass filter + click removal."""
    # High-pass filter — removes turntable rumble / motor hum
    filtered = sosfilt(_hp_sos, audio.astype(np.float32), axis=0)
    filtered = np.clip(filtered, -32768, 32767).astype(np.int16)

    # Click/pop removal
    cleaned = _remove_clicks(filtered)
    return cleaned


def record_snippet(duration: float = 12.0, preprocess: bool = True) -> bytes:
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
    if rms < _MIN_RMS:
        log.debug("Silence detected (RMS=%.1f < %d), skipping processing", rms, _MIN_RMS)
        buf = io.BytesIO()
        wavfile.write(buf, SAMPLE_RATE, audio)
        return buf.getvalue()

    if preprocess:
        audio = _preprocess(audio)

    # Re-calculate RMS after preprocessing (filter may change levels)
    rms = np.sqrt(np.mean(audio.astype(np.float32) ** 2))
    if rms >= _MIN_RMS:
        gain = _TARGET_RMS / rms
        audio = np.clip(audio.astype(np.float32) * gain, -32768, 32767).astype(np.int16)

    buf = io.BytesIO()
    wavfile.write(buf, SAMPLE_RATE, audio)
    return buf.getvalue()


def probe_silence(duration: float = 2.0) -> bool:
    """Record a short probe and return True if the audio is silence.

    Used for adaptive recording — skip the full recording + Shazam call
    when nothing is playing.
    """
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
    is_silent = bool(rms < _MIN_RMS)
    if is_silent:
        log.debug("Probe: silence (RMS=%.1f)", rms)
    else:
        log.debug("Probe: audio detected (RMS=%.1f)", rms)
    return is_silent
