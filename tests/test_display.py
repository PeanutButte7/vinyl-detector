"""Tests for display image generation."""

import io
from unittest.mock import patch, MagicMock

from PIL import Image
import pytest

from display import (
    extract_colors,
    generate_error_image,
    generate_image,
    generate_searching_image,
    _ensure_contrast,
    _wcag_luminance,
)
from recognize import TrackInfo


def _make_track(**kwargs) -> TrackInfo:
    defaults = dict(
        title="Test Song",
        artist="Test Artist",
        album="Test Album",
        album_art_url="https://example.com/art.jpg",
        duration_ms=240000,
        track_number=3,
        total_tracks=12,
        shazam_key="12345",
    )
    defaults.update(kwargs)
    return TrackInfo(**defaults)


class TestGenerateImage:
    def test_returns_valid_webp(self):
        track = _make_track()
        colors = ((20, 20, 20), (255, 255, 255), (180, 80, 80))
        result = generate_image(track, colors, elapsed_s=60.0)

        assert isinstance(result, bytes)
        # Verify it's a valid image
        img = Image.open(io.BytesIO(result))
        assert img.size == (64, 64)
        assert img.format == "WEBP"

    def test_static_image_for_short_title(self):
        track = _make_track(title="Short")
        colors = ((20, 20, 20), (255, 255, 255), (180, 80, 80))
        result = generate_image(track, colors, elapsed_s=60.0)

        img = Image.open(io.BytesIO(result))
        assert img.size == (64, 64)

    def test_animated_image_for_long_title(self):
        track = _make_track(title="This Is A Very Long Song Title That Should Scroll")
        colors = ((20, 20, 20), (255, 255, 255), (180, 80, 80))
        result = generate_image(track, colors, elapsed_s=60.0)

        img = Image.open(io.BytesIO(result))
        assert img.size == (64, 64)
        assert img.n_frames > 1  # animated

    def test_no_duration_produces_static(self):
        track = _make_track(duration_ms=0)
        colors = ((20, 20, 20), (255, 255, 255), (180, 80, 80))
        result = generate_image(track, colors, elapsed_s=60.0)

        img = Image.open(io.BytesIO(result))
        assert img.n_frames == 1


class TestGenerateErrorImage:
    def test_returns_valid_webp(self):
        result = generate_error_image("MIC", "NO INPUT", (180, 50, 50))
        assert isinstance(result, bytes)

        img = Image.open(io.BytesIO(result))
        assert img.size == (64, 64)

    def test_single_line(self):
        result = generate_error_image("ERROR")
        assert isinstance(result, bytes)


class TestGenerateSearchingImage:
    def test_returns_animated_webp(self):
        result = generate_searching_image()
        assert isinstance(result, bytes)

        img = Image.open(io.BytesIO(result))
        assert img.size == (64, 64)
        assert img.n_frames > 1  # animated dashed bar


class TestColorExtraction:
    @patch("display.requests.get")
    def test_network_error_returns_defaults(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException("timeout")

        result = extract_colors("https://example.com/art.jpg")
        assert result == ((20, 20, 20), (255, 255, 255), (180, 80, 80))

    @patch("display.requests.get")
    def test_successful_extraction(self, mock_get):
        # Create a simple test image
        img = Image.new("RGB", (100, 100), (100, 50, 50))
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        mock_response = MagicMock()
        mock_response.content = buf.getvalue()
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = extract_colors("https://example.com/art.jpg")
        assert len(result) == 3
        assert all(len(c) == 3 for c in result)


class TestContrastUtils:
    def test_wcag_luminance_black(self):
        assert _wcag_luminance((0, 0, 0)) == pytest.approx(0.0)

    def test_wcag_luminance_white(self):
        assert _wcag_luminance((255, 255, 255)) == pytest.approx(1.0, abs=0.01)

    def test_ensure_contrast_good_pair(self):
        # White on black — excellent contrast
        result = _ensure_contrast((0, 0, 0), (255, 255, 255))
        assert result == (255, 255, 255)

    def test_ensure_contrast_bad_pair_returns_fallback(self):
        # Dark gray on dark gray — poor contrast, should fall back
        result = _ensure_contrast((30, 30, 30), (40, 40, 40))
        assert result in ((255, 255, 255), (0, 0, 0))
