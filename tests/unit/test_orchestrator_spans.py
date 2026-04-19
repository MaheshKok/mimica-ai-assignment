"""Tests for the manual OTel spans emitted by :func:`app.core.orchestrator.run`.

Contract (derived from ``architect.md`` §10 and the orchestrator docstring):

- A successful request emits exactly these five span names, in parent/
  child order: ``enriched_qa.handler`` (root of this block) with
  ``workflow.stream``, ``storage.fetch_batch``, ``relevance.rank``, and
  ``workflow.qa_answer`` as descendants.
- The handler span records the cardinality attributes
  ``images_considered`` and ``images_relevant`` so they can be alert-on.
- The empty-window short-circuit only emits the handler + stream spans -
  fetch/rank/qa are skipped entirely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util._once import Once

from app.adapters.relevance_fake import FakeRelevanceRanker
from app.adapters.storage_fake import FakeScreenshotStorage
from app.adapters.workflow_fake import FakeWorkflowServicesClient
from app.api.schemas import EnrichedQARequest
from app.config import Settings
from app.core import orchestrator as orchestrator_mod
from app.core.models import ScreenshotRef
from app.core.orchestrator import run
from app.deps import Ports
from app.observability import tracing as tracing_mod

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def capture_spans() -> Iterator[InMemorySpanExporter]:
    """Install a tracer provider with an in-memory exporter for the test.

    The OTel ``Once`` guard that prevents provider reassignment is reset
    before each test and the module flag is cleared on teardown.
    """
    tracing_mod._configured = False
    tracing_mod._instrumented_apps = set()
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    trace._TRACER_PROVIDER = None
    exporter = InMemorySpanExporter()
    tracing_mod.configure(Settings(_env_file=None), exporter=exporter)  # type: ignore[arg-type]
    # The orchestrator caches ``_tracer`` at module import time; after we
    # swap the provider for the test we must re-resolve it so new spans
    # flow to the test exporter rather than the shutdown one.
    orchestrator_mod._tracer = trace.get_tracer(orchestrator_mod.__name__)
    yield exporter
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.force_flush()
    tracing_mod.shutdown()


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "max_concurrent_fetches": 5,
        "global_fetch_concurrency": 100,
        "max_relevant_images": 10,
        "max_rank_input": 500,
        "max_fetch_failure_ratio": 0.5,
        "assume_sorted_stream": True,
        "request_timeout_ms": 15_000,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _ports(refs: list[ScreenshotRef], answer: str = "ok") -> Ports:
    return Ports(
        workflow=FakeWorkflowServicesClient(refs=refs, canned_answer=answer),
        storage=FakeScreenshotStorage(images={r.image_id: b"x" for r in refs}),
        relevance=FakeRelevanceRanker(),
    )


def _force_flush() -> None:
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    provider.force_flush()


class TestSuccessfulRequestEmitsAllFiveSpans:
    @pytest.mark.asyncio
    async def test_all_expected_span_names_present(
        self, capture_spans: InMemorySpanExporter
    ) -> None:
        refs = [ScreenshotRef(timestamp=i, image_id=f"i{i}") for i in range(3)]
        req = EnrichedQARequest.model_validate(
            {
                "project_id": str(uuid4()),
                "from": 0,
                "to": 100,
                "question": "q",
            }
        )
        await run(req, _ports(refs), _settings(), request_id="rid")
        _force_flush()

        names = [s.name for s in capture_spans.get_finished_spans()]
        expected = {
            "enriched_qa.handler",
            "workflow.stream",
            "storage.fetch_batch",
            "relevance.rank",
            "workflow.qa_answer",
        }
        assert expected.issubset(set(names)), (
            f"missing spans: {expected - set(names)} (got {names})"
        )

    @pytest.mark.asyncio
    async def test_handler_span_records_cardinality_attributes(
        self, capture_spans: InMemorySpanExporter
    ) -> None:
        refs = [ScreenshotRef(timestamp=i, image_id=f"i{i}") for i in range(3)]
        req = EnrichedQARequest.model_validate(
            {
                "project_id": str(uuid4()),
                "from": 0,
                "to": 100,
                "question": "q",
            }
        )
        await run(req, _ports(refs), _settings(), request_id="rid")
        _force_flush()

        handler_spans = [
            s for s in capture_spans.get_finished_spans() if s.name == "enriched_qa.handler"
        ]
        assert len(handler_spans) == 1
        attrs = handler_spans[0].attributes or {}
        assert attrs["images_considered"] == 3
        assert attrs["images_relevant"] == 3  # fake ranker returns everything

    @pytest.mark.asyncio
    async def test_child_spans_share_handler_trace_id(
        self, capture_spans: InMemorySpanExporter
    ) -> None:
        """All five spans must belong to the same trace."""
        refs = [ScreenshotRef(timestamp=i, image_id=f"i{i}") for i in range(2)]
        req = EnrichedQARequest.model_validate(
            {
                "project_id": str(uuid4()),
                "from": 0,
                "to": 100,
                "question": "q",
            }
        )
        await run(req, _ports(refs), _settings(), request_id="rid")
        _force_flush()

        trace_ids = {s.context.trace_id for s in capture_spans.get_finished_spans()}
        assert len(trace_ids) == 1, f"expected one trace, got {len(trace_ids)}"


class TestEmptyWindowShortCircuit:
    @pytest.mark.asyncio
    async def test_empty_stream_skips_fetch_rank_qa_spans(
        self, capture_spans: InMemorySpanExporter
    ) -> None:
        """Zero refs in the window means fetch/rank/qa are never entered."""
        req = EnrichedQARequest.model_validate(
            {
                "project_id": str(uuid4()),
                "from": 0,
                "to": 100,
                "question": "q",
            }
        )
        # Refs completely outside the window.
        refs = [ScreenshotRef(timestamp=10_000, image_id="x")]
        await run(req, _ports(refs), _settings(), request_id="rid")
        _force_flush()

        names = {s.name for s in capture_spans.get_finished_spans()}
        assert "enriched_qa.handler" in names
        assert "workflow.stream" in names
        # These must NOT be emitted for the empty-window case.
        assert "storage.fetch_batch" not in names
        assert "relevance.rank" not in names
        assert "workflow.qa_answer" not in names
