"""FastAPI dependency providers for the Enriched QA Service.

Resources that back the dependencies (``Ports`` bundle, ``Settings``) are
owned by the FastAPI :func:`~app.main.lifespan` context and stashed on
``app.state`` at startup. These providers resolve them through the incoming
``Request``, so every request reads the same live instances the lifespan
built, and shutdown cleans them up in one place.

Phase 3 populates :data:`app.state.ports` with in-memory fakes assembled
by :func:`build_demo_ports`. Phase 4 replaces the lifespan body with
construction of HTTP adapters around a shared ``httpx.AsyncClient``; no
change to ``get_ports`` is needed because the dependency reads through
``request.app.state``.

Note: this module intentionally does *not* use ``from __future__ import
annotations``. FastAPI's dependency-analysis step classifies the
``Request`` parameter via a class-identity check; stringified annotations
make it misidentify ``request`` as a query parameter.
"""

from dataclasses import dataclass

from fastapi import Request

from app.adapters.relevance_fake import FakeRelevanceRanker
from app.adapters.storage_fake import FakeScreenshotStorage
from app.adapters.workflow_fake import FakeWorkflowServicesClient
from app.config import Settings
from app.core.models import ScreenshotRef
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


def build_demo_ports() -> Ports:
    """Construct the Phase 3 fake port bundle used by ``make run``.

    Populates the workflow fake with three refs inside ``[1_000_000,
    1_000_100)`` and the storage fake with matching bytes, so a curl
    against the running service returns a non-empty answer end-to-end.

    Returns:
        A :class:`Ports` bundle wrapping fresh fakes. Lifespan calls this
        once at startup; tests construct their own bundles via
        :func:`app.dependency_overrides` and never read from app state.
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


def get_settings(request: Request) -> Settings:
    """Return the :class:`Settings` stashed on ``request.app.state``.

    Lifespan populates ``app.state.settings`` at startup. Tests either
    override this provider via ``app.dependency_overrides`` or enter
    lifespan via ``with TestClient(app)``.

    Args:
        request: Incoming request; FastAPI injects it.

    Returns:
        The active Settings instance.
    """
    settings: Settings = request.app.state.settings
    return settings


def get_ports(request: Request) -> Ports:
    """Return the :class:`Ports` bundle stashed on ``request.app.state``.

    Lifespan owns the underlying resources (Phase 4: shared
    ``httpx.AsyncClient``; Phase 6: ``ProcessPoolExecutor``) and closes
    them cleanly on shutdown. This dependency stays stable across phases
    because the ownership moved, not the reading.

    Args:
        request: Incoming request; FastAPI injects it.

    Returns:
        The active Ports bundle.
    """
    ports: Ports = request.app.state.ports
    return ports
