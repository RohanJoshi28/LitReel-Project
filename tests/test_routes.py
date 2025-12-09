import io
from pathlib import Path
from types import SimpleNamespace

from litreel import backfill_legacy_projects
from litreel.extensions import db
from litreel.models import Project, User


def upload_project(client, document_path, title="Test Project"):
    with open(document_path, "rb") as handle:
        data = {
            "title": title,
            "document": (io.BytesIO(handle.read()), Path(document_path).name),
        }
    return client.post("/api/projects", data=data, content_type="multipart/form-data")


def test_project_creation_flow(client, sample_pdf, dummy_services):
    response = upload_project(client, sample_pdf, "Test Project")
    assert response.status_code == 201
    payload = response.get_json()["project"]
    assert payload["title"] == "Test Project"
    assert payload["concepts"], "Concepts should be returned"
    assert dummy_services["gemini"].called_with is not None
    first_slide = payload["concepts"][0]["slides"][0]
    assert "style" in first_slide
    assert first_slide["style"]["underline"] is False

    slide_id = payload["concepts"][0]["slides"][0]["id"]
    patch_response = client.patch(
        f"/api/slides/{slide_id}",
        json={"text": "Updated", "effect": "zoom-in", "transition": "slide"},
    )
    assert patch_response.status_code == 200
    slide_payload = patch_response.get_json()["slide"]
    assert slide_payload["text"] == "Updated"
    assert slide_payload["effect"] == "zoom-in"

    stock_response = client.get("/api/stock/search?q=history")
    assert stock_response.status_code == 200
    assert dummy_services["stock"].last_query == "history"

    concept_id = payload["concepts"][0]["id"]
    download_response = client.get(f"/api/projects/{payload['id']}/download?concept_id={concept_id}")
    assert download_response.status_code == 200
    assert download_response.content_type == "video/mp4"
    assert dummy_services["renderer"].called_with == (payload["id"], concept_id, "sarah")


def test_render_endpoint_creates_artifact_and_download(client, sample_pdf, dummy_services):
    response = upload_project(client, sample_pdf, "Render Flow")
    project = response.get_json()["project"]
    concept_id = project["concepts"][0]["id"]

    render_resp = client.post(
        f"/api/projects/{project['id']}/renders",
        json={"concept_id": concept_id},
    )
    assert render_resp.status_code == 201
    job = render_resp.get_json()["job"]
    assert job["status"] == "ready"
    assert job["job_id"]
    assert job["download_type"] == "blob"
    assert not job.get("storage_path")

    file_resp = client.get(f"/api/downloads/{job['job_id']}/file")
    assert file_resp.status_code == 200
    assert file_resp.data == b"fake"


def test_project_creation_background_fallback(monkeypatch, app, sample_pdf, auth_client_factory):
    app.config["TESTING"] = False
    app.config["TASK_QUEUE"] = None
    app.config["DATABASE_PROFILE"] = "production"
    app.config["FORCE_INLINE_GENERATION"] = False
    import threading

    started = {}

    real_thread_cls = threading.Thread

    class RecordingThread(real_thread_cls):
        def __init__(self, *args, **kwargs):
            name = kwargs.get("name", "") or ""
            self._tracked = name.startswith("project-gen-")
            if self._tracked:
                started["name"] = name
                started["daemon"] = kwargs.get("daemon")
            super().__init__(*args, **kwargs)

        def start(self):
            if self._tracked:
                started["started"] = True
            return super().start()

    monkeypatch.setattr("litreel.routes.api.threading.Thread", RecordingThread)

    client, _ = auth_client_factory()
    response = upload_project(client, sample_pdf, "Background Project")
    app.config["TESTING"] = True

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["job"]["mode"] == "background"
    assert payload["job"]["status"] == "queued"
    assert started.get("started") is True
    assert started.get("name", "").startswith("project-gen-")


def test_local_profile_forces_inline_generation(app, sample_pdf, auth_client_factory):
    app.config["DATABASE_PROFILE"] = "local"
    app.config["TASK_QUEUE"] = None
    client, _ = auth_client_factory()
    response = upload_project(client, sample_pdf, "Inline Local")
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["job"]["mode"] == "inline"
    assert (payload["job"].get("status", "") or "").lower() == (
        (payload["project"].get("status", "") or "").lower()
    )


def test_delete_project_triggers_async_rag_cleanup(monkeypatch, app, sample_pdf, auth_client_factory):
    rag = app.config["_dummy_services"]["rag"]
    rag.is_enabled = True
    delete_calls = []

    def fake_delete(book_id):
        delete_calls.append(book_id)

    rag.delete_book = fake_delete

    client, _ = auth_client_factory()
    response = upload_project(client, sample_pdf, "Async Delete")
    project_id = response.get_json()["project"]["id"]

    import threading

    started = {}

    real_thread_cls = threading.Thread

    class RecordingThread(real_thread_cls):
        def __init__(self, *args, **kwargs):
            name = kwargs.get("name", "") or ""
            self._tracked = name.startswith("rag-delete-")
            if self._tracked:
                started["name"] = name
                started["daemon"] = kwargs.get("daemon")
            super().__init__(*args, **kwargs)

        def start(self):
            if self._tracked:
                started["started"] = True
            return super().start()

    monkeypatch.setattr("litreel.routes.api.threading.Thread", RecordingThread)

    delete_resp = client.delete(f"/api/projects/{project_id}")
    assert delete_resp.status_code == 200
    assert delete_calls == [rag.book_id]
    assert started.get("started") is True
    assert started.get("name", "").startswith("rag-delete-")


def test_render_falls_back_when_queue_unhealthy(app, sample_pdf, auth_client_factory):
    class FailingConn:
        def ping(self):  # pragma: no cover - intentionally unhealthy
            raise RuntimeError("redis down")

    class DummyQueue:
        def __init__(self):
            self.connection = FailingConn()

        def enqueue(self, *args, **kwargs):  # pragma: no cover - should never be called
            raise AssertionError("queue should be bypassed when unhealthy")

    app.config["TASK_QUEUE"] = DummyQueue()
    app.config["DATABASE_PROFILE"] = ""  # ensure prod-like path

    client, _ = auth_client_factory()
    response = upload_project(client, sample_pdf, "Inline Fallback")
    project = response.get_json()["project"]
    concept_id = project["concepts"][0]["id"]

    render_resp = client.post(
        f"/api/projects/{project['id']}/renders",
        json={"concept_id": concept_id},
    )
    assert render_resp.status_code == 201
    job = render_resp.get_json()["job"]
    assert job["status"] == "ready"


def test_docx_upload_supported(client, sample_docx, dummy_services):
    response = upload_project(client, sample_docx, "DOCX Project")
    assert response.status_code == 201
    payload = response.get_json()["project"]
    assert payload["title"] == "DOCX Project"
    saved_path = dummy_services["gemini"].called_with
    assert saved_path is not None and saved_path.suffix == ".docx"


def test_epub_upload_supported(client, sample_epub, dummy_services):
    response = upload_project(client, sample_epub, "EPUB Project")
    assert response.status_code == 201
    payload = response.get_json()["project"]
    assert payload["title"] == "EPUB Project"
    saved_path = dummy_services["gemini"].called_with
    assert saved_path is not None and saved_path.suffix == ".epub"


def test_invalid_effect_validation(client, sample_pdf):
    response = upload_project(client, sample_pdf, "Bad")
    slide_id = response.get_json()["project"]["concepts"][0]["slides"][0]["id"]
    bad_patch = client.patch(f"/api/slides/{slide_id}", json={"effect": "spin"})
    assert bad_patch.status_code == 400


def test_project_creation_falls_back_when_gemini_fails(app, sample_pdf, auth_client_factory):
    client, _ = auth_client_factory(email="fallback@example.com")
    dummy = app.config["_dummy_services"]["gemini"]

    def _explode(_path):
        raise RuntimeError("boom")

    dummy.generate_from_text = _explode

    response = upload_project(client, sample_pdf, "Fallback Story")
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["generation_mode"] == "fallback"
    project = payload["project"]
    assert project["title"] == "Fallback Story"
    assert project["status"] == "generated-local"
    assert project["concepts"], "Fallback concepts should populate"


def test_slide_style_update(client, sample_pdf):
    response = upload_project(client, sample_pdf, "Style Project")
    slide_id = response.get_json()["project"]["concepts"][0]["slides"][0]["id"]

    patch_payload = {
        "text": "Styled Slide",
        "style": {
            "text_color": "#ffee00",
            "outline_color": "#101010",
            "font_weight": "400",
            "underline": True,
        },
        "image_url": "https://example.com/new.jpg",
    }
    patch_response = client.patch(f"/api/slides/{slide_id}", json=patch_payload)
    assert patch_response.status_code == 200
    slide = patch_response.get_json()["slide"]
    assert slide["text"] == "Styled Slide"
    assert slide["image_url"] == "https://example.com/new.jpg"
    assert slide["style"]["text_color"] == "#FFEE00"
    assert slide["style"]["outline_color"] == "#101010"
    assert slide["style"]["font_weight"] == "400"
    assert slide["style"]["underline"] is True


def test_slide_style_normalization(client, sample_pdf):
    response = upload_project(client, sample_pdf, "Normalize")
    slide_id = response.get_json()["project"]["concepts"][0]["slides"][0]["id"]

    patch_payload = {
        "text": "Bad Color",
        "style": {
            "text_color": "#zzz",  # invalid
            "outline_color": "#ggg",  # invalid
            "font_weight": "900",  # unsupported
            "underline": "notabool",
        },
    }
    patch_response = client.patch(f"/api/slides/{slide_id}", json=patch_payload)
    assert patch_response.status_code == 200
    style = patch_response.get_json()["slide"]["style"]
    assert style["text_color"] == "#FFFFFF"
    assert style["outline_color"] == "#000000"
    assert style["font_weight"] == "700"
    assert style["underline"] is False


def test_requests_require_authentication(app, sample_pdf):
    anon_client = app.test_client()
    response = upload_project(anon_client, sample_pdf, "Nope")
    assert response.status_code == 401
    stock = anon_client.get("/api/stock/search?q=history")
    assert stock.status_code == 401


def test_users_cannot_access_foreign_projects(auth_client_factory, sample_pdf):
    owner_client, _ = auth_client_factory(email="owner@example.com")
    response = upload_project(owner_client, sample_pdf, "Owner Story")
    project = response.get_json()["project"]

    intruder_client, _ = auth_client_factory(email="intruder@example.com")

    list_response = intruder_client.get("/api/projects")
    assert list_response.status_code == 200
    assert list_response.get_json()["projects"] == []

    proj_response = intruder_client.get(f"/api/projects/{project['id']}")
    assert proj_response.status_code == 404

    slide_id = project["concepts"][0]["slides"][0]["id"]
    patch_response = intruder_client.patch(f"/api/slides/{slide_id}", json={"text": "Hack"})
    assert patch_response.status_code == 404


def test_delete_project_removes_book_and_slides(client, sample_pdf, app):
    rag = app.config["_dummy_services"]["rag"]
    rag.is_enabled = True
    rag.book_id = "sb-cleanup"
    response = upload_project(client, sample_pdf, "Delete Me")
    project = response.get_json()["project"]
    delete_response = client.delete(f"/api/projects/{project['id']}")
    assert delete_response.status_code == 200
    payload = delete_response.get_json()["deleted"]
    assert payload["id"] == project["id"]
    assert payload["title"] == "Delete Me"

    list_response = client.get("/api/projects")
    assert list_response.get_json()["projects"] == []

    with app.app_context():
        assert Project.query.get(project["id"]) is None
    assert rag.delete_calls[-1] == "sb-cleanup"


def test_delete_project_requires_owner(auth_client_factory, sample_pdf):
    owner_client, _ = auth_client_factory(email="deleteme@example.com")
    response = upload_project(owner_client, sample_pdf, "Private Book")
    project = response.get_json()["project"]

    intruder_client, _ = auth_client_factory(email="nope@example.com")
    delete_response = intruder_client.delete(f"/api/projects/{project['id']}")
    assert delete_response.status_code == 404

    list_response = owner_client.get("/api/projects")
    assert list_response.status_code == 200
    assert list_response.get_json()["projects"], "Owner project should still exist"


def test_rag_concept_generation_flow(app, sample_pdf, auth_client_factory):
    rag = app.config["_dummy_services"]["rag"]
    rag.is_enabled = True
    rag.book_id = "sb-test"
    client, _ = auth_client_factory(email="rag@example.com")
    response = upload_project(client, sample_pdf, "RAG Story")
    project = response.get_json()["project"]
    assert project["supabase_book_id"] == "sb-test"
    concept_id = project["concepts"][0]["id"]
    payload = {"concept_id": concept_id, "context": "Make it feel like a TED talk."}
    rag_response = client.post(f"/api/projects/{project['id']}/concepts/rag", json=payload)
    assert rag_response.status_code == 202
    data = rag_response.get_json()
    job = data["job"]
    assert job["status"] == "succeeded"
    assert job["project_id"] == project["id"]
    assert job["concept_ids"], "Job should return created concept ids"
    refreshed = client.get(f"/api/projects/{project['id']}").get_json()["project"]
    created = [c for c in refreshed["concepts"] if c["id"] in job["concept_ids"]]
    assert created and created[0]["name"] == "Contextual Hook"
    assert rag.retrieve_calls, "RAG lookup should run"
    dummy_gemini = app.config["_dummy_services"]["gemini"]
    assert dummy_gemini.chunk_calls, "Gemini should receive RAG chunks before generating."
    assert dummy_gemini.chunk_calls[0]["chunks"] == ["Chunk A", "Chunk B"]


def test_concept_job_endpoint_requires_owner(app, sample_pdf, auth_client_factory):
    rag = app.config["_dummy_services"]["rag"]
    rag.is_enabled = True
    rag.book_id = "sb-job-ownership"
    owner_client, _ = auth_client_factory(email="concept-owner@example.com")
    response = upload_project(owner_client, sample_pdf, "Concept Job")
    project = response.get_json()["project"]
    concept_id = project["concepts"][0]["id"]
    payload = {"concept_id": concept_id, "context": "Ownership check"}
    job_response = owner_client.post(f"/api/projects/{project['id']}/concepts/rag", json=payload)
    assert job_response.status_code == 202
    job_id = job_response.get_json()["job"]["job_id"]

    intruder_client, _ = auth_client_factory(email="concept-intruder@example.com")
    forbidden = intruder_client.get(f"/api/concept-jobs/{job_id}")
    assert forbidden.status_code == 404

    allowed = owner_client.get(f"/api/concept-jobs/{job_id}")
    assert allowed.status_code == 200
    assert allowed.get_json()["job"]["job_id"] == job_id


def test_random_slice_generation_flow(app, sample_pdf, auth_client_factory):
    rag = app.config["_dummy_services"]["rag"]
    rag.is_enabled = True
    rag.book_id = "sb-random"
    rag.random_chunks = ["Slice 4", "Slice 2", "Slice 1", "Slice 5", "Slice 3"]
    arousal = app.config["_dummy_services"]["arousal"]
    arousal.rankings = [
        SimpleNamespace(text="Slice 4", score=0.92),
        SimpleNamespace(text="Slice 2", score=0.88),
        SimpleNamespace(text="Slice 1", score=0.77),
    ]
    app.config["RANDOM_SLICE_PROMPT"] = "Random slice request prompt"
    prev_sample = app.config["RANDOM_SLICE_SAMPLE_SIZE"]
    prev_top = app.config["RANDOM_SLICE_TOP_K"]
    app.config["RANDOM_SLICE_SAMPLE_SIZE"] = 4
    app.config["RANDOM_SLICE_TOP_K"] = 2
    prev_ratio = app.config.get("RANDOM_SLICE_SCORING_RATIO", 0.5)
    app.config["RANDOM_SLICE_SCORING_RATIO"] = 0.5
    client, _ = auth_client_factory(email="random@example.com")
    response = upload_project(client, sample_pdf, "Random Ready Book")
    project = response.get_json()["project"]
    payload = {"random_slice": True}
    try:
        rag_response = client.post(f"/api/projects/{project['id']}/concepts/rag", json=payload)
        assert rag_response.status_code == 202
        data = rag_response.get_json()
        job = data["job"]
        assert job["status"] == "succeeded"
        refreshed = client.get(f"/api/projects/{project['id']}").get_json()["project"]
        created = [c for c in refreshed["concepts"] if c["id"] in job["concept_ids"]]
        assert created, "Random slice should create a concept"
        dummy_gemini = app.config["_dummy_services"]["gemini"]
        last_call = dummy_gemini.chunk_calls[-1]
        assert last_call["chunks"] == ["Slice 4", "Slice 2"]
        assert last_call["context"] == "Random slice request prompt"
        assert rag.random_calls, "Random sampling should be requested from Supabase."
        assert rag.random_calls[-1]["sample_size"] == app.config["RANDOM_SLICE_SAMPLE_SIZE"]
        assert arousal.calls, "Arousal scoring should run before generation."
        expected_scoring = max(
            app.config["RANDOM_SLICE_TOP_K"],
            int(app.config["RANDOM_SLICE_SAMPLE_SIZE"] * app.config["RANDOM_SLICE_SCORING_RATIO"]),
        )
        assert arousal.calls[-1] == rag.random_chunks[:expected_scoring]
    finally:
        app.config["RANDOM_SLICE_SAMPLE_SIZE"] = prev_sample
        app.config["RANDOM_SLICE_TOP_K"] = prev_top
        app.config["RANDOM_SLICE_SCORING_RATIO"] = prev_ratio


def test_rag_concept_requires_supabase_id(client, sample_pdf, app):
    response = upload_project(client, sample_pdf, "No Supabase")
    project = response.get_json()["project"]
    rag = app.config["_dummy_services"]["rag"]
    assert project["supabase_book_id"] is None
    rag.is_enabled = True
    rag_response = client.post(f"/api/projects/{project['id']}/concepts/rag", json={"context": "Idea"})
    assert rag_response.status_code == 409


def test_rag_ingest_runs_sync_when_background_disabled(app, sample_pdf, auth_client_factory):
    rag = app.config["_dummy_services"]["rag"]
    rag.is_enabled = True
    rag.can_background_ingest = False
    rag.book_id = "sb-sync"
    client, _ = auth_client_factory(email="sync@example.com")
    response = upload_project(client, sample_pdf, "Sync RAG")
    project = response.get_json()["project"]
    assert rag.ingest_calls, "RAG ingest should run even when background threads are disabled."
    assert project["supabase_book_id"] == "sb-sync"


def test_rag_concept_returns_404_when_no_chunks(app, sample_pdf, auth_client_factory):
    rag = app.config["_dummy_services"]["rag"]
    rag.is_enabled = True
    rag.book_id = "sb-empty"
    rag.return_chunks = []
    client, _ = auth_client_factory(email="rag-empty@example.com")
    response = upload_project(client, sample_pdf, "Empty Retrieval")
    project = response.get_json()["project"]
    concept_id = project["concepts"][0]["id"]

    payload = {"concept_id": concept_id, "context": "Give me a new hook"}
    rag_response = client.post(f"/api/projects/{project['id']}/concepts/rag", json=payload)
    assert rag_response.status_code == 404
    dummy_gemini = app.config["_dummy_services"]["gemini"]
    assert not dummy_gemini.chunk_calls, "Gemini should not run when no chunks are returned."


def test_login_logout_flow(auth_client_factory):
    client, creds = auth_client_factory(email="flow@example.com")
    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    login = client.post("/api/auth/login", json=creds)
    assert login.status_code == 200
    me = client.get("/api/auth/me")
    assert me.status_code == 200


def test_login_switches_authenticated_user(auth_client_factory):
    client, _ = auth_client_factory(email="first@example.com")
    _, second_creds = auth_client_factory(email="second@example.com")

    initial = client.get("/api/auth/me")
    assert initial.status_code == 200
    assert initial.get_json()["user"]["email"] == "first@example.com"

    login = client.post("/api/auth/login", json=second_creds)
    assert login.status_code == 200
    assert login.get_json()["user"]["email"] == "second@example.com"

    updated = client.get("/api/auth/me")
    assert updated.status_code == 200
    assert updated.get_json()["user"]["email"] == "second@example.com"


def test_legacy_user_exists(app):
    with app.app_context():
        legacy_user = User.query.filter_by(email=app.config["LEGACY_USER_EMAIL"]).first()
        assert legacy_user is not None


def test_backfill_assigns_orphan_projects(app):
    with app.app_context():
        project = Project(title="Legacy", status="draft")
        db.session.add(project)
        db.session.commit()
        assert project.user_id is None

        backfill_legacy_projects(app)

        refreshed = Project.query.get(project.id)
        assert refreshed.user_id is not None


def test_public_pages_served(app):
    client = app.test_client()
    assert client.get("/").status_code == 200
    assert client.get("/landing").status_code == 200
    assert client.get("/studio").status_code == 200
