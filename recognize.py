"""Music recognition via ShazamIO — free, no API key required."""

from __future__ import annotations

import asyncio
import dataclasses
import logging

import aiohttp
from shazamio import Shazam

log = logging.getLogger(__name__)


class RecognitionError(Exception):
    """Raised when Shazam recognition fails due to a network or API error.

    Distinct from a None return, which means the audio was processed but no
    song was matched.
    """


# Apple Music API — publicly accessible catalog endpoint (no auth needed for basic lookups)
AM_LOOKUP_URL = "https://itunes.apple.com/lookup"


@dataclasses.dataclass
class TrackInfo:
    title: str
    artist: str
    album: str
    album_art_url: str
    duration_ms: int = 0    # 0 = unknown
    track_number: int = 0   # 0 = unknown
    total_tracks: int = 0   # 0 = unknown
    shazam_key: str = ""    # internal — used to drive enrich_track()


async def _enrich_from_itunes(track_adam_id: str, album_adam_id: str, info: TrackInfo) -> None:
    """Use the iTunes Search/Lookup API to get duration, track number, and track count.

    This API is free, requires no auth, and returns JSON with all the metadata we need.
    """
    try:
        async with aiohttp.ClientSession() as session:
            params = {
                "id": album_adam_id,
                "entity": "song",
                "country": "US",
            }
            async with session.get(AM_LOOKUP_URL, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    log.debug("iTunes lookup returned %d", resp.status)
                    return
                data = await resp.json(content_type=None)

        results = data.get("results", [])
        if not results:
            log.debug("iTunes lookup returned no results")
            return

        # First result is the album (collection), rest are tracks
        album_info = results[0] if results else {}
        tracks = [r for r in results if r.get("wrapperType") == "track"]

        info.total_tracks = album_info.get("trackCount", len(tracks))

        if not info.album and "collectionName" in album_info:
            info.album = album_info["collectionName"]

        # Find our track by ID first, then fall back to case-insensitive title match
        for t in tracks:
            if str(t.get("trackId", "")) == track_adam_id:
                info.duration_ms = t.get("trackTimeMillis", 0)
                info.track_number = t.get("trackNumber", 0)
                log.info(
                    "iTunes enriched: duration=%dms, track %d/%d",
                    info.duration_ms, info.track_number, info.total_tracks,
                )
                return

        for t in tracks:
            if t.get("trackName", "").lower() == info.title.lower():
                info.duration_ms = t.get("trackTimeMillis", 0)
                info.track_number = t.get("trackNumber", 0)
                log.info(
                    "iTunes enriched (title match): duration=%dms, track %d/%d",
                    info.duration_ms, info.track_number, info.total_tracks,
                )
                return

        log.debug("Track %s not found among %d album tracks", track_adam_id, len(tracks))

    except aiohttp.ClientError as exc:
        log.warning("iTunes enrichment network error: %s", exc)
    except asyncio.TimeoutError:
        log.warning("iTunes enrichment timed out")
    except Exception:
        log.warning("iTunes enrichment failed", exc_info=True)


async def enrich_track(info: TrackInfo) -> None:
    """Fetch iTunes metadata (duration, track#, total_tracks) in-place.

    Safe to call concurrently with other coroutines — designed to run alongside
    extract_colors() via asyncio.gather().
    """
    if not info.shazam_key:
        return
    shazam = Shazam()
    try:
        about = await asyncio.wait_for(
            shazam.track_about(track_id=int(info.shazam_key)),
            timeout=8,
        )
    except asyncio.TimeoutError:
        log.debug("track_about timed out for key=%s", info.shazam_key)
        return
    except Exception as exc:
        log.debug("track_about failed for key=%s: %s", info.shazam_key, exc)
        return

    if not isinstance(about, dict):
        return

    album_adam_id = about.get("albumadamid", "")
    track_adam_id = about.get("trackadamid", "")
    log.debug("adamids: album=%s, track=%s", album_adam_id, track_adam_id)

    if album_adam_id:
        await _enrich_from_itunes(track_adam_id, album_adam_id, info)


async def identify(wav_bytes: bytes) -> TrackInfo | None:
    """Send WAV bytes to Shazam and return basic TrackInfo (without iTunes enrichment).

    Call enrich_track(info) separately — typically in parallel with extract_colors().
    Retries up to 3 times with exponential backoff on transient failures.
    """
    shazam = Shazam()
    result = None

    for attempt in range(3):
        try:
            result = await asyncio.wait_for(shazam.recognize(wav_bytes), timeout=15)
            break
        except asyncio.TimeoutError:
            log.warning("Shazam timed out (attempt %d/3)", attempt + 1)
        except Exception as exc:
            log.warning("Shazam recognition error (attempt %d/3): %s", attempt + 1, exc)
        if attempt < 2:
            await asyncio.sleep(1.0 * (2 ** attempt))

    if result is None:
        raise RecognitionError("Shazam recognition failed after 3 attempts")

    track = result.get("track")
    if not track:
        return None

    # Extract album art — try highest quality first
    images = track.get("images", {})
    art_url = images.get("coverarthq") or images.get("coverart", "")

    # Extract album name from sections metadata
    album = ""
    for section in track.get("sections", []):
        if section.get("type") == "SONG":
            for meta in section.get("metadata", []):
                if meta.get("title") == "Album":
                    album = meta.get("text", "")

    info = TrackInfo(
        title=track.get("title", "Unknown"),
        artist=track.get("subtitle", "Unknown"),
        album=album,
        album_art_url=art_url,
        shazam_key=str(track.get("key", "")),
    )

    log.info("Identified: %s — %s (%s)", info.title, info.artist, info.album)
    return info
