"""Generate pre-recorded TTS audio files for Red Alert announcements.

Uses OpenAI TTS API to create WAV files for each alert state.
Run once to generate the audio, then the actuator plays them locally.

Usage:
  OPENAI_API_KEY=sk-... python generate_audio.py
"""

import os
from pathlib import Path

import httpx

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
AUDIO_DIR = Path(__file__).parent / "audio"
OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"

MESSAGES = {
    "red_alert": (
        "Red alert. Active threat detected. Seek shelter immediately."
    ),
    "early_warning": (
        "Early warning. Alerts are expected shortly in your area. "
        "Move to a protected space and stay nearby."
    ),
    "all_clear": (
        "All clear. The event has ended. You may leave the protected space."
    ),
    "threshold_100": (
        "Nationwide alert. Over 100 areas are under simultaneous active alert across Israel."
    ),
    "threshold_200": (
        "Major attack in progress. Over 200 areas are under simultaneous active alert across Israel."
    ),
    "threshold_500": (
        "Large scale attack. Over 500 areas are under simultaneous active alert across Israel."
    ),
    "threshold_1000": (
        "Unprecedented nationwide alert. Over 1000 areas are under simultaneous active alert."
    ),
}


def generate():
    if not OPENAI_API_KEY:
        print("Error: OPENAI_API_KEY not set")
        return

    AUDIO_DIR.mkdir(exist_ok=True)

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    for name, text in MESSAGES.items():
        out_path = AUDIO_DIR / f"{name}.wav"
        if out_path.exists():
            print(f"  Skipping {name} (already exists)")
            continue

        print(f"  Generating {name}...")
        resp = httpx.post(
            OPENAI_TTS_URL,
            headers=headers,
            json={
                "model": "tts-1",
                "input": text,
                "voice": "onyx",
                "response_format": "wav",
            },
            timeout=30,
        )

        if resp.status_code == 200:
            out_path.write_bytes(resp.content)
            print(f"    Saved {out_path} ({len(resp.content)} bytes)")
        else:
            print(f"    Error: {resp.status_code} {resp.text[:200]}")

    print("Done.")


if __name__ == "__main__":
    generate()
