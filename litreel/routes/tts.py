from flask import Blueprint, request, jsonify, send_file
from io import BytesIO
from litreel.services.tts_service import generate_tts_bytes

tts_bp = Blueprint("tts", __name__)

@tts_bp.post("/tts")
def tts_generate():
    data = request.get_json() or {}
    text = (data.get("text") or "").strip()
    voice = (data.get("voice") or "").strip()

    if not text:
        return jsonify({"error": "No text provided"}), 400

    try:
        chosen_voice = voice if voice else "sarah"
        audio_bytes = generate_tts_bytes(text, chosen_voice)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    buffer = BytesIO(audio_bytes)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="audio/mpeg",
        as_attachment=False,
        download_name="tts.mp3"
    )
