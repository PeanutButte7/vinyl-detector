#!/usr/bin/env python3
"""Vinyl Detector — identify what's playing on your turntable and display it on Tuneshine."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time

import requests

from audio import record_snippet
from display import (
    extract_colors,
    generate_error_image,
    generate_image,
    generate_searching_image,
)
from recognize import RecognitionError, TrackInfo, enrich_track, identify
from tuneshine import TuneshineClient

log = logging.getLogger("vinyl_detector")

# Defaults — all overridable via CLI flags
_DEFAULT_MAX_MISSES = 3
_DEFAULT_CYCLE_SLEEP = 2.0
_DEFAULT_IDLE_SLEEP = 8.0
_DEFAULT_RECORD_DURATION = 6.0
_DEFAULT_HANDOFF_TIMEOUT = 120.0  # seconds idle before clearing display and handing back to Tuneshine
_DEFAULT_SEARCHING_GRACE = 20.0   # seconds after music stops before switching display to searching

# Extra seconds of buffer added to RECORD_DURATION before timing out the audio thread
_RECORD_TIMEOUT_BUFFER = 5.0

# Timeout (seconds) for pushing a WebP image to the Tuneshine device
_PUSH_TIMEOUT = 15.0

# Accent colours for error states
_ACCENT_MIC = (180, 50, 50)       # red   — mic / hardware
_ACCENT_NETWORK = (180, 120, 40)  # orange — Shazam / network
_ACCENT_STARTUP = (50, 90, 180)   # blue   — startup / discovery


def _push(client: TuneshineClient, webp: bytes, **meta) -> None:
    """Fire-and-forget synchronous push — errors are logged, not raised."""
    try:
        client.push_image(webp, **meta)
    except requests.RequestException as exc:
        log.warning("Failed to push image to Tuneshine: %s", exc)


async def run(
    client: TuneshineClient,
    *,
    max_misses: int,
    cycle_sleep: float,
    idle_sleep: float,
    record_duration: float,
    handoff_timeout: float,
    searching_grace: float,
) -> None:
    current_track: TrackInfo | None = None
    identified_at: float = 0.0
    colors: tuple[tuple[int, ...], ...] = ((0, 0, 0), (255, 255, 255), (180, 80, 80))
    miss_count = 0
    idle_since: float = time.time()

    # ── Startup: find the device ──────────────────────────────────────────────
    log.info("Discovering Tuneshine...")
    client.discover()
    log.info("Tuneshine ready. Listening for music...")

    # Signal that the app is alive and listening
    await asyncio.to_thread(
        _push, client,
        generate_error_image("VINYL", "STARTING", _ACCENT_STARTUP),
        idle=True, overridable=True,
    )
    await asyncio.sleep(1.5)
    await asyncio.to_thread(_push, client, generate_searching_image(), idle=True, overridable=True)

    # Track what's currently on the display so we only push on state changes
    # "searching" | "playing" | "mic_error" | "network_error" | "handed_off"
    display_state = "searching"

    while True:
        # ── Record from mic ───────────────────────────────────────────────────
        log.debug("Recording %.0fs snippet...", record_duration)
        try:
            wav_bytes = await asyncio.wait_for(
                asyncio.to_thread(record_snippet, record_duration),
                timeout=record_duration + _RECORD_TIMEOUT_BUFFER,
            )
        except (asyncio.TimeoutError, OSError) as exc:
            log.warning("Audio recording failed: %s", exc)
            if display_state != "mic_error":
                await asyncio.to_thread(
                    _push, client,
                    generate_error_image("MIC", "NO INPUT", _ACCENT_MIC),
                    idle=True, overridable=True,
                )
                display_state = "mic_error"
            await asyncio.sleep(cycle_sleep)
            continue

        # Mic recovered — reset error state so searching image is re-pushed below
        if display_state in ("mic_error", "handed_off"):
            display_state = "searching_dirty"  # force a re-push
            idle_since = time.time()

        # ── Identify ──────────────────────────────────────────────────────────
        track: TrackInfo | None = None
        try:
            track = await identify(wav_bytes)
        except RecognitionError as exc:
            log.warning("Recognition failed: %s", exc)
            if display_state != "network_error":
                await asyncio.to_thread(
                    _push, client,
                    generate_error_image("NETWORK", "SHAZAM", _ACCENT_NETWORK),
                    idle=True, overridable=True,
                )
                display_state = "network_error"

        # Network recovered — reset so searching image is re-pushed below
        if track is not None and display_state in ("network_error", "handed_off"):
            display_state = "searching_dirty"
            idle_since = time.time()

        if track:
            miss_count = 0
            if not current_track or track.title != current_track.title:
                current_track = track
                identified_at = time.time()
                log.info("Now playing: %s — %s", track.title, track.artist)

                # Enrich iTunes metadata and fetch album art colours in parallel
                tasks: list[asyncio.Task] = [asyncio.ensure_future(enrich_track(track))]
                color_task: asyncio.Task | None = None
                if track.album_art_url:
                    color_task = asyncio.ensure_future(
                        asyncio.to_thread(extract_colors, track.album_art_url)
                    )
                    tasks.append(color_task)

                results = await asyncio.gather(*tasks, return_exceptions=True)

                if color_task is not None:
                    color_result = results[-1]
                    if isinstance(color_result, tuple):
                        colors = color_result
                    elif isinstance(color_result, Exception):
                        log.debug("Color extraction raised: %s", color_result)

        else:
            if track is None and display_state not in ("network_error",):
                # No recognition error, just no song detected
                miss_count += 1
                if miss_count >= max_misses and current_track:
                    log.info("Music seems to have stopped")
                    current_track = None
                    idle_since = time.time()

        # ── Update display ────────────────────────────────────────────────────
        if current_track:
            elapsed = time.time() - identified_at
            webp = generate_image(current_track, colors, elapsed)
            is_new_track = track and track.title == current_track.title
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        client.push_image,
                        webp,
                        track_name=current_track.title,
                        artist_name=current_track.artist,
                        album_name=current_track.album,
                        animation="dissolve" if is_new_track else "none",
                    ),
                    timeout=_PUSH_TIMEOUT,
                )
            except asyncio.TimeoutError:
                log.warning("Timed out pushing image to Tuneshine")
            except requests.RequestException as exc:
                log.warning("Failed to push image to Tuneshine: %s", exc)
            display_state = "playing"
            idle_since = time.time()

        elif display_state == "handed_off":
            # Already handed off — stay silent until music is detected again
            pass

        elif time.time() - idle_since >= handoff_timeout:
            # Been idle too long — clear our image and let Tuneshine manage its own display
            log.info("Idle for %.0fs — handing display back to Tuneshine", handoff_timeout)
            try:
                client.clear()
            except requests.RequestException as exc:
                log.warning("Failed to clear display: %s", exc)
            display_state = "handed_off"

        elif display_state not in ("searching", "network_error", "mic_error"):
            # No track and no active error — show the searching indicator, but only
            # after the grace period elapses. This prevents a brief recognition miss
            # mid-song from flashing the searching state before the song is re-detected.
            if time.time() - idle_since >= searching_grace:
                await asyncio.to_thread(_push, client, generate_searching_image(), idle=True, overridable=True)
                display_state = "searching"

        # ── Sleep before next cycle ───────────────────────────────────────────
        sleep = idle_sleep if not current_track and not track else cycle_sleep
        await asyncio.sleep(sleep)


def main() -> None:
    parser = argparse.ArgumentParser(description="Vinyl Detector — Tuneshine companion")
    parser.add_argument(
        "--host",
        help="Tuneshine hostname (e.g. tuneshine-ABCD.local). Skips mDNS discovery.",
    )
    parser.add_argument(
        "--max-misses",
        type=int,
        default=_DEFAULT_MAX_MISSES,
        metavar="N",
        help=f"Consecutive recognition failures before music is marked stopped (default: {_DEFAULT_MAX_MISSES})",
    )
    parser.add_argument(
        "--cycle-sleep",
        type=float,
        default=_DEFAULT_CYCLE_SLEEP,
        metavar="SEC",
        help=f"Seconds between listen cycles while music is playing (default: {_DEFAULT_CYCLE_SLEEP})",
    )
    parser.add_argument(
        "--idle-sleep",
        type=float,
        default=_DEFAULT_IDLE_SLEEP,
        metavar="SEC",
        help=f"Seconds between listen cycles when idle (default: {_DEFAULT_IDLE_SLEEP})",
    )
    parser.add_argument(
        "--record-duration",
        type=float,
        default=_DEFAULT_RECORD_DURATION,
        metavar="SEC",
        help=f"Audio snippet length in seconds (default: {_DEFAULT_RECORD_DURATION})",
    )
    parser.add_argument(
        "--handoff-timeout",
        type=float,
        default=_DEFAULT_HANDOFF_TIMEOUT,
        metavar="SEC",
        help=f"Seconds idle before clearing display and handing back to Tuneshine (default: {_DEFAULT_HANDOFF_TIMEOUT:.0f})",
    )
    parser.add_argument(
        "--searching-grace",
        type=float,
        default=_DEFAULT_SEARCHING_GRACE,
        metavar="SEC",
        help=f"Seconds after music stops before switching display to searching (default: {_DEFAULT_SEARCHING_GRACE:.0f})",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    client = TuneshineClient(host=args.host)

    loop = asyncio.new_event_loop()

    def shutdown(sig: int, frame) -> None:
        log.info("Shutting down...")
        try:
            client.clear()
        except requests.RequestException:
            pass
        loop.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    loop.run_until_complete(run(
        client,
        max_misses=args.max_misses,
        cycle_sleep=args.cycle_sleep,
        idle_sleep=args.idle_sleep,
        record_duration=args.record_duration,
        handoff_timeout=args.handoff_timeout,
        searching_grace=args.searching_grace,
    ))


if __name__ == "__main__":
    main()
