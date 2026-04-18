"""FastAPI dependency providers for the Enriched QA Service.

The default wiring returns a :class:`Ports` bundle of the Phase 2 in-memory
fakes so ``make run`` produces a deterministic response before Phase 4
introduces real HTTP adapters. Tests override the providers via
``app.dependency_overrides``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from app.adapters.relevance_fake import FakeRelevanceRanker
from app.adapters.storage_fake import FakeScreenshotStorage
from app.adapters.workflow_fake import FakeWorkflowServicesClient
from app.config import Settings
from app.core.models import ScreenshotRef

if TYPE_CHECKING:
    from app.ports.relevance import RelevanceRanker
    from app.ports.storage import ScreenshotStorageClient
    from app.ports.workflow import WorkflowServicesClient


@dataclass(frozen=True)
class Ports:
    """Bundle of Protocol dependencies the orchestrator needs.

    Passing a single aggregate keeps the orchestrator signature small and
    makes test overrides a one-line swap.

    Attributes:
        workflow: Workflow Services client (stream_project + qa_answer).
        storage: Screenshot storage client (get_image).
        relevance: Relevance ranker (rank).
    """

    workflow: WorkflowServicesClient
    storage: ScreenshotStorageClient
    relevance: RelevanceRanker


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-cached :class:`Settings` instance.

    Use FastAPI ``Depends(get_settings)`` to inject settings into a route.
    """
    return Settings()


def _demo_ports() -> Ports:
    """Build the default fake port bundle used by ``make run``.

    Populates the fakes with a small demo time window (``[1_000_000,
    1_000_100)``) so a curl against the running service returns a
    non-empty answer end-to-end.
    """
    demo_refs = [
        ScreenshotRef(timestamp=1_000_000, image_id="img-demo-1.png"),
        ScreenshotRef(timestamp=1_000_030, image_id="img-demo-2.png"),
        ScreenshotRef(timestamp=1_000_060, image_id="img-demo-3.png"),
    ]
    return Ports(
        workflow=FakeWorkflowServicesClient(
            refs=demo_refs,
            canned_answer="demo answer from fake workflow",
        ),
        storage=FakeScreenshotStorage(
            images={ref.image_id: f"demo-bytes::{ref.image_id}".encode() for ref in demo_refs},
        ),
        relevance=FakeRelevanceRanker(),
    )


_DEFAULT_PORTS: Ports | None = None


def get_ports() -> Ports:
    """Return the active :class:`Ports` bundle for FastAPI dependency injection.

    Phase 3 returns the demo fakes from :func:`_demo_ports`. Phase 4 will
    swap the default to the HTTP adapters. Tests replace this function via
    ``app.dependency_overrides``.
    """
    global _DEFAULT_PORTS
    if _DEFAULT_PORTS is None:
        _DEFAULT_PORTS = _demo_ports()
    return _DEFAULT_PORTS
