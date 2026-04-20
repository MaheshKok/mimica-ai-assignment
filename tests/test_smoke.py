"""Smoke tests verifying the package layout.

These tests prove that:

- The top-level ``app`` and ``mock_services`` packages are importable.
- Every sub-package declared in the directory layout is present and importable.
- ``app.__version__`` is exposed and is a non-empty string.

They intentionally do not exercise any application logic - they catch
import-time regressions (missing ``__init__.py``, broken top-level
imports, version metadata stripped) before the behavioural suites run.
"""

from __future__ import annotations

import importlib

import pytest

EXPECTED_APP_SUBPACKAGES: tuple[str, ...] = (
    "app.api",
    "app.core",
    "app.ports",
    "app.adapters",
    "app.observability",
)

EXPECTED_MOCK_SUBPACKAGES: tuple[str, ...] = (
    "mock_services.workflow_api",
    "mock_services.storage_api",
)


def test_app_package_importable() -> None:
    """The top-level ``app`` package imports and exposes ``__version__``."""
    module = importlib.import_module("app")
    assert hasattr(module, "__version__"), "app.__version__ must be defined"
    assert isinstance(module.__version__, str)
    assert module.__version__, "app.__version__ must be a non-empty string"


def test_mock_services_package_importable() -> None:
    """The ``mock_services`` package imports cleanly."""
    importlib.import_module("mock_services")


@pytest.mark.parametrize("module_name", EXPECTED_APP_SUBPACKAGES)
def test_app_subpackages_importable(module_name: str) -> None:
    """Every expected ``app`` sub-package imports without error."""
    importlib.import_module(module_name)


@pytest.mark.parametrize("module_name", EXPECTED_MOCK_SUBPACKAGES)
def test_mock_subpackages_importable(module_name: str) -> None:
    """Every expected ``mock_services`` sub-package imports without error."""
    importlib.import_module(module_name)
