"""CPU-bound ``RelevanceRanker`` adapter backed by a ``ProcessPoolExecutor``.

The ranker keeps the event loop responsive under CPU load by dispatching the
synchronous hashing work to a separate process via
:meth:`asyncio.AbstractEventLoop.run_in_executor`. The pool itself is owned by
the FastAPI ``lifespan`` context (see :mod:`app.main`) and injected here so
this adapter never creates or tears down workers at request time.

``_rank_sync`` is defined at module scope so it is picklable by the ``spawn``
start method used on macOS and Windows. It does a deterministic SHA-256 hash
of each ``(image_id, question)`` pair and returns the ``top_k`` ids with the
lowest hash prefix - a stable, CPU-shaped operation with no real ML.
"""

from __future__ import annotations

import asyncio
import hashlib
import multiprocessing
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.errors import RelevanceRankerError

if TYPE_CHECKING:
    from concurrent.futures import ProcessPoolExecutor

    from app.core.models import ScreenshotWithBytes


def _rank_sync(image_ids: list[str], question: str, top_k: int) -> list[str]:
    """Rank ``image_ids`` deterministically by SHA-256 of ``image_id|question``.

    Runs inside a ``ProcessPoolExecutor`` worker - must be a top-level
    function so ``spawn`` can pickle the reference. Cheap bytes (ids +
    string) cross the process boundary; image payloads are intentionally
    left on the parent side because this stand-in implementation does not
    need them. A real embedding-based ranker would accept the bytes here.

    Args:
        image_ids: Candidate ids to rank. May be empty.
        question: Natural-language question, mixed into the hash input so
            different questions reorder the same ids.
        top_k: Maximum number of ids to return. Non-positive yields ``[]``.

    Returns:
        At most ``top_k`` ids sorted by ascending hash. Shorter than
        ``top_k`` iff fewer candidates were supplied.
    """
    if top_k <= 0 or not image_ids:
        return []
    ordered = sorted(
        image_ids,
        key=lambda image_id: hashlib.sha256(
            (image_id + "|" + question).encode("utf-8")
        ).hexdigest(),
    )
    return ordered[:top_k]


def _current_process_name() -> str:
    """Return the name of the process this call is running in.

    Test-observable hook: submitting this to the pool and asserting the
    result is not ``MainProcess`` proves rank work really crosses the
    process boundary. Keeping the helper in the same module as
    ``_rank_sync`` avoids a second module whose only purpose is a test
    import, and avoids putting pickle-sensitive helpers in test files.
    """
    return multiprocessing.current_process().name


@dataclass
class CpuRelevanceRanker:
    """Adapter wrapping a caller-owned ``ProcessPoolExecutor``.

    The rank call is a coroutine (Protocol-compliant) that schedules
    :func:`_rank_sync` on the pool and awaits the result. The adapter
    truncates oversized inputs defensively so a bug in the orchestrator's
    pre-fetch sampling cannot cause a runaway worker.

    Attributes:
        pool: Process pool owned by the application lifespan. Not closed
            by this adapter.
        max_input: Defensive upper bound on the number of screenshots the
            adapter will forward to the pool. Excess items are dropped
            from the tail without resampling; the orchestrator is
            expected to sample before reaching this adapter.
    """

    pool: ProcessPoolExecutor
    max_input: int

    async def rank(
        self,
        screenshots: list[ScreenshotWithBytes],
        question: str,
        top_k: int,
    ) -> list[str]:
        """Rank ``screenshots`` in a worker process and return up to ``top_k`` ids.

        Args:
            screenshots: Candidates to rank. May be empty.
            question: Natural-language question.
            top_k: Maximum number of ids to return. Must be non-negative.

        Returns:
            At most ``top_k`` image ids, ordered most relevant first. The
            ordering is stable for any given ``(image_ids, question, top_k)``
            input because :func:`_rank_sync` is pure.

        Raises:
            RelevanceRankerError: When the underlying pool is broken (a
                worker died mid-task) or already shut down. The route
                handler maps this to HTTP 503. The exception preserves
                the original cause for logging.
        """
        if top_k <= 0 or not screenshots:
            return []
        bounded = screenshots[: self.max_input]
        image_ids = [s.ref.image_id for s in bounded]
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self.pool, _rank_sync, image_ids, question, top_k)
        except BrokenProcessPool as exc:
            raise RelevanceRankerError(exc) from exc
        except RuntimeError as exc:
            # Raised by `run_in_executor` when the pool has already
            # shut down (e.g. a rank call racing with lifespan exit).
            # We surface the failure; recreation is not this adapter's
            # responsibility - see the RelevanceRankerError docstring.
            raise RelevanceRankerError(exc) from exc
