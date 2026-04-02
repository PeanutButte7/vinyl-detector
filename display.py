"""Generate 64x64 pixel images for the Tuneshine display."""

from __future__ import annotations

import io
import logging
import math
from pathlib import Path

import requests
from colorthief import ColorThief
from PIL import Image, ImageDraw, ImageFont

from recognize import TrackInfo

log = logging.getLogger(__name__)

DISPLAY_SIZE = 64
FONT_PATH = Path(__file__).parent / "fonts" / "pixel.ttf"
FONT_ARTIST = 8   # artist name — top, prominent
FONT_TITLE = 8    # song title — must match Silkscreen's native 8px
FONT_TINY = 8     # track number, time display — same native size

# Layout constants (y positions)
#  0- 1  top padding
#  2-10  artist name (8px font)
# 12-19  song title (7px font) — scrolls if long
# 22-29  track number "3 / 12" (7px font)
#    ...
# 50-56  time display (7px font)
# 59-61  progress bar (3px tall)

Y_ARTIST = 2
Y_TITLE = 13
Y_TRACK_NUM = 24
Y_TIME = 50
Y_PROGRESS = 61
PROGRESS_HEIGHT = 3
MARGIN = 2
TEXT_AREA_W = DISPLAY_SIZE - 2 * MARGIN

# Scroll animation (for song title)
SCROLL_FPS = 12
SCROLL_PAUSE_FRAMES = 18  # ~1.5s pause before scrolling restarts
SCROLL_PX_PER_FRAME = 1
SCROLL_GAP = 12  # pixel gap between end and restart of text

# Time ticker animation (for static titles — keeps clock live)
TIME_TICK_FRAMES = 12   # 12 frames × 1s each = 12-second loop
TIME_TICK_MS = 1000     # 1 frame per second

# ── Font cache ────────────────────────────────────────────────────────────────
# Fonts are loaded once per size and reused across every render call.
_font_cache: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if size not in _font_cache:
        try:
            _font_cache[size] = ImageFont.truetype(str(FONT_PATH), size)
        except OSError:
            _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


# ── Text measurement ──────────────────────────────────────────────────────────

def _text_width(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str) -> int:
    """Return pixel width of text rendered with font (replaces deprecated getlength)."""
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0]


# ── Colour contrast (WCAG) ────────────────────────────────────────────────────

def _wcag_luminance(c: tuple[int, ...]) -> float:
    """WCAG relative luminance (0–1) for an sRGB colour."""
    def _ch(v: int) -> float:
        s = v / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    return 0.2126 * _ch(c[0]) + 0.7152 * _ch(c[1]) + 0.0722 * _ch(c[2])


def _contrast_ratio(c1: tuple[int, ...], c2: tuple[int, ...]) -> float:
    """WCAG contrast ratio between two sRGB colours."""
    l1, l2 = _wcag_luminance(c1), _wcag_luminance(c2)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _ensure_contrast(
    bg: tuple[int, ...],
    fg: tuple[int, ...],
    min_ratio: float = 4.5,
) -> tuple[int, ...]:
    """Return fg if it meets WCAG AA contrast against bg, else return black or white."""
    if _contrast_ratio(bg, fg) >= min_ratio:
        return fg
    white_ratio = _contrast_ratio(bg, (255, 255, 255))
    black_ratio = _contrast_ratio(bg, (0, 0, 0))
    return (255, 255, 255) if white_ratio >= black_ratio else (0, 0, 0)


def extract_colors(album_art_url: str) -> tuple[tuple[int, ...], ...]:
    """Fetch album art and extract a 3-color palette: (bg, text, accent)."""
    try:
        resp = requests.get(album_art_url, timeout=10)
        resp.raise_for_status()
        ct = ColorThief(io.BytesIO(resp.content))
        palette = ct.get_palette(color_count=4, quality=5)
        # Mute the background — scale down brightness while preserving hue,
        # giving the dimmed secondary text (track number, time) enough contrast headroom.
        bg = _dim(palette[0], 0.55)
        text = _ensure_contrast(bg, palette[1])
        accent = _ensure_contrast(bg, palette[2] if len(palette) > 2 else palette[1])
        return (bg, text, accent)
    except requests.RequestException:
        log.warning("Color extraction failed (network), using defaults")
        return ((20, 20, 20), (255, 255, 255), (180, 80, 80))
    except Exception:
        log.warning("Color extraction failed, using defaults")
        return ((20, 20, 20), (255, 255, 255), (180, 80, 80))


def _fmt_time(seconds: float) -> str:
    """Format seconds as m:ss."""
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"


def _dim(color: tuple[int, ...], factor: float) -> tuple[int, ...]:
    return tuple(int(c * factor) for c in color)


def _draw_base(
    track: TrackInfo,
    colors: tuple[tuple[int, ...], ...],
    elapsed_s: float,
) -> Image.Image:
    """Draw everything except the song title (artist, track#, time, progress bar)."""
    bg, text_color, accent = colors
    img = Image.new("RGB", (DISPLAY_SIZE, DISPLAY_SIZE), bg)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"  # bitmap rendering — no anti-aliasing, crisp pixels

    font_artist = _load_font(FONT_ARTIST)
    font_tiny = _load_font(FONT_TINY)

    # --- Artist name (top, prominent) ---
    draw.text((MARGIN, Y_ARTIST), track.artist, fill=text_color, font=font_artist)

    # --- Track number "3 / 12" ---
    if track.track_number > 0:
        tn = f"{track.track_number}"
        if track.total_tracks > 0:
            tn += f" / {track.total_tracks}"
        draw.text((MARGIN, Y_TRACK_NUM), tn, fill=_dim(text_color, 0.6), font=font_tiny)

    # --- Time display ---
    # If duration is known: "1:23 / 4:56" right-aligned
    # If duration unknown but we have elapsed: "1:23" (elapsed only) right-aligned
    if track.duration_ms > 0:
        dur_s = track.duration_ms / 1000.0
        current_s = min(elapsed_s, dur_s)
        time_str = f"{_fmt_time(current_s)} / {_fmt_time(dur_s)}"
    elif elapsed_s > 0:
        time_str = _fmt_time(elapsed_s)
    else:
        time_str = None

    if time_str:
        draw.text(
            (MARGIN, Y_TIME),
            time_str,
            fill=_dim(text_color, 0.5),
            font=font_tiny,
        )

    # --- Progress bar (bold, 6px tall) — only when duration is known ---
    if track.duration_ms > 0 and elapsed_s > 0:
        progress = min(elapsed_s / (track.duration_ms / 1000.0), 1.0)
    else:
        progress = 0.0

    bar_full_w = DISPLAY_SIZE
    draw.rectangle(
        [0, Y_PROGRESS, bar_full_w - 1, Y_PROGRESS + PROGRESS_HEIGHT - 1],
        fill=_dim(accent, 0.25),
    )
    bar_w = int(bar_full_w * progress)
    if bar_w > 0:
        draw.rectangle(
            [0, Y_PROGRESS, bar_w - 1, Y_PROGRESS + PROGRESS_HEIGHT - 1],
            fill=accent,
        )

    return img


def generate_image(
    track: TrackInfo,
    colors: tuple[tuple[int, ...], ...],
    elapsed_s: float,
) -> bytes:
    """Generate a 64x64 WebP image (static or animated) for the track."""
    font_title = _load_font(FONT_TITLE)
    text_w = _text_width(font_title, track.title)

    if text_w <= TEXT_AREA_W:
        return _generate_static(track, colors, elapsed_s)
    else:
        return _generate_animated(track, colors, elapsed_s)


def _generate_static(
    track: TrackInfo,
    colors: tuple[tuple[int, ...], ...],
    elapsed_s: float,
) -> bytes:
    bg, text_color, accent = colors
    font_title = _load_font(FONT_TITLE)

    # No duration → single static frame (nothing to tick)
    if track.duration_ms <= 0:
        img = _draw_base(track, colors, elapsed_s)
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        draw.text((MARGIN, Y_TITLE), track.title, fill=text_color, font=font_title)
        buf = io.BytesIO()
        img.save(buf, format="WEBP", lossless=True)
        return buf.getvalue()

    # Animated: 12 frames × 1s each — time/progress bar advances live
    frames = []
    for i in range(TIME_TICK_FRAMES):
        img = _draw_base(track, colors, elapsed_s + i)
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        draw.text((MARGIN, Y_TITLE), track.title, fill=text_color, font=font_title)
        frames.append(img)

    buf = io.BytesIO()
    frames[0].save(
        buf, format="WEBP", save_all=True,
        append_images=frames[1:],
        duration=TIME_TICK_MS, loop=0, lossless=True,
    )
    return buf.getvalue()


# ── Status / error images ─────────────────────────────────────────────────────
# Pushed to Tuneshine so the display is never blank while the app is running.

_STATUS_BG = (10, 10, 12)           # near-black for all status screens
_SEARCH_DASH_COLOR = (48, 48, 58)   # dim blue-grey dashes


def generate_searching_image() -> bytes:
    """Subtle animated dashed bar at the bottom — shown while listening with no active track.

    The scrolling pattern gives a gentle 'alive' signal without demanding attention.
    """
    dash_len = 4   # pixels on per dash
    gap_len = 4    # pixels off per dash
    period = dash_len + gap_len  # 8px repeat — one full scroll per 8 frames

    frames: list[Image.Image] = []
    for frame_i in range(period):
        img = Image.new("RGB", (DISPLAY_SIZE, DISPLAY_SIZE), _STATUS_BG)
        draw = ImageDraw.Draw(img)
        for x in range(DISPLAY_SIZE):
            if ((x + frame_i) % period) < dash_len:
                draw.rectangle(
                    [x, Y_PROGRESS, x, Y_PROGRESS + PROGRESS_HEIGHT - 1],
                    fill=_SEARCH_DASH_COLOR,
                )
        frames.append(img)

    buf = io.BytesIO()
    frames[0].save(
        buf, format="WEBP", save_all=True,
        append_images=frames[1:],
        duration=175, loop=0,
    )
    return buf.getvalue()


def generate_error_image(
    line1: str,
    line2: str = "",
    accent: tuple[int, int, int] = (180, 50, 50),
) -> bytes:
    """Error/status display: two lines of text + solid accent bar at the bottom.

    accent colours:
      red    (180,  50,  50) — mic / hardware error
      orange (180, 120,  40) — network / API error
      blue   ( 50,  90, 180) — startup / discovery
    """
    bg = _dim(accent, 0.12)   # very dark tinted background
    text_col = (210, 210, 210)
    detail_col = _dim(text_col, 0.55)

    img = Image.new("RGB", (DISPLAY_SIZE, DISPLAY_SIZE), bg)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    font = _load_font(FONT_ARTIST)
    draw.text((MARGIN, Y_ARTIST), line1, fill=text_col, font=font)
    if line2:
        draw.text((MARGIN, Y_TITLE), line2, fill=detail_col, font=font)

    # Solid accent bar along the bottom
    draw.rectangle(
        [0, Y_PROGRESS, DISPLAY_SIZE - 1, Y_PROGRESS + PROGRESS_HEIGHT - 1],
        fill=accent,
    )

    buf = io.BytesIO()
    img.save(buf, format="WEBP", lossless=True)
    return buf.getvalue()


def _generate_animated(
    track: TrackInfo,
    colors: tuple[tuple[int, ...], ...],
    elapsed_s: float,
) -> bytes:
    bg, text_color, accent = colors
    font_title = _load_font(FONT_TITLE)
    title = track.title
    text_w = int(math.ceil(_text_width(font_title, title)))
    scroll_total = text_w + SCROLL_GAP

    frames: list[Image.Image] = []
    frame_s = 1.0 / SCROLL_FPS  # seconds per frame

    # Pause frames at start position — time advances each frame
    for pi in range(SCROLL_PAUSE_FRAMES):
        frame = _draw_base(track, colors, elapsed_s + pi * frame_s)
        draw = ImageDraw.Draw(frame)
        draw.fontmode = "1"
        draw.text((MARGIN, Y_TITLE), title, fill=text_color, font=font_title)
        frames.append(frame)

    # Scrolling frames — time continues to advance
    n_scroll = scroll_total // SCROLL_PX_PER_FRAME
    for i in range(n_scroll):
        offset = (i + 1) * SCROLL_PX_PER_FRAME
        frame_elapsed = elapsed_s + (SCROLL_PAUSE_FRAMES + i) * frame_s
        frame = _draw_base(track, colors, frame_elapsed)
        draw = ImageDraw.Draw(frame)
        draw.fontmode = "1"
        # Primary text scrolling left
        draw.text((MARGIN - offset, Y_TITLE), title, fill=text_color, font=font_title)
        # Wrap-around copy appearing from right
        draw.text((MARGIN - offset + scroll_total, Y_TITLE), title, fill=text_color, font=font_title)
        frames.append(frame)

    buf = io.BytesIO()
    frame_duration = 1000 // SCROLL_FPS  # ms per frame
    frames[0].save(
        buf,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=frame_duration,
        loop=0,
        lossless=True,
    )
    return buf.getvalue()
