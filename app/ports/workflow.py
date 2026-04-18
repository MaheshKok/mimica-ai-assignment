"""Workflow Services client port.

Exposes the two operations the orchestrator needs:

- ``stream_project`` yields :class:`~app.core.models.ScreenshotRef` values for
  a project as they arrive on the upstream NDJSON stream.
- ``qa_answer`` posts the question plus the ranked image ids to the
  upstream QA endpoint and returns the answer string.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from app.core.models import ScreenshotRef


@runtime_checkable
class WorkflowServicesClient(Protocol):
    """Port for interacting with the upstream Workflow Services API."""

    def stream_project(self, project_id: UUID) -> AsyncIterator[ScreenshotRef]:
        """Stream ``ScreenshotRef`` values for a project as they arrive.

        No ordering guarantee is required at the Protocol level. The
        orchestrator toggles its short-circuit behaviour via the
        ``ASSUME_SORTED_STREAM`` config flag.

        Args:
            project_id: UUID of the Mimica project.

        Returns:
            An async iterator of :class:`~app.core.models.ScreenshotRef` values.

        Raises:
            WorkflowUpstreamError: On transport failure or non-2xx response.
        """
        ...

    async def qa_answer(self, question: str, relevant_images: list[str]) -> str:
        """Ask the upstream QA endpoint for an answer.

        Args:
            question: The natural-language question.
            relevant_images: Image ids in ranker order (most relevant first).

        Returns:
            The raw answer string returned by upstream. May be empty.

        Raises:
            WorkflowUpstreamError: On transport failure or non-2xx response.
        """
        ...
