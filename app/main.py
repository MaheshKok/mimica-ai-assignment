"""FastAPI application entry point.

Exposes a :data:`app` global that ``uvicorn app.main:app`` picks up.
Wires the route module, installs exception handlers that map domain errors
to the uniform ``{error, detail, request_id}`` envelope from
``architect.md`` section 7, and defines a ``lifespan`` context whose body
is empty in Phase 3 - Phase 4 adds the shared ``httpx.AsyncClient`` and
Phase 6 adds the ``ProcessPoolExecutor``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.errors import (
    PartialFailureThresholdExceededError,
    WorkflowUpstreamError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _request_id(request: Request) -> str:
    """Return the correlation id bound by middleware, or a fresh UUID.

    Phase 3 doesn't yet have the request-id middleware wired. Handlers
    generate their own id; if an exception fires before that point (for
    example ``RequestValidationError``) we still return *some* id so the
    envelope contract holds. Phase 7 populates ``request.state.request_id``
    from the middleware and this helper reads it from there.
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


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan shape.

    Phase 3 owns no resources. Phase 4 will construct the shared
    :class:`httpx.AsyncClient` here; Phase 6 the ranker
    :class:`~concurrent.futures.ProcessPoolExecutor`.
    """
    yield


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    Installed in a factory (rather than at module import) so tests can
    construct isolated instances if needed.

    Returns:
        A configured :class:`FastAPI` instance with the Enriched QA route
        and all domain-error handlers attached.
    """
    application = FastAPI(title="Enriched QA Service", lifespan=lifespan)
    application.include_router(router)

    @application.exception_handler(RequestValidationError)
    async def _on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Override FastAPI's default 422 with 400 + the uniform envelope."""
        return _error_envelope(
            400,
            error="invalid_request",
            detail=str(exc.errors()),
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
        """Map total-budget timeouts to HTTP 504."""
        return _error_envelope(
            504,
            error="request_timeout",
            detail="Request exceeded the configured timeout.",
            request_id=_request_id(request),
        )

    return application


app = create_app()
