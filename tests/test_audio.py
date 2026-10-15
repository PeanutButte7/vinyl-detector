"""Tests for audio capture and preprocessing."""

from unittest.mock import patch

import numpy as np
import pytest

from audio import (
    SAMPLE_RATE,
    _MIN_RMS,
    _TARGET_RMS,
    _preprocess,
    _remove_clicks,
    probe_silence,
    record_snippet,
)


def _make_tone(freq: float = 440.0, duration: float = 1.0, amplitude: int = 5000) -> np.ndarray:
    """Generate a simple sine wave as int16 samples."""
    t = np.arange(int(duration * SAMPLE_RATE)) / SAMPLE_RATE
    samples = (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.int16)
    return samples.reshape(-1, 1)


def _make_silence(duration: float = 1.0) -> np.ndarray:
    """Generate near-silence (RMS well below _MIN_RMS)."""
    return np.zeros((int(duration * SAMPLE_RATE), 1), dtype=np.int16)


class TestRecordSnippet:
    @patch("audio.sd")
    def test_returns_wav_bytes(self, mock_sd):
        """record_snippet returns valid WAV bytes."""
        tone = _make_tone(duration=0.5)
        mock_sd.rec.return_value = tone
        mock_sd.wait.return_value = None
        mock_sd.PortAudioError = Exception

        result = record_snippet(duration=0.5, preprocess=False)
        assert isinstance(result, bytes)
        assert result[:4] == b"RIFF"  # WAV header

    @patch("audio.sd")
    def test_silence_skips_processing(self, mock_sd):
        """Silence (RMS < _MIN_RMS) returns WAV without normalization."""
        silence = _make_silence(duration=0.5)
        mock_sd.rec.return_value = silence
        mock_sd.wait.return_value = None
        mock_sd.PortAudioError = Exception

        result = record_snippet(duration=0.5, preprocess=True)
        assert isinstance(result, bytes)
        assert result[:4] == b"RIFF"

    @patch("audio.sd")
    def test_normalization_boosts_quiet_audio(self, mock_sd):
        """Quiet audio should be normalized to approximately _TARGET_RMS."""
        quiet_tone = _make_tone(amplitude=200, duration=0.5)
        mock_sd.rec.return_value = quiet_tone.copy()
        mock_sd.wait.return_value = None
        mock_sd.PortAudioError = Exception

        result = record_snippet(duration=0.5, preprocess=False)
        # Parse the WAV to check the normalized audio
        import io
        from scipy.io import wavfile
        buf = io.BytesIO(result)
        _, data = wavfile.read(buf)
        rms = np.sqrt(np.mean(data.astype(np.float32) ** 2))
        assert rms > 200  # should be boosted

    @patch("audio.sd")
    def test_mic_error_raises_oserror(self, mock_sd):
        """PortAudioError is converted to OSError."""
        mock_sd.PortAudioError = type("PortAudioError", (Exception,), {})
        mock_sd.rec.side_effect = mock_sd.PortAudioError("no device")

        with pytest.raises(OSError, match="Microphone not available"):
            record_snippet(duration=0.5)

    @patch("audio.sd")
    def test_preprocess_flag_respected(self, mock_sd):
        """When preprocess=False, _preprocess is not called."""
        tone = _make_tone(duration=0.5)
        mock_sd.rec.return_value = tone
        mock_sd.wait.return_value = None
        mock_sd.PortAudioError = Exception

        with patch("audio._preprocess") as mock_pp:
            record_snippet(duration=0.5, preprocess=False)
            mock_pp.assert_not_called()


class TestPreprocessing:
    def test_highpass_removes_low_frequency(self):
        """High-pass filter should attenuate a 30Hz signal."""
        low_tone = _make_tone(freq=30.0, amplitude=10000, duration=1.0)
        processed = _preprocess(low_tone)
        rms_before = np.sqrt(np.mean(low_tone.astype(np.float32) ** 2))
        rms_after = np.sqrt(np.mean(processed.astype(np.float32) ** 2))
        assert rms_after < rms_before * 0.5  # should be significantly attenuated

    def test_highpass_preserves_music_frequency(self):
        """High-pass filter should pass a 1kHz signal with minimal loss."""
        tone = _make_tone(freq=1000.0, amplitude=10000, duration=1.0)
        processed = _preprocess(tone)
        rms_before = np.sqrt(np.mean(tone.astype(np.float32) ** 2))
        rms_after = np.sqrt(np.mean(processed.astype(np.float32) ** 2))
        assert rms_after > rms_before * 0.8  # should be mostly preserved

    def test_click_removal(self):
        """Click removal should reduce spike amplitude."""
        # Create a clean signal with an artificial click
        tone = _make_tone(freq=440.0, amplitude=3000, duration=0.5)
        samples = tone.copy()
        # Insert a sharp click
        click_pos = len(samples) // 2
        samples[click_pos] = 32000
        samples[click_pos + 1] = -32000

        cleaned = _remove_clicks(samples)
        # The click should be attenuated
        assert abs(int(cleaned[click_pos][0])) < abs(int(samples[click_pos][0]))


class TestProbeSilence:
    @patch("audio.sd")
    def test_detects_silence(self, mock_sd):
        silence = _make_silence(duration=2.0)
        mock_sd.rec.return_value = silence
        mock_sd.wait.return_value = None
        mock_sd.PortAudioError = Exception

        assert probe_silence(duration=2.0) is True

    @patch("audio.sd")
    def test_detects_audio(self, mock_sd):
        tone = _make_tone(amplitude=5000, duration=2.0)
        mock_sd.rec.return_value = tone
        mock_sd.wait.return_value = None
        mock_sd.PortAudioError = Exception

        assert probe_silence(duration=2.0) is False

    @patch("audio.sd")
    def test_mic_error_raises_oserror(self, mock_sd):
        mock_sd.PortAudioError = type("PortAudioError", (Exception,), {})
        mock_sd.rec.side_effect = mock_sd.PortAudioError("no device")

        with pytest.raises(OSError):
            probe_silence()
