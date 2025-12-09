from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.exc import SQLAlchemyError

from .extensions import db
from .models import RenderArtifact

JOB_PREFIX = "renderjob:v1:"
BLOB_PREFIX = "renderjobblob:v1:"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_key(job_id: str) -> str:
    return f"{JOB_PREFIX}{job_id}"


def _blob_key(job_id: str) -> str:
    return f"{BLOB_PREFIX}{job_id}"


def job_ttl(app) -> int:
    return int(app.config.get("RENDER_JOB_TTL_SECONDS", 3600))


@runtime_checkable
class RedisLike(Protocol):
    def setex(self, name: str, time: int, value: Any) -> Any: ...

    def get(self, name: str) -> Any: ...

    def delete(self, *names: str) -> Any: ...


def get_redis_connection(app) -> RedisLike | None:
    from .task_queue import get_redis_connection as _queue_connection

    return _queue_connection(app)


def save_job(app, job_id: str, payload: dict[str, Any]) -> bool:
    conn = get_redis_connection(app)
    if conn is None:
        return False
    payload = dict(payload)
    payload.setdefault("job_id", job_id)
    timestamp = _now_iso()
    payload.setdefault("requested_at", timestamp)
    payload["updated_at"] = timestamp
    data = json.dumps(payload)
    conn.setex(_job_key(job_id), job_ttl(app), data)
    _sync_render_artifact(app, payload)
    return True


def update_job(app, job_id: str, **fields: Any) -> dict[str, Any] | None:
    existing = fetch_job(app, job_id)
    if existing is None:
        existing = {"job_id": job_id}
    existing.update(fields)
    existing["updated_at"] = _now_iso()
    save_job(app, job_id, existing)
    return existing


def fetch_job(app, job_id: str) -> dict[str, Any] | None:
    conn = get_redis_connection(app)
    data = None
    if conn is not None:
        try:
            data = conn.get(_job_key(job_id))
        except Exception:
            data = None
    if not data:
        artifact = _load_artifact(job_id)
        if artifact:
            return artifact.to_job_payload()
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        artifact = _load_artifact(job_id)
        return artifact.to_job_payload() if artifact else None


def save_blob(app, job_id: str, data: bytes) -> bool:
    conn = get_redis_connection(app)
    if conn is None:
        return False
    conn.setex(_blob_key(job_id), job_ttl(app), data)
    return True


def fetch_blob(app, job_id: str) -> bytes | None:
    conn = get_redis_connection(app)
    if conn is None:
        return None
    return conn.get(_blob_key(job_id))


def delete_blob(app, job_id: str) -> None:
    conn = get_redis_connection(app)
    if conn is None:
        return
    conn.delete(_blob_key(job_id))


def _load_artifact(job_id: str) -> RenderArtifact | None:
    return RenderArtifact.query.filter_by(job_id=job_id).first()


def _parse_iso(value: Any):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _sync_render_artifact(app, payload: dict[str, Any]) -> None:
    job_id = payload.get("job_id")
    project_id = payload.get("project_id")
    if not job_id or not project_id:
        return
    try:
        artifact = RenderArtifact.query.filter_by(job_id=job_id).first()
        concept_id = payload.get("concept_id")
        user_id = payload.get("user_id") or payload.get("requested_by")
        if artifact is None:
            artifact = RenderArtifact(
                job_id=job_id,
                project_id=project_id,
                concept_id=concept_id,
                user_id=user_id,
            )
        else:
            if concept_id is not None:
                artifact.concept_id = concept_id
            if user_id is not None:
                artifact.user_id = user_id
        artifact.status = payload.get("status") or artifact.status
        artifact.voice = payload.get("voice")
        artifact.download_type = payload.get("download_type")
        artifact.download_url = payload.get("download_url")
        artifact.storage_path = payload.get("storage_path")
        artifact.file_size = payload.get("file_size")
        artifact.suggested_filename = payload.get("suggested_filename")
        artifact.render_signature = payload.get("render_signature")
        artifact.cache_hit = bool(payload.get("cache_hit", artifact.cache_hit))
        artifact.error = payload.get("error")
        completed_at = _parse_iso(payload.get("completed_at"))
        if completed_at:
            artifact.completed_at = completed_at
        db.session.add(artifact)
        db.session.commit()
    except SQLAlchemyError as exc:  # pragma: no cover - defensive rollback
        db.session.rollback()
        db.session.remove()
        try:
            db.engine.dispose()
        except Exception:
            pass
        if app:
            app.logger.warning(
                "render_artifact_sync_failed",
                extra={"job_id": job_id, "error": str(exc)},
            )


__all__ = [
    "save_job",
    "update_job",
    "fetch_job",
    "save_blob",
    "fetch_blob",
    "delete_blob",
    "job_ttl",
    "get_redis_connection",
]
