# vinyl-detector

An unofficial companion app for [Tuneshine](https://tuneshine.com) that listens to your turntable via microphone, identifies what's playing using Shazam, and pushes a live now-playing display to your Tuneshine device.

> **Not affiliated with or endorsed by Tuneshine.** This is an independent community project.

---

## Features

- Automatic Tuneshine discovery via mDNS (no manual IP config needed)
- Song identification powered by [ShazamIO](https://github.com/dotX12/ShazamIO) — no API key required
- Album art color extraction for a palette-matched display
- Scrolling title animation for long track names
- Live progress bar and elapsed/total time (enriched from iTunes metadata)
- Graceful error states for mic failures and network issues
- Clean shutdown — clears the display on exit

## Requirements

- Python 3.10+
- A microphone accessible to the system
- A Tuneshine device on the same local network

## Installation

### macOS

```bash
# Install PortAudio first (required by sounddevice)
brew install portaudio

git clone https://github.com/adambarta/vinyl-detector.git
cd vinyl-detector
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Raspberry Pi

Tested on Raspberry Pi 3 B+ running Raspberry Pi OS Lite 64-bit.

```bash
git clone https://github.com/adambarta/vinyl-detector.git
cd vinyl-detector
bash scripts/install-pi.sh
```

This will:
- Install system dependencies (`libportaudio2`, etc.)
- Create a Python virtual environment and install requirements
- Register and start a `systemd` service so the app runs on boot
- Set up a cron job that polls GitHub every minute and auto-restarts the service when new changes are pushed

## Usage

```bash
# Auto-discover Tuneshine via mDNS and start listening
python vinyl_detector.py

# Skip discovery — connect directly by hostname
python vinyl_detector.py --host tuneshine-ABCD.local

# Verbose logging
python vinyl_detector.py -v
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--host HOST` | _(auto)_ | Tuneshine hostname, skips mDNS discovery |
| `--max-misses N` | `3` | Consecutive recognition failures before marking music stopped |
| `--cycle-sleep SEC` | `2.0` | Seconds between listen cycles while music is playing |
| `--idle-sleep SEC` | `8.0` | Seconds between listen cycles when idle |
| `--record-duration SEC` | `6.0` | Audio snippet length sent to Shazam |
| `--handoff-timeout SEC` | `120` | Seconds idle before clearing display and handing back to Tuneshine |
| `-v, --verbose` | off | Enable debug logging |

## How it works

1. Records a short audio snippet from the default microphone
2. Sends it to Shazam for identification
3. Fetches track metadata (duration, track number) from the iTunes catalog API
4. Extracts a color palette from the album art
5. Renders a 64×64 WebP image and pushes it to Tuneshine over HTTP

## Dependencies

| Package | Purpose |
|---|---|
| [shazamio](https://github.com/dotX12/ShazamIO) | Song recognition |
| [sounddevice](https://python-sounddevice.readthedocs.io) | Microphone capture |
| [scipy](https://scipy.org) | WAV encoding |
| [Pillow](https://python-pillow.org) | Image generation |
| [colorthief](https://github.com/fengsp/color-thief-py) | Album art color extraction |
| [zeroconf](https://github.com/python-zeroconf/python-zeroconf) | mDNS device discovery |
| [requests](https://requests.readthedocs.io) | HTTP communication |

## License

MIT
