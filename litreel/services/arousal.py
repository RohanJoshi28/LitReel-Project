from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Sequence
from uuid import uuid4

import httpx


@dataclass(frozen=True)
class RankedChunk:
    text: str
    score: float


class NarrativeArousalClient:
    def __init__(
        self,
        *,
        base_url: str,
        max_workers: int = 12,
        split_words: int = 250,
        request_timeout: float = 30.0,
        stream_timeout: float = 60.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.max_workers = max(1, max_workers)
        self.split_words = max(50, split_words)
        self._request_timeout = request_timeout
        self._stream_timeout = stream_timeout
        timeout = httpx.Timeout(timeout=None, connect=10.0, read=stream_timeout, write=10.0)
        self._client = httpx.Client(timeout=timeout)
        self._api_prefix: str | None = None
        self._fn_index: int | None = None
        self._protocol: str | None = None
        self._logger = logging.getLogger(__name__)

    @property
    def is_ready(self) -> bool:
        if not self.base_url:
            return False
        try:
            self._ensure_metadata()
            return True
        except Exception:
            self._logger.exception("Narrative arousal API metadata fetch failed")
            return False

    def ping(self) -> bool:
        try:
            self._ensure_metadata()
            return True
        except Exception as exc:  # pragma: no cover - startup diagnostics only
            self._logger.warning("Narrative arousal API unavailable: %s", exc)
            return False

    def score_chunks(self, chunks: Sequence[str]) -> list[RankedChunk]:
        if not chunks or not self.is_ready:
            return []
        segments: list[tuple[int, str]] = []
        normalized_chunks = [chunk or "" for chunk in chunks]
        for idx, chunk in enumerate(normalized_chunks):
            for part in self._split_chunk(chunk):
                if part:
                    segments.append((idx, part))
        if not segments:
            return []

        scores: dict[int, list[float]] = {idx: [] for idx in range(len(normalized_chunks))}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_map = {executor.submit(self._score_segment, text): idx for idx, text in segments}
            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    value = future.result()
                except Exception as exc:  # pragma: no cover - defensive logging
                    self._logger.warning("Narrative arousal scoring failed: %s", exc)
                    continue
                if value is not None:
                    scores.setdefault(idx, []).append(value)

        ranked: list[RankedChunk] = []
        for idx, values in scores.items():
            if not values:
                continue
            average = sum(values) / len(values)
            ranked.append(RankedChunk(text=normalized_chunks[idx], score=average))
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked

    def _ensure_metadata(self) -> None:
        if self._fn_index is not None and self._api_prefix:
            return
        if not self.base_url:
            raise RuntimeError("Narrative arousal base URL missing.")
        response = self._client.get(f"{self.base_url}/config", timeout=self._request_timeout)
        response.raise_for_status()
        config = response.json()
        self._api_prefix = config.get("api_prefix") or "/gradio_api"
        self._protocol = config.get("protocol") or "sse_v3"
        dependencies = config.get("dependencies") or []
        for dep in dependencies:
            if dep.get("api_name") == "predict":
                self._fn_index = int(dep.get("id"))
                break
        if self._fn_index is None:
            raise RuntimeError("Unable to locate predict endpoint on the arousal space.")

    def _split_chunk(self, text: str) -> list[str]:
        words = (text or "").split()
        if not words:
            return []
        midpoint = max(1, len(words) // 2)
        first = " ".join(words[:midpoint]).strip()
        second = " ".join(words[midpoint:]).strip()
        parts = [part for part in (first, second) if part]
        return parts or [text]

    def _score_segment(self, text: str) -> float | None:
        if not text.strip():
            return None
        self._ensure_metadata()
        assert self._api_prefix is not None  # for mypy
        assert self._fn_index is not None
        queue_url = f"{self.base_url}{self._api_prefix}/queue/join"
        stream_url = f"{self.base_url}{self._api_prefix}/queue/data"
        session_hash = uuid4().hex
        payload = {
            "data": [text],
            "fn_index": self._fn_index,
            "session_hash": session_hash,
        }
        response = self._client.post(queue_url, json=payload, timeout=self._request_timeout)
        response.raise_for_status()
        event_id = response.json().get("event_id")
        if not event_id:
            raise RuntimeError("Narrative arousal API did not return an event identifier.")

        with self._client.stream(
            "GET",
            stream_url,
            params={"session_hash": session_hash},
            timeout=httpx.Timeout(timeout=None, connect=10.0, read=self._stream_timeout, write=10.0),
        ) as stream:
            for raw_line in stream.iter_lines():
                if not raw_line:
                    continue
                if isinstance(raw_line, bytes):
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                else:
                    line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                message = json.loads(line[5:])
                msg_type = message.get("msg")
                if msg_type == "queue_full":
                    raise RuntimeError("Narrative arousal queue is full.")
                if msg_type == "process_completed" and message.get("event_id") == event_id:
                    output = message.get("output", {})
                    data = output.get("data") or []
                    if not data:
                        raise RuntimeError("Narrative arousal response missing data payload.")
                    # prefer original scale (index 1) when available
                    return float(data[1] if len(data) > 1 else data[0])
                if msg_type == "close_stream":
                    break
        raise RuntimeError("Narrative arousal stream ended without completion.")
