"""Structured JSON logging for MHEAT.

Two log formats are supported:

* ``json`` — one JSON object per line, suitable for log shippers.
* ``text`` — human-readable; default when running locally without Docker.

Selected at startup via the ``LOG_FORMAT`` env var. Every log record emitted
inside a request context automatically receives a ``request_id`` field.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
from typing import Any

from .middleware import current_request_id

_STANDARD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        base: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": current_request_id(),
        }
        # Stream in any custom `extra=` kwargs (path, status_code, duration_ms, ...)
        for k, v in record.__dict__.items():
            if k in _STANDARD_ATTRS or k.startswith("_"):
                continue
            if k == "request_id":
                base["request_id"] = v
                continue
            try:
                json.dumps(v)
                base[k] = v
            except (TypeError, ValueError):
                base[k] = repr(v)
        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(base, ensure_ascii=False)


class _RequestIdFilter(logging.Filter):
    """Attach the active request id to every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        if not hasattr(record, "request_id"):
            record.request_id = current_request_id()
        return True


def configure_logging(level: str = "INFO", fmt: str | None = None) -> None:
    """Install the MHEAT log configuration on the root logger.

    :param level: log level name, e.g. ``INFO``, ``DEBUG``.
    :param fmt:   ``json`` or ``text``; defaults to ``LOG_FORMAT`` env (json).
    """
    fmt = (fmt or os.environ.get("LOG_FORMAT") or "json").lower()
    # Force UTF-8 on stdout if available so log messages with non-ASCII chars
    # (arrows, accented names) don't crash the cp1252 default Windows console.
    with contextlib.suppress(AttributeError, ValueError):  # pragma: no cover — non-Windows or already configured
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s [rid=%(request_id)s] - %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
    root = logging.getLogger()
    # Replace existing handlers so repeated calls are idempotent.
    root.handlers = [handler]
    root.setLevel(level.upper())
