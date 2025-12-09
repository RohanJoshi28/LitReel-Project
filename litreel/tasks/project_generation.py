from __future__ import annotations

import concurrent.futures
from typing import Any

from flask import current_app

from ..extensions import db
from ..models import Concept, Project, Slide, SlideStyle
from ..services.local_slides import FallbackOptions, build_local_concepts
from .utils import ensure_app_context


def generate_project_job(
    project_id: int,
    *,
    user_id: int,
    title: str,
    raw_text: str,
    fallback_max_concepts: int | None = None,
    fallback_slides_per_concept: int | None = None,
) -> dict[str, Any]:
    """Background job that runs Gemini + fallback generation for a project."""
    app, ctx = ensure_app_context()
    try:
        app.logger.info(
            "async_generation_job_start",
            extra={
                "project_id": project_id,
                "user_id": user_id,
                "title": title,
                "text_chars": len(raw_text or ""),
            },
        )
        return _generate_within_context(
            app,
            project_id=project_id,
            user_id=user_id,
            title=title,
            raw_text=raw_text,
            fallback_opts=FallbackOptions(
                max_concepts=fallback_max_concepts or 3,
                slides_per_concept=fallback_slides_per_concept or 8,
            ),
        )
    finally:
        if ctx is not None:
            ctx.pop()


def _generate_within_context(app, *, project_id: int, user_id: int, title: str, raw_text: str, fallback_opts: FallbackOptions):
    if not raw_text:
        app.logger.error("async_generation_missing_text", extra={"project_id": project_id})
        return {"status": "failed", "project_id": project_id}

    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        app.logger.warning("async_generation_missing_project", extra={"project_id": project_id, "user_id": user_id})
        return {"status": "missing", "project_id": project_id}

    project.status = "processing"
    db.session.commit()
    app.logger.info(
        "async_generation_project_processing",
        extra={"project_id": project_id, "status": project.status},
    )

    generator = app.config["GEMINI_SERVICE"]
    rag_service = app.config.get("RAG_SERVICE")
    rag_enabled = bool(rag_service and getattr(rag_service, "is_enabled", True))
    rag_can_background = bool(getattr(rag_service, "can_background_ingest", True)) if rag_service else False

    concepts = None
    supabase_book_id = None
    fallback_used = False

    rag_future = None
    rag_ingest_completed = False
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            gemini_future = executor.submit(generator.generate_from_text, raw_text)
            if rag_service and rag_enabled and rag_can_background:
                rag_future = executor.submit(rag_service.ingest_book, title=title, text=raw_text)
            concepts = gemini_future.result()
            app.logger.info(
                "async_generation_gemini_complete",
                extra={"project_id": project_id, "concept_count": len(getattr(concepts, "concepts", []) or [])},
            )
    except Exception as exc:  # pragma: no cover - relies on remote services
        app.logger.exception(
            "async_generation_gemini_failed", extra={"project_id": project_id, "error": str(exc)}
        )
        concepts = None
    finally:
        if rag_future:
            rag_ingest_completed = True
            try:
                supabase_book_id = rag_future.result()
                app.logger.info(
                    "async_generation_supabase_complete",
                    extra={"project_id": project_id, "supabase_book_id": supabase_book_id, "mode": "async"},
                )
            except Exception as rag_exc:  # pragma: no cover - external call
                app.logger.exception(
                    "async_generation_supabase_failed",
                    extra={"project_id": project_id, "error": str(rag_exc), "mode": "async"},
                )

    if (
        not rag_ingest_completed
        and rag_service
        and rag_enabled
    ):
        try:
            supabase_book_id = rag_service.ingest_book(title=title, text=raw_text)
            rag_ingest_completed = True
            app.logger.info(
                "async_generation_supabase_complete",
                extra={"project_id": project_id, "supabase_book_id": supabase_book_id, "mode": "sync"},
            )
        except Exception as rag_exc:  # pragma: no cover - external call
            app.logger.exception(
                "async_generation_supabase_failed",
                extra={"project_id": project_id, "error": str(rag_exc), "mode": "sync"},
            )

    if concepts is None:
        try:
            concepts = build_local_concepts(raw_text=raw_text, options=fallback_opts)
            fallback_used = True
        except Exception as fallback_exc:  # pragma: no cover - deterministic fallback
            project.status = "failed"
            db.session.commit()
            app.logger.exception("async_generation_fallback_failed", extra={"project_id": project_id})
            raise fallback_exc

    if not concepts or not getattr(concepts, "concepts", None):
        project.status = "failed"
        db.session.commit()
        app.logger.error("async_generation_empty_concepts", extra={"project_id": project_id})
        return {"status": "failed", "project_id": project_id}

    try:
        # Clear stale concepts if a retry is running.
        for existing in list(project.concepts):
            db.session.delete(existing)
        db.session.flush()

        for idx, concept_data in enumerate(concepts.concepts):
            concept = Concept(
                project_id=project.id,
                name=concept_data.name,
                description=concept_data.description,
                order_index=idx,
            )
            db.session.add(concept)
            db.session.flush()

            for slide_idx, text in enumerate(concept_data.slides):
                slide = Slide(
                    concept_id=concept.id,
                    text=text,
                    order_index=slide_idx,
                )
                db.session.add(slide)
                db.session.flush()
                db.session.add(SlideStyle(slide_id=slide.id))

            if project.active_concept_id is None:
                project.active_concept_id = concept.id

        if supabase_book_id:
            project.supabase_book_id = supabase_book_id
        project.status = "generated-local" if fallback_used else "generated"
        db.session.commit()
        app.logger.info(
            "async_generation_complete",
            extra={
                "project_id": project_id,
                "status": project.status,
                "concepts": len(project.concepts or []),
                "fallback_used": fallback_used,
                "supabase_book_id": supabase_book_id,
            },
        )
    except Exception as exc:  # pragma: no cover - defensive DB guard
        db.session.rollback()
        try:
            failed_project = Project.query.filter_by(id=project_id).first()
            if failed_project:
                failed_project.status = "failed"
                db.session.commit()
        except Exception:
            db.session.rollback()
        app.logger.exception("async_generation_persist_failed", extra={"project_id": project_id, "error": str(exc)})
        raise

    return {"status": project.status, "project_id": project_id, "fallback_used": fallback_used}


__all__ = ["generate_project_job"]
