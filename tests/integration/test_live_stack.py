"""Live-stack integration test - the Phase 5 production-wiring gate.

Starts the workflow mock, storage mock, and the real FastAPI app as
three ``uvicorn`` subprocesses on ephemeral TCP ports, waits for each
to bind, then issues a real HTTP request to ``POST /enriched-qa``.

Unlike the component-level ASGITransport suite in
``test_end_to_end.py``, this test:

- uses real TCP sockets between every hop,
- runs the app through its own ``lifespan`` context (so
  ``build_http_ports`` and the shared ``httpx.AsyncClient`` are
  exercised),
- points the app at the mocks via ``WORKFLOW_API_URL`` /
  ``STORAGE_BASE_URL`` env vars - the exact knobs documented in
  ``.env.example`` - so a misconfigured default would fail here,
- catches early-exit failures of any subprocess (port already in use,
  import error, crash during startup) via a readiness probe that also
  watches ``kill -0`` on the pid.

This small suite is marked ``integration`` so CI can opt in or out
explicitly.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


pytestmark = pytest.mark.integration


_REPO_ROOT = Path(__file__).resolve().parents[2]
_READINESS_TIMEOUT_S = 15.0
_POLL_INTERVAL_S = 0.1
_RUN_MOCKS_SCRIPT = _REPO_ROOT / "scripts" / "run_mocks.sh"


def _free_port() -> int:
    """Return an ephemeral TCP port on 127.0.0.1.

    Binds to port 0 so the kernel picks an unused port, reads it back,
    then closes. A brief race exists between close and the child's
    re-bind; uvicorn retries so it is not a problem in practice.

    Returns:
        A free TCP port number on the loopback interface.
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _spawn(
    app_import: str,
    *,
    port: int,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Launch ``uvicorn app_import`` as a detached subprocess.

    Args:
        app_import: Dotted import path to the ASGI app, e.g.
            ``mock_services.workflow_api.app:app``.
        port: TCP port to bind on 127.0.0.1.
        extra_env: Extra environment variables to pass to the child
            (layered on top of the current process env).

    Returns:
        The spawned :class:`subprocess.Popen` handle.
    """
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            app_import,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=_REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_tcp_ready(
    port: int,
    proc: subprocess.Popen[bytes],
    label: str,
) -> None:
    """Block until ``127.0.0.1:port`` accepts a TCP connection.

    Aborts early if ``proc`` exits before the port is bound, so we fail
    fast when a mock crashes at startup instead of waiting the full
    timeout.

    Args:
        port: Port to probe.
        proc: Subprocess expected to bind that port.
        label: Human-readable name used in error messages.

    Raises:
        RuntimeError: If the subprocess exits early or the port is not
            bound within :data:`_READINESS_TIMEOUT_S` seconds.
    """
    deadline = time.monotonic() + _READINESS_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"{label} subprocess exited before binding :{port} (rc={proc.returncode})"
            )
        try:
            with closing(socket.create_connection(("127.0.0.1", port), timeout=1.0)):
                return
        except OSError:
            time.sleep(_POLL_INTERVAL_S)
    raise RuntimeError(f"{label} did not bind 127.0.0.1:{port} within {_READINESS_TIMEOUT_S}s")


def _port_is_closed(port: int) -> bool:
    """Return True when ``127.0.0.1:port`` refuses TCP connections."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _terminate_all(procs: list[subprocess.Popen[bytes]]) -> None:
    """Best-effort terminate-then-kill for a list of subprocesses.

    Sends SIGTERM first, waits up to 5s, then SIGKILL. Safe to call
    even if some processes have already exited.
    """
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
    for proc in procs:
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)


def _wait_for_log(
    path: Path,
    expected: str,
    proc: subprocess.Popen[bytes],
    *,
    timeout_s: float = _READINESS_TIMEOUT_S,
) -> str:
    """Wait until ``expected`` appears in ``path`` or ``proc`` exits."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        text = path.read_text(encoding="utf-8", errors="replace")
        if expected in text:
            return text
        if proc.poll() is not None:
            raise RuntimeError(f"process exited early rc={proc.returncode}\n{text}")
        time.sleep(_POLL_INTERVAL_S)
    text = path.read_text(encoding="utf-8", errors="replace")
    raise RuntimeError(f"did not see {expected!r} within {timeout_s}s\n{text}")


@pytest.fixture
def live_stack() -> Iterator[dict[str, str]]:
    """Start workflow mock, storage mock, and app on ephemeral ports.

    Yields a dict of ``{"workflow", "storage", "app"}`` base URLs. On
    teardown, all three subprocesses are terminated (then killed if
    SIGTERM is ignored).
    """
    workflow_port = _free_port()
    storage_port = _free_port()
    app_port = _free_port()

    workflow_url = f"http://127.0.0.1:{workflow_port}"
    storage_url = f"http://127.0.0.1:{storage_port}"
    app_url = f"http://127.0.0.1:{app_port}"

    workflow_proc = _spawn(
        "mock_services.workflow_api.app:app",
        port=workflow_port,
    )
    storage_proc = _spawn(
        "mock_services.storage_api.app:app",
        port=storage_port,
    )
    app_proc = _spawn(
        "app.main:app",
        port=app_port,
        extra_env={
            "WORKFLOW_API_URL": workflow_url,
            "STORAGE_BASE_URL": storage_url,
        },
    )
    procs = [workflow_proc, storage_proc, app_proc]

    try:
        _wait_tcp_ready(workflow_port, workflow_proc, "workflow mock")
        _wait_tcp_ready(storage_port, storage_proc, "storage mock")
        _wait_tcp_ready(app_port, app_proc, "enriched-qa app")
    except Exception:
        _terminate_all(procs)
        raise

    try:
        yield {
            "workflow": workflow_url,
            "storage": storage_url,
            "app": app_url,
        }
    finally:
        _terminate_all(procs)


async def test_live_stack_returns_200_via_real_sockets(
    live_stack: dict[str, str],
) -> None:
    """Round-trip one request through real TCP between app and both mocks.

    Asserts the production-wired response envelope shape matches the
    contract and that the default ten-ref workflow stream is fully
    consumed end-to-end.
    """
    body = {
        "project_id": str(uuid4()),
        "from": 1_700_000_000,
        "to": 1_700_001_000,
        "question": "what is happening?",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(f"{live_stack['app']}/enriched-qa", json=body)

    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data.keys()) == {"answer", "meta"}
    assert data["meta"]["errors"] == {}
    assert data["meta"]["images_considered"] == 10
    assert data["meta"]["images_relevant"] == 10
    assert data["answer"].startswith("Q: what is happening? | IDs: ")


def test_run_mocks_script_launches_module_entrypoints_and_cleans_up(
    tmp_path: Path,
) -> None:
    """Exercise the evaluator-facing ``make run-mocks`` script path."""
    workflow_port = _free_port()
    storage_port = _free_port()
    log_path = tmp_path / "run-mocks.log"
    env = os.environ.copy()
    env.update(
        {
            "WORKFLOW_PORT": str(workflow_port),
            "STORAGE_PORT": str(storage_port),
            "READINESS_TIMEOUT_S": "10",
        }
    )
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            ["bash", str(_RUN_MOCKS_SCRIPT)],
            cwd=_REPO_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    try:
        _wait_for_log(log_path, "Both mocks ready", proc)
        _wait_tcp_ready(workflow_port, proc, "workflow mock script child")
        _wait_tcp_ready(storage_port, proc, "storage mock script child")

        workflow_url = f"http://127.0.0.1:{workflow_port}"
        storage_url = f"http://127.0.0.1:{storage_port}"
        workflow_response = httpx.get(
            f"{workflow_url}/projects/{uuid4()}/stream",
            timeout=5.0,
        )
        storage_response = httpx.get(f"{storage_url}/images/smoke.png", timeout=5.0)

        assert workflow_response.status_code == 200
        assert storage_response.status_code == 200
        assert storage_response.content == b"fake-image::smoke.png"

        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=10.0) == 0
        assert _port_is_closed(workflow_port)
        assert _port_is_closed(storage_port)
    finally:
        if proc.poll() is None:
            _terminate_all([proc])


def test_run_mocks_script_rejects_occupied_ports_before_starting(
    tmp_path: Path,
) -> None:
    """A foreign listener must not satisfy mock readiness."""
    workflow_port = _free_port()
    log_path = tmp_path / "occupied-port.log"
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as occupied:
        occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        storage_port = int(occupied.getsockname()[1])

        env = os.environ.copy()
        env.update(
            {
                "WORKFLOW_PORT": str(workflow_port),
                "STORAGE_PORT": str(storage_port),
                "READINESS_TIMEOUT_S": "3",
            }
        )
        with log_path.open("wb") as log:
            proc = subprocess.Popen(
                ["bash", str(_RUN_MOCKS_SCRIPT)],
                cwd=_REPO_ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )

        try:
            assert proc.wait(timeout=10.0) == 1
        finally:
            if proc.poll() is None:
                _terminate_all([proc])

    text = log_path.read_text(encoding="utf-8", errors="replace")
    assert "ERROR: storage port 127.0.0.1:" in text
    assert "Both mocks ready" not in text
