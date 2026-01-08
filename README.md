# Popebot

A MicroPython bot that posts Alexander Pope's poetry couplets to Bluesky, one at a time, from a Raspberry Pi Pico 2 W.

While this is set up for Pope, it works for any text - swap out `couplets.txt` with your own content (entries separated by `---`) and you've got a bot for any poet, quote collection, or text you like.

## Requirements

- Raspberry Pi Pico 2 W (must be the W variant for WiFi)
- MicroPython firmware
- A Bluesky account with an [App Password](https://bsky.app/settings/app-passwords)

## Setup

### 1. Install MicroPython

Flash MicroPython to your Pico 2 W. Download the firmware from [micropython.org](https://micropython.org/download/RPI_PICO2_W/).

### 2. Install urequests

The easiest way is via Thonny: **Tools > Manage packages**, search for `urequests`, and install it.

Or via the REPL:

```python
import mip
mip.install("urequests")
```

### 3. Configure

Copy `config.example.json` to `config.json` and add your credentials:

```json
{
    "wifi_ssid": "YourWiFiName",
    "wifi_password": "YourWiFiPassword",
    "bluesky_handle": "yourhandle.bsky.social",
    "bluesky_password": "xxxx-xxxx-xxxx-xxxx",
    "interval_minutes": 60
}
```

Use an App Password from Bluesky settings, not your main password.

### 4. Copy files to Pico

Copy these files to the Pico's filesystem:

- `main.py`
- `popebot.py`
- `config.json`
- `couplets.txt`

### 5. Power on

The bot will:
1. Wait 5 seconds (time to interrupt if needed)
2. Connect to WiFi
3. Sync time via NTP
4. Authenticate with Bluesky
5. Post one couplet per hour until complete

The LED stays on when connected. It blinks 2x after a successful post, 5x on error.

## Testing on Desktop

Test without a Pico using the included test harness:

```bash
# Test couplet reading (no network)
/usr/bin/python3 test_popebot.py read

# Test Bluesky authentication
/usr/bin/python3 test_popebot.py auth

# Dry run - show what would post
/usr/bin/python3 test_popebot.py dry

# Actually post one couplet
/usr/bin/python3 test_popebot.py post

# Reset to first couplet
/usr/bin/python3 test_popebot.py reset
```

## Files

| File | Description |
|------|-------------|
| `popebot.py` | Main bot code |
| `main.py` | Auto-start on power-up |
| `config.example.json` | Template for credentials |
| `config.json` | Your WiFi and Bluesky credentials (create from template) |
| `couplets.txt` | 6,616 couplets from Pope's works |
| `state.json` | Tracks position (created automatically) |
| `test_popebot.py` | Desktop test harness |

## How It Works

- Couplets are stored in `couplets.txt`, separated by `---`
- Position is tracked in `state.json` as a byte offset
- Only one couplet is loaded into memory at a time
- Session tokens are refreshed automatically when they expire
- Progress survives power loss

## Resetting

To start over from the first couplet, delete `state.json` from the Pico.
