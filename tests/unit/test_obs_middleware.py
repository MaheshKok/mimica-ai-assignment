"""Tests for :mod:`app.observability.middleware`.

Contract (derived from the middleware docstring):

- Every HTTP request either accepts an inbound ``X-Request-Id`` header
  or receives a fresh UUID4. Empty values are treated as absent.
- The id is stamped on ``request.state.request_id`` so the route handler
  can read it.
- The id is bound to the ``structlog`` contextvars for the duration of
  the request and cleared afterwards.
- The id is set as an attribute on the current OTel span.
- The outbound response includes an ``X-Request-Id`` header equal to
  the stamped id.
- Non-HTTP scopes (lifespan, websocket) pass through untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
import structlog
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util._once import Once

from app.config import Settings
from app.observability import tracing as tracing_mod
from app.observability.middleware import REQUEST_ID_HEADER, RequestIdMiddleware

if TYPE_CHECKING:
    from collections.abc import Iterator


# --------------------------------------------------------------------------- #
# Shared helpers                                                              #
# --------------------------------------------------------------------------- #


def _make_app() -> tuple[FastAPI, dict[str, object]]:
    """Build a tiny FastAPI app that echoes what the middleware stamped.

    Returns:
        The wired application and a capture dict the route populates.
    """
    captured: dict[str, object] = {}
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/echo")
    def _echo(request: Request) -> dict[str, object]:
        captured["state_request_id"] = request.state.request_id
        log = structlog.get_logger("middleware-test")
        # Trigger one log line so the test can assert the contextvar
        # binding flowed through at handler time.
        captured["bound_context"] = dict(structlog.contextvars.get_contextvars())
        log.info("handled")
        return {"seen_state": request.state.request_id}

    return app, captured


# --------------------------------------------------------------------------- #
# Request-id propagation                                                      #
# --------------------------------------------------------------------------- #


class TestRequestIdIntoState:
    def test_inbound_header_propagates_to_state_and_response(self) -> None:
        app, captured = _make_app()
        client = TestClient(app)
        inbound = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        r = client.get("/echo", headers={"X-Request-Id": inbound})

        assert r.status_code == 200
        assert r.headers.get(REQUEST_ID_HEADER) == inbound
        assert captured["state_request_id"] == inbound

    def test_missing_header_mints_uuid4(self) -> None:
        app, captured = _make_app()
        client = TestClient(app)

        r = client.get("/echo")

        assert r.status_code == 200
        minted = r.headers.get(REQUEST_ID_HEADER)
        assert minted is not None
        # Must parse as UUID and survive round-trip (case-insensitive)
        parsed = UUID(minted)
        assert str(parsed) == minted.lower()
        assert captured["state_request_id"] == minted

    def test_empty_inbound_header_treated_as_absent(self) -> None:
        """A client sending an empty X-Request-Id must not poison the id."""
        app, captured = _make_app()
        client = TestClient(app)

        r = client.get("/echo", headers={"X-Request-Id": ""})

        assert r.status_code == 200
        minted = r.headers.get(REQUEST_ID_HEADER)
        assert minted
        UUID(minted)  # fresh uuid, not empty
        assert captured["state_request_id"] == minted

    def test_whitespace_inbound_header_treated_as_absent(self) -> None:
        """Whitespace-only header should not be honoured either."""
        app, captured = _make_app()
        client = TestClient(app)

        r = client.get("/echo", headers={"X-Request-Id": "   "})

        minted = r.headers.get(REQUEST_ID_HEADER)
        assert minted is not None
        assert minted.strip()
        UUID(minted)
        assert captured["state_request_id"] == minted

    def test_two_independent_requests_get_distinct_ids(self) -> None:
        app, _captured = _make_app()
        client = TestClient(app)

        r1 = client.get("/echo")
        first = r1.headers[REQUEST_ID_HEADER]
        r2 = client.get("/echo")
        second = r2.headers[REQUEST_ID_HEADER]
        assert first != second


# --------------------------------------------------------------------------- #
# structlog contextvar binding                                                #
# --------------------------------------------------------------------------- #


class TestContextvarBinding:
    def test_request_id_available_as_contextvar_during_handler(self) -> None:
        app, captured = _make_app()
        client = TestClient(app)
        inbound = "11111111-2222-3333-4444-555555555555"

        client.get("/echo", headers={"X-Request-Id": inbound})

        bound = captured["bound_context"]
        assert isinstance(bound, dict)
        assert bound.get("request_id") == inbound

    def test_contextvar_cleared_after_request(self) -> None:
        """The binding must not leak to the next request or test run."""
        app, _captured = _make_app()
        client = TestClient(app)

        client.get("/echo", headers={"X-Request-Id": "leak-test"})

        # The middleware reset runs in the request task. On the test
        # thread (the one running this assertion), no contextvar should
        # carry over.
        assert "request_id" not in structlog.contextvars.get_contextvars()


# --------------------------------------------------------------------------- #
# OTel span attribute                                                         #
# --------------------------------------------------------------------------- #


@pytest.fixture
def fresh_tracing() -> Iterator[InMemorySpanExporter]:
    """Install an in-memory exporter and reset OTel private state around the test."""
    tracing_mod._configured = False
    tracing_mod._instrumented_apps = set()
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    trace._TRACER_PROVIDER = None
    exporter = InMemorySpanExporter()
    tracing_mod.configure(Settings(_env_file=None), exporter=exporter)  # type: ignore[arg-type]
    yield exporter
    tracing_mod.shutdown()


class TestOTelSpanAttribute:
    def test_request_id_set_on_current_span(self, fresh_tracing: InMemorySpanExporter) -> None:
        """A manual parent span should carry ``request_id`` from the middleware."""
        # Run the middleware inside an active span so
        # ``trace.get_current_span().set_attribute`` has somewhere to go.
        app = FastAPI()
        app.add_middleware(RequestIdMiddleware)

        tracer = trace.get_tracer("middleware-test")

        @app.get("/trace")
        def _trace(request: Request) -> dict[str, str]:
            return {"ok": request.state.request_id}

        # Wrap the TestClient call in an outer span so the middleware's
        # set_attribute has a non-INVALID span to target.
        inbound = "99999999-8888-7777-6666-555555555555"
        client = TestClient(app)
        with tracer.start_as_current_span("outer"):
            client.get("/trace", headers={"X-Request-Id": inbound})

        # Force the span out to the exporter.
        provider = trace.get_tracer_provider()
        assert isinstance(provider, TracerProvider)
        provider.force_flush()
        # The outer span recorded the attribute the middleware set when
        # it was the current span.
        outer_ended = [s for s in fresh_tracing.get_finished_spans() if s.name == "outer"]
        assert outer_ended, "outer span should have been exported"
        assert outer_ended[0].attributes is not None
        assert outer_ended[0].attributes.get("request_id") == inbound


# --------------------------------------------------------------------------- #
# Non-HTTP scope passthrough                                                  #
# --------------------------------------------------------------------------- #


class TestScopeStatePreservation:
    def test_preserves_existing_state_attributes(self) -> None:
        """Middleware must add request_id without destroying prior State attributes.

        Regression gate for the non-destructive state fix: the previous
        implementation replaced scope["state"] unconditionally, discarding
        any attributes a prior ASGI layer (e.g. OTel middleware) had set.
        """
        import asyncio

        from starlette.datastructures import State

        captured: dict[str, object] = {}

        async def inner_app(scope: dict[str, object], receive: object, send: object) -> None:
            state = scope.get("state")
            if isinstance(state, State):
                captured["prior_value"] = getattr(state, "prior_value", None)
                captured["request_id"] = getattr(state, "request_id", None)

        prior_state = State()
        prior_state.prior_value = "must_survive"

        middleware = RequestIdMiddleware(inner_app)  # type: ignore[arg-type]

        async def _receive() -> dict[str, str]:
            return {"type": "http.disconnect"}

        async def _send(msg: object) -> None:
            pass

        asyncio.run(
            middleware(  # type: ignore[arg-type]
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/",
                    "query_string": b"",
                    "headers": [],
                    "state": prior_state,
                },
                _receive,
                _send,
            )
        )
        assert captured.get("prior_value") == "must_survive"
        assert captured.get("request_id") is not None


class TestNonHttpScopePassthrough:
    def test_lifespan_scope_passes_through(self) -> None:
        """A lifespan scope must reach the wrapped app untouched."""
        received: list[dict[str, object]] = []

        async def inner(scope: dict[str, object], receive: object, send: object) -> None:
            received.append(dict(scope))

        middleware = RequestIdMiddleware(inner)  # type: ignore[arg-type]

        import asyncio

        asyncio.run(
            middleware(  # type: ignore[arg-type]
                {"type": "lifespan"},
                _noop_receive,  # type: ignore[arg-type]
                _noop_send,  # type: ignore[arg-type]
            )
        )
        assert received == [{"type": "lifespan"}]
        # No state was added
        assert "state" not in received[0]


async def _noop_receive() -> dict[str, str]:
    return {"type": "lifespan.startup"}


async def _noop_send(message: dict[str, object]) -> None:  # pragma: no cover - trivial
    return None
