"""Tests for ``app.adapters.relevance_fake.FakeRelevanceRanker``.

Contract: returns at most ``top_k`` ids, is deterministic for the same
input, and different questions will (with overwhelming probability) yield
a different ordering of the same set of screenshots.
"""

from __future__ import annotations

from app.adapters.relevance_fake import FakeRelevanceRanker
from app.core.models import ScreenshotRef, ScreenshotWithBytes


def _item(image_id: str, ts: int = 0) -> ScreenshotWithBytes:
    return ScreenshotWithBytes(ref=ScreenshotRef(timestamp=ts, image_id=image_id), data=b"x")


class TestRank:
    async def test_returns_empty_for_empty_input(self) -> None:
        ranker = FakeRelevanceRanker()
        assert await ranker.rank([], "q", 10) == []

    async def test_returns_empty_for_zero_top_k(self) -> None:
        ranker = FakeRelevanceRanker()
        assert await ranker.rank([_item("a")], "q", 0) == []

    async def test_returns_empty_for_negative_top_k(self) -> None:
        ranker = FakeRelevanceRanker()
        assert await ranker.rank([_item("a")], "q", -1) == []

    async def test_length_capped_at_top_k(self) -> None:
        ranker = FakeRelevanceRanker()
        items = [_item(f"img-{i}") for i in range(10)]
        out = await ranker.rank(items, "q", 3)
        assert len(out) == 3

    async def test_length_bounded_by_input_when_top_k_larger(self) -> None:
        ranker = FakeRelevanceRanker()
        items = [_item("a"), _item("b")]
        out = await ranker.rank(items, "q", 100)
        assert len(out) == 2
        assert set(out) == {"a", "b"}

    async def test_deterministic_for_same_input(self) -> None:
        ranker = FakeRelevanceRanker()
        items = [_item(f"img-{i}") for i in range(5)]
        first = await ranker.rank(items, "q", 5)
        second = await ranker.rank(items, "q", 5)
        assert first == second

    async def test_different_question_reorders_when_possible(self) -> None:
        ranker = FakeRelevanceRanker()
        items = [_item(f"img-{i}") for i in range(20)]
        a = await ranker.rank(items, "question A", 20)
        b = await ranker.rank(items, "question B", 20)
        # Both contain the same set; only the order differs.
        assert set(a) == set(b)
        assert a != b, (
            "Different questions must reorder the same candidates; if this "
            "assertion ever fires the hash keying changed and ordering is no "
            "longer sensitive to the question."
        )

    async def test_returns_only_ids_that_were_in_input(self) -> None:
        ranker = FakeRelevanceRanker()
        items = [_item(f"img-{i}") for i in range(5)]
        out = await ranker.rank(items, "q", 5)
        assert set(out).issubset({f"img-{i}" for i in range(5)})

    async def test_call_count_increments(self) -> None:
        ranker = FakeRelevanceRanker()
        await ranker.rank([], "q", 1)
        await ranker.rank([], "q", 1)
        assert ranker.call_count == 2
