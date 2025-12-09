from __future__ import annotations

from typing import Any
import json
from datetime import datetime, timezone

from flask import current_app

from .task_queue import get_redis_connection


JOB_PREFIX = "conceptjob:v1:"
DEFAULT_TTL_SECONDS = 60 * 60  # 1 hour


def _job_key(job_id: str) -> str:
    return f"{JOB_PREFIX}{job_id}"


def job_ttl(app) -> int:
    return int(app.config.get("CONCEPT_JOB_TTL_SECONDS", DEFAULT_TTL_SECONDS))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_job(app, job_id: str, payload: dict[str, Any]) -> bool:
    connection = get_redis_connection(app)
    if not connection:
        return False
    payload.setdefault("job_id", job_id)
    payload.setdefault("created_at", _now_iso())
    payload.setdefault("updated_at", payload["created_at"])
    try:
        connection.setex(_job_key(job_id), job_ttl(app), json.dumps(payload))
        return True
    except Exception:
        current_app.logger.exception("concept_job_store_failed", extra={"job_id": job_id})
        return False


def fetch_job(app, job_id: str) -> dict[str, Any] | None:
    connection = get_redis_connection(app)
    if not connection:
        return None
    try:
        data = connection.get(_job_key(job_id))
    except Exception:
        current_app.logger.exception("concept_job_fetch_failed", extra={"job_id": job_id})
        return None
    if not data:
        return None
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        payload = json.loads(data)
        return payload
    except Exception:
        current_app.logger.exception("concept_job_decode_failed", extra={"job_id": job_id})
        return None


def update_job(app, job_id: str, **fields: Any) -> dict[str, Any] | None:
    existing = fetch_job(app, job_id) or {"job_id": job_id}
    existing.update(fields)
    existing["updated_at"] = _now_iso()
    save_job(app, job_id, existing)
    return existing


def delete_job(app, job_id: str) -> None:
    connection = get_redis_connection(app)
    if not connection:
        return
    try:
        connection.delete(_job_key(job_id))
    except Exception:
        current_app.logger.exception("concept_job_delete_failed", extra={"job_id": job_id})


__all__ = ["save_job", "fetch_job", "update_job", "delete_job", "job_ttl"]
