"""HTTP route handlers for the Enriched QA Service.

Single route: ``POST /enriched-qa``. Generates a ``request_id``, delegates
to :func:`app.core.orchestrator.run`, and returns the response. Exception
mapping lives in :mod:`app.main` so the handler signature stays thin.
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends

from app.api.schemas import EnrichedQARequest, EnrichedQAResponse
from app.config import Settings  # noqa: TC001  (FastAPI resolves dependency annotations at runtime)
from app.core.orchestrator import run
from app.deps import Ports, get_ports, get_settings

router = APIRouter()


@router.post("/enriched-qa", response_model=EnrichedQAResponse)
async def post_enriched_qa(
    req: EnrichedQARequest,
    ports: Annotated[Ports, Depends(get_ports)],
    config: Annotated[Settings, Depends(get_settings)],
) -> EnrichedQAResponse:
    """Handle ``POST /enriched-qa``.

    Generates a fresh ``request_id`` and invokes the orchestrator. Phase 7
    moves ``request_id`` generation into middleware; this route will switch
    to reading ``request.state.request_id`` at that point.

    Args:
        req: Validated request body.
        ports: Protocol-dependency bundle injected via :func:`get_ports`.
        config: Runtime settings injected via :func:`get_settings`.

    Returns:
        :class:`EnrichedQAResponse` with the upstream answer and metadata.
    """
    request_id = str(uuid4())
    return await run(req, ports, config, request_id)
