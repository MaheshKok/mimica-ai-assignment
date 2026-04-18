"""Tests for :mod:`app.deps`.

Exercises the demo-port factory and the request-scoped providers. The
providers read from ``request.app.state``, so lifespan owns the
resources - there is no module-global singleton to worry about.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.adapters.relevance_fake import FakeRelevanceRanker
from app.adapters.storage_fake import FakeScreenshotStorage
from app.adapters.workflow_fake import FakeWorkflowServicesClient
from app.config import Settings
from app.deps import Ports, build_demo_ports, get_ports, get_settings


def _stub_request(app: FastAPI) -> SimpleNamespace:
    """Build a minimal object exposing ``.app`` the providers can read."""
    return SimpleNamespace(app=app)


class TestBuildDemoPorts:
    def test_returns_ports_with_fake_adapters(self) -> None:
        ports = build_demo_ports()
        assert isinstance(ports, Ports)
        assert isinstance(ports.workflow, FakeWorkflowServicesClient)
        assert isinstance(ports.storage, FakeScreenshotStorage)
        assert isinstance(ports.relevance, FakeRelevanceRanker)

    def test_demo_refs_match_storage_images(self) -> None:
        # Regression guard: make run's curl would 502 with partial-failure
        # if a demo ref id lacks a matching storage entry.
        ports = build_demo_ports()
        wf = ports.workflow
        st = ports.storage
        assert isinstance(wf, FakeWorkflowServicesClient)
        assert isinstance(st, FakeScreenshotStorage)
        assert wf.refs, "demo fake workflow must have refs for a non-empty answer"
        for ref in wf.refs:
            assert ref.image_id in st.images

    def test_each_call_returns_fresh_instance(self) -> None:
        # Resource ownership is lifespan's job; this factory must not
        # share state across invocations.
        a = build_demo_ports()
        b = build_demo_ports()
        assert a is not b
        assert a.workflow is not b.workflow


class TestGetSettings:
    def test_reads_from_app_state(self) -> None:
        app = FastAPI()
        app.state.settings = Settings(_env_file=None)  # type: ignore[arg-type]
        assert get_settings(_stub_request(app)) is app.state.settings  # type: ignore[arg-type]

    def test_raises_when_lifespan_did_not_run(self) -> None:
        app = FastAPI()
        with pytest.raises(RuntimeError, match="Application lifespan has not run"):
            get_settings(_stub_request(app))  # type: ignore[arg-type]


class TestGetPorts:
    def test_reads_from_app_state(self) -> None:
        app = FastAPI()
        ports = build_demo_ports()
        app.state.ports = ports
        assert get_ports(_stub_request(app)) is ports  # type: ignore[arg-type]

    def test_raises_when_lifespan_did_not_run(self) -> None:
        app = FastAPI()
        with pytest.raises(RuntimeError, match="Application lifespan has not run"):
            get_ports(_stub_request(app))  # type: ignore[arg-type]
