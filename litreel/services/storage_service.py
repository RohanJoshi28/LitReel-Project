import os
import uuid

def save_user_audio(user_id: int, audio_bytes: bytes):
    folder = f"instance/audio/{user_id}"
    os.makedirs(folder, exist_ok=True)

    filename = f"{uuid.uuid4().hex}.mp3"
    path = os.path.join(folder, filename)

    with open(path, "wb") as f:
        f.write(audio_bytes)

    return f"/api/media/audio/{user_id}/{filename}"
