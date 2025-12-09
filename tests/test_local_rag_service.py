from litreel import create_app
from litreel.extensions import db
from litreel.services.rag import LocalRagService


def test_local_rag_ingest_and_retrieve(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'local_rag.db'}",
        }
    )

    with app.app_context():
        service = LocalRagService(
            session=db.session,
            gemini_api_key="test-key",
            embedding_model="test-embed",
        )
        service._batch_embed = lambda chunks, title: [[float(idx)] for idx, _ in enumerate(chunks, 1)]  # type: ignore[attr-defined]
        service._embed_query = lambda text: [1.0]  # type: ignore[attr-defined]

        book_id = service.ingest_book(title="Sample", text="One two three four five.")
        assert book_id is not None

        chunks = service.get_relevant_chunks(book_id, "query", match_count=1)
        assert len(chunks) == 1

        random_chunks = service.sample_random_chunks(book_id, sample_size=1)
        assert len(random_chunks) == 1

        service.delete_book(book_id)
        assert service.get_relevant_chunks(book_id, "query") == []
