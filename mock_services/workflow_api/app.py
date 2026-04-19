"""Mock Workflow Services API.

FastAPI app exposing the two routes the real adapter talks to:

- ``GET /projects/{project_id}/stream``: NDJSON rows of
  ``{"timestamp": <int>, "screenshot_url": <str>}``. Rows are sorted by
  timestamp ascending by default (the assumption the orchestrator relies
  on). Pass ``?shuffle=true`` to exercise the drain-to-EOF fallback.
- ``POST /qa/answer``: returns ``{"answer": ...}`` echoing the question
  plus the received ``relevant_images`` in the order received. Order
  preservation matters: integration tests assert the ranker's order
  survives the wire trip.

An ``create_app(refs=...)`` factory lets integration tests spin up a
mock with custom refs (e.g. identifiers containing URL-reserved
characters) without touching module-level state.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003  (FastAPI resolves path params at runtime)

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# Default refs used by ``make run-mocks``. Ten rows, 30 seconds apart,
# starting at an arbitrary epoch. Tests override this via create_app.
DEFAULT_REFS: tuple[dict[str, object], ...] = tuple(
    {"timestamp": 1_700_000_000 + i * 30, "screenshot_url": f"img-{i:03d}.png"} for i in range(10)
)


class QARequest(BaseModel):
    """Body schema for ``POST /qa/answer``."""

    question: str = Field(min_length=1, max_length=1024)
    relevant_images: list[str] = Field(default_factory=list)


def create_app(refs: list[dict[str, object]] | None = None) -> FastAPI:
    """Build the workflow mock FastAPI app.

    Args:
        refs: Optional override for the stream's default refs. Each ref is
            a dict with ``timestamp`` (int) and ``screenshot_url`` (str).
            When ``None``, :data:`DEFAULT_REFS` is used.

    Returns:
        A configured :class:`FastAPI` instance.
    """
    application = FastAPI(title="Mock Workflow API", docs_url=None, redoc_url=None)
    ref_list = list(refs if refs is not None else DEFAULT_REFS)

    @application.get("/projects/{project_id}/stream")
    async def stream_project(
        project_id: UUID,
        shuffle: bool = False,
    ) -> StreamingResponse:
        """Stream NDJSON rows for ``project_id``.

        ``shuffle=true`` permutes the rows deterministically using
        ``project_id`` as the seed so the same request reproduces the same
        order.
        """
        rows = list(ref_list)
        if shuffle:
            random.Random(int(project_id)).shuffle(rows)

        async def _body() -> AsyncIterator[bytes]:
            for row in rows:
                yield (json.dumps(row) + "\n").encode("utf-8")
                # Tiny sleep so aiter_lines() actually iterates chunk-by-chunk
                # rather than delivering the whole body in one read.
                await asyncio.sleep(0.001)

        return StreamingResponse(_body(), media_type="application/x-ndjson")

    @application.post("/qa/answer")
    async def qa_answer(req: QARequest) -> dict[str, str]:
        """Echo the question and image ids verbatim (no reordering)."""
        return {
            "answer": f"Q: {req.question} | IDs: {','.join(req.relevant_images)}",
        }

    return application


app = create_app()
