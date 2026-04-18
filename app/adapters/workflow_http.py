"""HTTP adapter for the Workflow Services API.

Implements :class:`~app.ports.workflow.WorkflowServicesClient` on top of
``httpx.AsyncClient``. The adapter owns two HTTP concerns the orchestrator
never sees:

- Mapping the upstream NDJSON field ``screenshot_url`` onto
  :class:`~app.core.models.ScreenshotRef.image_id`. The rest of the code
  never learns that the upstream name is ``screenshot_url``.
- Translating any transport failure or non-2xx response into
  :class:`~app.core.errors.WorkflowUpstreamError`. Malformed NDJSON
  lines are logged and skipped - they do not raise.

The underlying :class:`httpx.AsyncClient` and base URL are injected so
that the FastAPI ``lifespan`` owns their construction and shutdown.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import httpx

from app.core.errors import WorkflowUpstreamError
from app.core.models import ScreenshotRef

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID


logger = logging.getLogger(__name__)


class HttpxWorkflowServicesClient:
    """HTTP implementation of :class:`WorkflowServicesClient`.

    Attributes:
        _client: Shared :class:`httpx.AsyncClient` (owned by lifespan).
        _base_url: Workflow Services base URL, without trailing slash.
    """

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        """Store the injected dependencies.

        Args:
            client: The shared async HTTP client. Ownership stays with the
                caller (lifespan); this adapter never closes it.
            base_url: Base URL of the Workflow Services API. Trailing
                slashes are normalised away.
        """
        self._client = client
        self._base_url = base_url.rstrip("/")

    def stream_project(self, project_id: UUID) -> AsyncIterator[ScreenshotRef]:
        """Return an async iterator of :class:`ScreenshotRef` for the project.

        Consumers iterate with ``async for`` and the underlying HTTP
        response is held open until the iteration completes or the
        generator is closed.

        Args:
            project_id: Mimica project UUID.

        Returns:
            An async iterator yielding one :class:`ScreenshotRef` per
            valid NDJSON line. Malformed lines are skipped.

        Raises:
            WorkflowUpstreamError: When the stream cannot be opened or the
                upstream returns a non-2xx status.
        """
        return self._stream(project_id)

    async def _stream(self, project_id: UUID) -> AsyncIterator[ScreenshotRef]:
        """Generator body - opens the streaming request and yields refs."""
        url = f"{self._base_url}/projects/{project_id}/stream"
        try:
            async with self._client.stream("GET", url) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    ref = _parse_ndjson_line(line)
                    if ref is not None:
                        yield ref
        except httpx.HTTPError as exc:
            raise WorkflowUpstreamError(exc) from exc

    async def qa_answer(self, question: str, relevant_images: list[str]) -> str:
        """POST the question and ranked image ids; return the answer string.

        Args:
            question: Natural-language question.
            relevant_images: Image ids in ranker order.

        Returns:
            The ``answer`` field from the upstream response.

        Raises:
            WorkflowUpstreamError: Any transport failure, non-2xx response,
                or malformed JSON body from the upstream.
        """
        url = f"{self._base_url}/qa/answer"
        try:
            response = await self._client.post(
                url,
                json={"question": question, "relevant_images": relevant_images},
            )
            response.raise_for_status()
            payload = response.json()
            return _extract_answer(payload)
        except (httpx.HTTPError, ValueError) as exc:
            # ValueError covers both json decode errors from non-JSON
            # bodies and schema failures raised by _extract_answer.
            raise WorkflowUpstreamError(exc) from exc


def _parse_ndjson_line(line: str) -> ScreenshotRef | None:
    """Parse a single NDJSON line into a :class:`ScreenshotRef`.

    Maps the upstream ``screenshot_url`` field onto ``image_id``. Any parse
    or schema error is logged at WARNING and returns ``None`` so the caller
    skips the row without aborting the stream.
    """
    if not line or not line.strip():
        return None
    try:
        row = json.loads(line)
        timestamp = int(row["timestamp"])
        image_id = str(row["screenshot_url"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        logger.warning("skipping malformed NDJSON line: %s", exc)
        return None
    return ScreenshotRef(timestamp=timestamp, image_id=image_id)


def _extract_answer(payload: object) -> str:
    """Pull ``answer`` from the upstream response body as a string.

    Guards against the upstream returning anything other than a dict with
    a string ``answer`` field; either case becomes
    :class:`WorkflowUpstreamError` via the caller's except clause.
    """
    if not isinstance(payload, dict) or "answer" not in payload:
        raise ValueError("workflow qa_answer response missing 'answer' field")
    answer = payload["answer"]
    if not isinstance(answer, str):
        raise ValueError("workflow qa_answer 'answer' must be a string")
    return answer
