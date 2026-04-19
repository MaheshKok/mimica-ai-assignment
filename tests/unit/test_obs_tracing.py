"""Tests for :mod:`app.observability.tracing`.

Contract (derived from the module docstring + OTel API):

- ``configure(settings)`` installs a real SDK :class:`TracerProvider`
  replacing OTel's default ``ProxyTracerProvider``.
- Picking the exporter respects ``settings.otel_exporter_otlp_endpoint``
  (unset -> console; set -> OTLP). Tests use an in-memory override to
  capture spans without touching the network.
- ``configure`` is idempotent: a second call returns the same provider
  and does not stack additional processors.
- ``shutdown`` flushes and resets, so the next ``configure`` installs
  a fresh provider rather than reusing the old one.
- ``instrument_app`` attaches :class:`FastAPIInstrumentor` exactly once
  per application instance.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util._once import Once

from app.config import Settings
from app.observability import tracing as tracing_mod


@pytest.fixture(autouse=True)
def _reset_tracing_module() -> None:
    """Force each test to re-enter ``configure`` from a clean module flag.

    OTel's :func:`trace.set_tracer_provider` is guarded by a module-level
    ``Once`` sentinel that refuses subsequent calls with a warning. Tests
    deliberately reach into that private state so each case installs a
    fresh provider and sees its own spans.
    """
    tracing_mod._configured = False
    tracing_mod._instrumented_apps = set()
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    trace._TRACER_PROVIDER = None


class TestConfigureInstallsSdkProvider:
    def test_installs_sdk_tracer_provider(self) -> None:
        settings = Settings(_env_file=None)  # type: ignore[arg-type]
        exporter = InMemorySpanExporter()
        tracing_mod.configure(settings, exporter=exporter)
        assert isinstance(trace.get_tracer_provider(), TracerProvider)

    def test_configure_returns_the_same_provider_twice(self) -> None:
        settings = Settings(_env_file=None)  # type: ignore[arg-type]
        first = tracing_mod.configure(settings, exporter=InMemorySpanExporter())
        second = tracing_mod.configure(settings, exporter=InMemorySpanExporter())
        assert first is second, "idempotent configure must not replace the provider"


class TestConfigureExporterSelection:
    def test_no_endpoint_falls_back_to_console_exporter(self) -> None:
        """With ``otel_exporter_otlp_endpoint`` unset the default exporter is the console one."""
        settings = Settings(_env_file=None)  # type: ignore[arg-type]
        exporter = tracing_mod._default_exporter(settings)
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        assert isinstance(exporter, ConsoleSpanExporter)

    def test_endpoint_set_picks_otlp_exporter(self) -> None:
        settings = Settings(
            _env_file=None,  # type: ignore[arg-type]
            otel_exporter_otlp_endpoint="http://collector.example:4317",
        )
        exporter = tracing_mod._default_exporter(settings)
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        assert isinstance(exporter, OTLPSpanExporter)


class TestSpansReachExporter:
    def test_manual_span_flows_through_configured_provider(self) -> None:
        """A span started after ``configure`` must reach the injected exporter."""
        settings = Settings(_env_file=None)  # type: ignore[arg-type]
        exporter = InMemorySpanExporter()
        tracing_mod.configure(settings, exporter=exporter)

        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("unit.span"):
            pass

        # Flush so the BatchSpanProcessor writes out.
        provider = trace.get_tracer_provider()
        assert isinstance(provider, TracerProvider)
        provider.force_flush()

        spans = exporter.get_finished_spans()
        assert [s.name for s in spans] == ["unit.span"]


class TestShutdown:
    def test_shutdown_allows_reconfigure(self) -> None:
        settings = Settings(_env_file=None)  # type: ignore[arg-type]
        tracing_mod.configure(settings, exporter=InMemorySpanExporter())
        tracing_mod.shutdown()
        # After shutdown the module flag is reset, so a new exporter
        # replaces whatever was there.
        second_exporter = InMemorySpanExporter()
        tracing_mod.configure(settings, exporter=second_exporter)

        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("post.reconfigure"):
            pass
        provider = trace.get_tracer_provider()
        assert isinstance(provider, TracerProvider)
        provider.force_flush()
        # Only the post-reconfigure span should reach second_exporter.
        assert [s.name for s in second_exporter.get_finished_spans()] == ["post.reconfigure"]

    def test_shutdown_noop_when_never_configured(self) -> None:
        """``shutdown`` without a preceding ``configure`` must not raise."""
        tracing_mod.shutdown()  # no error


class TestInstrumentApp:
    def test_instrument_app_is_idempotent_per_app(self) -> None:
        settings = Settings(_env_file=None)  # type: ignore[arg-type]
        tracing_mod.configure(settings, exporter=InMemorySpanExporter())
        app = FastAPI()
        tracing_mod.instrument_app(app)
        # Second call must not raise; the FastAPI instrumentor's internal
        # "already instrumented" guard would raise otherwise.
        tracing_mod.instrument_app(app)
