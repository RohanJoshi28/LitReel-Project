from types import SimpleNamespace

from litreel.tasks import render_job


class DummyLogger:
    def __init__(self):
        self.records = []

    def warning(self, msg, extra=None):
        self.records.append(("warning", msg, extra))

    def error(self, msg, extra=None):
        self.records.append(("error", msg, extra))

    def exception(self, msg, extra=None):
        self.records.append(("exception", msg, extra))


def make_app(config: dict):
    default = {
        "RENDER_STORAGE_BUCKET": "litreel-renders",
        "SUPABASE_URL": "",
        "SUPABASE_API_KEY": "",
        "DATABASE_PROFILE": "",
    }
    default.update(config)
    return SimpleNamespace(config=default, logger=DummyLogger())


def test_persist_render_output_skips_supabase_for_local_profile(tmp_path, monkeypatch):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video-bytes")
    app = make_app(
        {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_API_KEY": "supakey",
            "DATABASE_PROFILE": "local",
        }
    )

    def fail_upload(*args, **kwargs):
        raise AssertionError("Supabase upload should be skipped for local profile")

    blob_called = {}

    def fake_blob(_app, job_id, _path, filename, size):
        blob_called["called"] = (job_id, filename, size)
        return {"type": "blob", "url": "blob://memory", "path": "blob", "filename": filename, "size": size}

    monkeypatch.setattr(render_job, "_upload_to_supabase", fail_upload)
    monkeypatch.setattr(render_job, "_persist_render_blob", fake_blob)

    result = render_job._persist_render_output(app, "job-1", video_path, "Test Project")
    assert result["type"] == "blob"
    assert blob_called["called"][0] == "job-1"


def test_persist_render_output_falls_back_when_supabase_unavailable(tmp_path, monkeypatch):
    video_path = tmp_path / "movie.mp4"
    video_path.write_bytes(b"bytes")
    app = make_app(
        {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_API_KEY": "supakey",
            "DATABASE_PROFILE": "",
        }
    )

    called = {"upload": 0, "blob": 0}

    def failing_upload(*args, **kwargs):
        called["upload"] += 1
        return None

    def fake_blob(_app, job_id, _path, filename, size):
        called["blob"] += 1
        return {"type": "blob", "url": "blob://memory", "path": "blob", "filename": filename, "size": size}

    monkeypatch.setattr(render_job, "_upload_to_supabase", failing_upload)
    monkeypatch.setattr(render_job, "_persist_render_blob", fake_blob)

    result = render_job._persist_render_output(app, "job-22", video_path, "Fallback Project")
    assert result["type"] == "blob"
    assert called["upload"] == 1
    assert called["blob"] == 1
