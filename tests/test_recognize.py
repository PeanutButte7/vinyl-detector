"""Tests for music recognition and enrichment."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from recognize import RecognitionError, TrackInfo, identify


@pytest.fixture
def shazam_match_response():
    """A typical Shazam response for a matched track."""
    return {
        "track": {
            "title": "Bohemian Rhapsody",
            "subtitle": "Queen",
            "key": "12345",
            "images": {
                "coverart": "https://example.com/art.jpg",
                "coverarthq": "https://example.com/art_hq.jpg",
            },
            "sections": [
                {
                    "type": "SONG",
                    "metadata": [
                        {"title": "Album", "text": "A Night at the Opera"},
                        {"title": "Label", "text": "EMI"},
                    ],
                }
            ],
        }
    }


@pytest.fixture
def shazam_no_match_response():
    """A Shazam response when no song is matched."""
    return {}


class TestIdentify:
    @pytest.mark.asyncio
    async def test_successful_match(self, shazam_match_response):
        with patch("recognize.Shazam") as MockShazam:
            instance = MockShazam.return_value
            instance.recognize = AsyncMock(return_value=shazam_match_response)

            result = await identify(b"fake-wav-data")

            assert result is not None
            assert result.title == "Bohemian Rhapsody"
            assert result.artist == "Queen"
            assert result.album == "A Night at the Opera"
            assert result.album_art_url == "https://example.com/art_hq.jpg"
            assert result.shazam_key == "12345"

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, shazam_no_match_response):
        with patch("recognize.Shazam") as MockShazam:
            instance = MockShazam.return_value
            instance.recognize = AsyncMock(return_value=shazam_no_match_response)

            result = await identify(b"fake-wav-data")
            assert result is None

    @pytest.mark.asyncio
    async def test_retries_on_timeout(self, shazam_match_response):
        with patch("recognize.Shazam") as MockShazam:
            instance = MockShazam.return_value
            # First call times out, second succeeds
            instance.recognize = AsyncMock(
                side_effect=[asyncio.TimeoutError(), shazam_match_response]
            )

            with patch("recognize.asyncio.sleep", new_callable=AsyncMock):
                result = await identify(b"fake-wav-data")

            assert result is not None
            assert result.title == "Bohemian Rhapsody"
            assert instance.recognize.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_3_failures(self):
        with patch("recognize.Shazam") as MockShazam:
            instance = MockShazam.return_value
            instance.recognize = AsyncMock(side_effect=asyncio.TimeoutError())

            with patch("recognize.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(RecognitionError):
                    await identify(b"fake-wav-data")

            assert instance.recognize.call_count == 3

    @pytest.mark.asyncio
    async def test_retries_on_exception(self, shazam_match_response):
        with patch("recognize.Shazam") as MockShazam:
            instance = MockShazam.return_value
            instance.recognize = AsyncMock(
                side_effect=[RuntimeError("connection reset"), shazam_match_response]
            )

            with patch("recognize.asyncio.sleep", new_callable=AsyncMock):
                result = await identify(b"fake-wav-data")

            assert result is not None
            assert instance.recognize.call_count == 2

    @pytest.mark.asyncio
    async def test_prefers_hq_cover_art(self, shazam_match_response):
        with patch("recognize.Shazam") as MockShazam:
            instance = MockShazam.return_value
            instance.recognize = AsyncMock(return_value=shazam_match_response)

            result = await identify(b"fake-wav-data")
            assert result.album_art_url == "https://example.com/art_hq.jpg"

    @pytest.mark.asyncio
    async def test_falls_back_to_standard_cover_art(self):
        response = {
            "track": {
                "title": "Test",
                "subtitle": "Artist",
                "key": "1",
                "images": {"coverart": "https://example.com/art.jpg"},
                "sections": [],
            }
        }
        with patch("recognize.Shazam") as MockShazam:
            instance = MockShazam.return_value
            instance.recognize = AsyncMock(return_value=response)

            result = await identify(b"fake-wav-data")
            assert result.album_art_url == "https://example.com/art.jpg"
