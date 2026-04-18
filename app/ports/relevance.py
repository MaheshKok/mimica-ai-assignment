"""Relevance ranker port.

The ranker reduces a list of fetched screenshots to at most ``top_k``
image ids, ordered most relevant first. CPU-bound implementations route
the work through a :class:`~concurrent.futures.ProcessPoolExecutor`; fakes
can return a deterministic slice in-process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.core.models import ScreenshotWithBytes


@runtime_checkable
class RelevanceRanker(Protocol):
    """Port for ranking screenshots by relevance to a question."""

    async def rank(
        self,
        screenshots: list[ScreenshotWithBytes],
        question: str,
        top_k: int,
    ) -> list[str]:
        """Return the top_k most relevant image ids, ordered.

        Args:
            screenshots: Candidates to rank. May be empty.
            question: The natural-language question.
            top_k: Maximum number of ids to return. Must be non-negative.

        Returns:
            A list of at most ``top_k`` image ids, ordered most relevant
            first. May be shorter than ``top_k`` if fewer candidates were
            supplied.
        """
        ...
