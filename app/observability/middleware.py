"""Request-ID middleware.

Generates (or accepts inbound) an ``X-Request-Id`` header and propagates
it to three places so every log line, every span, and the response
envelope carry the same correlation id:

1. ``request.state.request_id`` - read by the route handler and error
   handlers in :mod:`app.main`.
2. ``structlog.contextvars`` - merged into every log line emitted while
   the request is in flight.
3. The current OTel span attributes - makes the id searchable alongside
   the auto-instrumented server span.

Implemented as a pure ASGI middleware (not Starlette's
``BaseHTTPMiddleware``) because ``BaseHTTPMiddleware`` runs its dispatch
in a separate task, which breaks ``contextvars`` propagation to child
tasks in some Starlette versions. A plain ASGI callable keeps everything
in one task, so ``bind_contextvars`` stays visible to every ``await``
inside the request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
from opentelemetry import trace
from starlette.datastructures import State

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


REQUEST_ID_HEADER = "x-request-id"


class RequestIdMiddleware:
    """ASGI middleware that stamps a request id onto state, logs, and span.

    Reads the inbound ``X-Request-Id`` (case-insensitive) if present - so
    a caller already running a distributed trace can pin the id across
    services - otherwise mints a UUID4. The same id is returned in the
    response's ``X-Request-Id`` header so clients can correlate without
    parsing the body.

    Attributes:
        app: The wrapped ASGI application.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Store the wrapped ASGI app.

        Args:
            app: The downstream ASGI callable (FastAPI).
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Propagate the request id for every HTTP request.

        Non-HTTP scopes (lifespan, websocket) pass through untouched.

        Args:
            scope: ASGI scope.
            receive: ASGI receive callable.
            send: ASGI send callable, wrapped to append the response header.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _extract_or_mint_request_id(scope)

        # 1) state.request_id for error handlers + the route body.
        # ASGI scope state is conventionally a dict; Starlette wraps that dict
        # in Request.state later. Preserve the existing shape so other ASGI
        # layers can still use dict-style state after this middleware runs.
        existing = scope.get("state")
        if isinstance(existing, dict):
            existing["request_id"] = request_id
        elif isinstance(existing, State):
            existing.request_id = request_id
        else:
            scope["state"] = {"request_id": request_id}

        # 2) structlog contextvar for every log line emitted below.
        # 3) OTel span attribute for searchability.
        trace.get_current_span().set_attribute("request_id", request_id)
        tokens = structlog.contextvars.bind_contextvars(request_id=request_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append(
                    (REQUEST_ID_HEADER.encode("latin-1"), request_id.encode("latin-1")),
                )
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            structlog.contextvars.reset_contextvars(**tokens)


def _extract_or_mint_request_id(scope: Scope) -> str:
    """Return an inbound ``X-Request-Id`` header value or a fresh UUID4.

    Header names in ASGI arrive lower-cased as raw bytes. Empty values are
    treated as absent so a client sending ``X-Request-Id:`` with nothing
    after the colon gets a server-generated id rather than the empty
    string.
    """
    for name, value in scope.get("headers") or []:
        if name == REQUEST_ID_HEADER.encode("latin-1"):
            decoded: str = value.decode("latin-1").strip()
            if decoded:
                return decoded
    return str(uuid4())
