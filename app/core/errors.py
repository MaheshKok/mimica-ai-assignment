"""Port-level exception hierarchy for the enriched QA pipeline.

These are raised by adapters (``StorageFetchError``, ``WorkflowUpstreamError``)
or by the orchestrator (``PartialFailureThresholdExceededErrorError``). Route
exception handlers catch them and produce the uniform error envelope defined
in ``architect.md`` section 7.

Every error here is a direct subclass of :class:`Exception` so callers can
catch the hierarchy by :class:`EnrichedQAError` or each class individually.

Per-class ``__init__`` signatures deliberately take typed domain arguments
(``image_id``, ``cause``, counts) rather than only a pre-formatted message;
``# noqa: B904`` / ``# noqa: B042`` markers silence ``flake8-bugbear`` because
these exceptions are consumed in-process at HTTP handler boundaries and are
not pickled or ``copy.copy``-traversed.
"""

from __future__ import annotations


class EnrichedQAError(Exception):
    """Base class for all service-raised errors.

    Grouping these lets handlers catch the domain hierarchy without
    swallowing arbitrary third-party exceptions.
    """


class StorageFetchError(EnrichedQAError):
    """Raised when a single-image fetch fails at the storage port.

    Adapters wrap HTTP 404s, 5xx responses, and transport failures in this
    error. The orchestrator catches it, increments a counter, and either
    continues or triggers :class:`PartialFailureThresholdExceededErrorError`.

    Attributes:
        image_id: The image that failed to fetch.
        cause: The underlying exception (transport error, response error,
            ``KeyError`` from an in-memory fake, etc.).
    """

    def __init__(self, image_id: str, cause: Exception) -> None:
        """Initialize with the failed image id and the underlying cause.

        Args:
            image_id: The image identifier that failed to fetch.
            cause: The exception that triggered the failure. Stored verbatim
                so callers can inspect it.
        """
        super().__init__(f"storage fetch failed for {image_id!r}: {cause}")
        self.image_id = image_id
        self.cause = cause


class WorkflowUpstreamError(EnrichedQAError):
    """Raised when the Workflow Services API fails for any reason.

    Covers both ``stream_project`` and ``qa_answer`` failures: non-2xx
    responses, transport errors, or malformed payloads that prevent
    processing.

    Attributes:
        cause: The underlying exception.
    """

    def __init__(self, cause: Exception) -> None:
        """Initialize wrapping the underlying cause.

        Args:
            cause: The exception the adapter wants to surface to the
                orchestrator.
        """
        super().__init__(f"workflow upstream error: {cause}")
        self.cause = cause


class PartialFailureThresholdExceededError(EnrichedQAError):
    """Raised when too many storage fetches fail during a single request.

    If ``failed / total > MAX_FETCH_FAILURE_RATIO`` the orchestrator aborts
    the request rather than answering from a degraded image set. Route
    handler maps this to HTTP 502.

    Attributes:
        failed: Number of fetches that failed.
        total: Total number of fetches attempted. Always ``> 0`` when this
            error is raised — the empty-window path is handled separately.
    """

    def __init__(self, failed: int, total: int) -> None:
        """Initialize with the failure and total counts.

        Args:
            failed: Count of failed fetches.
            total: Count of attempted fetches. Must be greater than zero
                when constructing this error.
        """
        ratio = failed / total if total else 0.0
        super().__init__(
            f"partial failure threshold exceeded: "
            f"{failed}/{total} storage fetches failed ({ratio:.1%})"
        )
        self.failed = failed
        self.total = total
