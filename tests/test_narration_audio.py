import numpy as np

from litreel.services.video_renderer import VideoRenderer


def test_decode_audio_with_pyav_handles_invalid_data(tmp_path):
    renderer = VideoRenderer(output_dir=tmp_path)
    # Invalid bytes should trigger the PyAV error path and return None rather than crashing.
    assert renderer._decode_audio_with_pyav(b"not a real mp3 stream") is None


def test_decode_audio_falls_back_to_ffmpeg_when_pyav_fails(tmp_path, monkeypatch):
    renderer = VideoRenderer(output_dir=tmp_path)
    fake_audio = np.array([0.0, 0.5, -0.25], dtype=np.float32)

    monkeypatch.setattr(renderer, "_decode_audio_with_pyav", lambda _: None)
    monkeypatch.setattr(renderer, "_decode_audio_via_ffmpeg", lambda _: fake_audio)

    assert renderer._decode_audio(b"placeholder") is fake_audio


def test_mix_audio_returns_none_when_no_tracks(tmp_path):
    renderer = VideoRenderer(output_dir=tmp_path)
    assert (
        renderer._mix_audio(
            [None, None],
            start_times=[0.0, 0.1],
            durations=[0.1, 0.1],
        )
        is None
    )


def test_mix_audio_handles_numpy_arrays_without_truthiness_errors(tmp_path):
    renderer = VideoRenderer(output_dir=tmp_path)
    slide_audios = [
        np.array([0.25, 0.25], dtype=np.float32),
        np.array([-0.5, -0.5], dtype=np.float32),
    ]
    buffer = renderer._mix_audio(
        slide_audios,
        start_times=[0.0, 0.001],
        durations=[0.001, 0.001],
    )
    assert buffer is not None
    # First chunk should include the first slide audio contribution.
    np.testing.assert_allclose(buffer[:2], slide_audios[0], atol=1e-6)
