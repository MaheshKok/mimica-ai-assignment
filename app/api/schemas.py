"""Wire schemas for the Enriched QA REST endpoint.

These are the Pydantic models that cross the HTTP boundary. Domain types
(:class:`~app.core.models.ScreenshotRef`,
:class:`~app.core.models.ScreenshotWithBytes`) deliberately do not live
here so core code never depends on Pydantic.

The request schema uses ``from_`` for the ``from`` field because ``from``
is a Python keyword; ``populate_by_name=True`` means the model accepts
either spelling so tests can target either.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EnrichedQARequest(BaseModel):
    """Inbound request body for ``POST /enriched-qa``.

    Validates the project identifier, time window, and question text. A
    model-level validator rejects requests where ``from_ >= to`` so the
    window is always non-empty.

    Attributes:
        project_id: UUID of the Mimica project to query.
        from_: Unix seconds, inclusive lower bound. Wire alias is ``from``.
        to: Unix seconds, exclusive upper bound. Must be strictly greater
            than ``from_``.
        question: Natural-language question, 1-1024 characters.
    """

    model_config = ConfigDict(populate_by_name=True)

    project_id: UUID
    from_: int = Field(alias="from", ge=0)
    to: int = Field(ge=0)
    question: Annotated[str, Field(min_length=1, max_length=1024)]

    @model_validator(mode="after")
    def _check_window(self) -> EnrichedQARequest:
        """Reject time windows where ``from_`` is not strictly less than ``to``."""
        if self.from_ >= self.to:
            raise ValueError("'from' must be strictly less than 'to'")
        return self


class Meta(BaseModel):
    """Metadata attached to every successful response.

    Counts are non-negative integers. Default-empty dicts let handlers and
    the orchestrator populate only the keys they have data for.

    Attributes:
        request_id: Correlation id for logs, spans, and clients.
        images_considered: Number of ``ScreenshotRef`` values entering the
            fetch phase after time-window filtering.
        images_relevant: Number of image ids forwarded to the QA endpoint.
            Upper-bounded by the ranker's ``top_k``.
        errors: Per-kind failure counts, e.g. ``{"storage_fetch_failed": 3}``.
        latency_ms: Wall-clock timing per stage, e.g. ``{"total": 940}``.
    """

    request_id: str
    images_considered: int = Field(ge=0)
    images_relevant: int = Field(ge=0)
    errors: dict[str, int] = Field(default_factory=dict)
    latency_ms: dict[str, int] = Field(default_factory=dict)


class EnrichedQAResponse(BaseModel):
    """Successful response body for ``POST /enriched-qa``.

    Attributes:
        answer: The natural-language answer produced by the upstream QA
            endpoint. May be the empty string for empty windows.
        meta: Per-request metadata.
    """

    answer: str
    meta: Meta
