"""FastAPI application entry point.

Exposes a :data:`app` global that ``uvicorn app.main:app`` picks up.
The lifespan context **owns** the per-application resources - Phase 3
stashes the :class:`~app.deps.Ports` bundle and :class:`~app.config.Settings`
on ``app.state``, Phase 4 adds the shared ``httpx.AsyncClient``, Phase 6
adds the ``ProcessPoolExecutor``. Dependencies resolve these from
``request.app.state`` so nothing is owned at module scope.

Exception handlers map domain errors to the uniform ``{error, detail,
request_id}`` envelope from ``architect.md`` section 7. The validation
handler deliberately does **not** echo the request payload back in
``detail`` - it returns a compact ``loc: msg`` summary so long or
sensitive request bodies are not reflected to callers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import Settings
from app.core.errors import (
    PartialFailureThresholdExceededError,
    WorkflowUpstreamError,
)
from app.deps import build_demo_ports

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _request_id(request: Request) -> str:
    """Return the id the handler stashed on ``request.state``, or fall back.

    The route handler sets ``request.state.request_id`` before calling the
    orchestrator, so every error envelope raised *inside* the pipeline
    correlates to the same id the orchestrator used. The fallback covers
    the narrow window where validation fails before the handler body runs.
    """
    return getattr(request.state, "request_id", None) or str(uuid4())


def _error_envelope(
    status_code: int,
    *,
    error: str,
    detail: str,
    request_id: str,
) -> JSONResponse:
    """Build the uniform error envelope response.

    Shape matches ``architect.md`` section 7: ``{error, detail, request_id}``.
    """
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "detail": detail, "request_id": request_id},
    )


def _format_validation_errors(exc: RequestValidationError) -> str:
    """Render a Pydantic ``RequestValidationError`` as a sanitised string.

    The raw ``exc.errors()`` includes each failing field's ``input`` value,
    which for a validation failure IS the user-supplied payload. Echoing
    it back exposes every input field in error responses. This helper
    strips ``input`` (and the verbose ``ctx``) and returns one
    ``loc: msg`` clause per error, joined with semicolons.
    """
    parts: list[str] = []
    for err in exc.errors():
        loc_parts = err.get("loc", ())
        loc = ".".join(str(p) for p in loc_parts) if loc_parts else "(root)"
        msg = err.get("msg", "invalid")
        parts.append(f"{loc}: {msg}")
    return "; ".join(parts) or "invalid request"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Application lifespan - owns the per-app resources.

    Phase 3 constructs fresh fakes and :class:`Settings`. Phase 4 will
    additionally create a shared ``httpx.AsyncClient`` here and close it
    in the ``finally`` branch; Phase 6 adds the
    ``ProcessPoolExecutor`` in the same shape. The dependency layer
    (:mod:`app.deps`) never owns these resources - it reads them from
    ``app.state``.
    """
    application.state.settings = Settings()
    application.state.ports = build_demo_ports()
    try:
        yield
    finally:
        # Phase 4 closes httpx client; Phase 6 shuts down process pool.
        pass


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    Installed in a factory (rather than at module import) so tests can
    construct isolated instances when they need a fresh state dict.

    Returns:
        A configured :class:`FastAPI` instance with the Enriched QA route
        and all domain-error handlers attached.
    """
    application = FastAPI(title="Enriched QA Service", lifespan=lifespan)
    application.include_router(router)

    @application.exception_handler(RequestValidationError)
    async def _on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Override FastAPI's default 422 with 400 + a sanitised envelope."""
        return _error_envelope(
            400,
            error="invalid_request",
            detail=_format_validation_errors(exc),
            request_id=_request_id(request),
        )

    @application.exception_handler(PartialFailureThresholdExceededError)
    async def _on_partial_failure(
        request: Request, exc: PartialFailureThresholdExceededError
    ) -> JSONResponse:
        """Map the partial-failure threshold error to HTTP 502."""
        return _error_envelope(
            502,
            error="storage_partial_failure",
            detail=str(exc),
            request_id=_request_id(request),
        )

    @application.exception_handler(WorkflowUpstreamError)
    async def _on_workflow_upstream(request: Request, exc: WorkflowUpstreamError) -> JSONResponse:
        """Map Workflow Services failures to HTTP 502."""
        return _error_envelope(
            502,
            error="workflow_upstream_failure",
            detail=str(exc),
            request_id=_request_id(request),
        )

    @application.exception_handler(TimeoutError)
    async def _on_timeout(request: Request, _exc: TimeoutError) -> JSONResponse:
        """Map per-request budget timeouts to HTTP 504.

        Fires when :func:`asyncio.timeout` wraps the orchestrator call and
        the per-request budget ``REQUEST_TIMEOUT_MS`` elapses.
        """
        return _error_envelope(
            504,
            error="request_timeout",
            detail="Request exceeded the configured timeout.",
            request_id=_request_id(request),
        )

    return application


app = create_app()
