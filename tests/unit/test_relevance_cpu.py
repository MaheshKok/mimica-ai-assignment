"""Tests for ``app.adapters.relevance_cpu``.

Contract (derived from ``RelevanceRanker`` Protocol + module docstring):

- ``_rank_sync`` is a pure, top-level, picklable function that returns at
  most ``top_k`` ids deterministically.
- ``CpuRelevanceRanker.rank`` dispatches work to the injected
  :class:`~concurrent.futures.ProcessPoolExecutor` (so the event loop
  stays responsive) and never reshapes the result.
- The adapter truncates oversized inputs to ``max_input`` without
  re-sampling; the orchestrator is trusted to have sampled already.

The child-process assertion uses a dedicated module-level helper
(``_current_process_name``) defined in the adapter module: submitting it
through the same pool the adapter uses proves the work really leaves
the main process.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.adapters.relevance_cpu import (
    CpuRelevanceRanker,
    _current_process_name,
    _rank_sync,
)
from app.core.errors import RelevanceRankerError
from app.core.models import ScreenshotRef, ScreenshotWithBytes

if TYPE_CHECKING:
    from collections.abc import Iterator


def _item(image_id: str, ts: int = 0, data: bytes = b"x") -> ScreenshotWithBytes:
    """Minimal ``ScreenshotWithBytes`` for ranker input construction."""
    return ScreenshotWithBytes(ref=ScreenshotRef(timestamp=ts, image_id=image_id), data=data)


@pytest.fixture(scope="module")
def pool() -> Iterator[ProcessPoolExecutor]:
    """Module-scoped :class:`ProcessPoolExecutor` shared by the test cases.

    Spawning a worker is expensive (~100ms on macOS), so amortise that
    cost across every test in this module rather than per-test.
    """
    with ProcessPoolExecutor(max_workers=2) as executor:
        yield executor


# --------------------------------------------------------------------------- #
# _rank_sync - pure function, no pool needed                                  #
# --------------------------------------------------------------------------- #


class TestRankSync:
    def test_returns_empty_for_empty_ids(self) -> None:
        assert _rank_sync([], "q", 5) == []

    def test_returns_empty_for_zero_top_k(self) -> None:
        assert _rank_sync(["a", "b"], "q", 0) == []

    def test_returns_empty_for_negative_top_k(self) -> None:
        assert _rank_sync(["a", "b"], "q", -1) == []

    def test_length_capped_at_top_k(self) -> None:
        out = _rank_sync([f"img-{i}" for i in range(10)], "q", 3)
        assert len(out) == 3

    def test_length_bounded_by_input_when_top_k_larger(self) -> None:
        out = _rank_sync(["a", "b"], "q", 100)
        assert len(out) == 2
        assert set(out) == {"a", "b"}

    def test_deterministic_for_same_input(self) -> None:
        ids = [f"img-{i}" for i in range(8)]
        assert _rank_sync(ids, "q", 5) == _rank_sync(ids, "q", 5)

    def test_different_question_reorders_same_ids(self) -> None:
        ids = [f"img-{i}" for i in range(20)]
        a = _rank_sync(ids, "question A", 20)
        b = _rank_sync(ids, "question B", 20)
        assert set(a) == set(b)
        assert a != b, (
            "different questions must rehash; if this fires, the hash key no "
            "longer mixes the question and ordering became question-insensitive"
        )

    def test_returns_only_ids_present_in_input(self) -> None:
        ids = [f"img-{i}" for i in range(5)]
        out = _rank_sync(ids, "q", 5)
        assert set(out) <= set(ids)

    def test_input_order_does_not_change_output(self) -> None:
        ids = [f"img-{i}" for i in range(10)]
        shuffled = list(reversed(ids))
        assert _rank_sync(ids, "q", 10) == _rank_sync(shuffled, "q", 10)


# --------------------------------------------------------------------------- #
# CpuRelevanceRanker - pool-backed adapter                                    #
# --------------------------------------------------------------------------- #


class TestCpuRelevanceRanker:
    async def test_returns_empty_for_empty_screenshots(self, pool: ProcessPoolExecutor) -> None:
        ranker = CpuRelevanceRanker(pool=pool, max_input=10)
        assert await ranker.rank([], "q", 5) == []

    async def test_returns_empty_for_zero_top_k(self, pool: ProcessPoolExecutor) -> None:
        ranker = CpuRelevanceRanker(pool=pool, max_input=10)
        assert await ranker.rank([_item("a")], "q", 0) == []

    async def test_returns_empty_for_negative_top_k(self, pool: ProcessPoolExecutor) -> None:
        ranker = CpuRelevanceRanker(pool=pool, max_input=10)
        assert await ranker.rank([_item("a")], "q", -1) == []

    async def test_length_capped_at_top_k(self, pool: ProcessPoolExecutor) -> None:
        ranker = CpuRelevanceRanker(pool=pool, max_input=100)
        items = [_item(f"img-{i}") for i in range(10)]
        out = await ranker.rank(items, "q", 3)
        assert len(out) == 3

    async def test_order_matches_pure_rank_sync(self, pool: ProcessPoolExecutor) -> None:
        """Adapter must not reshape the pool result."""
        ranker = CpuRelevanceRanker(pool=pool, max_input=100)
        items = [_item(f"img-{i}") for i in range(8)]
        out = await ranker.rank(items, "q", 8)
        expected = _rank_sync([s.ref.image_id for s in items], "q", 8)
        assert out == expected

    async def test_deterministic_across_calls(self, pool: ProcessPoolExecutor) -> None:
        ranker = CpuRelevanceRanker(pool=pool, max_input=100)
        items = [_item(f"img-{i}") for i in range(5)]
        first = await ranker.rank(items, "q", 5)
        second = await ranker.rank(items, "q", 5)
        assert first == second

    async def test_different_question_reorders(self, pool: ProcessPoolExecutor) -> None:
        ranker = CpuRelevanceRanker(pool=pool, max_input=100)
        items = [_item(f"img-{i}") for i in range(20)]
        a = await ranker.rank(items, "q-A", 20)
        b = await ranker.rank(items, "q-B", 20)
        assert set(a) == set(b)
        assert a != b

    async def test_truncates_to_max_input_without_resampling(
        self, pool: ProcessPoolExecutor
    ) -> None:
        """A 50-item input with max_input=10 must see only the first ten ids.

        The adapter drops from the tail (no clever sampling) because the
        orchestrator is trusted to have sampled before calling. This
        asserts the defensive bound IS applied.
        """
        ranker = CpuRelevanceRanker(pool=pool, max_input=10)
        first_ten = [_item(f"keep-{i}") for i in range(10)]
        tail = [_item(f"drop-{i}") for i in range(40)]
        out = await ranker.rank(first_ten + tail, "q", 50)
        assert all(image_id.startswith("keep-") for image_id in out)
        assert len(out) == 10

    async def test_result_contains_only_ids_from_input(self, pool: ProcessPoolExecutor) -> None:
        ranker = CpuRelevanceRanker(pool=pool, max_input=100)
        items = [_item(f"img-{i}") for i in range(5)]
        out = await ranker.rank(items, "q", 5)
        assert set(out) <= {s.ref.image_id for s in items}


# --------------------------------------------------------------------------- #
# Child-process assertion                                                     #
# --------------------------------------------------------------------------- #


class TestPoolRunsInChildProcess:
    """Proof that ranker work actually leaves the main process.

    A local-only ranker would pass every functional test above. This
    submits the helper through the same pool and asserts the returned
    process name is not ``MainProcess``.
    """

    async def test_pool_worker_reports_child_process_name(self, pool: ProcessPoolExecutor) -> None:
        loop = asyncio.get_running_loop()
        name = await loop.run_in_executor(pool, _current_process_name)
        assert name != "MainProcess", (
            f"ranker must run in a worker, got {name!r} - "
            "this suggests the pool is a thread pool or the call ran inline"
        )

    def test_called_inline_reports_main_process(self) -> None:
        """Direct (non-pool) invocation returns ``MainProcess``.

        Paired with the pool test above this bounds the helper's
        behaviour on both sides - the hook is a true process-name
        readback, not a hard-coded string.
        """
        assert _current_process_name() == "MainProcess"

    async def test_rank_dispatches_rank_sync_via_pool(self, pool: ProcessPoolExecutor) -> None:
        """Prove ``rank()`` hands ``_rank_sync`` to the pool, not inline.

        The earlier ``test_pool_worker_reports_child_process_name`` only
        shows the pool *can* run work in a child; it does not show that
        ``rank()`` is the dispatch site. This spy on ``run_in_executor``
        closes that gap: exactly one call, first arg is the pool, second
        arg is ``_rank_sync`` by identity.
        """
        ranker = CpuRelevanceRanker(pool=pool, max_input=10)
        loop = asyncio.get_running_loop()
        with patch.object(loop, "run_in_executor", wraps=loop.run_in_executor) as spy:
            await ranker.rank([_item("a"), _item("b")], "q", 2)
        assert spy.call_count == 1
        (dispatched_executor, dispatched_fn, *_), _ = spy.call_args
        assert dispatched_executor is pool
        assert dispatched_fn is _rank_sync


# --------------------------------------------------------------------------- #
# Broken-pool / shutdown recovery path                                        #
# --------------------------------------------------------------------------- #


class TestBrokenPoolHandling:
    """``CpuRelevanceRanker.rank`` must translate pool failures.

    Without translation, a ``BrokenProcessPool`` (worker crashed or
    killed) or ``RuntimeError`` from a shut-down pool propagates as a
    generic 500. The adapter wraps both in ``RelevanceRankerError`` so
    the route's 503 handler can produce a controlled envelope, and so
    the underlying cause is preserved via ``__cause__`` for logging.
    """

    async def test_broken_process_pool_becomes_relevance_error(self) -> None:
        pool = ProcessPoolExecutor(max_workers=1)
        try:
            ranker = CpuRelevanceRanker(pool=pool, max_input=10)
            loop = asyncio.get_running_loop()
            broken = BrokenProcessPool("worker 42 died")
            with (
                patch.object(loop, "run_in_executor", side_effect=broken),
                pytest.raises(RelevanceRankerError) as excinfo,
            ):
                await ranker.rank([_item("a")], "q", 1)
            assert excinfo.value.cause is broken
            assert excinfo.value.__cause__ is broken
            assert "worker 42 died" in str(excinfo.value)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    async def test_shut_down_pool_runtimeerror_becomes_relevance_error(
        self,
    ) -> None:
        """``run_in_executor`` raises ``RuntimeError`` when the pool has shut down.

        Simulates a rank call that races with lifespan teardown: the
        adapter must still translate to ``RelevanceRankerError`` so the
        503 envelope path fires instead of a generic 500.
        """
        pool = ProcessPoolExecutor(max_workers=1)
        try:
            ranker = CpuRelevanceRanker(pool=pool, max_input=10)
            loop = asyncio.get_running_loop()
            runtime_err = RuntimeError("cannot schedule new futures after shutdown")
            with (
                patch.object(loop, "run_in_executor", side_effect=runtime_err),
                pytest.raises(RelevanceRankerError) as excinfo,
            ):
                await ranker.rank([_item("a")], "q", 1)
            assert excinfo.value.cause is runtime_err
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    async def test_early_empty_short_circuit_bypasses_pool(self, pool: ProcessPoolExecutor) -> None:
        """Empty input must not touch the pool at all.

        Matters because a broken pool plus an empty request should still
        return [] cleanly, not a 503. Asserts the adapter does not
        dispatch when there is nothing to rank.
        """
        ranker = CpuRelevanceRanker(pool=pool, max_input=10)
        loop = asyncio.get_running_loop()
        with patch.object(loop, "run_in_executor") as spy:
            assert await ranker.rank([], "q", 5) == []
            assert await ranker.rank([_item("a")], "q", 0) == []
        assert spy.call_count == 0
