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
from app.core.orchestrator import _sample_uniform_over_window, run
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


# --------------------------------------------------------------------------- #
# Pre-fetch sampling                                                          #
# --------------------------------------------------------------------------- #


class TestSampleUniformOverWindow:
    def test_returns_input_unchanged_when_under_limit(self) -> None:
        refs = [ScreenshotRef(timestamp=i, image_id=f"i-{i}") for i in range(5)]
        out = _sample_uniform_over_window(refs, from_=0, to=100, max_input=10)
        assert out == refs

    def test_returns_input_unchanged_when_exactly_at_limit(self) -> None:
        refs = [ScreenshotRef(timestamp=i, image_id=f"i-{i}") for i in range(10)]
        out = _sample_uniform_over_window(refs, from_=0, to=100, max_input=10)
        assert out == refs

    def test_caps_at_max_input(self) -> None:
        refs = [ScreenshotRef(timestamp=i, image_id=f"i-{i}") for i in range(100)]
        out = _sample_uniform_over_window(refs, from_=0, to=100, max_input=10)
        assert len(out) <= 10

    def test_preserves_stream_order(self) -> None:
        # Sampling keeps the first-encountered ref per bucket, which when
        # iteration order == timestamp order equals timestamp-ascending.
        refs = [ScreenshotRef(timestamp=i, image_id=f"i-{i}") for i in range(100)]
        out = _sample_uniform_over_window(refs, from_=0, to=100, max_input=10)
        timestamps = [r.timestamp for r in out]
        assert timestamps == sorted(timestamps)

    def test_distributes_across_window(self) -> None:
        # With 1000 refs spanning [0, 100), sampling into 10 buckets of
        # width 10 should yield one ref per bucket. The selected refs'
        # timestamps should cover each bucket exactly once.
        refs = [ScreenshotRef(timestamp=i, image_id=f"i-{i}") for i in range(1000)]
        out = _sample_uniform_over_window(refs, from_=0, to=100, max_input=10)
        # Not stricter than the contract, but distribution should be even.
        assert len(out) == 10
        bucket_widths = [r.timestamp // 10 for r in out]
        assert sorted(bucket_widths) == list(range(10))

    def test_clustered_input_returns_fewer_than_max(self) -> None:
        # If every ref falls in the same bucket, only one is returned.
        # The contract says "at most max_input", not "exactly max_input".
        refs = [ScreenshotRef(timestamp=5, image_id=f"i-{i}") for i in range(50)]
        out = _sample_uniform_over_window(refs, from_=0, to=100, max_input=10)
        assert len(out) == 1
        assert out[0].image_id == "i-0"  # first-wins per bucket

    def test_empty_input_returns_empty(self) -> None:
        assert _sample_uniform_over_window([], from_=0, to=100, max_input=10) == []

    def test_max_input_of_one_returns_one_ref(self) -> None:
        refs = [ScreenshotRef(timestamp=i, image_id=f"i-{i}") for i in range(100)]
        out = _sample_uniform_over_window(refs, from_=0, to=100, max_input=1)
        assert len(out) == 1

    def test_boundary_timestamp_near_upper_bound(self) -> None:
        # Timestamp at to-1 is in the window; float rounding must not push
        # it into a bucket beyond max_input-1.
        refs = [ScreenshotRef(timestamp=99, image_id="a")]
        out = _sample_uniform_over_window(refs, from_=0, to=100, max_input=10)
        assert out == refs


class TestOrchestratorSampling:
    async def test_images_considered_counts_pre_sample_not_post(self) -> None:
        # 100 refs, but MAX_RANK_INPUT=5. images_considered must show the
        # full 100 the filter saw - not the sampled 5. This is the value
        # downstream observability will graph, so it must not silently
        # underreport.
        refs = [ScreenshotRef(timestamp=i, image_id=f"i-{i}") for i in range(100)]
        ports = _ports(refs=refs)
        resp = await run(
            _req(from_=0, to=100),
            ports,
            _settings(max_rank_input=5, max_relevant_images=100),
            "r",
        )
        assert resp.meta.images_considered == 100

    async def test_fetch_only_happens_for_sampled_refs(self) -> None:
        # Prove sampling happens BEFORE fetch: with 100 refs and
        # max_rank_input=5, the storage fake's call_count must be <= 5.
        refs = [ScreenshotRef(timestamp=i, image_id=f"i-{i}") for i in range(100)]
        ports = _ports(refs=refs)
        await run(
            _req(from_=0, to=100),
            ports,
            _settings(max_rank_input=5),
            "r",
        )
        storage = ports.storage
        assert isinstance(storage, FakeScreenshotStorage)
        assert storage.call_count <= 5, (
            f"sampling must happen before fetch; storage was called "
            f"{storage.call_count} times for a max_rank_input of 5"
        )
        # Also sanity: actually called SOMETHING
        assert storage.call_count >= 1

    async def test_partial_failure_ratio_computed_over_sampled_total(self) -> None:
        # 100 refs filtered in; max_rank_input=10 narrows to 10 fetches.
        # Mark 3 of those as missing (30%). With default ratio 0.2, that's
        # above threshold and should raise.
        refs = [ScreenshotRef(timestamp=i, image_id=f"i-{i}") for i in range(100)]
        # We can't predict exactly which 10 get sampled, but every 10th
        # timestamp is a good bet with bucket_width=10 and stride 1:
        # sampler picks timestamps 0, 10, 20, ... 90 -> that's the sample.
        # Mark 0, 10, 20 as missing to drive >20% failure.
        sampled_guess = {"i-0", "i-10", "i-20"}
        ports = _ports(refs=refs, missing=sampled_guess)
        with pytest.raises(PartialFailureThresholdExceededError) as excinfo:
            await run(
                _req(from_=0, to=100),
                ports,
                _settings(max_rank_input=10, max_fetch_failure_ratio=0.2),
                "r",
            )
        assert excinfo.value.total == 10  # sampled size, not 100
        assert excinfo.value.failed == 3
