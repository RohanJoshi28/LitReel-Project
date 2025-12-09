from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import secrets
import time
from uuid import uuid4

from flask import Flask, Response, jsonify, send_from_directory, g, request
from sqlalchemy import inspect, text
from werkzeug.exceptions import HTTPException, NotFound
from dotenv import load_dotenv

load_dotenv()

try:  # pragma: no cover - platform specific
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from .config import Config, DEFAULT_INSTANCE_ROOT
from .extensions import db, login_manager
from .routes.api import api_bp
from .routes.auth import auth_bp
from .routes.tts import tts_bp
from .services.gemini_runner import GeminiSlideshowGenerator
from .services.stock_images import StockImageService
from .services.video_renderer import VideoRenderer
from .services.rag import LocalRagService, SupabaseRagService
from .services.arousal import NarrativeArousalClient
from .logging_utils import setup_logging
from .task_queue import init_task_queue


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, static_folder=None, instance_path=str(DEFAULT_INSTANCE_ROOT))
    app.config.from_object(Config)

    if test_config:
        app.config.update(test_config)

    asset_max_age = int(app.config.get("FRONTEND_ASSET_MAX_AGE", 0))
    try:
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = asset_max_age
    except Exception:
        pass

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    _ensure_secret_key(app)
    _configure_session_security(app)

    setup_logging(app)

    try:
        engine_opts_repr = repr(app.config.get("SQLALCHEMY_ENGINE_OPTIONS"))
        app.logger.warning("database_engine_options %s", engine_opts_repr)
    except Exception:
        pass
    _register_request_hooks(app)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = None

    lock_path = Path(app.instance_path) / ".startup.lock"
    with _startup_lock(lock_path):
        with app.app_context():
            should_bootstrap_schema = _should_bootstrap_schema(app)
            if should_bootstrap_schema:
                db.create_all()
            _run_post_migrations(app)
            try:
                backfill_legacy_projects(app)
            except Exception as exc:  # pragma: no cover - safety during first boot
                app.logger.warning("legacy_backfill_skipped", extra={"error": str(exc)})

    @login_manager.user_loader
    def load_user(user_id: str):  # type: ignore[override]
        if not user_id:
            return None
        from .models import User  # local import to avoid circular dependency

        try:
            return User.query.get(int(user_id))
        except (TypeError, ValueError):
            return None

    @login_manager.unauthorized_handler
    def _unauthorized():
        return jsonify({"error": "Authentication required."}), 401

    _configure_services(app)
    init_task_queue(app)

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(tts_bp, url_prefix="/api")

    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    assets_dir = frontend_dir / "assets"

    def _send_with_cache_control(directory: Path, filename: str):
        response = send_from_directory(directory, filename, max_age=asset_max_age)
        if asset_max_age <= 0:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.route("/")
    def public_landing():
        return _send_with_cache_control(frontend_dir, "landing.html")

    @app.route("/landing")
    def legacy_landing():
        return _send_with_cache_control(frontend_dir, "landing.html")

    @app.route("/studio")
    def studio():
        return _send_with_cache_control(frontend_dir, "index.html")

    @app.route("/assets/<path:filename>")
    def assets(filename: str):
        return _send_with_cache_control(assets_dir, filename)

    _register_error_handlers(app, frontend_dir)

    return app


def _configure_services(app: Flask) -> None:
    if "GEMINI_SERVICE" not in app.config:
        app.config["GEMINI_SERVICE"] = GeminiSlideshowGenerator(
            api_key=app.config.get("GEMINI_API_KEY"),
            model_name=app.config.get("GEMINI_MODEL_NAME"),
        )

    if "STOCK_IMAGE_SERVICE" not in app.config:
        app.config["STOCK_IMAGE_SERVICE"] = StockImageService(
            api_key=app.config.get("PEXELS_API_KEY"),
            results_per_page=app.config.get("STOCK_IMAGES_PER_PAGE", 12),
        )

    if "VIDEO_RENDERER" not in app.config:
        render_root = Path(app.instance_path) / "renders"
        render_root.mkdir(parents=True, exist_ok=True)
        app.config["VIDEO_RENDERER"] = VideoRenderer(output_dir=render_root)

    if "RAG_SERVICE" not in app.config:
        db_profile = str(app.config.get("DATABASE_PROFILE", "")).strip().lower()
        prefer_local_rag = db_profile in {"local", "dev", "sqlite"}
        supabase_url = app.config.get("SUPABASE_URL", "")
        supabase_key = app.config.get("SUPABASE_API_KEY", "")
        if prefer_local_rag or not (supabase_url and supabase_key):
            rag_service = LocalRagService(
                session=db.session,
                gemini_api_key=app.config.get("GEMINI_API_KEY", ""),
                embedding_model=app.config.get("GEMINI_EMBED_MODEL_NAME", "gemini-embedding-001"),
                default_match_count=int(app.config.get("SUPABASE_MAX_MATCHES", 6)),
                embed_parallelism=int(app.config.get("SUPABASE_EMBED_CONCURRENCY", 8)),
            )
        else:
            rag_service = SupabaseRagService(
                supabase_url=supabase_url,
                supabase_key=supabase_key,
                gemini_api_key=app.config.get("GEMINI_API_KEY", ""),
                embedding_model=app.config.get("GEMINI_EMBED_MODEL_NAME", "gemini-embedding-001"),
                book_table=app.config.get("SUPABASE_BOOK_TABLE", "book"),
                chunk_table=app.config.get("SUPABASE_CHUNK_TABLE", "book_chunk"),
                chunk_text_column=app.config.get("SUPABASE_CHUNK_TEXT_COLUMN", "content"),
                match_function=app.config.get("SUPABASE_MATCH_FUNCTION", "match_book_chunks"),
                default_match_count=int(app.config.get("SUPABASE_MAX_MATCHES", 6)),
                embed_parallelism=int(app.config.get("SUPABASE_EMBED_CONCURRENCY", 8)),
            )
        app.config["RAG_SERVICE"] = rag_service

    if "AROUSAL_CLIENT" not in app.config:
        arousal_client = NarrativeArousalClient(
            base_url=app.config.get("AROUSAL_SPACE_URL", ""),
            max_workers=int(app.config.get("AROUSAL_MAX_WORKERS", 12)),
            split_words=int(app.config.get("AROUSAL_SPLIT_WORDS", 250)),
        )
        if arousal_client.base_url:
            is_ready = arousal_client.ping()
            app.logger.info(
                "Narrative arousal API status",
                extra={"ready": is_ready, "url": arousal_client.base_url},
            )
        app.config["AROUSAL_CLIENT"] = arousal_client


def _configure_session_security(app: Flask) -> None:
    session_name = (app.config.get("SESSION_COOKIE_NAME") or "litreel_session").strip() or "litreel_session"
    remember_name = (app.config.get("REMEMBER_COOKIE_NAME") or "litreel_remember").strip() or "litreel_remember"
    app.config["SESSION_COOKIE_NAME"] = session_name
    app.config["REMEMBER_COOKIE_NAME"] = remember_name

    if not app.config.get("SESSION_COOKIE_SAMESITE"):
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    if not app.config.get("REMEMBER_COOKIE_SAMESITE"):
        app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"

    if app.config.get("TESTING"):
        app.config["SESSION_COOKIE_SECURE"] = False
        app.config["REMEMBER_COOKIE_SECURE"] = False


def _ensure_secret_key(app: Flask) -> None:
    if app.config.get("TESTING"):
        current = str(app.config.get("SECRET_KEY") or "").strip()
        if not current or current == "dev-secret-key":
            app.config["SECRET_KEY"] = "test-secret-key"
        return

    configured = str(app.config.get("SECRET_KEY") or "").strip()
    if configured and configured != "dev-secret-key":
        app.config["SECRET_KEY"] = configured
        return

    secret_path = Path(app.instance_path) / "secret.key"
    if secret_path.exists():
        existing = secret_path.read_text().strip()
        if existing:
            app.config["SECRET_KEY"] = existing
            return

    secret_path.parent.mkdir(parents=True, exist_ok=True)
    new_key = secrets.token_hex(32)
    secret_path.write_text(new_key)
    try:
        os.chmod(secret_path, 0o600)
    except OSError:
        pass
    app.config["SECRET_KEY"] = new_key
    try:
        app.logger.warning("secret_key_rotated", extra={"path": str(secret_path)})
    except Exception:
        pass


def _register_request_hooks(app: Flask) -> None:
    request_id_header = app.config.get("REQUEST_ID_HEADER", "X-Request-ID")

    @app.before_request
    def _start_request_timer():
        g.request_id = request.headers.get(request_id_header) or uuid4().hex
        g.request_started_at = time.perf_counter()

    @app.after_request
    def _log_request(response):
        start = getattr(g, "request_started_at", None)
        duration_ms = None
        if start is not None:
            duration_ms = round((time.perf_counter() - start) * 1000, 3)

        request_id = getattr(g, "request_id", uuid4().hex)
        response.headers.setdefault(request_id_header, request_id)
        if duration_ms is not None:
            response.headers.setdefault("X-Response-Time", f"{duration_ms}ms")

        app.logger.info(
            "request_completed",
            extra={
                "status_code": response.status_code,
                "duration": duration_ms,
            },
        )
        return response


def _register_error_handlers(app: Flask, frontend_dir: Path) -> None:
    def _wants_json_response() -> bool:
        if request.path.startswith("/api"):
            return True
        accept = request.accept_mimetypes
        best = accept.best_match(["application/json", "text/html"])
        if best == "application/json" and accept[best] > accept["text/html"]:
            return True
        return False

    def _json_error(message: str, status_code: int):
        payload = {"error": message, "status_code": status_code}
        request_id = getattr(g, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        return jsonify(payload), status_code

    def _frontend_error_page(filename: str, fallback_title: str, status_code: int):
        try:
            response = send_from_directory(frontend_dir, filename)
        except NotFound:
            response = Response(f"<h1>{fallback_title}</h1>", mimetype="text/html")
        response.status_code = status_code
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.errorhandler(404)
    def _handle_not_found(error):
        app.logger.warning(
            "not_found",
            extra={"status_code": 404, "error": str(error)},
        )
        if _wants_json_response():
            return _json_error("This page was not found.", 404)
        return _frontend_error_page("404.html", "This Page Was Not Found", 404)

    @app.errorhandler(HTTPException)
    def _handle_http_exception(error: HTTPException):
        if isinstance(error, NotFound):
            return _handle_not_found(error)
        status_code = error.code or 500
        message = error.description or "Request failed."
        log_fn = app.logger.warning if status_code < 500 else app.logger.error
        log_fn(
            "http_error",
            extra={"status_code": status_code, "error": message},
        )
        if _wants_json_response():
            return _json_error(message, status_code)
        return _frontend_error_page("error.html", "Something Went Wrong", status_code)

    @app.errorhandler(Exception)
    def _handle_uncaught_exception(error: Exception):
        if isinstance(error, HTTPException):
            return _handle_http_exception(error)
        app.logger.exception("unhandled_exception")
        if _wants_json_response():
            return _json_error("An unexpected error occurred.", 500)
        return _frontend_error_page("error.html", "An Unexpected Error Occurred", 500)


@contextmanager
def _startup_lock(lock_path: Path):
    """Serialize DB bootstrapping so multiple gunicorn workers don't race on SQLite."""
    if fcntl is None:
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _should_bootstrap_schema(app: Flask) -> bool:
    flag = app.config.get("AUTO_DB_BOOTSTRAP")
    if isinstance(flag, bool):
        return flag
    if isinstance(flag, str):
        normalized = flag.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    if uri.startswith("sqlite:"):
        return True

    try:
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())
    except Exception as exc:
        app.logger.warning("schema_check_failed", extra={"error": str(exc)})
        return False

    required_tables = {table.name for table in db.Model.metadata.sorted_tables}
    missing_tables = required_tables - existing_tables
    if missing_tables:
        app.logger.info(
            "schema_bootstrap_required",
            extra={"missing_tables": sorted(missing_tables)},
        )
        return True

    return False


def _run_post_migrations(app: Flask) -> None:
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    metadata = db.Model.metadata
    missing_tables = [table for table in metadata.sorted_tables if table.name not in existing_tables]
    for table in missing_tables:
        try:
            table.create(bind=db.engine, checkfirst=True)
            app.logger.info("table_bootstrapped", extra={"table": table.name})
        except Exception as exc:
            app.logger.error(
                "table_bootstrap_failed",
                extra={"table": table.name, "error": str(exc)},
            )
            raise

    if missing_tables:
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())

    if "slide_styles" in existing_tables:
        slide_columns = {col["name"] for col in inspector.get_columns("slide_styles")}
        if "underline" not in slide_columns:
            with db.engine.connect() as connection:
                connection.execute(
                    text("ALTER TABLE slide_styles ADD COLUMN underline INTEGER NOT NULL DEFAULT 0")
                )
                connection.commit()

    if "projects" in existing_tables:
        project_columns = {col["name"] for col in inspector.get_columns("projects")}
        if "user_id" not in project_columns:
            with db.engine.connect() as connection:
                connection.execute(text("ALTER TABLE projects ADD COLUMN user_id INTEGER"))
                connection.commit()
        if "supabase_book_id" not in project_columns:
            with db.engine.connect() as connection:
                connection.execute(text("ALTER TABLE projects ADD COLUMN supabase_book_id TEXT"))
                connection.commit()
        if "active_concept_id" not in project_columns:
            with db.engine.connect() as connection:
                connection.execute(text("ALTER TABLE projects ADD COLUMN active_concept_id INTEGER"))
                connection.commit()
        if "voice" not in project_columns:
            with db.engine.connect() as connection:
                connection.execute(
                    text("ALTER TABLE projects ADD COLUMN voice VARCHAR(50) NOT NULL DEFAULT 'sarah'")
                )
                connection.commit()


def backfill_legacy_projects(app: Flask) -> None:
    from .models import Project, User

    email = (app.config.get("LEGACY_USER_EMAIL") or "").strip()
    password = (app.config.get("LEGACY_USER_PASSWORD") or "").strip()
    reset_password = bool(app.config.get("LEGACY_RESET_PASSWORD", True))

    if not email or not password:
        return

    user = User.query.filter_by(email=email).first()
    created = False
    if not user:
        user = User(email=email)
        created = True

    if created or reset_password or not user.password_hash:
        user.set_password(password)

    db.session.add(user)
    db.session.flush()

    Project.query.filter(Project.user_id.is_(None)).update(
        {"user_id": user.id}, synchronize_session=False
    )

    db.session.commit()
