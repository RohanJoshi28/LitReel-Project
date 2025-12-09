from types import MethodType, SimpleNamespace
import time

import pytest

from litreel.services.rag import SupabaseRagService


class FakeSupabase:
    def __init__(self):
        self.rows = {"book": [], "book_chunk": []}
        self.deleted_ids: list[str] = []
        self.error_on_book = None
        self.error_on_chunk = None
        self.rpc_calls: list[dict] = []
        self._current_table = None
        self._payload = None
        self._delete_filter = None
        self._filters = None
        self._select_fields = "*"
        self._in_filter = None

    def _reset_builder(self):
        self._payload = None
        self._filters = None
        self._in_filter = None
        self._select_fields = "*"
        self._action = None

    def table(self, name):
        self._current_table = name
        self._reset_builder()
        return self

    def insert(self, payload):
        self._payload = payload
        self._action = "insert"
        return self

    def delete(self):
        self._delete_filter = {}
        self._action = "delete"
        return self

    def select(self, columns="*"):
        self._select_fields = columns or "*"
        self._action = "select"
        return self

    def eq(self, field, value):
        if self._action == "delete":
            if self._delete_filter is not None:
                self._delete_filter[field] = value
        else:
            if self._filters is None:
                self._filters = {}
            self._filters[field] = value
        return self

    def in_(self, field, values):
        self._in_filter = (field, list(values))
        return self

    def execute(self):
        if self._delete_filter is not None:
            filters = dict(self._delete_filter)
            table = self._current_table
            self._delete_filter = None
            if table == "book":
                book_id = filters.get("id")
                self.deleted_ids.append(book_id)
                self.rows["book"] = [row for row in self.rows["book"] if row["id"] != book_id]
                return SimpleNamespace(data=[], error=None)
            if table == "book_chunk":
                book_id = filters.get("book_id")
                self.rows["book_chunk"] = [
                    row for row in self.rows["book_chunk"] if row["book_id"] != book_id
                ]
                self._reset_builder()
                return SimpleNamespace(data=[], error=None)
            return SimpleNamespace(data=None, error=None)

        if self._current_table == "book":
            if self.error_on_book:
                self._reset_builder()
                return SimpleNamespace(data=None, error=self.error_on_book)
            book_id = f"book-{len(self.rows['book']) + 1}"
            self.rows["book"].append({"id": book_id, **self._payload})
            self._reset_builder()
            return SimpleNamespace(data=[{"id": book_id}], error=None)

        if self._current_table == "book_chunk":
            if self._action == "select":
                rows = list(self.rows["book_chunk"])
                if self._filters:
                    for field, value in self._filters.items():
                        rows = [row for row in rows if row.get(field) == value]
                if self._in_filter:
                    field, values = self._in_filter
                    rows = [row for row in rows if row.get(field) in values]
                if self._select_fields and self._select_fields != "*":
                    fields = [field.strip() for field in self._select_fields.split(",") if field.strip()]
                    selected = [
                        {field: row.get(field) for field in fields}
                        for row in rows
                    ]
                else:
                    selected = [dict(row) for row in rows]
                self._filters = None
                self._in_filter = None
                self._select_fields = "*"
                self._action = None
                return SimpleNamespace(data=selected, error=None)
            if self.error_on_chunk:
                self._reset_builder()
                return SimpleNamespace(data=None, error=self.error_on_chunk)
            rows = self._payload if isinstance(self._payload, list) else [self._payload]
            for row in rows:
                if "id" not in row:
                    row["id"] = f"chunk-{len(self.rows['book_chunk']) + 1}"
                self.rows["book_chunk"].append(row)
            self._reset_builder()
            return SimpleNamespace(data=rows, error=None)

        self._reset_builder()
        return SimpleNamespace(data=None, error=None)

    def rpc(self, _fn, params):
        self.rpc_calls.append(params)
        matches = [
            {"content": row["content"]}
            for row in self.rows["book_chunk"]
            if row["book_id"] == params["book_id"]
        ]

        class _Response:
            def __init__(self, payload):
                self.payload = payload

            def execute(self):
                return SimpleNamespace(data=self.payload, error=None)

        return _Response(matches)


def build_service(fake_supabase, *, stub_batch: bool = True):
    service = SupabaseRagService(
        supabase_url="http://fake.local",
        supabase_key="key",
        gemini_api_key="gem",
        embedding_model="text-embedding-004",
        supabase_client=fake_supabase,
        gemini_client=None,
    )
    if stub_batch:
        service._batch_embed = lambda chunks, _title: [[0.1, 0.2]] * len(chunks)  # type: ignore[method-assign]
    service._embed_query = lambda _text: [0.5, 0.6]  # type: ignore[method-assign]
    return service


def test_ingest_book_persists_chunks():
    fake = FakeSupabase()
    service = build_service(fake)

    book_id = service.ingest_book(title="Test", text="One line of text.\n\nAnother detail to index.")

    assert book_id == "book-1"
    assert fake.rows["book"]
    assert fake.rows["book_chunk"], "Chunks should be stored when text exists."


def test_ingest_book_raises_on_supabase_error():
    fake = FakeSupabase()
    fake.error_on_book = "insert failed"
    service = build_service(fake)

    with pytest.raises(RuntimeError):
        service.ingest_book(title="Broken", text="Content")


def test_get_relevant_chunks_returns_results():
    fake = FakeSupabase()
    service = build_service(fake)

    book_id = service.ingest_book(title="Context", text="Alpha beta gamma delta.")
    chunks = service.get_relevant_chunks(book_id, "delta question")

    assert chunks, "Chunk query should return stored text when embeddings exist."
    assert fake.rpc_calls, "RPC should be invoked with embedding payload."


def test_debug_status_reports_config():
    fake = FakeSupabase()
    service = build_service(fake)
    status = service.debug_status()
    assert status["chunk_table"] == "book_chunk"
    assert status["chunk_text_column"] == "content"


def test_delete_book_removes_chunks():
    fake = FakeSupabase()
    service = build_service(fake)

    book_id = service.ingest_book(title="Delete", text="Chunk one.\nChunk two.")
    assert fake.rows["book_chunk"], "Precondition failed: chunks missing"

    service.delete_book(book_id)

    assert not any(row["book_id"] == book_id for row in fake.rows["book_chunk"])


def test_batch_embed_parallel_preserves_order():
    fake = FakeSupabase()
    service = build_service(fake, stub_batch=False)
    service.embed_parallelism = 4

    chunks = ["slow", "fast", "mid"]

    def fake_single(self, chunk):
        if chunk == "slow":
            time.sleep(0.05)
        return [float(len(chunk))]

    service._embed_single_chunk = MethodType(fake_single, service)  # type: ignore[method-assign]

    vectors = service._batch_embed(chunks, "Ignore Title")

    assert vectors == [[4.0], [4.0], [3.0]]


def test_batch_embed_uses_sequential_when_parallel_disabled():
    fake = FakeSupabase()
    service = build_service(fake, stub_batch=False)
    service.embed_parallelism = 1

    calls: list[list[str]] = []

    def fake_seq(self, seq):
        calls.append(list(seq))
        return [[1.0]] * len(seq)

    service._embed_chunks_sequential = MethodType(fake_seq, service)  # type: ignore[method-assign]
    vectors = service._batch_embed(["x", "y"], "Title")

    assert calls == [["x", "y"]]
    assert vectors == [[1.0], [1.0]]


def test_sample_random_chunks_returns_text(monkeypatch):
    fake = FakeSupabase()
    service = build_service(fake)
    fake.rows["book_chunk"] = [
        {"id": f"chunk-{idx}", "book_id": "book-xyz", "content": f"text-{idx}"}
        for idx in range(1, 5)
    ]
    monkeypatch.setattr(
        "litreel.services.rag.random.sample", lambda seq, size: list(seq)[:size]
    )

    chunks = service.sample_random_chunks("book-xyz", sample_size=2)

    assert chunks == ["text-1", "text-2"]
