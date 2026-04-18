"""Tests that the fakes satisfy their ``Protocol`` contracts.

All three ports are declared ``@runtime_checkable`` so structural
conformance can be asserted with ``isinstance``. These tests guard
against a fake drifting out of sync with its Protocol.
"""

from __future__ import annotations

from typing import Protocol

from app.adapters.relevance_fake import FakeRelevanceRanker
from app.adapters.storage_fake import FakeScreenshotStorage
from app.adapters.workflow_fake import FakeWorkflowServicesClient
from app.ports.relevance import RelevanceRanker
from app.ports.storage import ScreenshotStorageClient
from app.ports.workflow import WorkflowServicesClient


class _Empty:
    """A class that implements none of the port methods."""


def test_protocols_are_runtime_checkable() -> None:
    # Runtime-checkable Protocols must subclass Protocol.
    for proto in (ScreenshotStorageClient, WorkflowServicesClient, RelevanceRanker):
        assert issubclass(proto, Protocol)


def test_fake_storage_satisfies_port() -> None:
    assert isinstance(FakeScreenshotStorage(), ScreenshotStorageClient)


def test_fake_workflow_satisfies_port() -> None:
    assert isinstance(FakeWorkflowServicesClient(), WorkflowServicesClient)


def test_fake_ranker_satisfies_port() -> None:
    assert isinstance(FakeRelevanceRanker(), RelevanceRanker)


def test_empty_class_does_not_satisfy_storage_port() -> None:
    assert not isinstance(_Empty(), ScreenshotStorageClient)


def test_empty_class_does_not_satisfy_workflow_port() -> None:
    assert not isinstance(_Empty(), WorkflowServicesClient)


def test_empty_class_does_not_satisfy_ranker_port() -> None:
    assert not isinstance(_Empty(), RelevanceRanker)
