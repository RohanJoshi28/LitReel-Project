from pathlib import Path
from types import MethodType, SimpleNamespace
from io import BytesIO

from PIL import Image

from litreel.services.video_renderer import VideoRenderer


def make_img_bytes(color=(255, 0, 0)) -> bytes:
    img = Image.new("RGB", (800, 1200), color)
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_video_renderer_renders_project(tmp_path, monkeypatch):
    fetcher_calls = {}

    def fake_fetcher(url: str) -> bytes:
        fetcher_calls[url] = True
        return make_img_bytes()

    renderer = VideoRenderer(
        output_dir=tmp_path,
        image_fetcher=fake_fetcher,
        duration_per_slide=0.5,
        transition_duration=0.1,
    )

    slides = [
        SimpleNamespace(
            id=1,
            order_index=0,
            text="First slide text",
            image_url="http://example.com/1.jpg",
            effect="zoom-in",
            transition="fade",
        ),
        SimpleNamespace(
            id=2,
            order_index=1,
            text="Second slide text",
            image_url=None,
            effect="pan-left",
            transition="slide",
        ),
    ]
    concept = SimpleNamespace(order_index=0, slides=slides)
    project = SimpleNamespace(id=77, concepts=[concept])

    def fake_write(_bundle, target, has_audio=False):
        # Mimic the work _write_video would trigger so fetchers run
        for slide in slides:
            renderer._build_slide_context(slide)
        Path(target).write_bytes(b"fake video")

    monkeypatch.setattr(renderer, "_write_video", fake_write)
    monkeypatch.setattr("litreel.services.video_renderer.uuid4", lambda: SimpleNamespace(hex="abc123"))
    monkeypatch.setattr(
        renderer,
        "_build_slide_audios",
        lambda slides, voice: ([None for _ in slides], False),
    )

    output = renderer.render_project(project)
    assert output.exists()
    assert output.name == "project_77_abc123.mp4"
    assert output.read_bytes() == b"fake video"
    assert "http://example.com/1.jpg" in fetcher_calls


def test_video_renderer_silences_when_audio_unavailable(tmp_path, monkeypatch):
    renderer = VideoRenderer(output_dir=tmp_path)
    slide = SimpleNamespace(
        id=123,
        order_index=0,
        text="Needs narration",
        image_url=None,
        effect="none",
        transition="fade",
    )
    concept = SimpleNamespace(order_index=0, slides=[slide])
    project = SimpleNamespace(id=11, concepts=[concept])

    def fake_write(clip, target, has_audio=False):
        Path(target).write_bytes(b"video-no-audio")

    monkeypatch.setattr(renderer, "_write_video", fake_write)
    monkeypatch.setattr("litreel.services.video_renderer.generate_tts_bytes", lambda text, voice: b"bytes")
    monkeypatch.setattr(renderer, "_decode_audio", lambda data: None)
    warnings = []
    output = renderer.render_project(project, warnings=warnings)
    assert output.exists()
    assert output.read_bytes() == b"video-no-audio"
    assert warnings and "Narration audio" in warnings[0]


def test_video_renderer_uses_slide_style(tmp_path, monkeypatch):
    renderer = VideoRenderer(output_dir=tmp_path)
    slide = SimpleNamespace(
        id=99,
        order_index=0,
        text="Styled text",
        image_url=None,
        effect="none",
        transition="fade",
        style={"text_color": "#00FF00", "outline_color": "#000000", "font_weight": "400", "underline": True},
    )
    style = renderer._extract_style(slide)
    assert style["text_color"] == "#00FF00"
    assert style["font_weight"] == "400"
    assert style["underline"] is True


def test_transitions_do_not_replay_previous_slides(tmp_path, monkeypatch):
    colors = {
        "slide1": (32, 32, 32),
        "slide2": (160, 160, 160),
        "slide3": (240, 240, 240),
    }

    def make_bytes(color):
        image = Image.new("RGB", (1080, 1920), color)
        buf = BytesIO()
        image.save(buf, format="JPEG")
        return buf.getvalue()

    def fetch(url: str) -> bytes:
        return make_bytes(colors[url])

    renderer = VideoRenderer(
        output_dir=tmp_path,
        image_fetcher=fetch,
        duration_per_slide=1.4,
        transition_duration=0.6,
        fps=12,
    )

    logged: list[int] = []

    def fake_encode_frame(self, container, stream, frame_array):
        logged.append(int(frame_array[0, 0, 0]))

    def fake_write(bundle, target, has_audio=False):
        renderer._encode_video(
            None,
            None,
            bundle["slides"],
            bundle["frame_counts"],
            bundle["transitions"],
            bundle["overlap_frames"],
        )
        Path(target).write_bytes(b"log")

    monkeypatch.setattr(renderer, "_encode_frame", MethodType(fake_encode_frame, renderer))
    monkeypatch.setattr(renderer, "_write_video", fake_write)

    slides = [
        SimpleNamespace(
            id=idx + 1,
            order_index=idx,
            text=f"Slide {idx + 1}",
            image_url=f"slide{idx + 1}",
            effect="none",
            transition="fade",
        )
        for idx in range(3)
    ]
    concept = SimpleNamespace(order_index=0, slides=slides)
    project = SimpleNamespace(id=55, concepts=[concept])

    renderer.render_project(project)

    def label(value: int) -> str:
        if value == 32:
            return "slide1"
        if value == 160:
            return "slide2"
        if value == 240:
            return "slide3"
        return "blend"

    timeline = [label(value) for value in logged]
    assert "slide1" in timeline
    assert "slide2" in timeline
    assert "slide3" in timeline

    first_slide2 = timeline.index("slide2")
    assert "slide1" not in timeline[first_slide2 + 1 :]

    first_slide3 = timeline.index("slide3")
    assert "slide1" not in timeline[first_slide3 + 1 :]
    assert "slide2" not in timeline[first_slide3 + 1 :]
