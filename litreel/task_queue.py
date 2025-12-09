from __future__ import annotations

from typing import Optional
import time

from flask import Flask

try:  # pragma: no cover - optional at runtime
    from redis import Redis
    from rq import Queue
except Exception:  # pragma: no cover - redis may be missing in some environments
    Redis = None  # type: ignore
    Queue = None  # type: ignore

try:  # pragma: no cover - optional dependency
    import fakeredis
except Exception:  # pragma: no cover - fakeredis may be missing in some environments
    fakeredis = None


class LocalRedis:
    """Minimal Redis-compatible store used when fakeredis/redis are unavailable."""

    def __init__(self):
        self._data: dict[str, tuple[float | None, bytes]] = {}

    def _prune(self, key: str) -> bool:
        expires, _ = self._data.get(key, (None, b""))
        if expires is not None and expires < time.time():
            self._data.pop(key, None)
            return True
        return False

    def setex(self, key: str, ttl: int, value):
        expires_at = time.time() + int(ttl) if ttl else None
        payload = value if isinstance(value, bytes) else str(value).encode("utf-8")
        self._data[key] = (expires_at, payload)
        return True

    def get(self, key: str):
        if key not in self._data:
            return None
        if self._prune(key):
            return None
        return self._data[key][1]

    def delete(self, *keys: str):
        removed = 0
        for key in keys:
            if key in self._data:
                self._data.pop(key, None)
                removed += 1
        return removed

    def ping(self):
        return True


def _ensure_fake_redis(app: Flask) -> Optional["Redis"]:
    existing = app.config.get("LOCAL_REDIS")
    if existing is not None:
        return existing
    if fakeredis is not None:
        store = fakeredis.FakeRedis()
        app.logger.info("fakeredis_initialized")
    else:
        app.logger.warning("fakeredis_unavailable_falling_back")
        store = LocalRedis()
    app.config["LOCAL_REDIS"] = store
    return store


def _should_use_real_redis(app: Flask) -> bool:
    profile = str(app.config.get("DATABASE_PROFILE", "")).strip().lower()
    if profile in {"local", "dev", "sqlite"}:
        return False
    redis_url = (app.config.get("REDIS_URL") or "").strip()
    if not redis_url or Redis is None:
        return False
    return True


def _connection_healthy(app: Flask, connection) -> bool:
    if connection is None:
        return False
    try:
        connection.ping()
        return True
    except Exception as exc:  # pragma: no cover - network/auth failures
        app.logger.warning("redis_connection_unhealthy", extra={"error": str(exc)})
        return False


def init_task_queue(app: Flask) -> Optional["Queue"]:
    """Initialize the global RQ queue if Redis is configured."""
    profile = str(app.config.get("DATABASE_PROFILE", "")).strip().lower()
    redis_url = (app.config.get("REDIS_URL") or "").strip()
    connection = None
    use_real_redis = _should_use_real_redis(app)
    if use_real_redis:
        try:
            connection = Redis.from_url(redis_url)
            if _connection_healthy(app, connection):
                app.config["REDIS_CONNECTION"] = connection
            else:
                connection = None
        except Exception as exc:  # pragma: no cover - runtime safety
            app.logger.exception("task_queue_connection_failed", extra={"error": str(exc)})
            connection = None
    elif redis_url and not use_real_redis:
        app.logger.info(
            "task_queue_redis_skipped_for_profile",
            extra={"profile": profile or "", "redis_url_configured": True},
        )

    if connection is None:
        _ensure_fake_redis(app)

    if profile in {"local", "dev", "sqlite"}:
        app.logger.info("task_queue_skipped_for_profile", extra={"profile": profile})
        return None

    if not connection:
        if not redis_url:
            app.logger.info("task_queue_disabled_missing_url")
        elif Queue is None:
            app.logger.warning("task_queue_unavailable_missing_dependencies")
        return None
    if Queue is None:
        app.logger.warning("task_queue_unavailable_missing_dependencies")
        return None

    queue_name = app.config.get("WORK_QUEUE_NAME", "litreel-tasks")
    default_timeout = int(app.config.get("WORK_QUEUE_TIMEOUT", 900))
    queue = Queue(name=queue_name, connection=connection, default_timeout=default_timeout)
    app.config["TASK_QUEUE"] = queue
    app.logger.info(
        "task_queue_initialized",
        extra={"queue_name": queue_name, "queue_timeout": default_timeout},
    )
    return queue


def get_task_queue(app: Flask) -> Optional["Queue"]:
    return app.config.get("TASK_QUEUE")


def get_redis_connection(app: Flask) -> Optional["Redis"]:
    if not _should_use_real_redis(app):
        return _ensure_fake_redis(app)
    queue = app.config.get("TASK_QUEUE")
    if queue:
        conn = getattr(queue, "connection", None)
        if _connection_healthy(app, conn):
            return conn  # type: ignore[return-value]
    existing = app.config.get("REDIS_CONNECTION")
    if existing and _connection_healthy(app, existing):
        return existing
    if existing:
        app.config.pop("REDIS_CONNECTION", None)
    redis_url = (app.config.get("REDIS_URL") or "").strip()
    if redis_url and Redis is not None:
        try:
            connection = Redis.from_url(redis_url)
            if _connection_healthy(app, connection):
                app.config["REDIS_CONNECTION"] = connection
                return connection
        except Exception:
            app.logger.warning("redis_connection_failed", exc_info=True)
    fallback = _ensure_fake_redis(app)
    return fallback


def is_task_queue_healthy(app: Flask) -> bool:
    """Return True only when the configured queue can reach Redis."""
    queue = app.config.get("TASK_QUEUE")
    if not queue:
        return False
    connection = getattr(queue, "connection", None)
    return _connection_healthy(app, connection)


__all__ = [
    "init_task_queue",
    "get_task_queue",
    "get_redis_connection",
    "is_task_queue_healthy",
]
