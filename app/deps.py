"""FastAPI dependency providers for the Enriched QA Service.

Resources that back the dependencies (``Ports`` bundle, ``Settings``) are
owned by the FastAPI :func:`~app.main.lifespan` context and stashed on
``app.state`` at startup. These providers resolve them through the incoming
``Request``, so every request reads the same live instances the lifespan
built, and shutdown cleans them up in one place.

The default wiring constructs HTTP adapters around a shared
``httpx.AsyncClient`` via :func:`build_http_ports`.
:func:`build_demo_ports` is an offline fallback composed of the in-memory
fakes - useful for tests and for running the app without the mock
services. Because the dependencies read through ``request.app.state``,
swapping wiring is a single-line change in the lifespan.

Note: this module intentionally does *not* use ``from __future__ import
annotations``. FastAPI's dependency-analysis step classifies the
``Request`` parameter via a class-identity check; stringified annotations
make it misidentify ``request`` as a query parameter.
"""

import asyncio
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import cast

import httpx
from fastapi import Request

from app.adapters.relevance_cpu import CpuRelevanceRanker
from app.adapters.relevance_fake import FakeRelevanceRanker
from app.adapters.storage_fake import FakeScreenshotStorage
from app.adapters.storage_http import HttpxScreenshotStorageClient
from app.adapters.workflow_fake import FakeWorkflowServicesClient
from app.adapters.workflow_http import HttpxWorkflowServicesClient
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


def build_http_ports(
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    global_semaphore: asyncio.Semaphore,
    process_pool: ProcessPoolExecutor,
) -> Ports:
    """Construct a :class:`Ports` bundle using the real HTTP adapters.

    Default wiring used by ``make run``. The lifespan owns the shared
    ``httpx.AsyncClient``, the ``global_semaphore``, and the
    ``process_pool``; this factory just composes adapters from them.
    No network call or worker spawn happens at construction time.

    Args:
        client: Shared async HTTP client.
        settings: Active settings; supplies base URLs and the defensive
            ``max_rank_input`` bound forwarded to the ranker.
        global_semaphore: Process-wide storage concurrency cap. Injected
            into the storage adapter so every fetch honours it.
        process_pool: Worker pool owned by the lifespan. Injected into
            the relevance adapter so CPU-bound ranking never blocks the
            event loop. Not closed here.

    Returns:
        A :class:`Ports` bundle with the HTTP workflow/storage adapters
        and the process-pool-backed relevance ranker.
    """
    return Ports(
        workflow=HttpxWorkflowServicesClient(client=client, base_url=settings.workflow_api_url),
        storage=HttpxScreenshotStorageClient(
            client=client,
            base_url=settings.storage_base_url,
            global_semaphore=global_semaphore,
        ),
        relevance=CpuRelevanceRanker(
            pool=process_pool,
            max_input=settings.max_rank_input,
        ),
    )


def build_demo_ports() -> Ports:
    """Construct a fully-offline demo port bundle from the in-memory fakes.

    Kept available for tests that want a fully-offline bundle; the
    default `make run` path uses :func:`build_http_ports`. Populates
    the workflow fake with three refs inside ``[1_000_000, 1_000_100)``
    and the storage fake with matching bytes.

    Returns:
        A :class:`Ports` bundle wrapping fresh fakes. Each call builds
        new instances - resource ownership is the lifespan's job, not
        this factory's.
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


def _state_resource(request: Request, name: str) -> object:
    """Return a resource from app state or explain the missing lifespan.

    Starlette raises a generic ``AttributeError`` when ``app.state`` is
    missing an attribute. That is technically correct but unhelpful for the
    common test mistake of using ``TestClient(app)`` without entering the
    context manager, which means lifespan never populated ``app.state``.
    """
    try:
        return getattr(request.app.state, name)
    except AttributeError as exc:
        raise RuntimeError(
            f"Application lifespan has not run; app.state.{name} is missing. "
            "Use 'with TestClient(app) as client:' in tests or run the app "
            "through an ASGI server that supports lifespan."
        ) from exc


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
    return cast("Settings", _state_resource(request, "settings"))


def get_ports(request: Request) -> Ports:
    """Return the :class:`Ports` bundle stashed on ``request.app.state``.

    Lifespan owns the underlying resources (the shared
    ``httpx.AsyncClient`` and the ``ProcessPoolExecutor``) and closes
    them cleanly on shutdown. This dependency only reads from
    ``app.state`` so wiring changes never need to edit the resolver.

    Args:
        request: Incoming request; FastAPI injects it.

    Returns:
        The active Ports bundle.
    """
    return cast("Ports", _state_resource(request, "ports"))
