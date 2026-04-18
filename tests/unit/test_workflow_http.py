"""Adapter tests for :class:`~app.adapters.workflow_http.HttpxWorkflowServicesClient`.

Uses :class:`httpx.MockTransport` so the adapter sees a real
:class:`httpx.AsyncClient` but no network call leaves the process. Tests
derived from the contract in
:class:`~app.ports.workflow.WorkflowServicesClient` plus the docstring
on the HTTP adapter.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
import pytest

from app.adapters.workflow_http import HttpxWorkflowServicesClient
from app.core.errors import WorkflowUpstreamError

if TYPE_CHECKING:
    from collections.abc import Callable


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://workflow.test",
    )


# --------------------------------------------------------------------------- #
# stream_project                                                              #
# --------------------------------------------------------------------------- #


class TestStreamProject:
    async def test_parses_valid_ndjson(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            body = (
                b'{"timestamp": 1, "screenshot_url": "img-1.png"}\n'
                b'{"timestamp": 2, "screenshot_url": "img-2.png"}\n'
            )
            return httpx.Response(200, content=body)

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            refs = [r async for r in adapter.stream_project(uuid4())]
            assert [r.image_id for r in refs] == ["img-1.png", "img-2.png"]
            assert [r.timestamp for r in refs] == [1, 2]
        finally:
            await client.aclose()

    async def test_maps_screenshot_url_to_image_id(self) -> None:
        # Regression guard: the brief calls the field `screenshot_url`
        # but the domain always uses `image_id`. The mapping happens here.
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b'{"timestamp": 42, "screenshot_url": "abc"}\n',
            )

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            refs = [r async for r in adapter.stream_project(uuid4())]
            assert len(refs) == 1
            assert refs[0].image_id == "abc"
        finally:
            await client.aclose()

    async def test_skips_malformed_json_line(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            body = (
                b'{"timestamp": 1, "screenshot_url": "a"}\n'
                b"not-json-at-all\n"
                b'{"timestamp": 3, "screenshot_url": "c"}\n'
            )
            return httpx.Response(200, content=body)

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            refs = [r async for r in adapter.stream_project(uuid4())]
            assert [r.image_id for r in refs] == ["a", "c"]
        finally:
            await client.aclose()

    async def test_skips_line_missing_required_field(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            body = (
                b'{"timestamp": 1}\n'
                b'{"screenshot_url": "b"}\n'
                b'{"timestamp": 3, "screenshot_url": "c"}\n'
            )
            return httpx.Response(200, content=body)

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            refs = [r async for r in adapter.stream_project(uuid4())]
            assert [r.image_id for r in refs] == ["c"]
        finally:
            await client.aclose()

    async def test_skips_empty_lines(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b'\n{"timestamp": 1, "screenshot_url": "a"}\n\n',
            )

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            refs = [r async for r in adapter.stream_project(uuid4())]
            assert [r.image_id for r in refs] == ["a"]
        finally:
            await client.aclose()

    async def test_raises_workflow_upstream_on_5xx(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            with pytest.raises(WorkflowUpstreamError):
                [r async for r in adapter.stream_project(uuid4())]
        finally:
            await client.aclose()

    async def test_raises_workflow_upstream_on_4xx(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            with pytest.raises(WorkflowUpstreamError):
                [r async for r in adapter.stream_project(uuid4())]
        finally:
            await client.aclose()

    async def test_raises_workflow_upstream_on_transport_error(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns failure")

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            with pytest.raises(WorkflowUpstreamError) as excinfo:
                [r async for r in adapter.stream_project(uuid4())]
            assert isinstance(excinfo.value.cause, httpx.HTTPError)
        finally:
            await client.aclose()

    async def test_stream_url_includes_project_id(self) -> None:
        captured: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.append(str(request.url))
            return httpx.Response(200, content=b"")

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            pid = uuid4()
            _ = [r async for r in adapter.stream_project(pid)]
            assert captured == [f"http://workflow.test/projects/{pid}/stream"]
        finally:
            await client.aclose()


# --------------------------------------------------------------------------- #
# qa_answer                                                                   #
# --------------------------------------------------------------------------- #


class TestQaAnswer:
    async def test_returns_answer_string(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"answer": "the answer"})

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            out = await adapter.qa_answer("q", ["a", "b"])
            assert out == "the answer"
        finally:
            await client.aclose()

    async def test_posts_question_and_ids_as_json(self) -> None:
        captured: list[dict[str, object]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"answer": "ok"})

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            await adapter.qa_answer("q?", ["x", "y", "z"])
            assert captured == [{"question": "q?", "relevant_images": ["x", "y", "z"]}]
        finally:
            await client.aclose()

    async def test_preserves_image_id_order_in_request(self) -> None:
        # Order-preservation is asserted at the orchestrator layer too;
        # verifying it survives wire encoding closes the loop.
        captured: list[list[str]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content)["relevant_images"])
            return httpx.Response(200, json={"answer": "ok"})

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            await adapter.qa_answer("q", ["z", "a", "m"])
            assert captured == [["z", "a", "m"]]
        finally:
            await client.aclose()

    async def test_raises_on_5xx(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            with pytest.raises(WorkflowUpstreamError):
                await adapter.qa_answer("q", [])
        finally:
            await client.aclose()

    async def test_raises_on_transport_error(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            with pytest.raises(WorkflowUpstreamError):
                await adapter.qa_answer("q", [])
        finally:
            await client.aclose()

    async def test_raises_when_response_missing_answer_field(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"not_answer": "oops"})

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            with pytest.raises(WorkflowUpstreamError):
                await adapter.qa_answer("q", [])
        finally:
            await client.aclose()

    async def test_raises_when_answer_field_is_not_string(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"answer": 42})

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test")
            with pytest.raises(WorkflowUpstreamError):
                await adapter.qa_answer("q", [])
        finally:
            await client.aclose()


# --------------------------------------------------------------------------- #
# Base URL hygiene                                                            #
# --------------------------------------------------------------------------- #


class TestBaseUrl:
    async def test_trailing_slash_in_base_url_is_normalised(self) -> None:
        captured: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.append(str(request.url))
            return httpx.Response(200, json={"answer": "x"})

        client = _client(handler)
        try:
            adapter = HttpxWorkflowServicesClient(client, base_url="http://workflow.test/")
            await adapter.qa_answer("q", [])
            # No double slash before /qa/answer
            assert captured == ["http://workflow.test/qa/answer"]
        finally:
            await client.aclose()
