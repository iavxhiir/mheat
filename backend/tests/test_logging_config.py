"""Tests for structured JSON logging (`app.logging_config`)."""

from __future__ import annotations

import io
import json
import logging

from app.logging_config import JsonFormatter, configure_logging


def _make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="mheat.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def test_json_formatter_emits_single_line_object_with_mandatory_keys():
    fmt = JsonFormatter()
    line = fmt.format(_make_record())
    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "mheat.test"
    assert payload["message"] == "hello world"
    assert "timestamp" in payload and payload["timestamp"].endswith("Z")
    assert "request_id" in payload  # may be None outside a request scope


def test_json_formatter_copies_json_safe_extras_and_reprs_the_rest():
    fmt = JsonFormatter()

    class NotJsonable:
        def __repr__(self) -> str:
            return "<not-jsonable>"

    line = fmt.format(
        _make_record(path="/api/events", status_code=200, thing=NotJsonable()),
    )
    payload = json.loads(line)
    assert payload["path"] == "/api/events"
    assert payload["status_code"] == 200
    assert payload["thing"] == "<not-jsonable>"


def test_json_formatter_propagates_explicit_request_id_on_record():
    fmt = JsonFormatter()
    line = fmt.format(_make_record(request_id="abc-123"))
    assert json.loads(line)["request_id"] == "abc-123"


def test_json_formatter_serialises_exc_info():
    fmt = JsonFormatter()
    try:
        raise ValueError("bad input")
    except ValueError:
        import sys
        record = _make_record()
        record.exc_info = sys.exc_info()
    payload = json.loads(fmt.format(record))
    assert "exc_info" in payload and "ValueError" in payload["exc_info"]


def test_configure_logging_json_mode_writes_json_to_root_handler():
    configure_logging(level="DEBUG", fmt="json")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    buf = io.StringIO()
    handler.stream = buf  # capture output
    logging.getLogger("mheat.access").info(
        "http_request", extra={"path": "/api/health", "status_code": 200},
    )
    handler.flush()
    line = buf.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["message"] == "http_request"
    assert payload["path"] == "/api/health"


def test_configure_logging_text_mode_is_not_json_but_contains_message():
    configure_logging(level="INFO", fmt="text")
    root = logging.getLogger()
    handler = root.handlers[0]
    buf = io.StringIO()
    handler.stream = buf
    logging.getLogger("mheat.text").warning("careful now")
    handler.flush()
    line = buf.getvalue().strip().splitlines()[-1]
    try:
        json.loads(line)
        is_json = True
    except ValueError:
        is_json = False
    assert not is_json
    assert "careful now" in line
    assert "WARNING" in line


def test_configure_logging_is_idempotent_and_replaces_handlers():
    configure_logging(level="INFO", fmt="json")
    root = logging.getLogger()
    first = root.handlers
    configure_logging(level="INFO", fmt="text")
    second = root.handlers
    assert len(first) == 1 and len(second) == 1
    assert first[0] is not second[0]
