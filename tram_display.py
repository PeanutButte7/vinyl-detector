"""Render 3 minimal rows on 64x64 (Tuneshine format: 64x64 WebP).

row1: last departed (grey)   row2: next (bright, emphasized)   row3: after that
"""
from __future__ import annotations
import datetime as dt, io, math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from tram_golemio import Departure

SIZE = 64
FONT = Path(__file__).parent / "fonts" / "pixel.ttf"
BG = (8, 8, 12)
HILITE = (28, 28, 38)
BRIGHT = (255, 255, 255)
NORMAL = (175, 175, 185)
PAST = (75, 75, 85)
LATE = (255, 150, 40)
LATE_DIM = (130, 80, 30)


def _font(size=8):
    try:
        return ImageFont.truetype(str(FONT), size)
    except OSError:
        return ImageFont.load_default()


def _mins_label(d: Departure, now: dt.datetime) -> str:
    s = (d.when - now).total_seconds()
    if s < 0:
        return f"-{max(1, math.ceil(-s / 60))}m"
    m = int(s // 60)
    return "now" if m == 0 else f"{m}m"


def _row(draw, y, d, now, f, col, late_col):
    if d is None:
        draw.text((2, y), "-", fill=col, font=f)
        return
    draw.text((2, y), _mins_label(d, now), fill=col, font=f)
    clock = d.when.astimezone(now.tzinfo).strftime("%H:%M")
    delay = f"+{d.delay_min}" if d.delay_min and d.delay_min >= 1 else ""
    dw = draw.textlength(delay, font=f) if delay else 0
    cx = SIZE - 2 - dw - (2 if delay else 0) - draw.textlength(clock, font=f)
    draw.text((cx, y), clock, fill=col, font=f)
    if delay:
        draw.text((SIZE - 2 - dw, y), delay, fill=late_col, font=f)


def render(deps: list[Departure], now: dt.datetime, last: Departure | None = None) -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), BG)
    draw = ImageDraw.Draw(img); draw.fontmode = "1"
    f = _font()
    _row(draw, 6, last, now, f, PAST, LATE_DIM)
    draw.rectangle([0, 22, SIZE - 1, 41], fill=HILITE)
    _row(draw, 28, deps[0] if deps else None, now, f, BRIGHT, LATE)
    _row(draw, 50, deps[1] if len(deps) > 1 else None, now, f, NORMAL, LATE)
    return img


def to_webp(img: Image.Image) -> bytes:
    buf = io.BytesIO(); img.save(buf, format="WEBP", lossless=True); return buf.getvalue()
