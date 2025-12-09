import os
import requests

LEMONFOX_API_KEY = os.getenv("LEMONFOX_API_KEY")

TTS_URL = "https://api.lemonfox.ai/v1/audio/speech"


def generate_tts_bytes(text: str, voice: str = "sarah") -> bytes:
    """
    Returns raw MP3 bytes for narration.
    Used both by /api/tts preview AND by the video renderer.
    """
    headers = {
        "Authorization": f"Bearer {LEMONFOX_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "input": text,
        "voice": voice,
        "response_format": "mp3"
    }

    resp = requests.post(TTS_URL, json=payload, headers=headers)

    if resp.status_code != 200:
        raise Exception(f"Lemonfox API error: {resp.text}")

    return resp.content

# generate_tts_bytes("hi")