from __future__ import annotations

import hashlib
import json
import time
import os
from pathlib import Path
from uuid import uuid4

from flask import current_app
from sqlalchemy.exc import OperationalError

from ..extensions import db
from ..models import Project
from ..render_jobs import save_blob, update_job
from .utils import ensure_app_context

LOCAL_DATABASE_PROFILES = {"local", "dev", "sqlite"}


def process_render_job(
    job_id: str,
    *,
    project_id: int,
    concept_id: int | None,
    voice: str | None,
    user_id: int | None = None,
) -> None:
    app, ctx = ensure_app_context()
    try:
        project = _load_project_with_retry(app, project_id, user_id)
        if not project:
            update_job(
                app,
                job_id,
                status="failed",
                error="Project not found.",
            )
            return

        concept = None
        if concept_id is not None:
            concept = next((c for c in project.concepts if c.id == concept_id), None)
        if concept is None and project.concepts:
            concept = min(project.concepts, key=lambda c: c.order_index)

        signature = _render_signature(project, concept, voice)
        cached = _fetch_cached_render(app, signature)
        if cached:
            update_job(
                app,
                job_id,
                status="ready",
                completed_at=_now_iso(),
                download_type="url",
                download_url=cached["url"],
                storage_path=cached["path"],
                file_size=cached.get("size"),
                suggested_filename=cached.get("filename"),
                cache_hit=True,
                render_signature=signature,
            )
            return

        update_job(
            app,
            job_id,
            status="processing",
            started_at=_now_iso(),
            render_signature=signature,
            cache_hit=False,
        )
        renderer = app.config["VIDEO_RENDERER"]
        safe_voice = voice if voice not in {None, "", "none"} else None
        render_warnings: list[str] = []
        video_path = Path(
            renderer.render_project(
                project,
                concept_id=concept_id,
                voice=safe_voice,
                warnings=render_warnings,
            )
        )
        file_info = _persist_render_output(
            app,
            job_id,
            video_path,
            project.title or f"project-{project.id}",
            signature=signature,
        )
        try:
            Path(video_path).unlink(missing_ok=True)
        except Exception:
            pass
        final_payload = {
            "status": "ready",
            "completed_at": _now_iso(),
            "download_type": file_info.get("type"),
            "download_url": file_info.get("url"),
            "storage_path": file_info.get("path"),
            "file_size": file_info.get("size"),
            "suggested_filename": file_info.get("filename"),
            "cache_key": signature,
        }
        if render_warnings:
            final_payload["warnings"] = render_warnings
        update_job(
            app,
            job_id,
            **final_payload,
        )
    except Exception as exc:  # pragma: no cover - runtime guard
        if isinstance(exc, OperationalError):
            _reset_db_session()
        app.logger.exception("render_job_failed", extra={"job_id": job_id, "error": str(exc)})
        update_job(
            app,
            job_id,
            status="failed",
            error=str(exc),
        )
        raise
    finally:
        db.session.remove()
        if ctx is not None:
            ctx.pop()


def _persist_render_output(
    app,
    job_id: str,
    video_path: Path,
    project_title: str,
    *,
    signature: str | None = None,
) -> dict[str, str | int]:
    bucket = app.config.get("RENDER_STORAGE_BUCKET", "litreel-renders")
    supabase_url = (app.config.get("SUPABASE_URL") or "").strip()
    supabase_key = (app.config.get("SUPABASE_API_KEY") or "").strip()
    filename = _build_filename(project_title, video_path)
    file_size = os.path.getsize(video_path)
    profile = (app.config.get("DATABASE_PROFILE") or "").strip().lower()
    supabase_enabled = bool(supabase_url and supabase_key)
    if profile in LOCAL_DATABASE_PROFILES:
        supabase_enabled = False

    supabase_attempted = False
    if supabase_enabled:
        supabase_attempted = True
        info = _upload_to_supabase(
            app,
            bucket,
            supabase_url,
            supabase_key,
            video_path,
            filename,
            signature=signature,
        )
        if info:
            return {
                "type": "url",
                "url": info["public_url"],
                "path": info["storage_path"],
                "filename": filename,
                "size": file_size,
            }
        app.logger.warning(
            "render_upload_supabase_unavailable_falling_back",
            extra={"job_id": job_id, "bucket": bucket},
        )

    blob_payload = _persist_render_blob(app, job_id, video_path, filename, file_size)
    if blob_payload:
        return blob_payload

    if supabase_attempted:
        raise RuntimeError("Failed to upload render to Supabase storage and local blob fallback unavailable.")

    raise RuntimeError("Render storage unavailable.")


def _upload_to_supabase(
    app,
    bucket: str,
    url: str,
    key: str,
    video_path: Path,
    filename: str,
    *,
    signature: str | None = None,
) -> dict[str, str] | None:
    try:
        from supabase import create_client
    except Exception:  # pragma: no cover - supabase optional
        app.logger.warning("supabase_client_missing_for_render_upload")
        return None

    client = create_client(url, key)
    object_path = _render_storage_key(filename, signature)
    try:
        _ensure_bucket(client, bucket)
        with open(video_path, "rb") as fh:
            file_data = fh.read()
        client.storage.from_(bucket).upload(
            object_path,
            file_data,
            {
                "content-type": "video/mp4",
                "x-upsert": "true",
                "cache-control": "public,max-age=31536000,immutable",
            },
        )
        public_url = f"{url.rstrip('/')}/storage/v1/object/public/{bucket}/{object_path}"
        return {"public_url": public_url, "storage_path": object_path}
    except Exception as exc:
        app.logger.error(
            "render_upload_supabase_failed",
            extra={"error": str(exc), "bucket": bucket, "object_path": object_path},
        )
        return None


def _persist_render_blob(app, job_id: str, video_path: Path, filename: str, file_size: int):
    try:
        with open(video_path, "rb") as fh:
            blob = fh.read()
    except Exception as exc:
        app.logger.warning(
            "render_blob_read_failed",
            extra={"job_id": job_id, "error": str(exc)},
        )
        return None

    if save_blob(app, job_id, blob):
        app.logger.info(
            "render_blob_fallback_stored",
            extra={"job_id": job_id, "size": file_size},
        )
        return {"type": "blob", "filename": filename, "size": file_size}

    app.logger.warning(
        "render_blob_store_failed",
        extra={"job_id": job_id},
    )
    return None


def _load_project_with_retry(app, project_id: int, user_id: int | None, *, attempts: int = 2):
    last_exc: OperationalError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return Project.query.filter_by(id=project_id, user_id=user_id).first()
        except OperationalError as exc:
            last_exc = exc
            _reset_db_session()
            app.logger.warning(
                "render_job_project_query_retry",
                extra={
                    "project_id": project_id,
                    "user_id": user_id,
                    "attempt": attempt,
                    "error": str(exc),
                },
            )
            if attempt < attempts:
                time.sleep(min(0.5 * attempt, 2))
    if last_exc is not None:
        raise last_exc
    return None


def _reset_db_session():
    try:
        db.session.rollback()
    except Exception:
        pass
    db.session.remove()
    try:
        db.engine.dispose()
    except Exception:
        pass


def _ensure_bucket(client, bucket: str) -> None:
    try:
        client.storage.get_bucket(bucket)
    except Exception:
        try:
            client.storage.create_bucket(bucket, bucket, {"public": True})
        except Exception:
            pass


def _build_filename(project_title: str, video_path: Path) -> str:
    from werkzeug.utils import secure_filename

    title = secure_filename(project_title) or "litreel-project"
    if not title.endswith(".mp4"):
        title = f"{title}.mp4"
    return f"{Path(video_path).stem}-{title}"


def _render_storage_key(filename: str, signature: str | None) -> str:
    safe_name = filename if filename.endswith(".mp4") else f"{filename}.mp4"
    if signature:
        return f"cache/{signature}/{safe_name}"
    return f"{uuid4().hex}/{safe_name}"


def _render_signature(project: Project, concept, voice: str | None) -> str:
    payload: dict[str, object] = {
        "project_id": project.id,
        "concept_id": concept.id if concept else None,
        "voice": (voice or "").strip().lower(),
        "slides": [],
        "version": 1,
    }
    slides = []
    concepts = sorted(project.concepts, key=lambda c: c.order_index)
    target_concepts = [concept] if concept else concepts
    for concept_entry in target_concepts:
        for slide in sorted(concept_entry.slides, key=lambda s: s.order_index):
            style_payload = {}
            if hasattr(slide, "style_dict"):
                style_payload = slide.style_dict or {}
            elif getattr(slide, "style", None) and hasattr(slide.style, "to_dict"):
                style_payload = slide.style.to_dict()
            slide_payload = {
                "text": slide.text,
                "image_url": slide.image_url,
                "effect": (slide.effect or "").strip().lower(),
                "transition": (slide.transition or "").strip().lower(),
                "voice": (voice or "").strip().lower(),
                "style": style_payload,
            }
            slides.append(slide_payload)
    payload["slides"] = slides
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _fetch_cached_render(app, signature: str | None):
    if not signature:
        return None
    bucket = app.config.get("RENDER_STORAGE_BUCKET", "litreel-renders")
    supabase_url = (app.config.get("SUPABASE_URL") or "").strip()
    supabase_key = (app.config.get("SUPABASE_API_KEY") or "").strip()
    if not (supabase_url and supabase_key):
        return None
    try:
        from supabase import create_client
    except Exception:
        return None
    client = create_client(supabase_url, supabase_key)
    prefix = f"cache/{signature}"
    try:
        listing = client.storage.from_(bucket).list(prefix)
    except Exception:
        return None
    if not listing:
        return None
    first = listing[0]
    object_path = f"{prefix}/{first['name']}"
    public_url = f"{supabase_url.rstrip('/')}/storage/v1/object/public/{bucket}/{object_path}"
    return {"url": public_url, "path": object_path, "filename": first["name"], "size": first.get("metadata", {}).get("size")}


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


__all__ = ["process_render_job"]
