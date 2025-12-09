from __future__ import annotations

from flask import current_app

from ..concept_jobs import fetch_job as fetch_concept_job, update_job as update_concept_job
from ..extensions import db
from ..models import Project
from ..services.concept_lab_runner import (
    ConceptLabJobError,
    ConceptLabPayload,
    generate_concepts_for_project,
)


def process_concept_lab_job(job_id: str, project_id: int, payload: dict | None = None, user_id: int | None = None):
    payload = payload or {}
    app = current_app._get_current_object()
    job_snapshot = fetch_concept_job(app, job_id) or {}
    if not job_snapshot:
        update_concept_job(app, job_id, status="failed", error="Concept job metadata missing.")
        return
    if user_id and job_snapshot.get("user_id") and job_snapshot.get("user_id") != user_id:
        update_concept_job(app, job_id, status="failed", error="Unauthorized concept job request.")
        return

    update_concept_job(app, job_id, status="processing")

    project = db.session.get(Project, project_id)
    rag_service = current_app.config.get("RAG_SERVICE")
    gemini_service = current_app.config.get("GEMINI_SERVICE")
    arousal_client = current_app.config.get("AROUSAL_CLIENT")

    try:
        request_payload = ConceptLabPayload(
            context=str(payload.get("context") or ""),
            concept_id=payload.get("concept_id"),
            random_slice=bool(payload.get("random_slice")),
        )
        created = generate_concepts_for_project(
            project=project,
            payload=request_payload,
            rag_service=rag_service,
            gemini_service=gemini_service,
            arousal_client=arousal_client,
        )
        concept_ids = [concept.id for concept in created]
        update_concept_job(
            app,
            job_id,
            status="succeeded",
            concept_ids=concept_ids,
            project_id=project_id,
        )
    except ConceptLabJobError as exc:
        db.session.rollback()
        update_concept_job(app, job_id, status="failed", error=str(exc), status_code=exc.status_code)
    except Exception as exc:  # pragma: no cover - runtime guard
        db.session.rollback()
        current_app.logger.exception("concept_lab_job_failed", extra={"job_id": job_id})
        update_concept_job(app, job_id, status="failed", error="Concept generation failed.")


__all__ = ["process_concept_lab_job"]
