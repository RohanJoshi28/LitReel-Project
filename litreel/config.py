import os
from pathlib import Path
from urllib.parse import urlparse


LOCAL_DB_PROFILES = {"local", "dev", "sqlite"}


DEFAULT_PEXELS_KEY = "2NnkVPxZunC14PHZvkK2ZOswzJZVifIB49AloBErWWpt8eYgJd64gPCo"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INSTANCE_ROOT = Path(
    os.getenv("LITREEL_INSTANCE_PATH", PROJECT_ROOT / "instance")
)
DEFAULT_DB_PATH = Path(
    os.getenv("LITREEL_DB_PATH", DEFAULT_INSTANCE_ROOT / "litreel.db")
)
DEFAULT_UPLOAD_PATH = Path(
    os.getenv("LITREEL_UPLOAD_PATH", DEFAULT_INSTANCE_ROOT / "uploads")
)
DEFAULT_LOG_DIR = Path(
    os.getenv("LITREEL_LOG_DIR", DEFAULT_INSTANCE_ROOT / "logs")
)


def _env_flag(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"0", "false", "no", "off"}:
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    return default


def _normalize_samesite(value: str | None, default: str | None = "Lax") -> str | None:
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    if normalized == "none":
        return "None"
    if normalized in {"lax", "strict"}:
        return normalized.capitalize()
    return default


def _resolve_database_uri() -> str:
    profile = (os.getenv("DATABASE_PROFILE") or "").strip().lower()
    env_url = (os.getenv("DATABASE_URL") or "").strip()
    if profile in LOCAL_DB_PROFILES:
        return f"sqlite:///{DEFAULT_DB_PATH}"
    if env_url:
        return env_url
    return f"sqlite:///{DEFAULT_DB_PATH}"


def _build_engine_options(database_uri: str) -> dict:
    hostname = ""
    try:
        hostname = urlparse(database_uri).hostname or ""
    except Exception:
        hostname = ""

    # Supabase Session-mode poolers enforce a strict connection cap. Using NullPool ensures
    # each query grabs a short-lived connection instead of holding a process-local pool open.
    if hostname.endswith(".pooler.supabase.com"):
        try:
            from sqlalchemy.pool import NullPool  # type: ignore
        except Exception:  # pragma: no cover - SQLAlchemy missing/null
            NullPool = None  # type: ignore
        if NullPool is not None:
            return {
                "pool_pre_ping": True,
                "poolclass": NullPool,
            }

    options: dict[str, object] = {
        "pool_pre_ping": True,
        "pool_recycle": int(os.getenv("SQLALCHEMY_POOL_RECYCLE", "300")),
        "pool_timeout": int(os.getenv("SQLALCHEMY_POOL_TIMEOUT", "30")),
    }
    pool_size = os.getenv("SQLALCHEMY_POOL_SIZE")
    if pool_size:
        options["pool_size"] = max(1, int(pool_size))
    max_overflow = os.getenv("SQLALCHEMY_MAX_OVERFLOW")
    if max_overflow is not None and max_overflow != "":
        options["max_overflow"] = int(max_overflow)
    return options


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_DATABASE_URI = _resolve_database_uri()
    SQLALCHEMY_ENGINE_OPTIONS = _build_engine_options(SQLALCHEMY_DATABASE_URI)
    DATABASE_PROFILE = os.getenv("DATABASE_PROFILE", "")
    _profile_normalized = DATABASE_PROFILE.strip().lower()
    _local_profile = _profile_normalized in LOCAL_DB_PROFILES
    AUTO_DB_BOOTSTRAP = os.getenv("AUTO_DB_BOOTSTRAP", "")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.getenv(
        "UPLOAD_FOLDER",
        str(DEFAULT_UPLOAD_PATH),
    )
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-pro")
    GEMINI_EMBED_MODEL_NAME = os.getenv("GEMINI_EMBED_MODEL_NAME", "gemini-embedding-001")
    PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", DEFAULT_PEXELS_KEY)
    STOCK_IMAGES_PER_PAGE = int(os.getenv("STOCK_IMAGES_PER_PAGE", "12"))
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB uploads
    LEGACY_USER_EMAIL = os.getenv("LEGACY_USER_EMAIL", "testuser@litreel.app")
    LEGACY_USER_PASSWORD = os.getenv("LEGACY_USER_PASSWORD", "TestDrive123!")
    LEGACY_RESET_PASSWORD = os.getenv("LEGACY_RESET_PASSWORD", "1").lower() not in {"0", "false", "no"}
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY", "")
    SUPABASE_BOOK_TABLE = os.getenv("SUPABASE_BOOK_TABLE", "book")
    SUPABASE_CHUNK_TABLE = os.getenv("SUPABASE_CHUNK_TABLE", "book_chunk")
    SUPABASE_CHUNK_TEXT_COLUMN = os.getenv("SUPABASE_CHUNK_TEXT_COLUMN", "content")
    SUPABASE_MATCH_FUNCTION = os.getenv("SUPABASE_MATCH_FUNCTION", "match_book_chunks")
    SUPABASE_MAX_MATCHES = int(os.getenv("SUPABASE_MAX_MATCHES", "6"))
    SUPABASE_EMBED_CONCURRENCY = int(os.getenv("SUPABASE_EMBED_CONCURRENCY", "8"))
    SUPABASE_LOG_TABLE = os.getenv("SUPABASE_LOG_TABLE", "app_logs")
    SUPABASE_LOG_LEVEL = os.getenv("SUPABASE_LOG_LEVEL", "WARNING")
    SUPABASE_LOG_TIMEOUT = float(os.getenv("SUPABASE_LOG_TIMEOUT", "5.0"))
    SUPABASE_LOG_MAX_RETRIES = int(os.getenv("SUPABASE_LOG_MAX_RETRIES", "3"))
    AROUSAL_SPACE_URL = os.getenv(
        "AROUSAL_SPACE_URL", "https://RohanJoshi28-narrative-arousal-regressor.hf.space"
    )
    AROUSAL_MAX_WORKERS = int(os.getenv("AROUSAL_MAX_WORKERS", "12"))
    AROUSAL_SPLIT_WORDS = int(os.getenv("AROUSAL_SPLIT_WORDS", "250"))
    RANDOM_SLICE_SAMPLE_SIZE = int(os.getenv("RANDOM_SLICE_SAMPLE_SIZE", "33"))
    RANDOM_SLICE_TOP_K = int(os.getenv("RANDOM_SLICE_TOP_K", "8"))
    RANDOM_SLICE_PROMPT = os.getenv(
        "RANDOM_SLICE_PROMPT",
        "You selected the random slice option. Use only the provided passages and craft at most two cohesive slideshow concepts that feel like a glimpse into an emotional peak of the book.",
    )
    RANDOM_SLICE_SCORING_RATIO = float(os.getenv("RANDOM_SLICE_SCORING_RATIO", "0.5"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT = os.getenv("LOG_FORMAT", "json")
    LOG_TO_STDOUT = os.getenv("LOG_TO_STDOUT", "1").lower() not in {"0", "false", "no"}
    LOG_TO_FILE = os.getenv("LOG_TO_FILE", "1").lower() not in {"0", "false", "no"}
    LOG_VERBOSITY = os.getenv("LOG_VERBOSITY", "essential")
    REDIS_URL = os.getenv("REDIS_URL", "")
    WORK_QUEUE_NAME = os.getenv("WORK_QUEUE_NAME", "litreel-tasks")
    WORK_QUEUE_TIMEOUT = int(os.getenv("WORK_QUEUE_TIMEOUT", "900"))
    RENDER_STORAGE_BUCKET = os.getenv("RENDER_STORAGE_BUCKET", "litreel-renders")
    RENDER_JOB_TTL_SECONDS = int(os.getenv("RENDER_JOB_TTL_SECONDS", "7200"))
    CONCEPT_JOB_TTL_SECONDS = int(os.getenv("CONCEPT_JOB_TTL_SECONDS", "3600"))
    ENABLE_SYNC_DOWNLOAD = os.getenv("ENABLE_SYNC_DOWNLOAD", "0").lower() in {"1", "true", "yes"}
    LOG_DIR = os.getenv("LOG_DIR", str(DEFAULT_LOG_DIR))
    LOG_FILE = os.getenv("LOG_FILE", str(DEFAULT_LOG_DIR / "litreel.log"))
    LOG_FILE_MAX_BYTES = int(os.getenv("LOG_FILE_MAX_BYTES", str(10 * 1024 * 1024)))
    LOG_FILE_BACKUP_COUNT = int(os.getenv("LOG_FILE_BACKUP_COUNT", "5"))
    REQUEST_ID_HEADER = os.getenv("REQUEST_ID_HEADER", "X-Request-ID")
    FRONTEND_ASSET_MAX_AGE = int(os.getenv("FRONTEND_ASSET_MAX_AGE", "0"))
    SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "litreel_session")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = _normalize_samesite(os.getenv("SESSION_COOKIE_SAMESITE"), "Lax")
    SESSION_COOKIE_SECURE = _env_flag(os.getenv("SESSION_COOKIE_SECURE"), default=not _local_profile)
    REMEMBER_COOKIE_NAME = os.getenv("REMEMBER_COOKIE_NAME", "litreel_remember")
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = _normalize_samesite(os.getenv("REMEMBER_COOKIE_SAMESITE"), "Lax")
    REMEMBER_COOKIE_SECURE = _env_flag(os.getenv("REMEMBER_COOKIE_SECURE"), default=not _local_profile)
