from __future__ import annotations

import io
import logging
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator
from uuid import uuid4

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from av import AVError as PyAVError  # Older PyAV releases
except ImportError:  # PyAV >= 15 moves errors under av.error
    try:
        from av.error import FFmpegError as PyAVError
    except Exception:  # pragma: no cover - make sure we always have a fallback type
        PyAVError = Exception

from ..services.tts_service import generate_tts_bytes

LOGGER = logging.getLogger(__name__)

DEFAULT_SIZE = (1080, 1920)
PREVIEW_REF_WIDTH = 360
PREVIEW_FONT_RATIO = (1.2 * 16) / PREVIEW_REF_WIDTH
PREVIEW_PADDING_PX = 24
FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


@dataclass
class SlideRenderContext:
    image: Image.Image
    text_overlay: Image.Image
    effect: str
    transition: str


SILENT_AUDIO_WARNING = (
    "Narration audio could not be generated in this environment; rendering without narration."
)


class VideoRenderer:
    def __init__(
        self,
        output_dir: Path | str,
        image_fetcher=None,
        video_size: tuple[int, int] = DEFAULT_SIZE,
        duration_per_slide: float = 3.5,
        transition_duration: float = 0.5,
        fps: int = 24,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.video_size = video_size
        self.duration = duration_per_slide
        self.transition_duration = transition_duration
        self.fps = fps
        self.audio_sample_rate = 44100
        self.image_fetcher = image_fetcher or self._download_image

    def render_project(
        self,
        project,
        concept_id: int | None = None,
        voice: str | None = "sarah",
        *,
        warnings: list[str] | None = None,
    ) -> Path:
        warning_bucket: list[str] = warnings if warnings is not None else []
        slides = self._flatten_slides(project, concept_id=concept_id)
        if not slides:
            raise RuntimeError("Project does not contain any slides to render.")

        transitions = [
            (getattr(slide, "transition", "fade") or "fade").lower() for slide in slides
        ]
        slide_audios, audio_warn = self._build_slide_audios(slides, voice)
        if audio_warn:
            if SILENT_AUDIO_WARNING not in warning_bucket:
                warning_bucket.append(SILENT_AUDIO_WARNING)
            LOGGER.warning(SILENT_AUDIO_WARNING)
        overlaps = self._compute_overlaps(transitions)
        durations = self._compute_slide_durations_from_audio(slide_audios, overlaps)
        start_times = self._compute_start_times(durations, overlaps)
        frame_counts = [max(1, int(round(duration * self.fps))) for duration in durations]
        overlap_frames = [int(round(overlap * self.fps)) for overlap in overlaps]

        output_path = self.output_dir / f"project_{project.id}_{uuid4().hex}.mp4"
        render_bundle = {
            "slides": slides,
            "frame_counts": frame_counts,
            "transitions": transitions,
            "overlap_frames": overlap_frames,
            "slide_audios": slide_audios,
            "start_times": start_times,
            "durations": durations,
        }
        has_audio = any(audio is not None for audio in slide_audios)
        self._write_video(render_bundle, output_path, has_audio=has_audio)
        return output_path

    # ------------------------------------------------------------------
    # Video assembly
    # ------------------------------------------------------------------
    def _build_slide_context(self, slide) -> SlideRenderContext:
        base_image = self._prepare_frame(slide)
        text_overlay = self._render_text_overlay(slide)
        transition = (getattr(slide, "transition", "fade") or "fade").lower()
        effect = (getattr(slide, "effect", "none") or "none").lower()
        return SlideRenderContext(image=base_image, text_overlay=text_overlay, effect=effect, transition=transition)

    def _encode_video(
        self,
        container: av.container.OutputContainer,
        stream,
        slides,
        frame_counts: list[int],
        transitions: list[str],
        overlap_frames: list[int],
    ) -> None:
        prev_tail: tuple[SlideRenderContext, list[float]] | None = None
        total_slides = len(slides)
        for idx, slide in enumerate(slides):
            ctx = self._build_slide_context(slide)
            overlap_prev = overlap_frames[idx - 1] if idx > 0 else 0
            overlap_next = overlap_frames[idx] if idx < total_slides - 1 else 0
            frame_count = max(1, frame_counts[idx])
            progress_values = (
                [0.0]
                if frame_count <= 1
                else [frame_idx / (frame_count - 1) for frame_idx in range(frame_count)]
            )
            head_count = min(overlap_prev, len(progress_values))
            remaining_after_head = max(len(progress_values) - head_count, 0)
            tail_count = min(overlap_next, remaining_after_head)
            body_start = head_count
            body_end = len(progress_values) - tail_count
            head_progress = progress_values[:head_count]
            body_progress = progress_values[body_start:body_end]
            tail_progress = progress_values[body_end:]

            if overlap_prev and prev_tail:
                prev_ctx, prev_progress = prev_tail
                blended = self._encode_overlap(
                    container,
                    stream,
                    prev_ctx,
                    prev_progress,
                    ctx,
                    head_progress,
                    transitions[idx - 1],
                )
                if blended > 0:
                    prev_progress.clear()
                    head_progress = head_progress[blended:]
                else:
                    for progress in prev_progress:
                        frame_array = self._render_slide_frame(prev_ctx, progress)
                        self._encode_frame(container, stream, frame_array)
                    prev_progress.clear()
            elif prev_tail:
                prev_ctx, prev_progress = prev_tail
                for progress in prev_progress:
                    frame_array = self._render_slide_frame(prev_ctx, progress)
                    self._encode_frame(container, stream, frame_array)
                prev_progress.clear()

            for progress in head_progress:
                frame_array = self._render_slide_frame(ctx, progress)
                self._encode_frame(container, stream, frame_array)

            for progress in body_progress:
                frame_array = self._render_slide_frame(ctx, progress)
                self._encode_frame(container, stream, frame_array)

            prev_tail = (ctx, tail_progress)

        if prev_tail:
            prev_ctx, prev_progress = prev_tail
            for progress in prev_progress:
                frame_array = self._render_slide_frame(prev_ctx, progress)
                self._encode_frame(container, stream, frame_array)

    def _encode_overlap(
        self,
        container: av.container.OutputContainer,
        stream,
        prev_ctx: SlideRenderContext,
        prev_progress: list[float],
        current_ctx: SlideRenderContext,
        head_progress: list[float],
        transition: str,
    ) -> int:
        target = max(len(prev_progress), len(head_progress))
        if target == 0:
            return 0
        prev_samples = self._resample_progress(prev_progress, target)
        head_samples = self._resample_progress(head_progress, target)
        for idx in range(target):
            if target == 1:
                alpha = 0.5
            else:
                alpha = (idx + 1) / (target + 1)
            prev_frame = self._render_slide_frame(prev_ctx, prev_samples[idx])
            head_frame = self._render_slide_frame(current_ctx, head_samples[idx])
            blended = self._blend_frames(prev_frame, head_frame, transition, alpha)
            self._encode_frame(container, stream, blended)
        return target

    def _resample_progress(self, samples: list[float], target: int) -> list[float]:
        if target <= 0:
            return []
        if not samples:
            return [0.0 for _ in range(target)]
        if target == 1:
            return [samples[0]]
        if len(samples) == 1:
            return [samples[0] for _ in range(target)]
        if len(samples) == target:
            return list(samples)
        span = len(samples) - 1
        denom = max(target - 1, 1)
        resampled: list[float] = []
        for idx in range(target):
            pos = (idx * span) / denom
            low = int(math.floor(pos))
            high = min(len(samples) - 1, int(math.ceil(pos)))
            if low == high:
                resampled.append(samples[low])
                continue
            ratio = pos - low
            value = samples[low] * (1 - ratio) + samples[high] * ratio
            resampled.append(value)
        return resampled

    def _encode_frame(self, container, stream, frame_array: np.ndarray) -> None:
        frame = av.VideoFrame.from_ndarray(frame_array, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)

    def _render_slide_frame(self, ctx: SlideRenderContext, progress: float) -> np.ndarray:
        image = self._apply_effect(ctx.image, ctx.effect, progress)
        base = Image.new("RGB", self.video_size, (8, 10, 18))
        base.paste(image, (0, 0))
        composed = Image.alpha_composite(base.convert("RGBA"), ctx.text_overlay)
        return np.array(composed.convert("RGB"))

    def _apply_effect(self, image: Image.Image, effect: str, progress: float) -> Image.Image:
        effect = effect or "none"
        w, h = self.video_size
        if effect == "zoom-in":
            scale = 1.0 + 0.05 * progress
            return self._scale_and_crop(image, scale)
        if effect == "zoom-out":
            scale = 1.05 - 0.05 * progress
            return self._scale_and_crop(image, scale)
        if effect == "pan-left":
            enlarged = self._scale_and_crop(image, 1.1, raw=True)
            shift = int(60 * progress)
            return enlarged.crop((shift, 0, shift + w, h))
        if effect == "pan-right":
            enlarged = self._scale_and_crop(image, 1.1, raw=True)
            shift = int(60 * progress)
            start = max(0, enlarged.width - w - shift)
            return enlarged.crop((start, 0, start + w, h))
        if effect == "slide":
            enlarged = self._scale_and_crop(image, 1.05, raw=True)
            offset = int((1 - progress) * 40)
            start = max(0, offset)
            return enlarged.crop((start, 0, start + w, h))
        return image

    def _scale_and_crop(self, image: Image.Image, scale: float, raw: bool = False) -> Image.Image:
        w, h = self.video_size
        scaled_w = max(w, int(round(image.width * scale)))
        scaled_h = max(h, int(round(image.height * scale)))
        resized = image.resize((scaled_w, scaled_h), Image.LANCZOS)
        if raw:
            return resized
        left = max(0, (scaled_w - w) // 2)
        top = max(0, (scaled_h - h) // 2)
        return resized.crop((left, top, left + w, top + h))

    def _blend_frames(self, prev_frame: np.ndarray, current_frame: np.ndarray, transition: str, alpha: float) -> np.ndarray:
        transition = (transition or "fade").lower()
        if transition == "slide":
            return self._slide_transition(prev_frame, current_frame, alpha)
        if transition == "scale":
            current_frame = self._scale_transition(current_frame, alpha)
        prev = prev_frame.astype(np.float32)
        curr = current_frame.astype(np.float32)
        blended = prev * (1 - alpha) + curr * alpha
        return np.clip(blended, 0, 255).astype(np.uint8)

    def _slide_transition(self, prev_frame: np.ndarray, current_frame: np.ndarray, alpha: float) -> np.ndarray:
        w, h = self.video_size
        prev_img = Image.fromarray(prev_frame)
        curr_img = Image.fromarray(current_frame)
        fade_prev = prev_img.copy()
        fade_prev = Image.blend(fade_prev, Image.new("RGB", (w, h), (0, 0, 0)), alpha * 0.5)
        canvas = fade_prev.copy()
        offset = int((1 - alpha) * w)
        temp = Image.new("RGB", (w, h), (0, 0, 0))
        temp.paste(curr_img, (offset - w, 0))
        canvas = Image.composite(temp, canvas, Image.new("L", (w, h), int(alpha * 255)))
        return np.array(canvas)

    def _scale_transition(self, current_frame: np.ndarray, alpha: float) -> np.ndarray:
        w, h = self.video_size
        factor = 1.05 - 0.05 * alpha
        img = Image.fromarray(current_frame)
        scaled = img.resize((max(1, int(w * factor)), max(1, int(h * factor))), Image.LANCZOS)
        left = max(0, (scaled.width - w) // 2)
        top = max(0, (scaled.height - h) // 2)
        return np.array(scaled.crop((left, top, left + w, top + h)))

    def _write_video(self, render_bundle: dict, target_path: Path | str, has_audio: bool = False) -> None:
        slides = render_bundle["slides"]
        frame_counts = render_bundle["frame_counts"]
        transitions = render_bundle["transitions"]
        overlap_frames = render_bundle["overlap_frames"]
        slide_audios = render_bundle["slide_audios"]
        start_times = render_bundle["start_times"]
        durations = render_bundle["durations"]

        container = av.open(str(target_path), "w")

        video_stream = container.add_stream("libx264", rate=self.fps)
        video_stream.width = self.video_size[0]
        video_stream.height = self.video_size[1]
        video_stream.pix_fmt = "yuv420p"
        video_stream.options = {"preset": "ultrafast", "profile": "main"}

        audio_stream = None
        if has_audio:
            audio_stream = container.add_stream("aac", rate=self.audio_sample_rate)
            audio_stream.layout = "mono"
            audio_stream.codec_context.bit_rate = 128_000

        self._encode_video(
            container,
            video_stream,
            slides,
            frame_counts,
            transitions,
            overlap_frames,
        )

        if audio_stream:
            audio_buffer = self._mix_audio(
                slide_audios,
                start_times,
                durations,
            )
            self._encode_audio(container, audio_stream, audio_buffer)

        for packet in video_stream.encode():
            container.mux(packet)
        if audio_stream:
            for packet in audio_stream.encode():
                container.mux(packet)

        container.close()

    # ------------------------------------------------------------------
    # Audio helpers
    # ------------------------------------------------------------------
    def _build_slide_audios(self, slides, voice: str | None) -> tuple[list[np.ndarray | None], bool]:
        if not slides:
            return [], False

        results: list[np.ndarray | None] = []
        cleaned_voice = (voice or "").strip().lower()
        voice_requested = bool(cleaned_voice) and cleaned_voice != "none"
        decoder_failed = False
        for slide in slides:
            text = (slide.text or "").strip()
            if not voice_requested or not text:
                results.append(None)
                continue
            try:
                audio_bytes = generate_tts_bytes(text, cleaned_voice)
                decoded = self._decode_audio(audio_bytes)
            except Exception as exc:
                LOGGER.warning(
                    "Narration synthesis failed for slide %s: %s", getattr(slide, "id", "unknown"), exc
                )
                decoded = None
                decoder_failed = True
            if decoded is None or not len(decoded):
                decoder_failed = True
                results.append(None)
                continue
            pad = int(self.audio_sample_rate * 0.05)
            pad_arr = np.zeros(pad, dtype=np.float32)
            paced = np.concatenate([pad_arr, decoded, pad_arr])
            results.append(paced)
        return results, bool(decoder_failed and voice_requested)

    def _decode_audio(self, audio_bytes: bytes) -> np.ndarray | None:
        decoded = self._decode_audio_with_pyav(audio_bytes)
        if decoded is not None and len(decoded):
            return decoded
        fallback = self._decode_audio_via_ffmpeg(audio_bytes)
        if fallback is not None and len(fallback):
            return fallback
        return None

    def _decode_audio_with_pyav(self, audio_bytes: bytes) -> np.ndarray | None:
        buffer = io.BytesIO(audio_bytes)
        try:
            container = av.open(buffer, format="mp3")
        except PyAVError:
            return None
        try:
            resampler = av.audio.resampler.AudioResampler(
                format="flt",
                layout="mono",
                rate=self.audio_sample_rate,
            )
            samples: list[np.ndarray] = []
            for frame in container.decode(audio=0):
                frame.pts = None
                resampled = resampler.resample(frame)
                if not resampled:
                    continue
                chunks = resampled if isinstance(resampled, (list, tuple)) else [resampled]
                for chunk in chunks:
                    if chunk is None:
                        continue
                    try:
                        arr = chunk.to_ndarray()
                    except PyAVError:
                        continue
                    if arr.ndim > 1:
                        arr = arr.mean(axis=0)
                    samples.append(arr.astype(np.float32))
            if not samples:
                return None
            return np.concatenate(samples)
        except PyAVError:
            return None
        finally:
            try:
                container.close()
            except Exception:
                pass

    def _decode_audio_via_ffmpeg(self, audio_bytes: bytes) -> np.ndarray | None:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(self.audio_sample_rate),
            "pipe:1",
        ]
        try:
            result = subprocess.run(cmd, input=audio_bytes, capture_output=True, check=True)
        except FileNotFoundError:
            LOGGER.warning("ffmpeg binary missing; cannot decode narration audio fallback.")
            return None
        except subprocess.CalledProcessError as exc:
            LOGGER.warning("ffmpeg decode failed: %s", exc)
            return None
        data = result.stdout
        if not data:
            return None
        pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        if not len(pcm):
            return None
        pcm /= 32768.0
        return pcm

    def _mix_audio(
        self,
        slide_audios: list[np.ndarray | None],
        start_times: list[float],
        durations: list[float],
    ) -> np.ndarray | None:
        if not slide_audios or not any((audio is not None and len(audio) > 0) for audio in slide_audios):
            return None
        total_duration = start_times[-1] + durations[-1]
        audio_lengths = [
            (start_times[idx] + (len(audio) / self.audio_sample_rate))
            for idx, audio in enumerate(slide_audios)
            if audio is not None
        ]
        if audio_lengths:
            total_duration = max(total_duration, max(audio_lengths) + 0.3)
        total_samples = int(math.ceil(total_duration * self.audio_sample_rate))
        buffer = np.zeros(total_samples, dtype=np.float32)
        for idx, samples in enumerate(slide_audios):
            if samples is None or not len(samples):
                continue
            start_sample = int(round(start_times[idx] * self.audio_sample_rate))
            end_sample = min(total_samples, start_sample + len(samples))
            segment = samples[: end_sample - start_sample]
            buffer[start_sample:end_sample] += segment
        np.clip(buffer, -1.0, 1.0, out=buffer)
        return buffer

    def _encode_audio(self, container, stream, audio_buffer: np.ndarray | None) -> None:
        if audio_buffer is None:
            return
        block = 1024
        for offset in range(0, len(audio_buffer), block):
            chunk = audio_buffer[offset : offset + block]
            frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="flt", layout="mono")
            frame.sample_rate = self.audio_sample_rate
            for packet in stream.encode(frame):
                container.mux(packet)

    # ------------------------------------------------------------------
    # Shared helpers (text/images/timings)
    # ------------------------------------------------------------------
    def _flatten_slides(self, project, concept_id: int | None = None) -> list:
        concepts = sorted(project.concepts, key=lambda c: c.order_index)
        if concept_id is not None:
            concepts = [c for c in concepts if c.id == concept_id]
            if not concepts:
                raise RuntimeError("Concept not found for this project.")
        slides: list = []
        for concept in concepts:
            slides.extend(sorted(concept.slides, key=lambda s: s.order_index))
        return slides

    def _compute_start_times(self, durations: list[float], overlaps: list[float]) -> list[float]:
        start_times = [0.0]
        for idx in range(1, len(durations)):
            previous = start_times[idx - 1] + durations[idx - 1] - overlaps[idx - 1]
            start_times.append(max(0.0, previous))
        return start_times

    def _compute_slide_durations_from_audio(
        self,
        slide_audios: list[np.ndarray | None],
        overlaps: list[float],
        min_duration: float = 1.2,
    ) -> list[float]:
        durations: list[float] = []
        for idx, audio in enumerate(slide_audios):
            audio_len = len(audio) / self.audio_sample_rate if audio is not None else self.duration
            base = max(min_duration, audio_len)
            overlap = overlaps[idx] if idx < len(overlaps) else 0.0
            durations.append(base + overlap)
        return durations

    def _prepare_frame(self, slide) -> Image.Image:
        image = None
        if getattr(slide, "image_url", None):
            try:
                data = self.image_fetcher(slide.image_url)
                image = Image.open(io.BytesIO(data)).convert("RGB")
            except Exception:
                image = None
        if image is None:
            image = self._placeholder_image(slide.id)
        return self._fit_image(image)

    def _fit_image(self, image: Image.Image) -> Image.Image:
        target_w, target_h = self.video_size
        target_ratio = target_w / target_h
        img_ratio = image.width / image.height
        if img_ratio > target_ratio:
            new_height = target_h
            new_width = int(new_height * img_ratio)
        else:
            new_width = target_w
            new_height = int(new_width / img_ratio)
        resized = image.resize((new_width, new_height), Image.LANCZOS)
        left = (new_width - target_w) // 2
        top = (new_height - target_h) // 2
        return resized.crop((left, top, left + target_w, top + target_h))

    def _placeholder_image(self, seed: int) -> Image.Image:
        width, height = self.video_size
        colors = [
            (32, 37, 74),
            (54, 61, 112),
            (28, 31, 55),
            (82, 58, 136),
        ]
        top = colors[seed % len(colors)]
        bottom = colors[(seed + 1) % len(colors)]
        base = Image.new("RGB", (width, height), top)
        draw = ImageDraw.Draw(base)
        for y in range(height):
            ratio = y / max(height - 1, 1)
            r = int(top[0] * (1 - ratio) + bottom[0] * ratio)
            g = int(top[1] * (1 - ratio) + bottom[1] * ratio)
            b = int(top[2] * (1 - ratio) + bottom[2] * ratio)
            draw.line([(0, y), (width, y)], fill=(r, g, b))
        return base

    def _render_text_overlay(self, slide) -> Image.Image:
        width, height = self.video_size
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        text = " ".join((slide.text or "").split()).strip() or " "
        style = self._extract_style(slide)
        text_color = self._parse_color(style.get("text_color"), (255, 255, 255))
        outline_color = self._parse_color(style.get("outline_color"), (0, 0, 0))
        bold = style.get("font_weight", "700") != "400"

        scale = width / PREVIEW_REF_WIDTH
        base_font_size = int(round(max(width * PREVIEW_FONT_RATIO, 1)))
        min_font_size = max(24, int(round(width * 0.04)))
        padding_x = max(40, int(round(PREVIEW_PADDING_PX * scale)))
        padding_y = padding_x
        max_text_width = max(200, width - 2 * padding_x)
        max_text_height = max(200, height - 2 * padding_y)

        font_size = max(min_font_size, base_font_size)
        lines: list[str] = []
        font = self._get_font(font_size, bold=bold)
        while font_size >= min_font_size:
            font = self._get_font(font_size, bold=bold)
            lines = self._wrap_text(draw, text, font, max_text_width)
            bbox_height = self._text_height(lines, font)
            if bbox_height <= max_text_height:
                break
            font_size -= 4

        total_height = self._text_height(lines, font)
        y = (height - total_height) // 2
        for line in lines:
            bbox = font.getbbox(line or " ")
            line_width = bbox[2] - bbox[0]
            x = (width - line_width) // 2
            self._draw_text_with_outline(overlay, (x, y), line, font, text_color, outline_color, scale)
            y += bbox[3] - bbox[1] + 10
        return overlay

    def _wrap_text(self, draw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
        words = text.split(" ")
        lines: list[str] = []
        current = ""
        for word in words:
            tentative = f"{current} {word}".strip() if current else word
            bbox = font.getbbox(tentative or " ")
            width_line = bbox[2] - bbox[0]
            if not current or width_line <= max_width:
                current = tentative
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def _text_height(self, lines: list[str], font: ImageFont.FreeTypeFont) -> int:
        height = 0
        for line in lines:
            bbox = font.getbbox(line or " ")
            height += (bbox[3] - bbox[1]) + 10
        return max(1, height - 10)

    def _draw_text_with_outline(
        self,
        overlay: Image.Image,
        position: tuple[int, int],
        text: str,
        font: ImageFont.FreeTypeFont,
        fill: tuple[int, int, int],
        outline: tuple[int, int, int],
        scale: float,
    ) -> None:
        draw = ImageDraw.Draw(overlay)
        x, y = position
        for dx, dy in self._outline_offsets(scale):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=outline)
        draw.text((x, y), text, font=font, fill=fill)

    def _outline_offsets(self, scale: float) -> list[tuple[int, int]]:
        base_offsets = [
            (-2.0, 0.0),
            (2.0, 0.0),
            (0.0, -2.0),
            (0.0, 2.0),
            (-1.5, -1.5),
            (-1.5, 1.5),
            (1.5, -1.5),
            (1.5, 1.5),
        ]
        scaled: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for dx, dy in base_offsets:
            offset = (self._scale_offset(dx, scale), self._scale_offset(dy, scale))
            if offset not in seen:
                seen.add(offset)
                scaled.append(offset)
        scaled.append((0, 0))
        return scaled

    def _scale_offset(self, value: float, scale: float) -> int:
        if value == 0:
            return 0
        scaled = int(round(value * scale))
        if scaled == 0:
            return 1 if value > 0 else -1
        return scaled

    def _extract_style(self, slide) -> dict:
        default = {
            "text_color": "#FFFFFF",
            "outline_color": "#000000",
            "font_weight": "700",
            "underline": False,
        }
        style = getattr(slide, "style", None)
        if style:
            if hasattr(style, "to_dict"):
                default.update(style.to_dict())
            elif isinstance(style, dict):
                default.update(style)
        elif hasattr(slide, "style_dict"):
            default.update(getattr(slide, "style_dict"))
        return default

    def _parse_color(self, value: str | None, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
        if not isinstance(value, str):
            return fallback
        candidate = value.strip().lstrip("#")
        if len(candidate) == 3:
            candidate = "".join(ch * 2 for ch in candidate)
        if len(candidate) != 6:
            return fallback
        try:
            r = int(candidate[0:2], 16)
            g = int(candidate[2:4], 16)
            b = int(candidate[4:6], 16)
        except ValueError:
            return fallback
        return (r, g, b)

    def _get_font(self, size: int = 88, bold: bool = True) -> ImageFont.FreeTypeFont:
        candidates = [
            FONTS_DIR / ("Inter-Bold.ttf" if bold else "Inter-Regular.ttf"),
            Path("Inter-Bold.ttf" if bold else "Inter-Regular.ttf"),
            Path("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
            Path("Arial Bold.ttf") if bold else Path("Arial.ttf"),
        ]
        for candidate in candidates:
            try:
                return ImageFont.truetype(str(candidate), size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _compute_overlaps(self, transitions: Iterable[str]) -> list[float]:
        overlaps: list[float] = []
        for transition in transitions[:-1]:
            kind = (transition or "fade").lower()
            if kind in {"fade", "scale", "slide"}:
                overlaps.append(self.transition_duration)
            else:
                overlaps.append(0.0)
        return overlaps

    def _download_image(self, url: str) -> bytes:
        import requests

        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.content


__all__ = ["VideoRenderer"]
