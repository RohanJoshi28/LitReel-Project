from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from logging.config import dictConfig
from pathlib import Path
from typing import Any, Iterable

from flask import g, has_request_context, request
from flask_login import current_user

from .supabase_client import create_supabase_client


class RequestContextFilter(logging.Filter):
    """Injects request specific metadata into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - exercised implicitly
        record.request_id = getattr(record, "request_id", None)
        record.method = getattr(record, "method", None)
        record.path = getattr(record, "path", None)
        record.remote_addr = getattr(record, "remote_addr", None)
        record.user_id = getattr(record, "user_id", None)
        record.status_code = getattr(record, "status_code", None)
        record.duration = getattr(record, "duration", None)

        if has_request_context():
            record.request_id = getattr(g, "request_id", None) or record.request_id or "n/a"
            record.method = request.method
            record.path = request.full_path.rstrip("?")
            record.remote_addr = request.headers.get("X-Forwarded-For", request.remote_addr)
            if hasattr(current_user, "is_authenticated") and current_user.is_authenticated:
                record.user_id = current_user.get_id()
        else:
            record.request_id = record.request_id or "system"

        return True


class JsonFormatter(logging.Formatter):
    """Formats log records as JSON for easier ingestion by log platforms."""

    default_time_format = "%Y-%m-%dT%H:%M:%S"
    default_msec_format = "%s.%03dZ"

    def __init__(self, *, excluded_keys: Iterable[str] | None = None):
        super().__init__()
        self._excluded_keys = set(excluded_keys or ())

    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - formatting layer
        log_record: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key in ("request_id", "method", "path", "remote_addr", "user_id", "status_code", "duration"):
            value = getattr(record, key, None)
            if value is not None and key not in self._excluded_keys:
                log_record[key] = value

        if record.exc_info:
            log_record["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(log_record, default=_serialize_default, ensure_ascii=True)


_TRACE_FORMATTER = logging.Formatter()
_RESERVED_RECORD_FIELDS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "process",
    "processName",
    "message",
    "asctime",
    "stacklevel",
    "request_id",
    "method",
    "path",
    "remote_addr",
    "user_id",
    "status_code",
    "duration",
}


def _coerce_json_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _coerce_json_value(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce_json_value(item) for item in value]
    return str(value)


class SupabaseLogHandler(logging.Handler):
    """Asynchronously fan out structured logs to a Supabase table."""

    def __init__(
        self,
        *,
        supabase_url: str,
        supabase_key: str,
        table: str,
        timeout: float = 5.0,
        max_retries: int = 3,
        level: int | str = logging.WARNING,
    ) -> None:
        super().__init__(level)
        self._client = create_supabase_client(supabase_url, supabase_key, timeout=timeout)
        self._table = table
        self._max_retries = max(0, int(max_retries))
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="supabase-logs")
        self._closed = False

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - exercised indirectly
        if self._closed:
            return
        try:
            payload = self._serialize_record(record)
        except Exception:
            self.handleError(record)
            return
        self._executor.submit(self._send_payload, payload)

    def _send_payload(self, payload: dict) -> None:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.table(self._table).insert(payload).execute()
                error = getattr(response, "error", None)
                if error and attempt < self._max_retries:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                break
            except Exception:  # pragma: no cover - network/runtime noise
                if attempt < self._max_retries:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                break

    def _serialize_record(self, record: logging.LogRecord) -> dict:
        stacktrace = None
        if record.exc_info:
            stacktrace = _TRACE_FORMATTER.formatException(record.exc_info)
        status_code = getattr(record, "status_code", None)
        try:
            status_val = int(status_code) if status_code is not None else None
        except (TypeError, ValueError):
            status_val = None
        duration = getattr(record, "duration", None)
        try:
            duration_ms = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration_ms = None
        user_value = getattr(record, "user_id", None)
        serialized_user = None
        if user_value not in (None, ""):
            try:
                serialized_user = int(user_value)
            except (TypeError, ValueError):
                serialized_user = None
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "method": getattr(record, "method", None),
            "path": getattr(record, "path", None),
            "remote_addr": getattr(record, "remote_addr", None),
            "user_id": serialized_user,
            "status_code": status_val,
            "duration_ms": duration_ms,
            "stacktrace": stacktrace,
        }
        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_FIELDS or key in payload or key.startswith("_"):
                continue
            extras[key] = _coerce_json_value(value)
        payload["extra"] = extras or None
        return payload

    def close(self) -> None:  # pragma: no cover - shutdown path
        if not self._closed:
            self._closed = True
            self._executor.shutdown(wait=False)
        super().close()


def _serialize_default(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _resolve_log_level(level: Any) -> int:
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        normalized = level.strip()
        if not normalized:
            return logging.INFO
        if normalized.isdigit():
            return int(normalized)
        return getattr(logging, normalized.upper(), logging.INFO)
    return logging.INFO


def _clear_logger_handlers(*loggers: logging.Logger) -> None:
    for logger in loggers:
        if not logger:
            continue
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


def setup_logging(app) -> None:
    """Configure structured logging with selectable verbosity profiles."""

    log_format = str(app.config.get("LOG_FORMAT", "json")).lower()
    log_to_stdout = bool(app.config.get("LOG_TO_STDOUT", True))
    log_to_file = bool(app.config.get("LOG_TO_FILE", True))
    max_bytes = int(app.config.get("LOG_FILE_MAX_BYTES", 10 * 1024 * 1024))
    backup_count = int(app.config.get("LOG_FILE_BACKUP_COUNT", 5))
    verbosity = str(app.config.get("LOG_VERBOSITY", "essential")).strip().lower()

    logging.disable(logging.NOTSET)

    valid_verbosity = {"none", "essential", "verbose"}
    if verbosity not in valid_verbosity:
        verbosity = "essential"

    if verbosity == "none":
        _clear_logger_handlers(logging.getLogger(), logging.getLogger("werkzeug"), app.logger)
        logging.disable(logging.CRITICAL)
        app.logger.disabled = True
        return

    app.logger.disabled = False

    log_level_value = _resolve_log_level(app.config.get("LOG_LEVEL", "INFO"))
    if verbosity == "essential":
        log_level_value = max(log_level_value, logging.WARNING)

    log_level = logging.getLevelName(log_level_value)

    log_dir_cfg = app.config.get("LOG_DIR")
    default_dir = Path(app.instance_path) / "logs"
    log_dir = Path(log_dir_cfg) if log_dir_cfg else default_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = Path(app.config.get("LOG_FILE", log_dir / "litreel.log"))
    log_file.parent.mkdir(parents=True, exist_ok=True)

    handler_names: list[str] = []
    handlers: dict[str, Any] = {}

    formatter_class = "litreel.logging_utils.JsonFormatter" if log_format == "json" else None
    formatter_config: dict[str, Any]
    if formatter_class:
        formatter_config = {"()": formatter_class}
    else:
        formatter_config = {
            "format": "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        }

    formatters = {"standard": formatter_config}

    if log_to_stdout:
        handler_names.append("console")
        handlers["console"] = {
            "class": "logging.StreamHandler",
            "level": log_level,
            "formatter": "standard",
            "filters": ["request_meta"],
        }

    if log_to_file:
        handler_names.append("file")
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": log_level,
            "formatter": "standard",
            "filename": str(log_file),
            "maxBytes": max_bytes,
            "backupCount": backup_count,
            "encoding": "utf-8",
            "filters": ["request_meta"],
        }

    supabase_url = str(app.config.get("SUPABASE_URL", "")).strip()
    supabase_key = str(app.config.get("SUPABASE_API_KEY", "")).strip()
    supabase_table = str(app.config.get("SUPABASE_LOG_TABLE", "")).strip()
    if supabase_url and supabase_key and supabase_table:
        handler_names.append("supabase")
        handlers["supabase"] = {
            "class": "litreel.logging_utils.SupabaseLogHandler",
            "level": app.config.get("SUPABASE_LOG_LEVEL", log_level),
            "formatter": "standard",
            "filters": ["request_meta"],
            "supabase_url": supabase_url,
            "supabase_key": supabase_key,
            "table": supabase_table,
            "timeout": float(app.config.get("SUPABASE_LOG_TIMEOUT", 5.0)),
            "max_retries": int(app.config.get("SUPABASE_LOG_MAX_RETRIES", 3)),
        }

    if not handler_names:
        handler_names.append("console")
        handlers["console"] = {
            "class": "logging.StreamHandler",
            "level": log_level,
            "formatter": "standard",
            "filters": ["request_meta"],
        }

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_meta": {"()": "litreel.logging_utils.RequestContextFilter"},
            },
            "formatters": formatters,
            "handlers": handlers,
            "root": {
                "level": log_level,
                "handlers": handler_names,
            },
            "loggers": {
                "werkzeug": {
                    "level": "WARNING",
                    "handlers": handler_names,
                    "propagate": False,
                }
            },
        }
    )
