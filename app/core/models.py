"""Domain value objects used throughout the enriched QA pipeline.

``ScreenshotRef`` is the lightweight reference a Workflow adapter yields from
the NDJSON stream. ``ScreenshotWithBytes`` wraps it with fetched image bytes
and is what the relevance ranker sees. Both are frozen so downstream code
cannot mutate them accidentally and both are hashable so they can be placed
in sets or used as dictionary keys.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScreenshotRef:
    """A reference to a single screenshot in a project stream.

    The ``image_id`` value is the identifier used by both the storage service
    and the QA endpoint. The Workflow HTTP adapter maps the upstream
    ``screenshot_url`` field onto this attribute at the adapter boundary so
    the rest of the code never sees ``screenshot_url``.

    Attributes:
        timestamp: Unix seconds when the screenshot was captured.
        image_id: Stable identifier used by storage and QA.
    """

    timestamp: int
    image_id: str


@dataclass(frozen=True, slots=True)
class ScreenshotWithBytes:
    """A ``ScreenshotRef`` bundled with the fetched image payload.

    Keeping the ``ref`` (including its timestamp) available to the ranker
    means rank functions that weight by recency have everything they need
    without a second lookup.

    Attributes:
        ref: The originating ``ScreenshotRef``.
        data: Raw image bytes fetched from storage.
    """

    ref: ScreenshotRef
    data: bytes
