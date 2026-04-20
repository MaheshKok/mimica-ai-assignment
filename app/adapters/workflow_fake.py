"""In-memory fake implementation of the ``WorkflowServicesClient`` port.

Used by unit tests and by :func:`app.deps.build_demo_ports` for the
fully-offline demo wiring. Lets the orchestrator run end-to-end without
any network calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from app.core.models import ScreenshotRef


@dataclass
class FakeWorkflowServicesClient:
    """Canned-answer fake workflow client.

    ``stream_project`` returns an async iterator yielding whatever ``refs``
    is set to. ``qa_answer`` appends its call to ``qa_calls`` and returns
    ``canned_answer`` unchanged.

    Attributes:
        refs: Screenshots that ``stream_project`` will yield, in order.
        canned_answer: Return value for ``qa_answer``.
        stream_calls: Counter for ``stream_project`` invocations.
        qa_calls: List of ``(question, image_ids)`` tuples recording every
            ``qa_answer`` call so tests can assert both the ids passed and
            their order.
    """

    refs: list[ScreenshotRef] = field(default_factory=list)
    canned_answer: str = ""
    stream_calls: int = 0
    qa_calls: list[tuple[str, list[str]]] = field(default_factory=list)

    def stream_project(self, project_id: UUID) -> AsyncIterator[ScreenshotRef]:
        """Return an async iterator yielding the configured ``refs``.

        Args:
            project_id: Ignored by the fake; accepted to match the Protocol.

        Returns:
            An async iterator over ``self.refs``.
        """
        self.stream_calls += 1
        return self._iter()

    async def _iter(self) -> AsyncIterator[ScreenshotRef]:
        """Yield each configured ref in order."""
        for ref in self.refs:
            yield ref

    async def qa_answer(self, question: str, relevant_images: list[str]) -> str:
        """Record the call and return ``canned_answer``.

        Args:
            question: The question being asked.
            relevant_images: Image ids from the ranker, in order.

        Returns:
            The ``canned_answer`` attribute, unchanged.
        """
        self.qa_calls.append((question, list(relevant_images)))
        return self.canned_answer
