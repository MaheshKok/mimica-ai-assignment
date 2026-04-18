"""Tests for :mod:`app.deps`.

Exercises the default dependency providers directly so the demo fakes are
reachable from coverage and the cached ``get_ports`` behaviour is pinned
down.
"""

from __future__ import annotations

import app.deps as deps_module
from app.adapters.relevance_fake import FakeRelevanceRanker
from app.adapters.storage_fake import FakeScreenshotStorage
from app.adapters.workflow_fake import FakeWorkflowServicesClient
from app.config import Settings
from app.deps import Ports, get_ports, get_settings


class TestGetSettings:
    def test_returns_settings_instance(self) -> None:
        assert isinstance(get_settings(), Settings)

    def test_cached_returns_same_object(self) -> None:
        assert get_settings() is get_settings()


class TestGetPorts:
    def test_returns_ports_with_default_fakes(self) -> None:
        # Reset the module-global so this test sees a fresh singleton.
        deps_module._DEFAULT_PORTS = None
        ports = get_ports()
        assert isinstance(ports, Ports)
        assert isinstance(ports.workflow, FakeWorkflowServicesClient)
        assert isinstance(ports.storage, FakeScreenshotStorage)
        assert isinstance(ports.relevance, FakeRelevanceRanker)

    def test_cached_returns_same_instance(self) -> None:
        deps_module._DEFAULT_PORTS = None
        first = get_ports()
        second = get_ports()
        assert first is second, "get_ports must cache the default bundle"

    def test_demo_data_roundtrips(self) -> None:
        """Demo refs populated in the fake are also present in storage.

        The ``make run`` path depends on refs in the workflow fake matching
        image ids present in the storage fake. If this ever drifts, a live
        curl against the service will fail with a partial-failure error.
        """
        deps_module._DEFAULT_PORTS = None
        ports = get_ports()
        wf = ports.workflow
        st = ports.storage
        assert isinstance(wf, FakeWorkflowServicesClient)
        assert isinstance(st, FakeScreenshotStorage)
        for ref in wf.refs:
            assert ref.image_id in st.images
