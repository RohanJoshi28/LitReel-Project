from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .gemini_runner import BookConcepts, SlideConcept
from .pdf_parser import extract_text_from_document


@dataclass
class FallbackOptions:
    max_concepts: int = 3
    slides_per_concept: int = 8


def build_local_concepts(
    document_path=None, *, raw_text: str | None = None, options: FallbackOptions | None = None
) -> BookConcepts:
    """
    Deterministic, offline fallback generator so uploads keep working even
    when Gemini is unavailable or misconfigured.
    """
    opts = options or FallbackOptions()
    if raw_text:
        text = raw_text
    else:
        if document_path is None:
            raise ValueError("Either document_path or raw_text is required.")
        text = extract_text_from_document(document_path)
    paragraphs = _candidate_paragraphs(text)

    concepts: list[SlideConcept] = []
    for index, paragraph in enumerate(paragraphs[: opts.max_concepts]):
        slides = _slides_from_chunk(paragraph, opts.slides_per_concept)
        if not slides:
            continue
        title = _title_from_chunk(paragraph, index)
        description = _description_from_chunk(paragraph)
        concepts.append(
            SlideConcept(name=title, description=description, slides=slides)
        )

    if not concepts:
        slides = _slides_from_chunk(text, opts.slides_per_concept)
        if slides:
            concepts.append(
                SlideConcept(
                    name="Auto Generated Highlights",
                    description=(
                        "Locally generated story beats pulled directly from the book text."
                    ),
                    slides=slides,
                )
            )

    return BookConcepts(concepts=concepts)


def _candidate_paragraphs(raw_text: str) -> list[str]:
    cleaned = re.sub(r"\r", "", raw_text)
    chunks = re.split(r"\n{2,}", cleaned)
    return [
        chunk.strip()
        for chunk in chunks
        if len(chunk.strip().split()) >= 12
    ]


def _slides_from_chunk(chunk: str, max_slides: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", chunk.strip())
    trimmed = [_normalize_sentence(sentence) for sentence in sentences]
    slides = [line for line in trimmed if line]

    if not slides:
        slides = [_normalize_sentence(chunk)]

    primary = slides[:max_slides]
    if not primary:
        return []

    # Ensure hook slide feels strong.
    primary[0] = _hook_from_sentence(primary[0])

    return primary


def _normalize_sentence(sentence: str) -> str:
    stripped = " ".join(sentence.split())
    if not stripped:
        return ""
    if len(stripped) <= 150:
        return stripped
    return stripped[:147].rstrip() + "…"


def _hook_from_sentence(sentence: str) -> str:
    if len(sentence) <= 80:
        return sentence
    return sentence[:77].rstrip() + "…"


def _title_from_chunk(chunk: str, index: int) -> str:
    words = chunk.split()
    if not words:
        return f"Concept {index + 1}"
    return " ".join(words[:6]).strip().title()


def _description_from_chunk(chunk: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", chunk.strip())
    summary = " ".join(sentences[:2]).strip()
    if len(summary) <= 220:
        return summary
    return summary[:217].rstrip() + "…"
