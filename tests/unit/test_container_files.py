"""Regression tests for the repository's container entrypoints.

These are intentionally lightweight: they assert the Docker artifacts exist and
keep exposing the three-service local stack (app + both mocks) documented in
the README. The goal is to prevent the repo from drifting away from the
reviewer-facing container workflow.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_exists_and_runs_app_on_port_8000() -> None:
    dockerfile = REPO_ROOT / "Dockerfile"
    assert dockerfile.exists(), "Dockerfile should exist at the repository root"
    text = dockerfile.read_text(encoding="utf-8")
    assert "uv sync" in text
    assert "app.main:app" in text
    assert "8000" in text


def test_compose_file_wires_app_and_both_mocks() -> None:
    compose = REPO_ROOT / "compose.yaml"
    assert compose.exists(), "compose.yaml should exist at the repository root"
    text = compose.read_text(encoding="utf-8")
    for needle in (
        "app:",
        "workflow-mock:",
        "storage-mock:",
        "container_name: mimicaai-app",
        "container_name: mimicaai-workflow-mock",
        "container_name: mimicaai-storage-mock",
        "WORKFLOW_API_URL: http://workflow-mock:9000",
        "STORAGE_BASE_URL: http://storage-mock:9100",
        '"8000:8000"',
        '"9000:9000"',
        '"9100:9100"',
    ):
        assert needle in text
