# Red Alert Actuator

Physical alert outputs for Israel Pikud HaOref (Homefront Command) alerts — Snapcast TTS announcements and MQTT smart light control.

## What It Does

Consumes real-time alert data from the [Oref Alert Proxy](https://github.com/danielrosehill/Oref-Alert-Proxy) and triggers physical actions in your home:

- **Smart lights via MQTT** — Color changes based on alert state (red = active threat, orange = pre-warning, green = all clear, off after timeout)
- **Snapcast TTS** — Pre-recorded voice announcements played through your whole-house audio system

This is part of the [Red Alert Stack](https://github.com/danielrosehill/Red-Alert-Stack) microservices architecture.

```
┌─────────────────────┐
│  Oref Alert Proxy   │
│  (port 8764)        │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐     ┌──────────────┐
│  This Actuator      │────▶│  Snapcast    │──▶ Speakers
│                     │     │  (FIFO pipe) │
│  Polls proxy,       │     └──────────────┘
│  triggers actions   │
│                     │     ┌──────────────┐
│                     │────▶│  MQTT Broker │──▶ Smart Lights
│                     │     │  (Mosquitto) │
└─────────────────────┘     └──────────────┘
```

## Alert Behaviors

### Local Area Monitoring

Monitors a configurable area (default: Jerusalem South) for state transitions:

| State | Lights | TTS Announcement |
|-------|--------|------------------|
| Active threat (cat 1–12) | Red | "Red alert. Active threat detected. Seek shelter immediately." |
| Pre-warning (cat 14) | Orange | "Early warning. Alerts are expected shortly..." |
| All clear (cat 13) | Green | "All clear. The event has ended..." |
| Timeout after all-clear | Off | — |

### Nationwide Thresholds

Broadcasts when the total active area count crosses major thresholds:

| Threshold | TTS Announcement |
|-----------|------------------|
| 100+ areas | "Nationwide alert. Over 100 areas under simultaneous alert..." |
| 200+ areas | "Major attack in progress. Over 200 areas..." |
| 500+ areas | "Large scale attack. Over 500 areas..." |
| 1000+ areas | "Unprecedented nationwide alert. Over 1000 areas..." |

## Setup

### 1. Generate TTS audio files

Pre-record the announcement audio using OpenAI TTS:

```bash
pip install httpx
OPENAI_API_KEY=sk-... python generate_audio.py
```

This creates WAV files in `audio/`. You only need to run this once — the files are committed to the repo and played locally from then on.

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` with your MQTT broker address and light topics:

```
MQTT_BROKER=10.0.0.4
MQTT_LIGHT_TOPICS=zigbee2mqtt/alert-light-1/set,zigbee2mqtt/alert-light-2/set
LOCAL_AREA=ירושלים - דרום
```

### 3. Run

```bash
# Docker (mounts Snapcast FIFO from host)
docker compose up -d

# Or directly
pip install .
python actuator.py
```

### Prerequisites

- [Oref Alert Proxy](https://github.com/danielrosehill/Oref-Alert-Proxy) running
- Snapcast server with pipe source at `/tmp/snapfifo` (default config)
- MQTT broker (Mosquitto) accessible on the network
- MQTT-controllable lights that accept JSON color payloads

## MQTT Light Payload Format

The actuator publishes JSON to your configured topics. The default format works with Zigbee2MQTT:

```json
{"color": {"r": 255, "g": 0, "b": 0}}
```

For turning off:

```json
{"state": "OFF"}
```

If your lights use a different format (Tasmota, Home Assistant, etc.), modify the `COLORS` dict in `actuator.py`.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OREF_PROXY_URL` | `http://localhost:8764` | Oref Alert Proxy URL |
| `POLL_INTERVAL` | `3` | Proxy poll interval (seconds) |
| `MQTT_BROKER` | `10.0.0.4` | MQTT broker address |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USERNAME` | — | MQTT auth username (optional) |
| `MQTT_PASSWORD` | — | MQTT auth password (optional) |
| `MQTT_LIGHT_TOPICS` | — | Comma-separated MQTT topics for lights |
| `LIGHT_RESTORE_AFTER` | `120` | Seconds to turn off lights after event (0 = never) |
| `SNAPCAST_FIFO` | `/tmp/snapfifo` | Path to Snapcast pipe |
| `TTS_ENABLED` | `true` | Enable/disable TTS announcements |
| `TTS_COOLDOWN` | `60` | Min seconds between same TTS message |
| `LOCAL_AREA` | `ירושלים - דרום` | Hebrew area name to monitor |

## Related Projects

- [Oref-Alert-Proxy](https://github.com/danielrosehill/Oref-Alert-Proxy) — Data source (required)
- [Red-Alert-Telegram-Bot](https://github.com/danielrosehill/Red-Alert-Telegram-Bot) — Telegram alerts
- [Red-Alert-Geodash](https://github.com/danielrosehill/Red-Alert-Geodash) — Map dashboard

## License

MIT
