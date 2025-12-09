from __future__ import annotations

from dataclasses import dataclass
from typing import List

from flask import current_app

from ..extensions import db
from ..models import Concept, Project, Slide, SlideStyle
from ..services.rag import SupabaseRagService


class ConceptLabJobError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ConceptLabPayload:
    context: str = ""
    concept_id: int | None = None
    random_slice: bool = False


def generate_concepts_for_project(
    *,
    project: Project,
    payload: ConceptLabPayload,
    rag_service,
    gemini_service,
    arousal_client,
) -> List[Concept]:
    if not project:
        raise ConceptLabJobError("Project not found.", 404)
    if not project.supabase_book_id:
        raise ConceptLabJobError("This book is still indexing. Try again in a moment.", 409)
    if not rag_service or not getattr(rag_service, "is_enabled", False):
        raise ConceptLabJobError("Concept lab is not configured for this deployment.", 503)

    random_slice_mode = bool(payload.random_slice)
    context = "" if random_slice_mode else (payload.context or "").strip()
    concept_id = payload.concept_id if not random_slice_mode else None
    selected_concept: Concept | None = None
    slides_text = ""
    rag_chunks: list[str] = []
    reference_name: str | None = None
    user_context = context

    if not random_slice_mode:
        if concept_id is not None:
            selected_concept = Concept.query.filter_by(id=concept_id, project_id=project.id).first()
            if not selected_concept:
                raise ConceptLabJobError("Concept not found for this project.", 404)
            slides_text = "\n".join(slide.text for slide in selected_concept.slides if slide.text)

        search_terms = [segment for segment in (slides_text.strip(), context) if segment]
        if not search_terms:
            raise ConceptLabJobError("Add additional context or pick a concept to mirror.", 400)

        search_query = "\n\n".join(search_terms)
        current_app.logger.info(
            "Concept Lab retrieval start",
            extra={
                "project_id": project.id,
                "book_id": project.supabase_book_id,
                "concept_id": concept_id,
                "context_length": len(context),
            },
        )
        try:
            rag_chunks = rag_service.get_relevant_chunks(project.supabase_book_id, search_query)
        except Exception as exc:  # pragma: no cover - remote failure guard
            current_app.logger.exception("Failed to fetch RAG chunks", exc_info=exc)
            raise ConceptLabJobError("Unable to search your book context right now.", 502)
        if not rag_chunks:
            current_app.logger.warning(
                "Concept Lab retrieval returned no chunks",
                extra={"project_id": project.id, "book_id": project.supabase_book_id},
            )
            raise ConceptLabJobError("No relevant passages were found for this request.", 404)
        reference_name = selected_concept.name if selected_concept else None
    else:
        if not arousal_client or not getattr(arousal_client, "is_ready", False):
            raise ConceptLabJobError("Random slice mode is not available right now.", 503)
        sample_size = int(current_app.config.get("RANDOM_SLICE_SAMPLE_SIZE", 75))
        top_k = int(current_app.config.get("RANDOM_SLICE_TOP_K", 12))
        current_app.logger.info(
            "Concept Lab random slice start",
            extra={
                "project_id": project.id,
                "book_id": project.supabase_book_id,
                "sample_chunks": sample_size,
                "top_k": top_k,
            },
        )
        rag_chunks = _select_random_arousal_chunks(
            project=project,
            rag_service=rag_service,
            arousal_client=arousal_client,
            sample_size=sample_size,
            top_k=top_k,
        )
        random_prompt = current_app.config.get(
            "RANDOM_SLICE_PROMPT",
            "You selected the random slice option. Use only the provided passages and craft at most two cohesive slideshow concepts that feel like a glimpse into an emotional peak of the book.",
        )
        user_context = (random_prompt or "").strip()

    generator = gemini_service
    try:
        rag_response = generator.generate_from_chunks(
            chunks=rag_chunks,
            reference_concept=reference_name,
            user_context=user_context,
        )
    except Exception as exc:  # pragma: no cover - runtime guard
        current_app.logger.exception("Gemini contextual generation failed", exc_info=exc)
        raise ConceptLabJobError("Unable to craft a new concept right now. Try again later.", 502)

    concepts_payload = list(rag_response.concepts or [])
    if random_slice_mode:
        concepts_payload = concepts_payload[:2]
    if not concepts_payload:
        raise ConceptLabJobError("Gemini returned no concepts for this request.", 502)

    next_index = max((concept.order_index for concept in project.concepts), default=-1) + 1
    created: list[Concept] = []
    for offset, concept_payload in enumerate(concepts_payload):
        concept = Concept(
            project_id=project.id,
            name=concept_payload.name,
            description=concept_payload.description,
            order_index=next_index + offset,
        )
        db.session.add(concept)
        db.session.flush()
        for slide_idx, text in enumerate(concept_payload.slides):
            slide = Slide(
                concept_id=concept.id,
                text=text,
                order_index=slide_idx,
            )
            db.session.add(slide)
            db.session.flush()
            db.session.add(SlideStyle(slide_id=slide.id))
        created.append(concept)

    db.session.commit()
    current_app.logger.info(
        "Concept Lab generation complete",
        extra={
            "project_id": project.id,
            "concept_ids": [c.id for c in created],
            "chunk_count": len(rag_chunks),
            "random_slice": random_slice_mode,
        },
    )
    return created


def _select_random_arousal_chunks(
    *,
    project: Project,
    rag_service: SupabaseRagService,
    arousal_client,
    sample_size: int,
    top_k: int,
) -> list[str]:
    if not project.supabase_book_id:
        raise ConceptLabJobError("This book is not ready for random sampling yet.", 409)
    sample_size = max(1, sample_size)
    top_k = max(1, top_k)
    chunks = rag_service.sample_random_chunks(project.supabase_book_id, sample_size)
    if not chunks:
        raise ConceptLabJobError("No indexed passages were available for this book yet.", 404)
    scoring_ratio = current_app.config.get("RANDOM_SLICE_SCORING_RATIO", 1.0)
    try:
        scoring_ratio = float(scoring_ratio)
    except (TypeError, ValueError):
        scoring_ratio = 1.0
    scoring_ratio = max(0.1, min(scoring_ratio, 1.0))
    scoring_count = max(top_k, int(len(chunks) * scoring_ratio))
    scoring_count = min(len(chunks), max(1, scoring_count))
    scoring_targets = list(chunks[:scoring_count])
    ranked = arousal_client.score_chunks(scoring_targets)
    if not ranked:
        raise ConceptLabJobError("Random slice scoring returned no results.", 502)
    return [entry.text for entry in ranked[:top_k]]


__all__ = [
    "ConceptLabPayload",
    "ConceptLabJobError",
    "generate_concepts_for_project",
]
