"""HTTP route handlers for the Enriched QA Service.

Single route: ``POST /enriched-qa``. The handler owns two responsibilities
the orchestrator does not:

1. Enforce the per-request budget - wrap the orchestrator call in
   :func:`asyncio.timeout` sized by ``config.request_timeout_ms``. A
   :class:`TimeoutError` propagates to the 504 handler in :mod:`app.main`.
2. Dependency-inject the :class:`Ports` bundle and :class:`Settings`.

``request_id`` is set by :class:`~app.observability.middleware.RequestIdMiddleware`
before the handler runs - the handler reads ``request.state.request_id``
rather than generating its own. This keeps the middleware as the single
source of truth for the correlation id across logs, spans, and the
response envelope.

Exception mapping to the uniform envelope lives in :mod:`app.main` so
the handler signature stays thin.

Note: this module intentionally does *not* use ``from __future__ import
annotations``. FastAPI identifies the ``Request`` parameter via a
class-identity check during dependency analysis, which fails when the
annotation is a string. Other modules can enable postponed evaluation
freely - the constraint only applies to FastAPI route signatures.
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.schemas import EnrichedQARequest, EnrichedQAResponse
from app.config import Settings
from app.core.orchestrator import run
from app.deps import Ports, get_ports, get_settings

router = APIRouter()


@router.post("/enriched-qa", response_model=EnrichedQAResponse)
async def post_enriched_qa(
    request: Request,
    req: EnrichedQARequest,
    ports: Annotated[Ports, Depends(get_ports)],
    config: Annotated[Settings, Depends(get_settings)],
) -> EnrichedQAResponse:
    """Handle ``POST /enriched-qa``.

    Reads the correlation id stamped on ``request.state`` by the request-id
    middleware and wraps the orchestrator call in a total-budget timeout
    taken from ``config.request_timeout_ms``.

    Args:
        request: Incoming request; used to read the middleware-bound
            ``request_id`` so the orchestrator and response envelope
            share the same value.
        req: Validated request body.
        ports: Protocol-dependency bundle injected via :func:`get_ports`.
        config: Runtime settings injected via :func:`get_settings`.

    Returns:
        :class:`EnrichedQAResponse` with the upstream answer and metadata.

    Raises:
        TimeoutError: When the orchestrator does not complete within
            ``config.request_timeout_ms``. The :mod:`app.main` handler
            maps this to HTTP 504.
        PartialFailureThresholdExceededError: Propagated from the
            orchestrator. Maps to HTTP 502.
        WorkflowUpstreamError: Propagated from the orchestrator. Maps to
            HTTP 502.
    """
    request_id: str = request.state.request_id
    async with asyncio.timeout(config.request_timeout_ms / 1000):
        return await run(req, ports, config, request_id)
