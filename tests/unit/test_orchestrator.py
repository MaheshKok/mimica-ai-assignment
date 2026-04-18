"""Tests for :func:`app.core.orchestrator.run`.

Written from the docstring of ``run`` and the pipeline contract in
``plan.md`` Phase 3 / ``architect.md`` §4 and §8. Every scenario constructs
fresh fakes so call counters reflect only this test's actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from app.adapters.relevance_fake import FakeRelevanceRanker
from app.adapters.storage_fake import FakeScreenshotStorage
from app.adapters.workflow_fake import FakeWorkflowServicesClient
from app.api.schemas import EnrichedQARequest
from app.config import Settings
from app.core.errors import (
    PartialFailureThresholdExceededError,
    WorkflowUpstreamError,
)
from app.core.models import ScreenshotRef
from app.core.orchestrator import run
from app.deps import Ports

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _settings(**overrides: object) -> Settings:
    """Build a Settings instance with overrides. Bypasses env files/vars."""
    base: dict[str, object] = {
        "max_concurrent_fetches": 25,
        "global_fetch_concurrency": 100,
        "max_relevant_images": 20,
        "max_rank_input": 500,
        "max_fetch_failure_ratio": 0.2,
        "assume_sorted_stream": True,
        "request_timeout_ms": 15_000,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _req(
    *,
    from_: int = 0,
    to: int = 1_000,
    question: str = "q",
    project_id: UUID | None = None,
) -> EnrichedQARequest:
    return EnrichedQARequest.model_validate(
        {
            "project_id": str(project_id or uuid4()),
            "from": from_,
            "to": to,
            "question": question,
        }
    )


def _ports(
    *,
    refs: list[ScreenshotRef] | None = None,
    images: dict[str, bytes] | None = None,
    missing: set[str] | None = None,
    answer: str = "canned",
) -> Ports:
    wf = FakeWorkflowServicesClient(refs=refs or [], canned_answer=answer)
    st = FakeScreenshotStorage(
        images=images if images is not None else {r.image_id: b"x" for r in (refs or [])},
        missing=missing or set(),
    )
    rk = FakeRelevanceRanker()
    return Ports(workflow=wf, storage=st, relevance=rk)


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #


class TestHappyPath:
    async def test_returns_answer_and_correct_counts(self) -> None:
        refs = [
            ScreenshotRef(timestamp=10, image_id="a"),
            ScreenshotRef(timestamp=20, image_id="b"),
            ScreenshotRef(timestamp=30, image_id="c"),
        ]
        ports = _ports(refs=refs, answer="hello")
        resp = await run(_req(from_=0, to=100), ports, _settings(), "rid-1")
        assert resp.answer == "hello"
        assert resp.meta.images_considered == 3
        assert resp.meta.images_relevant == 3
        assert resp.meta.errors == {}
        assert resp.meta.request_id == "rid-1"

    async def test_images_relevant_capped_by_top_k(self) -> None:
        refs = [ScreenshotRef(timestamp=i, image_id=f"img-{i}") for i in range(50)]
        ports = _ports(refs=refs)
        resp = await run(_req(from_=0, to=100), ports, _settings(max_relevant_images=5), "r")
        assert resp.meta.images_considered == 50
        assert resp.meta.images_relevant == 5

    async def test_latency_ms_includes_expected_keys_on_full_path(self) -> None:
        refs = [ScreenshotRef(timestamp=1, image_id="a")]
        resp = await run(_req(from_=0, to=10), _ports(refs=refs), _settings(), "r")
        for key in ("stream", "fetch", "rank", "qa", "total"):
            assert key in resp.meta.latency_ms
        assert all(v >= 0 for v in resp.meta.latency_ms.values())


# --------------------------------------------------------------------------- #
# Empty-window short-circuit                                                  #
# --------------------------------------------------------------------------- #


class TestEmptyWindow:
    async def test_empty_stream_returns_zero_counts(self) -> None:
        ports = _ports(refs=[])
        resp = await run(_req(from_=0, to=100), ports, _settings(), "rid")
        assert resp.answer == ""
        assert resp.meta.images_considered == 0
        assert resp.meta.images_relevant == 0
        assert resp.meta.errors == {}

    async def test_empty_stream_skips_storage_ranker_qa(self) -> None:
        ports = _ports(refs=[])
        await run(_req(from_=0, to=100), ports, _settings(), "rid")
        # storage.call_count is hidden behind the interface; inspect the fake:
        from app.adapters.storage_fake import FakeScreenshotStorage

        assert isinstance(ports.storage, FakeScreenshotStorage)
        assert ports.storage.call_count == 0

        from app.adapters.relevance_fake import FakeRelevanceRanker

        assert isinstance(ports.relevance, FakeRelevanceRanker)
        assert ports.relevance.call_count == 0

        from app.adapters.workflow_fake import FakeWorkflowServicesClient

        assert isinstance(ports.workflow, FakeWorkflowServicesClient)
        assert ports.workflow.qa_calls == []

    async def test_all_rows_outside_window_returns_zero(self) -> None:
        refs = [
            ScreenshotRef(timestamp=-1, image_id="a"),  # filtered anyway; from=0
            ScreenshotRef(timestamp=1_000, image_id="b"),
            ScreenshotRef(timestamp=1_001, image_id="c"),
        ]
        # to=1000 is exclusive; only negative timestamps would be in range
        # but we set from_=0 so they are filtered too.
        resp = await run(_req(from_=0, to=1_000), _ports(refs=refs), _settings(), "r")
        assert resp.meta.images_considered == 0
        assert resp.answer == ""

    async def test_latency_total_present_on_empty_path(self) -> None:
        resp = await run(_req(from_=0, to=1), _ports(refs=[]), _settings(), "r")
        assert "total" in resp.meta.latency_ms
        # fetch/rank/qa keys should NOT appear on the short-circuit path.
        assert "fetch" not in resp.meta.latency_ms
        assert "rank" not in resp.meta.latency_ms
        assert "qa" not in resp.meta.latency_ms


# --------------------------------------------------------------------------- #
# Boundary inclusivity                                                        #
# --------------------------------------------------------------------------- #


class TestBoundaryInclusivity:
    async def test_timestamp_equal_to_from_is_included(self) -> None:
        refs = [ScreenshotRef(timestamp=100, image_id="a")]
        resp = await run(_req(from_=100, to=200), _ports(refs=refs), _settings(), "r")
        assert resp.meta.images_considered == 1

    async def test_timestamp_equal_to_to_is_excluded(self) -> None:
        refs = [ScreenshotRef(timestamp=200, image_id="a")]
        resp = await run(_req(from_=100, to=200), _ports(refs=refs), _settings(), "r")
        assert resp.meta.images_considered == 0


# --------------------------------------------------------------------------- #
# Stream-sorted short-circuit vs drain                                        #
# --------------------------------------------------------------------------- #


@dataclass
class _CountingWorkflow:
    """Workflow fake that records how many refs were actually drawn from the stream."""

    refs: list[ScreenshotRef] = field(default_factory=list)
    canned_answer: str = ""
    drawn: int = 0

    def stream_project(self, _project_id: UUID) -> AsyncIterator[ScreenshotRef]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[ScreenshotRef]:
        for ref in self.refs:
            self.drawn += 1
            yield ref

    async def qa_answer(self, _question: str, _relevant_images: list[str]) -> str:
        return self.canned_answer


class TestStreamSorted:
    async def test_sorted_short_circuits_on_first_past_window_row(self) -> None:
        # Refs 0..99 inside, then 100..200 past `to=100` (exclusive).
        # Short-circuit should stop after drawing the first past-window ref.
        refs = [ScreenshotRef(timestamp=i, image_id=f"i-{i}") for i in range(200)]
        wf = _CountingWorkflow(refs=refs, canned_answer="ok")
        ports = Ports(
            workflow=wf,
            storage=FakeScreenshotStorage(images={r.image_id: b"x" for r in refs}),
            relevance=FakeRelevanceRanker(),
        )
        await run(_req(from_=0, to=100), ports, _settings(assume_sorted_stream=True), "r")
        # We should have drawn exactly the 100 in-window refs plus the first
        # past-window ref (timestamp=100) before short-circuiting = 101.
        assert wf.drawn == 101

    async def test_unsorted_drain_consumes_entire_stream(self) -> None:
        # Same ref set but ASSUME_SORTED_STREAM=False must drain all 200.
        refs = [ScreenshotRef(timestamp=i, image_id=f"i-{i}") for i in range(200)]
        wf = _CountingWorkflow(refs=refs, canned_answer="ok")
        ports = Ports(
            workflow=wf,
            storage=FakeScreenshotStorage(images={r.image_id: b"x" for r in refs}),
            relevance=FakeRelevanceRanker(),
        )
        await run(_req(from_=0, to=100), ports, _settings(assume_sorted_stream=False), "r")
        assert wf.drawn == 200

    async def test_unsorted_picks_up_out_of_order_in_window_rows(self) -> None:
        # Row order: past-window, in-window, past-window, in-window. Sorted
        # mode would stop at the first past-window row; drain mode collects
        # both in-window rows.
        refs = [
            ScreenshotRef(timestamp=500, image_id="past-1"),
            ScreenshotRef(timestamp=10, image_id="in-1"),
            ScreenshotRef(timestamp=800, image_id="past-2"),
            ScreenshotRef(timestamp=20, image_id="in-2"),
        ]
        ports = _ports(refs=refs)
        resp = await run(
            _req(from_=0, to=100),
            ports,
            _settings(assume_sorted_stream=False),
            "r",
        )
        assert resp.meta.images_considered == 2


# --------------------------------------------------------------------------- #
# Partial failure                                                             #
# --------------------------------------------------------------------------- #


class TestPartialFailure:
    async def test_below_threshold_succeeds_and_records_count(self) -> None:
        refs = [ScreenshotRef(timestamp=i, image_id=f"img-{i}") for i in range(10)]
        ports = _ports(refs=refs, missing={"img-0"})  # 1/10 = 10% failure
        resp = await run(
            _req(from_=0, to=100),
            ports,
            _settings(max_fetch_failure_ratio=0.2),
            "r",
        )
        assert resp.meta.errors == {"storage_fetch_failed": 1}
        assert resp.meta.images_considered == 10
        # 1 fetch failed so only 9 reached the ranker
        assert resp.meta.images_relevant == 9

    async def test_above_threshold_raises(self) -> None:
        refs = [ScreenshotRef(timestamp=i, image_id=f"img-{i}") for i in range(10)]
        ports = _ports(refs=refs, missing={f"img-{i}" for i in range(5)})  # 50%
        with pytest.raises(PartialFailureThresholdExceededError) as excinfo:
            await run(
                _req(from_=0, to=100),
                ports,
                _settings(max_fetch_failure_ratio=0.2),
                "r",
            )
        assert excinfo.value.failed == 5
        assert excinfo.value.total == 10

    async def test_exactly_at_threshold_succeeds(self) -> None:
        # Contract: fails strictly *greater than* the ratio. Equal is OK.
        refs = [ScreenshotRef(timestamp=i, image_id=f"img-{i}") for i in range(10)]
        ports = _ports(refs=refs, missing={f"img-{i}" for i in range(2)})  # 20%
        resp = await run(
            _req(from_=0, to=100),
            ports,
            _settings(max_fetch_failure_ratio=0.2),
            "r",
        )
        assert resp.meta.errors == {"storage_fetch_failed": 2}
        assert resp.meta.images_relevant == 8

    async def test_all_succeed_has_empty_errors(self) -> None:
        refs = [ScreenshotRef(timestamp=i, image_id=f"img-{i}") for i in range(3)]
        resp = await run(_req(from_=0, to=100), _ports(refs=refs), _settings(), "r")
        assert resp.meta.errors == {}


# --------------------------------------------------------------------------- #
# Upstream error propagation                                                  #
# --------------------------------------------------------------------------- #


@dataclass
class _FailingStreamWorkflow:
    """Workflow that raises WorkflowUpstreamError mid-stream."""

    refs: list[ScreenshotRef] = field(default_factory=list)

    def stream_project(self, _project_id: UUID) -> AsyncIterator[ScreenshotRef]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[ScreenshotRef]:
        for ref in self.refs:
            yield ref
        raise WorkflowUpstreamError(RuntimeError("upstream-dead"))

    async def qa_answer(self, _question: str, _ids: list[str]) -> str:
        raise AssertionError("should not be called")


@dataclass
class _FailingQaWorkflow:
    """Workflow whose qa_answer raises WorkflowUpstreamError."""

    refs: list[ScreenshotRef] = field(default_factory=list)

    def stream_project(self, _project_id: UUID) -> AsyncIterator[ScreenshotRef]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[ScreenshotRef]:
        for ref in self.refs:
            yield ref

    async def qa_answer(self, _question: str, _ids: list[str]) -> str:
        raise WorkflowUpstreamError(RuntimeError("qa-down"))


class TestUpstreamErrors:
    async def test_stream_error_propagates_unchanged(self) -> None:
        wf = _FailingStreamWorkflow(refs=[])
        ports = Ports(
            workflow=wf,
            storage=FakeScreenshotStorage(),
            relevance=FakeRelevanceRanker(),
        )
        with pytest.raises(WorkflowUpstreamError):
            await run(_req(from_=0, to=100), ports, _settings(), "r")

    async def test_qa_answer_error_propagates_unchanged(self) -> None:
        refs = [ScreenshotRef(timestamp=1, image_id="a")]
        wf = _FailingQaWorkflow(refs=refs)
        ports = Ports(
            workflow=wf,
            storage=FakeScreenshotStorage(images={"a": b"x"}),
            relevance=FakeRelevanceRanker(),
        )
        with pytest.raises(WorkflowUpstreamError):
            await run(_req(from_=0, to=100), ports, _settings(), "r")


# --------------------------------------------------------------------------- #
# Order preservation                                                          #
# --------------------------------------------------------------------------- #


class TestOrderPreservation:
    async def test_ranker_order_passed_to_qa_answer_verbatim(self) -> None:
        refs = [ScreenshotRef(timestamp=i, image_id=f"img-{i}") for i in range(5)]
        ports = _ports(refs=refs)
        await run(_req(from_=0, to=100), ports, _settings(max_relevant_images=5), "r")
        wf = ports.workflow
        assert isinstance(wf, FakeWorkflowServicesClient)
        assert len(wf.qa_calls) == 1
        # Ranker is deterministic; the orchestrator must pass exactly what
        # it returned, with order preserved.
        qa_question, qa_ids = wf.qa_calls[0]
        assert qa_question == "q"

        # Recompute the ranker output independently to prove the orchestrator
        # didn't reorder. We call rank() through the SAME fake to pick up
        # its `call_count` increment too.
        expected_order = await FakeRelevanceRanker().rank(
            [_screenshot_with_bytes_for(ref) for ref in refs], "q", 5
        )
        assert qa_ids == expected_order


def _screenshot_with_bytes_for(ref: ScreenshotRef) -> object:
    """Helper to build a ScreenshotWithBytes mirroring orchestrator's internal type.

    Kept a tiny shim so the test doesn't need to import ScreenshotWithBytes at
    top-level purely for one assertion.
    """
    from app.core.models import ScreenshotWithBytes

    return ScreenshotWithBytes(ref=ref, data=b"x")


# --------------------------------------------------------------------------- #
# Concurrency bound                                                           #
# --------------------------------------------------------------------------- #


@dataclass
class _TrackingStorage:
    """Storage fake that records peak concurrent in-flight fetches."""

    images: dict[str, bytes]
    in_flight: int = 0
    peak: int = 0

    async def get_image(self, image_id: str) -> bytes:
        import asyncio as _asyncio

        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await _asyncio.sleep(0)  # yield control to let other fetches start
            return self.images[image_id]
        finally:
            self.in_flight -= 1


class TestConcurrencyBound:
    async def test_peak_does_not_exceed_max_concurrent(self) -> None:
        refs = [ScreenshotRef(timestamp=i, image_id=f"img-{i}") for i in range(50)]
        tracking = _TrackingStorage(images={r.image_id: b"x" for r in refs})
        ports = Ports(
            workflow=FakeWorkflowServicesClient(refs=refs, canned_answer="ok"),
            storage=tracking,
            relevance=FakeRelevanceRanker(),
        )
        await run(
            _req(from_=0, to=100),
            ports,
            _settings(max_concurrent_fetches=4),
            "r",
        )
        assert tracking.peak <= 4
        # Also prove the cap was actually exercised — we wouldn't want a
        # bug where the semaphore is mistakenly 1.
        assert tracking.peak >= 2
