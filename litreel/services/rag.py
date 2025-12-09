from __future__ import annotations

import json
import logging
import math
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable, Iterator, Sequence, TypeVar

T = TypeVar("T")

from google import genai
from google.genai import types
from ..models import Book as ORMBook, BookChunk as ORMBookChunk
from ..supabase_client import Client, SUPABASE_SDK_AVAILABLE, create_supabase_client


DEFAULT_EMBED_PARALLELISM = 8


class BaseRagService:
    can_background_ingest = True

    def __init__(
        self,
        *,
        gemini_api_key: str,
        embedding_model: str,
        default_match_count: int = 6,
        chunk_size_words: int = 220,
        chunk_overlap_words: int = 60,
        max_chunks: int = 256,
        insert_batch_size: int = 64,
        embed_parallelism: int = DEFAULT_EMBED_PARALLELISM,
        gemini_client: genai.Client | None = None,
    ) -> None:
        self.gemini_api_key = (gemini_api_key or "").strip()
        self.embedding_model = (embedding_model or "").strip()
        self.default_match_count = max(1, default_match_count or 6)
        self.chunk_size_words = max(80, chunk_size_words or 220)
        self.chunk_overlap_words = max(10, min(self.chunk_size_words // 2, chunk_overlap_words or 60))
        self.max_chunks = max(1, max_chunks or 256)
        self.insert_batch_size = max(1, insert_batch_size or 64)
        self.embed_parallelism = max(1, embed_parallelism or DEFAULT_EMBED_PARALLELISM)
        self._gemini: genai.Client | None = gemini_client
        self._logger = logging.getLogger(self.__class__.__name__)

    @property
    def is_enabled(self) -> bool:
        return bool(self.gemini_api_key and self.embedding_model)

    def _gemini_client(self) -> genai.Client:
        if self._gemini is None:
            if not self.gemini_api_key:
                raise RuntimeError("Gemini API key missing for RAG ingestion.")
            self._gemini = genai.Client(api_key=self.gemini_api_key)
        return self._gemini

    def _batch_embed(self, chunks: Sequence[str], title: str) -> list[list[float]]:
        del title  # retained for API compatibility
        if not chunks:
            return []
        if len(chunks) == 1 or self.embed_parallelism <= 1:
            return self._embed_chunks_sequential(chunks)
        return self._embed_chunks_parallel(chunks)

    def _embed_chunks_sequential(self, chunks: Sequence[str]) -> list[list[float]]:
        return [self._embed_single_chunk(chunk) for chunk in chunks]

    def _embed_chunks_parallel(self, chunks: Sequence[str]) -> list[list[float]]:
        worker_count = max(1, min(self.embed_parallelism, len(chunks)))
        ordered: list[list[float] | None] = [None] * len(chunks)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures_map = {
                executor.submit(self._embed_single_chunk, chunk): idx for idx, chunk in enumerate(chunks)
            }
            for future in as_completed(futures_map):
                idx = futures_map[future]
                ordered[idx] = future.result()
        return [vector if vector is not None else [] for vector in ordered]

    def _embed_single_chunk(self, chunk: str) -> list[float]:
        client = self._gemini_client()
        response = client.models.embed_content(
            model=self.embedding_model,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part(text=chunk)],
                )
            ],
            config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
        )
        embeddings = list(response.embeddings or [])
        if not embeddings:
            raise RuntimeError("Missing embedding for chunk.")
        return list(embeddings[0].values)

    def _embed_query(self, text: str) -> list[float]:
        client = self._gemini_client()
        result = client.models.embed_content(
            model=self.embedding_model,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part(text=text)],
                )
            ],
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
        )
        embeddings = list(result.embeddings or [])
        if not embeddings:
            raise RuntimeError("Query embedding missing from Gemini response.")
        return list(embeddings[0].values)

    def _chunk_text(self, text: str) -> Iterator[str]:
        words = text.split()
        if not words:
            return
        step = self.chunk_size_words
        overlap = self.chunk_overlap_words
        chunks_emitted = 0
        for start in range(0, len(words), step - overlap):
            end = min(len(words), start + step)
            chunk_words = words[start:end]
            chunk = " ".join(chunk_words).strip()
            if chunk:
                yield chunk
                chunks_emitted += 1
            if end >= len(words) or chunks_emitted >= self.max_chunks:
                break

    @staticmethod
    def _normalize_text(raw: str) -> str:
        stripped = " ".join((raw or "").split())
        return stripped.strip()


class SupabaseRagService(BaseRagService):
    def __init__(
        self,
        *,
        supabase_url: str,
        supabase_key: str,
        gemini_api_key: str,
        embedding_model: str,
        book_table: str = "book",
        chunk_table: str = "book_chunk",
        chunk_text_column: str = "content",
        match_function: str = "match_book_chunks",
        default_match_count: int = 6,
        chunk_size_words: int = 220,
        chunk_overlap_words: int = 60,
        max_chunks: int = 256,
        insert_batch_size: int = 64,
        embed_parallelism: int = DEFAULT_EMBED_PARALLELISM,
        supabase_client: Client | None = None,
        gemini_client: genai.Client | None = None,
    ) -> None:
        super().__init__(
            gemini_api_key=gemini_api_key,
            embedding_model=embedding_model,
            default_match_count=default_match_count,
            chunk_size_words=chunk_size_words,
            chunk_overlap_words=chunk_overlap_words,
            max_chunks=max_chunks,
            insert_batch_size=insert_batch_size,
            embed_parallelism=embed_parallelism,
            gemini_client=gemini_client,
        )
        self.supabase_url = (supabase_url or "").strip()
        self.supabase_key = (supabase_key or "").strip()
        self.book_table = book_table or "book"
        self.chunk_table = chunk_table or "book_chunk"
        self.chunk_text_column = chunk_text_column or "content"
        self.match_function = match_function or "match_book_chunks"
        self._supabase: Client | None = supabase_client
        self._has_supabase_sdk = SUPABASE_SDK_AVAILABLE
        self._logger = logging.getLogger("SupabaseRagService")

    @property
    def is_enabled(self) -> bool:
        supabase_ready = self._supabase is not None or bool(self.supabase_url and self.supabase_key)
        return bool(
            supabase_ready
            and (self.supabase_url or self._supabase is not None)
            and (self.supabase_key or self._supabase is not None)
            and super().is_enabled
        )

    def ingest_book(self, *, title: str, text: str) -> str | None:
        if not self.is_enabled:
            self._logger.warning(
                "Supabase RAG disabled, skipping ingestion for %s status=%s",
                title,
                self.debug_status(),
            )
            return None
        normalized_title = (title or "").strip() or "Untitled Manuscript"
        cleaned = self._normalize_text(text)
        if not cleaned:
            self._logger.warning("Supabase RAG skipped: empty text for %s", normalized_title)
            return None
        self._logger.info(
            "Supabase RAG ingest start",
            extra={"book_title": normalized_title, "text_length": len(cleaned)},
        )
        supabase = self._supabase_client()
        response = supabase.table(self.book_table).insert({"title": normalized_title}).execute()
        data = self._extract_data(response, action="book insert")
        book_id = str(data[0].get("id"))
        chunks = list(self._chunk_text(cleaned))
        if not chunks:
            self._logger.info("Supabase RAG ingest: no chunks generated for %s", book_id)
            return book_id
        embeddings = self._batch_embed(chunks, normalized_title)
        records = []
        for chunk_text, embedding in zip(chunks, embeddings):
            record = {
                "book_id": book_id,
                "chunk": embedding,
            }
            record[self.chunk_text_column] = chunk_text
            records.append(record)
        for batch in _batched(records, self.insert_batch_size):
            response = supabase.table(self.chunk_table).insert(batch).execute()
            self._ensure_ok(response, action="chunk insert")
        self._logger.info(
            "Supabase RAG ingest finished",
            extra={"book_id": book_id, "chunks_written": len(records)},
        )
        return book_id

    def delete_book(self, book_id: str | None) -> None:
        if not self.is_enabled or not book_id:
            return
        try:
            supabase = self._supabase_client()
            chunk_resp = supabase.table(self.chunk_table).delete().eq("book_id", book_id).execute()
            self._ensure_ok(chunk_resp, action="chunk delete", require_data=False)
            book_resp = supabase.table(self.book_table).delete().eq("id", book_id).execute()
            self._ensure_ok(book_resp, action="book delete", require_data=False)
            self._logger.info("Supabase RAG deleted book=%s", book_id)
        except Exception as exc:  # pragma: no cover - best effort cleanup
            self._logger.warning("Failed to delete Supabase book %s: %s", book_id, exc)

    def get_relevant_chunks(
        self,
        book_id: str,
        query_text: str,
        match_count: int | None = None,
    ) -> list[str]:
        if not self.is_enabled:
            raise RuntimeError("Supabase RAG service is not configured.")
        cleaned = self._normalize_text(query_text)
        if not cleaned:
            return []
        embedding = self._embed_query(cleaned)
        payload = {
            "embedding": embedding,
            "match_count": match_count or self.default_match_count,
            "book_id": book_id,
        }
        response = self._supabase_client().rpc(self.match_function, payload).execute()
        rows = self._extract_data(response, action="chunk query", require_data=False)
        chunks: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = (
                row.get(self.chunk_text_column)
                or row.get("content")
                or row.get("text")
                or row.get("chunk_text")
            )
            if text:
                chunks.append(str(text))
        self._logger.info(
            "Supabase RAG retrieval complete",
            extra={"book_id": book_id, "matches": len(chunks)},
        )
        return chunks

    def sample_random_chunks(self, book_id: str, sample_size: int = 75) -> list[str]:
        if not self.is_enabled or not book_id:
            return []
        supabase = self._supabase_client()
        id_response = (
            supabase.table(self.chunk_table)
            .select("id")
            .eq("book_id", book_id)
            .execute()
        )
        rows = self._extract_data(id_response, action="random chunk id fetch", require_data=False)
        chunk_ids = [row.get("id") for row in rows if row.get("id") is not None]
        if not chunk_ids:
            return []
        if sample_size <= 0 or sample_size >= len(chunk_ids):
            selected_ids = list(chunk_ids)
        else:
            selected_ids = random.sample(chunk_ids, sample_size)
        collected: list[str] = []
        for batch in _batched(selected_ids, self.insert_batch_size):
            query = (
                supabase.table(self.chunk_table)
                .select(f"id,{self.chunk_text_column}")
                .in_("id", batch)
            )
            response = query.execute()
            data = self._extract_data(response, action="random chunk fetch", require_data=False)
            lookup = {
                str(row.get("id")): str(
                    row.get(self.chunk_text_column)
                    or row.get("content")
                    or row.get("text")
                    or row.get("chunk_text")
                    or ""
                )
                for row in data
                if row.get("id") is not None
            }
            for chunk_id in batch:
                text = lookup.get(str(chunk_id))
                if text:
                    collected.append(text)
        return collected

    def _supabase_client(self) -> Client:
        if self._supabase is None:
            if not self.supabase_url or not self.supabase_key:
                raise RuntimeError("Supabase credentials are missing.")
            self._supabase = create_supabase_client(self.supabase_url, self.supabase_key)
        return self._supabase

    def _ensure_ok(self, response, *, action: str, require_data: bool = False):
        error = getattr(response, "error", None)
        if error:
            message = str(error)
            if "column" in message and self.chunk_text_column in message:
                message = (
                    f"{message} (ensure the '{self.chunk_text_column}' text column exists on "
                    f"{self.chunk_table})"
                )
            raise RuntimeError(f"{action} failed: {message}")
        if require_data and not getattr(response, "data", None):
            raise RuntimeError(f"{action} returned no data.")

    def _extract_data(self, response, *, action: str, require_data: bool = True):
        self._ensure_ok(response, action=action, require_data=require_data)
        data = getattr(response, "data", None) or []
        return data

    def debug_status(self) -> dict:
        return {
            "enabled": self.is_enabled,
            "has_supabase_sdk": self._has_supabase_sdk,
            "supabase_url_set": bool(self.supabase_url),
            "supabase_key_set": bool(self.supabase_key),
            "chunk_table": self.chunk_table,
            "chunk_text_column": self.chunk_text_column,
            "embed_model": self.embedding_model,
            "embed_parallelism": self.embed_parallelism,
        }


class LocalRagService(BaseRagService):
    can_background_ingest = False

    def __init__(
        self,
        *,
        session,
        gemini_api_key: str,
        embedding_model: str,
        default_match_count: int = 6,
        chunk_size_words: int = 220,
        chunk_overlap_words: int = 60,
        max_chunks: int = 256,
        insert_batch_size: int = 64,
        embed_parallelism: int = DEFAULT_EMBED_PARALLELISM,
        gemini_client: genai.Client | None = None,
        book_model=ORMBook,
        chunk_model=ORMBookChunk,
    ) -> None:
        super().__init__(
            gemini_api_key=gemini_api_key,
            embedding_model=embedding_model,
            default_match_count=default_match_count,
            chunk_size_words=chunk_size_words,
            chunk_overlap_words=chunk_overlap_words,
            max_chunks=max_chunks,
            insert_batch_size=insert_batch_size,
            embed_parallelism=embed_parallelism,
            gemini_client=gemini_client,
        )
        self._session = session
        self._book_model = book_model
        self._chunk_model = chunk_model
        self._logger = logging.getLogger("LocalRagService")

    def ingest_book(self, *, title: str, text: str) -> str | None:
        if not self.is_enabled:
            self._logger.warning("Local RAG disabled, skipping ingestion for %s", title)
            return None
        normalized_title = (title or "").strip() or "Untitled Manuscript"
        cleaned = self._normalize_text(text)
        if not cleaned:
            self._logger.warning("Local RAG skipped: empty text for %s", normalized_title)
            return None
        book = self._book_model(title=normalized_title)
        self._session.add(book)
        self._session.flush()
        chunks = list(self._chunk_text(cleaned))
        if not chunks:
            self._session.commit()
            return str(book.id)
        embeddings = self._batch_embed(chunks, normalized_title)
        for chunk_text, embedding in zip(chunks, embeddings):
            chunk_row = self._chunk_model(
                book_id=book.id,
                content=chunk_text,
                embedding=json.dumps(embedding),
            )
            self._session.add(chunk_row)
        self._session.commit()
        self._logger.info(
            "Local RAG ingest finished",
            extra={"book_id": book.id, "chunks_written": len(chunks)},
        )
        return str(book.id)

    def delete_book(self, book_id: str | None) -> None:
        if not book_id:
            return
        internal_id = self._coerce_book_id(book_id)
        if internal_id is None:
            return
        book = self._session.get(self._book_model, internal_id)
        if not book:
            return
        self._session.delete(book)
        self._session.commit()

    def get_relevant_chunks(
        self,
        book_id: str,
        query_text: str,
        match_count: int | None = None,
    ) -> list[str]:
        if not self.is_enabled:
            return []
        cleaned = self._normalize_text(query_text)
        if not cleaned:
            return []
        internal_id = self._coerce_book_id(book_id)
        if internal_id is None:
            return []
        rows = (
            self._session.query(self._chunk_model)
            .filter(self._chunk_model.book_id == internal_id)
            .all()
        )
        if not rows:
            return []
        query_embedding = self._embed_query(cleaned)
        scored: list[tuple[float, str]] = []
        for row in rows:
            vector = row.embedding_vector()
            if not vector:
                continue
            score = self._cosine_similarity(vector, query_embedding)
            scored.append((score, row.content))
        scored.sort(key=lambda item: item[0], reverse=True)
        limit = match_count or self.default_match_count
        return [text for _, text in scored[:limit]]

    def sample_random_chunks(self, book_id: str, sample_size: int = 75) -> list[str]:
        internal_id = self._coerce_book_id(book_id)
        if internal_id is None:
            return []
        rows = (
            self._session.query(self._chunk_model)
            .filter(self._chunk_model.book_id == internal_id)
            .all()
        )
        if not rows:
            return []
        total = len(rows)
        if sample_size <= 0 or sample_size >= total:
            return [row.content for row in rows]
        selected = random.sample(rows, sample_size)
        return [row.content for row in selected]

    def debug_status(self) -> dict:
        total_books = self._session.query(self._book_model).count()
        total_chunks = self._session.query(self._chunk_model).count()
        return {
            "enabled": self.is_enabled,
            "books": total_books,
            "chunks": total_chunks,
            "embed_model": self.embedding_model,
            "embed_parallelism": self.embed_parallelism,
        }

    @property  # type: ignore[override]
    def is_enabled(self) -> bool:
        return super().is_enabled

    @staticmethod
    def _coerce_book_id(book_id: str | None) -> int | None:
        try:
            return int(str(book_id))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


def _batched(items: Sequence[T], size: int) -> Iterable[list[T]]:
    if size <= 0:
        size = 1
    total = len(items)
    for start in range(0, total, size):
        yield list(items[start : start + size])
