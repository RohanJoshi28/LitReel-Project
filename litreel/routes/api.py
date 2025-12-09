from __future__ import annotations

import os
import time
import io
from pathlib import Path
import threading
from uuid import uuid4

from flask import Blueprint, Response, after_this_request, current_app, jsonify, redirect, request, send_file
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models import Concept, Project, Slide, SlideStyle, RenderArtifact
from ..render_jobs import delete_blob, fetch_blob, fetch_job, save_job, update_job
from ..concept_jobs import (
    fetch_job as fetch_concept_job,
    save_job as save_concept_job,
    update_job as update_concept_job,
)
from ..services.pdf_parser import SUPPORTED_EXTENSIONS as PARSER_EXTENSIONS, extract_text_from_document
from ..task_queue import get_task_queue, is_task_queue_healthy
from ..tasks.project_generation import generate_project_job
from ..tasks.concept_lab import process_concept_lab_job

bp = Blueprint("api", __name__)

ALLOWED_EXTENSIONS = {ext.lstrip(".") for ext in PARSER_EXTENSIONS}
SUPPORTED_UPLOAD_LABEL = ", ".join(ext.upper() for ext in sorted(ALLOWED_EXTENSIONS))
UPLOAD_FIELD_NAMES = ("document", "pdf")
ALLOWED_EFFECTS = {"none", "zoom-in", "zoom-out", "pan-left", "pan-right"}
ALLOWED_TRANSITIONS = {"fade", "slide", "scale"}
ALLOWED_VOICES = {"sarah", "bella", "adam", "liam"}
DEFAULT_STYLE = SlideStyle.default_dict()


def _normalize_hex_color(value: str | None, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    candidate = value.strip().lstrip("#")
    if len(candidate) == 3:
        candidate = "".join(ch * 2 for ch in candidate)
    if len(candidate) != 6:
        return fallback
    try:
        int(candidate, 16)
    except ValueError:
        return fallback
    return f"#{candidate.upper()}"


def _normalize_font_weight(value: str | None, fallback: str) -> str:
    allowed = {"400", "500", "600", "700"}
    if isinstance(value, (int, float)):
        value = str(int(value))
    if isinstance(value, str):
        v = value.strip()
        if v in allowed:
            return v
    return fallback


def _normalize_bool(value, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return fallback


def _get_gemini_service():
    return current_app.config["GEMINI_SERVICE"]


def _get_stock_service():
    return current_app.config["STOCK_IMAGE_SERVICE"]


def _get_renderer():
    return current_app.config["VIDEO_RENDERER"]


def _get_rag_service():
    return current_app.config.get("RAG_SERVICE")


def _get_arousal_client():
    return current_app.config.get("AROUSAL_CLIENT")


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _project_for_user(project_id: int) -> Project | None:
    if not current_user.is_authenticated:
        return None
    return Project.query.filter_by(id=project_id, user_id=current_user.id).first()


def _slide_for_user(slide_id: int) -> Slide | None:
    if not current_user.is_authenticated:
        return None
    return (
        Slide.query.join(Concept, Slide.concept_id == Concept.id)
        .join(Project, Concept.project_id == Project.id)
        .filter(Slide.id == slide_id, Project.user_id == current_user.id)
        .first()
    )


def _resolve_concept(project: Project, concept_id: int | None) -> Concept | None:
    if concept_id is None and project.active_concept_id:
        concept_id = project.active_concept_id
    if concept_id is None and project.concepts:
        return project.concepts[0]
    if concept_id is None:
        return None
    try:
        cid = int(concept_id)
    except (TypeError, ValueError):
        return None
    return Concept.query.filter_by(id=cid, project_id=project.id).first()


def _normalize_voice(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized in {"none", "off"}:
        return None
    return normalized


def _download_filename(project: Project, concept: Concept | None) -> str:
    slug = secure_filename(project.title or f"project-{project.id}") or f"project-{project.id}"
    concept_suffix = f"-concept-{concept.id}" if concept else ""
    return f"{slug}{concept_suffix}.mp4"


def _queue_available() -> bool:
    return is_task_queue_healthy(current_app)


def _should_use_queue() -> bool:
    if current_app.config.get("ENABLE_SYNC_DOWNLOAD", False):
        return False
    profile = str(current_app.config.get("DATABASE_PROFILE", "")).strip().lower()
    if profile in {"local", "dev", "sqlite"}:
        return False
    return _queue_available()


def _start_concept_lab_job(*, project: Project, payload: dict, user_id: int):
    queue = get_task_queue(current_app)
    job_id = uuid4().hex
    job_snapshot = {
        "job_id": job_id,
        "project_id": project.id,
        "user_id": user_id,
        "status": "queued",
        "random_slice": bool(payload.get("random_slice")),
        "concept_id": payload.get("concept_id"),
        "context_length": len(payload.get("context") or ""),
    }
    if not save_concept_job(current_app, job_id, job_snapshot):
        current_app.logger.error("concept_lab_job_store_unavailable")
        return None, "Concept lab job store unavailable.", 503

    if queue:
        try:
            queue.enqueue(
                "litreel.tasks.concept_lab.process_concept_lab_job",
                job_id,
                project.id,
                payload,
                user_id,
            )
            return job_snapshot, None, None
        except Exception as exc:  # pragma: no cover - enqueue failure
            current_app.logger.exception(
                "concept_lab_job_enqueue_failed", extra={"job_id": job_id, "error": str(exc)}
            )
            update_concept_job(current_app, job_id, status="failed", error=str(exc))
            return None, "Failed to enqueue concept lab job.", 503

    # Inline fallback when queue is unavailable (e.g., local dev)
    process_concept_lab_job(job_id, project.id, payload, user_id)
    final_job = fetch_concept_job(current_app, job_id)
    if final_job and str(final_job.get("status")).lower() == "failed":
        status_code = final_job.get("status_code") or 500
        message = final_job.get("error") or "Concept generation failed."
        return None, message, status_code
    return final_job or job_snapshot, None, None


def _start_render_job(
    *,
    project: Project,
    concept: Concept | None,
    voice: str | None,
    user_id: int,
) -> tuple[dict | None, str | None]:
    queue = get_task_queue(current_app) if _should_use_queue() else None
    job_id = uuid4().hex
    filename = _download_filename(project, concept)
    job_payload = {
        "job_id": job_id,
        "project_id": project.id,
        "concept_id": concept.id if concept else None,
        "voice": voice or None,
        "status": "queued",
        "requested_by": user_id,
        "user_id": user_id,
        "download_type": None,
        "suggested_filename": filename,
    }
    if not save_job(current_app, job_id, job_payload):
        current_app.logger.error("render_job_store_unavailable")
        return None, "Render job store unavailable."
    if queue:
        try:
            queue.enqueue(
                "litreel.tasks.render_job.process_render_job",
                job_id,
                project_id=project.id,
                concept_id=concept.id if concept else None,
                voice=voice,
                user_id=user_id,
            )
            return job_payload, None
        except Exception as exc:  # pragma: no cover - enqueue failure
            current_app.logger.exception("render_job_enqueue_failed", extra={"job_id": job_id, "error": str(exc)})
            update_job(current_app, job_id, status="failed", error=str(exc))
            return None, "Failed to enqueue the render job."
    return _process_render_inline(job_id, project, concept, voice, user_id, fallback_payload=job_payload)


def _process_render_inline(job_id, project, concept, voice, user_id, fallback_payload=None):
    try:
        from ..tasks.render_job import process_render_job
    except Exception as exc:  # pragma: no cover - import guard
        current_app.logger.exception("render_job_inline_import_failed", extra={"error": str(exc)})
        update_job(current_app, job_id, status="failed", error=str(exc))
        return fallback_payload, "Render pipeline unavailable."

    try:
        process_render_job(
            job_id,
            project_id=project.id,
            concept_id=concept.id if concept else None,
            voice=voice,
            user_id=user_id,
        )
    except Exception as exc:  # pragma: no cover - inline failure
        current_app.logger.exception("render_job_inline_failed", extra={"job_id": job_id, "error": str(exc)})
        update_job(current_app, job_id, status="failed", error=str(exc))
        job_state = fetch_job(current_app, job_id) or fallback_payload
        return job_state, f"Render failed: {exc}"
    final_job = fetch_job(current_app, job_id) or fallback_payload
    return final_job, None


def _not_found(entity: str):
    return jsonify({"error": f"{entity} not found."}), 404


def _launch_background_project_generation(*, project_id: int, user_id: int, title: str, raw_text: str):
    """Fire-and-forget fallback when Redis queues are unavailable."""
    app = current_app._get_current_object()

    def _run_generation():
        with app.app_context():
            try:
                result = generate_project_job(
                    project_id,
                    user_id=user_id,
                    title=title,
                    raw_text=raw_text,
                )
                app.logger.info(
                    "project_background_generation_complete",
                    extra={"project_id": project_id, "status": result.get("status")},
                )
            except Exception as exc:  # pragma: no cover - safety net, surfaced in logs
                app.logger.exception(
                    "project_background_generation_failed",
                    extra={"project_id": project_id, "error": str(exc)},
                )

    thread = threading.Thread(
        target=_run_generation,
        name=f"project-gen-{project_id}",
        daemon=True,
    )
    thread.start()
    return thread


def _schedule_rag_book_deletion(book_id: str | None) -> None:
    if not book_id:
        return
    rag_service = _get_rag_service()
    if not rag_service or not getattr(rag_service, "is_enabled", False):
        return
    app = current_app._get_current_object()

    def _cleanup():
        with app.app_context():
            try:
                rag_service.delete_book(book_id)
                app.logger.info(
                    "rag_book_deleted",
                    extra={"book_id": book_id},
                )
            except Exception as exc:  # pragma: no cover - network/SDK issues
                app.logger.exception(
                    "rag_book_delete_failed",
                    extra={"book_id": book_id, "error": str(exc)},
                )

    thread = threading.Thread(
        target=_cleanup,
        name=f"rag-delete-{book_id}",
        daemon=True,
    )
    thread.start()


@bp.route("/projects", methods=["POST"])
@login_required
def create_project():
    generator = _get_gemini_service()

    current_app.logger.info(
        "project_upload_request",
        extra={
            "user_id": getattr(current_user, "id", None),
            "file_fields": list(request.files.keys()),
            "form_keys": list(request.form.keys()),
            "content_length": request.content_length,
        },
    )

    upload_file = None
    for field in UPLOAD_FIELD_NAMES:
        if field in request.files:
            upload_file = request.files[field]
            break

    if upload_file is None:
        return jsonify({"error": "Missing document upload."}), 400

    if upload_file.filename == "":
        return jsonify({"error": "Empty filename."}), 400

    if not allowed_file(upload_file.filename):
        return jsonify({"error": f"Only {SUPPORTED_UPLOAD_LABEL} uploads are supported."}), 400

    filename = secure_filename(upload_file.filename)
    prefixed = f"{uuid4().hex}_{filename}"
    save_path = Path(current_app.config["UPLOAD_FOLDER"]) / prefixed
    upload_file.save(save_path)
    current_app.logger.info(
        "project_upload_file_saved",
        extra={
            "user_id": current_user.id if current_user.is_authenticated else None,
            "original_filename": filename,
            "saved_path": str(save_path),
            "size_bytes": getattr(upload_file, "content_length", None),
        },
    )

    title = request.form.get("title") or Path(filename).stem

    raw_text = None
    try:
        parser = getattr(generator, "document_parser", getattr(generator, "pdf_parser", None))
        if parser is None:
            raise AttributeError("Gemini generator is missing a document parser.")
        raw_text = parser(save_path)
    except Exception as exc:  # pragma: no cover - unexpected parser failure
            current_app.logger.exception("Failed to parse document text before Gemini call", exc_info=exc)

    if not raw_text:
        try:
            raw_text = extract_text_from_document(save_path)
        except Exception as parse_exc:
            current_app.logger.exception("Document parsing failed", exc_info=parse_exc)
            try:
                save_path.unlink(missing_ok=True)
            except OSError:
                pass
            return jsonify({"error": "Failed to analyze the uploaded document. Please try again."}), 500

    raw_text = (raw_text or "").strip()
    if not raw_text:
        try:
            save_path.unlink(missing_ok=True)
        except OSError:
            pass
        return jsonify({"error": "Uploaded document did not contain readable text."}), 400

    try:
        save_path.unlink(missing_ok=True)
    except OSError:
        pass

    project = Project(title=title, user_id=current_user.id, status="pending")
    db.session.add(project)
    db.session.commit()
    db.session.refresh(project)
    current_app.logger.info(
        "project_record_created",
        extra={
            "project_id": project.id,
            "user_id": current_user.id,
            "status": project.status,
        },
    )

    queue = get_task_queue(current_app)
    if queue and not is_task_queue_healthy(current_app):
        current_app.logger.warning(
            "project_queue_unhealthy",
            extra={"project_id": project.id},
        )
        queue = None
    current_app.logger.info(
        "project_queue_selection",
        extra={
            "project_id": project.id,
            "queue_available": bool(queue),
            "profile": str(current_app.config.get("DATABASE_PROFILE")),
        },
    )
    job_id = None
    if queue:
        try:
            job = queue.enqueue(
                "litreel.tasks.project_generation.generate_project_job",
                project.id,
                user_id=current_user.id,
                title=title,
                raw_text=raw_text,
            )
            job_id = job.id
            current_app.logger.info(
                "project_enqueued",
                extra={"project_id": project.id, "job_id": job_id, "queue": queue.name},
            )
        except Exception as enqueue_exc:
            current_app.logger.exception(
                "project_enqueue_failed",
                extra={"project_id": project.id, "error": str(enqueue_exc)},
            )

    if job_id is None:
        profile = str(current_app.config.get("DATABASE_PROFILE", "")).strip().lower()
        force_inline = bool(current_app.config.get("FORCE_INLINE_GENERATION"))
        inline_generation = current_app.config.get("TESTING") or force_inline or profile in {
            "local",
            "dev",
            "sqlite",
        }
        if inline_generation:
            result = generate_project_job(
                project.id,
                user_id=current_user.id,
                title=title,
                raw_text=raw_text,
            )
            db.session.refresh(project)
            current_app.logger.info(
                "project_inline_generation_complete",
                extra={
                    "project_id": project.id,
                    "final_status": project.status,
                    "fallback_used": result.get("fallback_used"),
                },
            )
            payload = {
                "project": serialize_project(project),
                "job": {
                    "mode": "inline",
                    "status": result.get("status", project.status),
                },
            }
            if result.get("fallback_used"):
                payload["generation_mode"] = "fallback"
            else:
                payload["generation_mode"] = "gemini"
            return jsonify(payload), 201

        _launch_background_project_generation(
            project_id=project.id,
            user_id=current_user.id,
            title=title,
            raw_text=raw_text,
        )
        current_app.logger.info(
            "project_background_generation_launched",
            extra={"project_id": project.id, "user_id": current_user.id},
        )
        payload = {
            "project": serialize_project(project),
            "job": {
                "mode": "background",
                "status": "queued",
            },
            "generation_mode": "deferred",
        }
        current_app.logger.info(
            "project_upload_response",
            extra={"project_id": project.id, "response_mode": "background", "job_status": "queued"},
        )
        return jsonify(payload), 201

    payload = {
        "project": serialize_project(project),
        "job": {"id": job_id, "status": "queued"},
    }
    current_app.logger.info(
        "project_upload_response",
        extra={"project_id": project.id, "response_mode": "queued", "job_id": job_id},
    )
    return jsonify(payload), 201


@bp.route("/projects", methods=["GET"])
@login_required
def list_projects():
    projects = (
        Project.query.filter_by(user_id=current_user.id)
        .order_by(Project.created_at.desc())
        .all()
    )
    return jsonify({"projects": [serialize_project(p) for p in projects]})


@bp.route("/projects/<int:project_id>/renders", methods=["POST"])
@login_required
def create_render_job(project_id: int):
    project = _project_for_user(project_id)
    if not project:
        return _not_found("Project")
    payload = request.get_json(silent=True) or {}
    concept = _resolve_concept(project, payload.get("concept_id"))
    if not concept:
        return jsonify({"error": "Concept not found for this project."}), 404
    voice = payload.get("voice")
    normalized_voice = _normalize_voice(voice if voice is not None else project.voice)
    job_payload, error_message = _start_render_job(
        project=project,
        concept=concept,
        voice=normalized_voice,
        user_id=current_user.id,
    )
    if not job_payload and error_message:
        return jsonify({"error": error_message}), 503

    status = (job_payload or {}).get("status", "").lower()
    status_code = 201 if status == "ready" else 202
    response = {"job": job_payload}
    if error_message:
        response["warning"] = error_message
    return jsonify(response), status_code

@bp.route("/projects/<int:project_id>/downloads", methods=["POST"])
@login_required
def create_download_job(project_id: int):
    project = _project_for_user(project_id)
    if not project:
        return _not_found("Project")
    payload = request.get_json(silent=True) or {}
    concept_id = payload.get("concept_id")
    concept = _resolve_concept(project, concept_id)
    if not concept:
        return jsonify({"error": "Concept not found for this project."}), 404
    voice = payload.get("voice")
    normalized_voice = _normalize_voice(voice if voice is not None else project.voice)

    if not _should_use_queue():
        return _render_project_response(project, concept.id if concept else None, normalized_voice)

    job_payload, error_message = _enqueue_render_job(
        project=project,
        concept=concept,
        voice=normalized_voice,
        user_id=current_user.id,
    )
    if error_message or not job_payload:
        return jsonify({"error": error_message or "Failed to enqueue render job."}), 503
    return jsonify({"job": job_payload}), 202


@bp.route("/projects/<int:project_id>", methods=["GET"])
@login_required
def get_project(project_id: int):
    project = _project_for_user(project_id)
    if not project:
        return _not_found("Project")
    current_app.logger.info(
        "project_poll_response",
        extra={
            "project_id": project.id,
            "status": project.status,
            "concepts": len(project.concepts or []),
            "user_id": project.user_id,
        },
    )
    return jsonify({"project": serialize_project(project)})


@bp.route("/projects/<int:project_id>", methods=["PATCH"])
@login_required
def update_project(project_id: int):
    project = _project_for_user(project_id)
    if not project:
        return _not_found("Project")

    payload = request.get_json(silent=True) or {}
    title = payload.get("title")
    voice = payload.get("voice")
    active_concept_id = payload.get("active_concept_id")

    if title is not None:
        cleaned = (title or "").strip()
        if not cleaned:
            return jsonify({"error": "Title cannot be empty."}), 400
        project.title = cleaned[:255]
    if voice is not None:
        normalized_voice = str(voice).strip().lower()
        if normalized_voice and normalized_voice not in ALLOWED_VOICES:
            return jsonify({"error": "Unsupported voice."}), 400
        project.voice = normalized_voice
    if active_concept_id is not None:
        try:
            concept_id_int = int(active_concept_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid concept id."}), 400
        concept = Concept.query.filter_by(id=concept_id_int, project_id=project.id).first()
        if not concept:
            return jsonify({"error": "Concept not found for this project."}), 404
        project.active_concept_id = concept.id

    db.session.commit()
    return jsonify({"project": serialize_project(project)})


@bp.route("/projects/<int:project_id>", methods=["DELETE"])
@login_required
def delete_project(project_id: int):
    project = _project_for_user(project_id)
    if not project:
        return _not_found("Project")
    supabase_book_id = project.supabase_book_id
    deleted_payload = {"id": project.id, "title": project.title}
    db.session.delete(project)
    db.session.commit()
    _schedule_rag_book_deletion(supabase_book_id)
    return jsonify({"deleted": deleted_payload})


@bp.route("/slides/<int:slide_id>", methods=["PATCH"])
@login_required
def update_slide(slide_id: int):
    slide = _slide_for_user(slide_id)
    if not slide:
        return _not_found("Slide")
    payload = request.get_json(silent=True) or {}

    text = payload.get("text")
    effect = payload.get("effect")
    transition = payload.get("transition")
    image_url = payload.get("image_url")
    style_payload = payload.get("style")
    order_index = payload.get("order_index")

    if order_index is not None:
        concept = slide.concept
        slides = sorted(concept.slides, key=lambda s: s.order_index)
        slides = [s for s in slides if s.id != slide.id]
        try:
            new_idx = int(order_index)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid order index."}), 400
        new_idx = max(0, min(new_idx, len(slides)))
        slides.insert(new_idx, slide)
        for idx, s in enumerate(slides):
            s.order_index = idx
        db.session.flush()

    if text is not None:
        slide.text = text.strip()
    if effect is not None:
        if effect not in ALLOWED_EFFECTS:
            return jsonify({"error": "Unsupported effect."}), 400
        slide.effect = effect
    if transition is not None:
        if transition not in ALLOWED_TRANSITIONS:
            return jsonify({"error": "Unsupported transition."}), 400
        slide.transition = transition
    if image_url is not None:
        slide.image_url = image_url
    if style_payload:
        style = slide.style or SlideStyle(slide=slide)
        style.text_color = _normalize_hex_color(
            style_payload.get("text_color"), style.text_color or DEFAULT_STYLE["text_color"]
        )
        style.outline_color = _normalize_hex_color(
            style_payload.get("outline_color"), style.outline_color or DEFAULT_STYLE["outline_color"]
        )
        style.font_weight = _normalize_font_weight(
            style_payload.get("font_weight"), style.font_weight or DEFAULT_STYLE["font_weight"]
        )
        style.underline = _normalize_bool(
            style_payload.get("underline"), bool(style.underline) if style.underline is not None else DEFAULT_STYLE["underline"]
        )
        db.session.add(style)

    db.session.commit()

    return jsonify({"slide": serialize_slide(slide)})


@bp.route("/slides/<int:slide_id>", methods=["DELETE"])
@login_required
def delete_slide(slide_id: int):
    slide = _slide_for_user(slide_id)
    if not slide:
        return _not_found("Slide")
    concept_id = slide.concept_id
    db.session.delete(slide)
    db.session.commit()
    concept = Concept.query.get(concept_id)
    return jsonify({"deleted": slide_id, "concept": serialize_concept(concept) if concept else None})


@bp.route("/concepts/<int:concept_id>", methods=["DELETE"])
@login_required
def delete_concept(concept_id: int):
    concept = Concept.query.join(Project).filter(
        Concept.id == concept_id, Project.user_id == current_user.id
    ).first()
    if not concept:
        return _not_found("Concept")

    project = concept.project
    db.session.delete(concept)
    db.session.flush()

    remaining = sorted(project.concepts, key=lambda c: c.order_index)
    for idx, c in enumerate(remaining):
        c.order_index = idx

    if project.active_concept_id == concept_id:
        project.active_concept_id = remaining[0].id if remaining else None

    db.session.commit()
    return jsonify(
        {
            "deleted": concept_id,
            "project": serialize_project(project),
        }
    )


@bp.route("/stock/search")
@login_required
def stock_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Missing query."}), 400
    service = _get_stock_service()
    results = service.search(query)
    return jsonify({"results": results})


@bp.route("/projects/<int:project_id>/concepts/rag", methods=["POST"])
@login_required
def generate_contextual_concept(project_id: int):
    project = _project_for_user(project_id)
    if not project:
        return _not_found("Project")

    rag_service = _get_rag_service()
    if not rag_service or not getattr(rag_service, "is_enabled", False):
        current_app.logger.warning(
            "Concept Lab request blocked: service unavailable",
            extra={"project_id": project_id, "rag_status": getattr(rag_service, "debug_status", lambda: None)()},
        )
        return jsonify({"error": "Concept lab is not configured for this deployment."}), 503
    if not project.supabase_book_id:
        current_app.logger.info(
            "Concept Lab request blocked: indexing pending",
            extra={"project_id": project_id, "book_id": project.supabase_book_id},
        )
        return jsonify({"error": "This book is still indexing. Try again in a moment."}), 409

    payload = request.get_json(silent=True) or {}
    random_slice_mode = bool(payload.get("random_slice"))
    raw_context = payload.get("context")
    context = "" if random_slice_mode else (raw_context or "").strip()
    concept_id = None
    if not random_slice_mode:
        raw_concept_id = payload.get("concept_id")
        if raw_concept_id not in (None, "__none__", "__random__"):
            try:
                concept_id = int(raw_concept_id)
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid concept id."}), 400
            exists = Concept.query.filter_by(id=concept_id, project_id=project.id).first()
            if not exists:
                return _not_found("Concept")
        if not context and concept_id is None:
            return jsonify({"error": "Add additional context or pick a concept to mirror."}), 400
    else:
        arousal_client = _get_arousal_client()
        if not arousal_client or not getattr(arousal_client, "is_ready", False):
            return jsonify({"error": "Random slice mode is not available right now."}), 503

    sanitized_payload = {
        "context": context,
        "concept_id": concept_id,
        "random_slice": random_slice_mode,
    }

    job_payload, error_message, error_status = _start_concept_lab_job(
        project=project,
        payload=sanitized_payload,
        user_id=current_user.id,
    )
    if error_message or not job_payload:
        return jsonify({"error": error_message or "Failed to start concept lab job."}), error_status or 503

    return jsonify({"job": job_payload}), 202


@bp.route("/projects/<int:project_id>/download", methods=["GET"])
@login_required
def download_project(project_id: int):
    project = _project_for_user(project_id)
    if not project:
        return _not_found("Project")
    concept_id = request.args.get("concept_id", type=int)
    concept = _resolve_concept(project, concept_id)
    if not concept:
        return jsonify({"error": "Concept not found for this project."}), 404
    voice = request.args.get("voice")
    normalized_voice = _normalize_voice(voice if voice is not None else project.voice)

    if not _should_use_queue():
        return _render_project_response(project, concept.id if concept else None, normalized_voice)

    job_payload, error_message = _enqueue_render_job(
        project=project,
        concept=concept,
        voice=normalized_voice,
        user_id=current_user.id,
    )
    if error_message or not job_payload:
        return jsonify({"error": error_message or "Failed to enqueue render job."}), 503
    return jsonify({"job": job_payload}), 202


@bp.route("/concept-jobs/<string:job_id>", methods=["GET"])
@login_required
def get_concept_job(job_id: str):
    job = fetch_concept_job(current_app, job_id)
    if not job or job.get("user_id") != current_user.id:
        return jsonify({"error": "Concept job not found."}), 404
    return jsonify({"job": job})


def _render_project_response(project, concept_id: int | None, voice: str | None):
    renderer = _get_renderer()

    try:
        render_warnings: list[str] = []
        video_path = renderer.render_project(
            project,
            concept_id=concept_id,
            voice=voice,
            warnings=render_warnings,
        )
    except Exception as exc:
        current_app.logger.exception("Render failed for project=%s concept=%s", project.id, concept_id)
        return jsonify({"error": f"Failed to render video: {exc}"}), 500

    if not video_path or not Path(video_path).exists():
        return jsonify({"error": "Rendered video missing"}), 500

    file_size = os.path.getsize(video_path)

    def generate():
        try:
            with open(video_path, "rb") as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                Path(video_path).unlink(missing_ok=True)
            except OSError:
                pass

    response = Response(generate(), mimetype="video/mp4")
    response.headers["Content-Length"] = str(file_size)
    response.headers["Content-Encoding"] = "identity"
    response.direct_passthrough = True

    filename = f"project-{project.id}-concept-{concept_id}.mp4"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    response.headers["Cache-Control"] = "no-store"
    if render_warnings:
        response.headers["X-LitReel-Render-Warnings"] = " | ".join(render_warnings)

    return response


@bp.route("/downloads/<string:job_id>", methods=["GET"])
@login_required
def get_render_job(job_id: str):
    job = fetch_job(current_app, job_id)
    if not job or job.get("requested_by") != current_user.id:
        return _not_found("Render job")
    return jsonify({"job": job})


@bp.route("/downloads/<string:job_id>/file", methods=["GET"])
@login_required
def download_render_job(job_id: str):
    job = fetch_job(current_app, job_id)
    if not job or job.get("requested_by") != current_user.id:
        return _not_found("Render job")
    download_type = job.get("download_type")
    filename = job.get("suggested_filename") or f"litreel-{job_id}.mp4"
    if download_type == "url" and job.get("download_url"):
        _record_render_download(job_id)
        return redirect(job["download_url"])
    if download_type == "file":
        storage_path = job.get("storage_path")
        if not storage_path or not Path(storage_path).exists():
            return jsonify({"error": "Render file missing. Please re-render."}), 410
        response = send_file(
            storage_path,
            mimetype="video/mp4",
            as_attachment=True,
            download_name=filename,
            max_age=0,
        )
        response.headers["Cache-Control"] = "no-store"
        _record_render_download(job_id)
        return response
    if download_type != "blob":
        return jsonify({"error": "Render file is not available yet."}), 409
    blob = fetch_blob(current_app, job_id)
    if not blob:
        return jsonify({"error": "Render file expired. Please generate again."}), 410
    buffer = io.BytesIO(blob)
    buffer.seek(0)
    response = send_file(
        buffer,
        mimetype="video/mp4",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store"
    _record_render_download(job_id)
    return response


def serialize_project(project: Project) -> dict:
    return {
        "id": project.id,
        "title": project.title,
        "status": project.status,
        "voice": getattr(project, "voice", "sarah"),
        "active_concept_id": getattr(project, "active_concept_id", None),
        "created_at": project.created_at.isoformat(),
        "supabase_book_id": project.supabase_book_id,
        "concepts": [serialize_concept(c) for c in sorted(project.concepts, key=lambda c: c.order_index)],
    }


def serialize_concept(concept: Concept) -> dict:
    return {
        "id": concept.id,
        "name": concept.name,
        "description": concept.description,
        "order_index": concept.order_index,
        "slides": [serialize_slide(s) for s in sorted(concept.slides, key=lambda s: s.order_index)],
        "latest_render": serialize_render_artifact(_latest_render_for_concept(concept)),
    }


def serialize_slide(slide: Slide) -> dict:
    return {
        "id": slide.id,
        "text": slide.text,
        "order_index": slide.order_index,
        "image_url": slide.image_url,
        "effect": slide.effect,
        "transition": slide.transition,
        "style": slide.style_dict,
    }


def serialize_render_artifact(artifact: RenderArtifact | None) -> dict | None:
    if not artifact:
        return None
    return {
        "job_id": artifact.job_id,
        "status": artifact.status,
        "download_type": artifact.download_type,
        "download_url": artifact.download_url,
        "file_size": artifact.file_size,
        "suggested_filename": artifact.suggested_filename,
        "completed_at": artifact.completed_at.isoformat() if artifact.completed_at else None,
        "updated_at": artifact.updated_at.isoformat() if artifact.updated_at else None,
        "voice": artifact.voice,
        "cache_hit": artifact.cache_hit,
    }


def _latest_render_for_concept(concept: Concept) -> RenderArtifact | None:
    artifacts = getattr(concept, "render_artifacts", None) or []
    for artifact in artifacts:
        if artifact.status == "ready":
            return artifact
    return artifacts[0] if artifacts else None


def _record_render_download(job_id: str) -> None:
    artifact = RenderArtifact.query.filter_by(job_id=job_id).first()
    if not artifact:
        return
    artifact.download_count = (artifact.download_count or 0) + 1
    db.session.add(artifact)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


@bp.route("/concepts/<int:concept_id>/slides", methods=["POST"])
@login_required
def create_slide(concept_id: int):
    concept = Concept.query.join(Project).filter(
        Concept.id == concept_id, Project.user_id == current_user.id
    ).first()
    if not concept:
        return _not_found("Concept")

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    order_index = (payload.get("order_index") if payload.get("order_index") is not None else None)

    if order_index is None:
        last = (
            Slide.query.filter_by(concept_id=concept.id)
            .order_by(Slide.order_index.desc())
            .first()
        )
        order_index = (last.order_index + 1) if last else 0

    slide = Slide(concept_id=concept.id, text=text, order_index=order_index)
    db.session.add(slide)
    db.session.flush()
    db.session.add(SlideStyle(slide_id=slide.id))
    db.session.commit()

    return jsonify({"slide": serialize_slide(slide), "concept": serialize_concept(concept)}), 201


api_bp = bp
