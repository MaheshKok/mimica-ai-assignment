"""Deterministic in-process fake implementation of the ``RelevanceRanker`` port.

Used by unit tests where the real process-pool ranker would be overkill.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.models import ScreenshotWithBytes


@dataclass
class FakeRelevanceRanker:
    """Deterministic ranker based on a SHA-256 hash of ``image_id + question``.

    For any given input the output is stable. Two different ``question``
    strings will generally produce different orderings, which lets tests
    detect ordering bugs downstream.

    Attributes:
        call_count: Number of times ``rank`` has been invoked.
    """

    call_count: int = 0

    async def rank(
        self,
        screenshots: list[ScreenshotWithBytes],
        question: str,
        top_k: int,
    ) -> list[str]:
        """Return at most ``top_k`` image ids ordered by a deterministic hash.

        Args:
            screenshots: Candidates to rank. May be empty.
            question: Natural-language question, used as part of the hash
                input so different questions reorder the same screenshots.
            top_k: Maximum number of ids to return.

        Returns:
            A list of at most ``top_k`` image ids. Shorter when fewer
            candidates were supplied or when ``top_k`` is zero.
        """
        self.call_count += 1
        if top_k <= 0:
            return []
        ordered = sorted(
            screenshots,
            key=lambda s: hashlib.sha256(
                (s.ref.image_id + "|" + question).encode("utf-8")
            ).hexdigest(),
        )
        return [s.ref.image_id for s in ordered[:top_k]]
