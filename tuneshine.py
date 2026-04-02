"""Tuneshine LED display — mDNS discovery and HTTP API communication."""

from __future__ import annotations

import json
import logging
import socket
import time

import requests
from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf

log = logging.getLogger(__name__)

MDNS_TYPE = "_tuneshine._tcp.local."
DISCOVER_TIMEOUT = 10  # seconds

# Retry settings for HTTP calls
_MAX_ATTEMPTS = 3
_BASE_DELAY = 1.0  # seconds; doubled each retry (1s, 2s)


def _retry(fn, *args, **kwargs):
    """Call fn(*args, **kwargs) up to _MAX_ATTEMPTS times with exponential backoff."""
    last_exc: requests.RequestException | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < _MAX_ATTEMPTS - 1:
                delay = _BASE_DELAY * (2 ** attempt)
                log.debug("HTTP retry %d/%d after %.1fs: %s", attempt + 1, _MAX_ATTEMPTS, delay, exc)
                time.sleep(delay)
    raise last_exc  # type: ignore[misc]


class TuneshineClient:
    def __init__(self, host: str | None = None):
        """If host is given (e.g. 'tuneshine-ABCD.local'), skip mDNS discovery."""
        self._base_url: str | None = None
        if host:
            self._base_url = f"http://{host}"

    def discover(self) -> str:
        """Discover the Tuneshine via mDNS. Returns the base URL."""
        if self._base_url:
            return self._base_url

        found: list[str] = []

        def on_change(zc: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
            if state_change is ServiceStateChange.Added:
                info = zc.get_service_info(service_type, name)
                if info and info.addresses:
                    ip = socket.inet_ntoa(info.addresses[0])
                    port = info.port
                    found.append(f"http://{ip}:{port}")
                    log.info("Found Tuneshine at %s:%d", ip, port)

        zc = Zeroconf()
        browser = ServiceBrowser(zc, MDNS_TYPE, handlers=[on_change])

        deadline = time.time() + DISCOVER_TIMEOUT
        while not found and time.time() < deadline:
            time.sleep(0.2)

        browser.cancel()
        zc.close()

        if not found:
            raise RuntimeError(
                f"No Tuneshine found via mDNS within {DISCOVER_TIMEOUT}s. "
                "Pass --host tuneshine-XXXX.local to skip discovery."
            )

        self._base_url = found[0]
        return self._base_url

    def get_state(self) -> dict:
        """GET /state — returns the current Tuneshine state."""
        base = self.discover()

        def _call() -> dict:
            resp = requests.get(f"{base}/state", timeout=5)
            resp.raise_for_status()
            return resp.json()

        return _retry(_call)

    def push_image(
        self,
        webp_bytes: bytes,
        track_name: str | None = None,
        artist_name: str | None = None,
        album_name: str | None = None,
        animation: str = "dissolve",
        idle: bool = False,
        overridable: bool = False,
    ) -> None:
        """Upload a 64x64 WebP image to the Tuneshine via multipart/form-data.

        Pass idle=True, overridable=True for searching/error states so that
        remote sources (e.g. Spotify) can take over without needing a DELETE first.
        Retries up to 3 times with exponential backoff on network errors.
        """
        base = self.discover()
        url = f"{base}/image"

        metadata: dict = {}
        if track_name:
            metadata["trackName"] = track_name
        if artist_name:
            metadata["artistName"] = artist_name
        if album_name:
            metadata["albumName"] = album_name
        metadata["serviceName"] = "Vinyl Detector"
        metadata["animation"] = animation
        if idle:
            metadata["idle"] = True
        if overridable:
            metadata["overridable"] = True

        parts: dict = {
            "image": ("display.webp", webp_bytes, "image/webp"),
        }
        if metadata:
            parts["metadata"] = (None, json.dumps(metadata), "application/json")

        def _call() -> None:
            resp = requests.post(url, files=parts, timeout=10)
            resp.raise_for_status()
            log.debug("Image pushed (%d bytes) → %s", len(webp_bytes), resp.json())

        _retry(_call)

    def clear(self) -> None:
        """DELETE /image — remove custom image, revert to idle."""
        base = self.discover()

        def _call() -> None:
            resp = requests.delete(f"{base}/image", timeout=5)
            resp.raise_for_status()

        try:
            _retry(_call)
        except requests.RequestException as exc:
            log.warning("Failed to clear display: %s", exc)
