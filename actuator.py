"""Red Alert Actuator — Physical alert outputs for Pikud HaOref alerts.

Consumes alert data from the Oref Alert Proxy and triggers physical actions:
  - Snapcast TTS announcements via pipe (pre-recorded audio files)
  - MQTT smart light color changes (red/orange/green/off)

Sits downstream of the Oref Alert Proxy as part of the Red Alert Stack.

Environment variables: see .env.example
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx

try:
    import paho.mqtt.client as mqtt

    HAS_MQTT = True
except ImportError:
    HAS_MQTT = False

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("redalert.actuator")

# ── Configuration ────────────────────────────────────────────────────────────

OREF_PROXY_URL = os.environ.get("OREF_PROXY_URL", "http://localhost:8764")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "3"))

MQTT_BROKER = os.environ.get("MQTT_BROKER", "10.0.0.4")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
MQTT_LIGHT_TOPICS = [
    t.strip()
    for t in os.environ.get("MQTT_LIGHT_TOPICS", "").split(",")
    if t.strip()
]
LIGHT_RESTORE_AFTER = int(os.environ.get("LIGHT_RESTORE_AFTER", "120"))

SNAPCAST_FIFO = os.environ.get("SNAPCAST_FIFO", "/tmp/snapfifo")
TTS_ENABLED = os.environ.get("TTS_ENABLED", "true").lower() in ("true", "1", "yes")
TTS_COOLDOWN = int(os.environ.get("TTS_COOLDOWN", "60"))

LOCAL_AREA = os.environ.get("LOCAL_AREA", "ירושלים - דרום")

AUDIO_DIR = Path(__file__).parent / "audio"

# Alert categories
ACTIVE_CATEGORIES = {1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 14}
RED_CATEGORIES = {1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12}
THRESHOLD_LEVELS = [1000, 500, 200, 100]  # checked high to low

# Light colors
COLORS = {
    "red": {"color": {"r": 255, "g": 0, "b": 0}},
    "orange": {"color": {"r": 255, "g": 140, "b": 0}},
    "green": {"color": {"r": 0, "g": 255, "b": 0}},
    "off": {"state": "OFF"},
}

# ── MQTT Client ──────────────────────────────────────────────────────────────


class LightController:
    def __init__(self):
        self.client: mqtt.Client | None = None
        self.current_color: str = ""

        if not HAS_MQTT:
            log.warning("paho-mqtt not installed — light control disabled")
            return
        if not MQTT_LIGHT_TOPICS:
            log.info("No MQTT_LIGHT_TOPICS configured — light control disabled")
            return

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if MQTT_USERNAME:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        try:
            self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
            self.client.loop_start()
            log.info(
                "MQTT connected to %s:%d (%d lights)",
                MQTT_BROKER,
                MQTT_PORT,
                len(MQTT_LIGHT_TOPICS),
            )
        except Exception as e:
            log.error("MQTT connection failed: %s", e)
            self.client = None

    def set_color(self, color: str):
        """Set all lights to a color. color is one of: red, orange, green, off."""
        if not self.client or color == self.current_color:
            return

        payload = json.dumps(COLORS.get(color, COLORS["off"]))
        for topic in MQTT_LIGHT_TOPICS:
            self.client.publish(topic, payload)

        log.info("Lights → %s (%d lights)", color, len(MQTT_LIGHT_TOPICS))
        self.current_color = color

    def close(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()


# ── Snapcast TTS ─────────────────────────────────────────────────────────────


class TTSPlayer:
    def __init__(self):
        self.last_played: dict[str, float] = {}
        self.fifo_path = SNAPCAST_FIFO

        if not TTS_ENABLED:
            log.info("TTS disabled")
            return

        if not Path(self.fifo_path).exists():
            log.warning("Snapcast FIFO not found at %s — TTS will fail", self.fifo_path)

        available = [f.stem for f in AUDIO_DIR.glob("*.wav")]
        log.info("TTS audio files available: %s", ", ".join(available) or "none")

    def play(self, name: str):
        """Play a pre-recorded TTS message by name (e.g., 'red_alert')."""
        if not TTS_ENABLED:
            return

        # Cooldown check
        now = time.time()
        last = self.last_played.get(name, 0)
        if now - last < TTS_COOLDOWN:
            return

        audio_file = AUDIO_DIR / f"{name}.wav"
        if not audio_file.exists():
            log.warning("Audio file not found: %s", audio_file)
            return

        try:
            audio_data = audio_file.read_bytes()
            # Skip WAV header (44 bytes) — Snapcast expects raw PCM
            pcm_data = audio_data[44:]

            with open(self.fifo_path, "wb") as fifo:
                fifo.write(pcm_data)

            self.last_played[name] = now
            log.info("TTS played: %s", name)
        except Exception as e:
            log.error("TTS play error (%s): %s", name, e)


# ── Alert Monitor ────────────────────────────────────────────────────────────


class AlertMonitor:
    def __init__(
        self, http_client: httpx.AsyncClient, lights: LightController, tts: TTSPlayer
    ):
        self.http_client = http_client
        self.lights = lights
        self.tts = tts

        # State tracking
        self.prev_local_state: str = ""  # "", "warning", "active", "clear"
        self.prev_threshold: int = 0
        self.prev_alert_ids: set[str] = set()
        self.last_active_time: float = 0
        self.all_clear_sent: bool = False

    async def poll(self):
        """Fetch alerts from proxy and trigger actions on state changes."""
        try:
            resp = await self.http_client.get(
                f"{OREF_PROXY_URL}/api/alerts", timeout=10
            )
            data = resp.json()
            alerts = data.get("alerts", [])
        except Exception as e:
            log.error("Proxy poll error: %s", e)
            return

        # Normalize category field
        for a in alerts:
            if "cat" in a and "category" not in a:
                a["category"] = a["cat"]

        # Detect changes
        current_ids = {f"{a.get('data', '')}:{a.get('category', 0)}" for a in alerts}
        if current_ids == self.prev_alert_ids:
            # No change — but check if we need to restore lights after all-clear
            self._check_light_restore()
            return
        self.prev_alert_ids = current_ids

        # Classify
        active = [a for a in alerts if a.get("category", 0) in ACTIVE_CATEGORIES]
        active_areas = {a.get("data", "") for a in active}
        active_count = len(active_areas)

        red_alerts = [a for a in alerts if a.get("category", 0) in RED_CATEGORIES]
        warnings = [a for a in alerts if a.get("category", 0) == 14]
        all_clears = [a for a in alerts if a.get("category", 0) == 13]

        # ── Local area state ─────────────────────────────────────────────

        local_state = ""
        for a in alerts:
            if a.get("data", "") == LOCAL_AREA:
                cat = a.get("category", 0)
                if cat in RED_CATEGORIES:
                    local_state = "active"
                    break
                elif cat == 14:
                    local_state = "warning"
                elif cat == 13:
                    local_state = "clear"

        if local_state != self.prev_local_state:
            if local_state == "active":
                self.lights.set_color("red")
                self.tts.play("red_alert")
                self.last_active_time = time.time()
                self.all_clear_sent = False
            elif local_state == "warning":
                self.lights.set_color("orange")
                self.tts.play("early_warning")
                self.last_active_time = time.time()
                self.all_clear_sent = False
            elif local_state == "clear" and self.prev_local_state in (
                "active",
                "warning",
            ):
                self.lights.set_color("green")
                self.tts.play("all_clear")
                self.all_clear_sent = True
            elif local_state == "" and self.prev_local_state:
                # Area dropped from alerts entirely
                if not self.all_clear_sent and self.prev_local_state in (
                    "active",
                    "warning",
                ):
                    self.lights.set_color("green")
                    self.tts.play("all_clear")
                    self.all_clear_sent = True

            self.prev_local_state = local_state

        # ── Nationwide thresholds ────────────────────────────────────────

        current_threshold = 0
        for t in THRESHOLD_LEVELS:
            if active_count >= t:
                current_threshold = t
                break

        if current_threshold > self.prev_threshold:
            audio_name = f"threshold_{current_threshold}"
            self.tts.play(audio_name)
            # If no local alert is active, flash red for nationwide threshold
            if not local_state or local_state == "clear":
                self.lights.set_color("red")
                self.last_active_time = time.time()
                self.all_clear_sent = False

        self.prev_threshold = current_threshold

    def _check_light_restore(self):
        """Turn off lights after LIGHT_RESTORE_AFTER seconds of no activity."""
        if LIGHT_RESTORE_AFTER <= 0:
            return
        if not self.last_active_time:
            return
        if self.lights.current_color == "off" or self.lights.current_color == "":
            return

        elapsed = time.time() - self.last_active_time
        if elapsed > LIGHT_RESTORE_AFTER:
            self.lights.set_color("off")
            self.last_active_time = 0


# ── Main ─────────────────────────────────────────────────────────────────────


async def main():
    lights = LightController()
    tts = TTSPlayer()

    log.info("Red Alert Actuator starting...")
    log.info("Proxy: %s", OREF_PROXY_URL)
    log.info("Local area: %s", LOCAL_AREA)
    log.info("MQTT lights: %d topics", len(MQTT_LIGHT_TOPICS))
    log.info("TTS: %s (cooldown: %ds)", "enabled" if TTS_ENABLED else "disabled", TTS_COOLDOWN)

    async with httpx.AsyncClient() as http_client:
        monitor = AlertMonitor(http_client, lights, tts)

        try:
            while True:
                await monitor.poll()
                await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            pass
        finally:
            lights.close()


if __name__ == "__main__":
    asyncio.run(main())
