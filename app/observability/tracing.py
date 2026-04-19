"""OpenTelemetry tracing setup and auto-instrumentation.

Wires an SDK :class:`~opentelemetry.sdk.trace.TracerProvider` with a
``BatchSpanProcessor`` feeding either the OTLP exporter (when
``OTEL_EXPORTER_OTLP_ENDPOINT`` is set) or a console exporter (so a local
``make run`` emits spans to stdout without extra infrastructure). The
FastAPI and httpx auto-instrumentors are attached once per application;
manual spans in the orchestrator become children of the server span.

Both :func:`configure` and :func:`instrument_app` are idempotent: the
module tracks its own state so repeated calls (test reloads, multiple
``create_app`` invocations) do not stack exporters or raise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.config import Settings


SERVICE_NAME_VALUE = "enriched-qa-service"

_configured = False
_instrumented_apps: set[int] = set()


def configure(settings: Settings, *, exporter: SpanExporter | None = None) -> TracerProvider:
    """Install a global :class:`TracerProvider` for the service.

    The first call constructs a provider with a single
    ``BatchSpanProcessor``. The exporter is picked in this order:

    1. The explicit ``exporter`` argument (tests inject an in-memory one).
    2. :class:`OTLPSpanExporter` when ``settings.otel_exporter_otlp_endpoint``
       is set.
    3. :class:`ConsoleSpanExporter` - spans stream to stdout, which is
       what the Phase 7 gate checks.

    Subsequent calls are no-ops (apart from returning the existing
    provider) so that test-reloads and repeated ``create_app`` runs do
    not stack processors.

    Args:
        settings: Active :class:`Settings`; read for the OTLP endpoint.
        exporter: Optional override - used by tests to capture spans
            without a live collector.

    Returns:
        The global :class:`TracerProvider`.
    """
    global _configured
    if _configured:
        return _current_sdk_provider_or_raise()

    resource = Resource.create({SERVICE_NAME: SERVICE_NAME_VALUE})
    provider = TracerProvider(resource=resource)

    chosen = exporter or _default_exporter(settings)
    provider.add_span_processor(BatchSpanProcessor(chosen))

    trace.set_tracer_provider(provider)
    _configured = True
    return provider


def instrument_app(application: FastAPI) -> None:
    """Attach FastAPI + httpx auto-instrumentation to ``application``.

    Must be called after :func:`configure` so the auto-instrumentors pick
    up the SDK provider. Idempotent per application instance - safe to
    call in ``create_app`` even when tests build multiple apps.

    Args:
        application: The FastAPI app to instrument.
    """
    key = id(application)
    if key in _instrumented_apps:
        return
    FastAPIInstrumentor.instrument_app(application)
    # ``instrument()`` is process-global: calling it more than once is a
    # no-op but logs a warning. Gate with the module flag.
    if not _instrumented_apps:
        HTTPXClientInstrumentor().instrument()
    _instrumented_apps.add(key)


def shutdown() -> None:
    """Flush and shut down the active tracer provider.

    Called from the FastAPI ``lifespan`` finally branch so batched spans
    are exported before the process exits. Resets the module flag *and*
    OTel's internal ``set_tracer_provider`` guard so the next
    ``configure`` call installs a fresh provider (relevant under test
    reloads and under uvicorn reload workers).
    """
    global _configured, _instrumented_apps
    if not _configured:
        return
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.shutdown()
    # OTel guards ``set_tracer_provider`` with a module-level ``Once``
    # sentinel that refuses subsequent overrides with a warning. A fresh
    # ``configure`` after shutdown has to swap the provider, so we reset
    # the sentinel along with our own flags. This is the only officially
    # documented way to run the SDK twice in one process.
    from opentelemetry.util._once import Once

    trace._TRACER_PROVIDER_SET_ONCE = Once()
    trace._TRACER_PROVIDER = None
    _configured = False
    _instrumented_apps = set()


def _default_exporter(settings: Settings) -> SpanExporter:
    """Pick an exporter based on ``settings.otel_exporter_otlp_endpoint``."""
    endpoint = settings.otel_exporter_otlp_endpoint
    if endpoint:
        return OTLPSpanExporter(endpoint=endpoint)
    return ConsoleSpanExporter()


def _current_sdk_provider_or_raise() -> TracerProvider:
    """Return the active SDK provider, asserting the configure invariant.

    Invoked only after the module flag says we configured. If some other
    code swapped the global provider in the meantime we surface that
    loudly rather than silently returning an unrelated instance.
    """
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        raise RuntimeError(
            "tracing.configure() was called, but the global tracer "
            "provider is no longer an SDK TracerProvider. Something "
            "overwrote it."
        )
    return provider
