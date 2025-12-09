from __future__ import annotations

from types import SimpleNamespace

from litreel.services.arousal import NarrativeArousalClient


class _DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _DummyStream:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_lines(self):
        for line in self._lines:
            yield line


class _DummyHttpx:
    def __init__(self, lines):
        self._lines = lines
        self.post_calls: list[tuple] = []

    def get(self, url, timeout):
        return _DummyResponse(
            {
                "api_prefix": "/gradio_api",
                "dependencies": [{"api_name": "predict", "id": 2}],
            }
        )

    def post(self, url, json, timeout):
        self.post_calls.append((url, json))
        return _DummyResponse({"event_id": json["session_hash"]})

    def stream(self, method, url, params, timeout):
        assert method == "GET"
        event_id = params["session_hash"]
        formatted = [line.replace("{event_id}", event_id) for line in self._lines]
        return _DummyStream(formatted)


def test_score_segment_handles_string_lines(monkeypatch):
    lines = [
        'data: {"msg":"process_completed","event_id":"{event_id}","output":{"data":[0.5, 0.8]}}',
        'data: {"msg":"close_stream"}',
    ]
    client = NarrativeArousalClient(base_url="https://example.com", max_workers=1)
    dummy_httpx = _DummyHttpx(lines)
    client._client = dummy_httpx  # type: ignore[assignment]
    result = client._score_segment("Sample chunk")

    assert result == 0.8
    assert dummy_httpx.post_calls, "Queue join should be invoked"
