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

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, model_validator


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

    # The ``json_schema_extra`` example is what Swagger's "Try it out"
    # panel pre-fills. Using the assignment brief's canonical payload
    # (docs/1_assignment.md) means hitting Execute with no edits lands
    # inside the mock stream and produces the license-plate answer.
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "project_id": "8b80353b-aee6-4835-ba7e-c3b79010bc0b",
                "from": 1754037000,
                "to": 1754039000,
                "question": "What car license plates are being looked at?",
            }
        },
    )

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

    Counts are non-negative integers. Default-empty containers let handlers
    and the orchestrator populate only the keys they have data for.

    Attributes:
        request_id: Correlation id for logs, spans, and clients.
        images_considered: Number of ``ScreenshotRef`` values entering the
            fetch phase after time-window filtering.
        images_relevant: Number of image ids forwarded to the QA endpoint.
            Upper-bounded by the ranker's ``top_k``.
        errors: Per-kind failure counts, e.g. ``{"storage_fetch_failed": 3}``.
        latency_ms: Wall-clock timing per stage, e.g. ``{"total": 940}``.
        relevant_image_ids: The ranker's output, in rank order, as a
            machine-parseable list. Mirrors what was POSTed to the upstream
            ``/qa/answer`` endpoint so clients can inspect the ranker's
            selection without parsing the free-form ``answer`` string.
            Empty for empty-window requests.
    """

    request_id: str
    images_considered: NonNegativeInt
    images_relevant: NonNegativeInt
    errors: dict[str, NonNegativeInt] = Field(default_factory=dict)
    latency_ms: dict[str, NonNegativeInt] = Field(default_factory=dict)
    relevant_image_ids: list[str] = Field(default_factory=list)


class EnrichedQAResponse(BaseModel):
    """Successful response body for ``POST /enriched-qa``.

    Attributes:
        answer: The natural-language answer produced by the upstream QA
            endpoint. May be the empty string for empty windows.
        meta: Per-request metadata.
    """

    answer: str
    meta: Meta
