"""Tests for ``app.adapters.workflow_fake.FakeWorkflowServicesClient``.

Contract: ``stream_project`` yields refs in insertion order; ``qa_answer``
returns the configured ``canned_answer`` and records each call's
``(question, image_ids)`` tuple so tests can assert exact arguments and
ordering.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.adapters.workflow_fake import FakeWorkflowServicesClient
from app.core.models import ScreenshotRef


def _collect() -> FakeWorkflowServicesClient:
    return FakeWorkflowServicesClient(
        refs=[
            ScreenshotRef(timestamp=1, image_id="a"),
            ScreenshotRef(timestamp=2, image_id="b"),
            ScreenshotRef(timestamp=3, image_id="c"),
        ],
        canned_answer="the canned answer",
    )


async def _drain(client: FakeWorkflowServicesClient, project_id: UUID) -> list[ScreenshotRef]:
    return [ref async for ref in client.stream_project(project_id)]


class TestStream:
    async def test_yields_refs_in_order(self) -> None:
        client = _collect()
        refs = await _drain(client, uuid4())
        assert [r.image_id for r in refs] == ["a", "b", "c"]

    async def test_yields_empty_when_configured_empty(self) -> None:
        client = FakeWorkflowServicesClient()
        refs = await _drain(client, uuid4())
        assert refs == []

    async def test_stream_increments_counter(self) -> None:
        client = _collect()
        await _drain(client, uuid4())
        await _drain(client, uuid4())
        assert client.stream_calls == 2

    async def test_stream_ignores_project_id(self) -> None:
        client = _collect()
        a = await _drain(client, uuid4())
        b = await _drain(client, uuid4())
        assert [r.image_id for r in a] == [r.image_id for r in b]


class TestQaAnswer:
    async def test_returns_canned_answer(self) -> None:
        client = _collect()
        out = await client.qa_answer("q", ["a", "b"])
        assert out == "the canned answer"

    async def test_records_call(self) -> None:
        client = _collect()
        await client.qa_answer("q1", ["a", "b"])
        assert client.qa_calls == [("q1", ["a", "b"])]

    async def test_preserves_id_order_in_recorded_call(self) -> None:
        client = _collect()
        await client.qa_answer("q", ["b", "a", "c"])
        assert client.qa_calls[-1][1] == ["b", "a", "c"]

    async def test_multiple_calls_are_all_recorded(self) -> None:
        client = _collect()
        await client.qa_answer("q1", ["a"])
        await client.qa_answer("q2", ["b"])
        assert client.qa_calls == [("q1", ["a"]), ("q2", ["b"])]

    async def test_recorded_list_is_a_copy_not_alias(self) -> None:
        # Mutating the caller's list after the call must not change the record.
        client = _collect()
        ids = ["a", "b"]
        await client.qa_answer("q", ids)
        ids.append("c")
        assert client.qa_calls[0][1] == ["a", "b"]
