"""OpenTelemetry wiring for MHEAT.

Kept entirely optional: if ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset — or if the
OpenTelemetry packages are not installed — :func:`init_otel` is a no-op and
:func:`span` is a pass-through context manager. This keeps cold-start fast
and avoids forcing the dependency in minimal builds.

Environment variables:

* ``OTEL_EXPORTER_OTLP_ENDPOINT`` — if set, enables OTLP/grpc exporter.
* ``OTEL_SERVICE_NAME``           — service name; default ``mheat``.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)

_INITIALIZED = False
_TRACER: Any = None


def _try_import_otel() -> tuple[Any, Any, Any, Any, Any] | None:
    """Return (trace, TracerProvider, BatchSpanProcessor, OTLPExporter, Resource) or None."""
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        return trace, TracerProvider, BatchSpanProcessor, OTLPSpanExporter, Resource
    except Exception:  # noqa: BLE001
        return None


def init_otel(app: Any | None = None) -> None:
    """Initialize OpenTelemetry tracing if OTEL_EXPORTER_OTLP_ENDPOINT is set."""
    global _INITIALIZED, _TRACER
    if _INITIALIZED:
        return

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        logger.debug("OTEL endpoint not set — tracing disabled")
        _INITIALIZED = True
        return

    bits = _try_import_otel()
    if bits is None:
        logger.warning("OTEL endpoint set but opentelemetry not installed — tracing disabled")
        _INITIALIZED = True
        return

    trace, TracerProvider, BatchSpanProcessor, OTLPSpanExporter, Resource = bits

    service_name = os.environ.get("OTEL_SERVICE_NAME", "mheat")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer("mheat")

    # Auto-instrument FastAPI + httpx if the packages are installed.
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        if app is not None:
            FastAPIInstrumentor.instrument_app(app)
    except Exception as e:  # noqa: BLE001
        logger.debug("FastAPI auto-instrumentation skipped: %s", e)

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception as e:  # noqa: BLE001
        logger.debug("httpx auto-instrumentation skipped: %s", e)

    logger.info("OpenTelemetry tracing enabled → %s (service=%s)", endpoint, service_name)
    _INITIALIZED = True


@contextlib.contextmanager
def span(name: str, **attrs: Any) -> Iterator[None]:
    """Context manager that opens a tracer span when OTEL is active.

    Falls back to a no-op context manager when tracing is disabled, so
    call sites can sprinkle ``with span("detect_cube"): ...`` unconditionally.
    """
    if _TRACER is None:
        yield
        return
    with _TRACER.start_as_current_span(name) as s:
        for k, v in attrs.items():
            with contextlib.suppress(Exception):
                s.set_attribute(k, v)
        yield
