# Makefile targets for the Enriched QA Service.
# All targets assume `uv` is installed. Run `make help` for a list.

.PHONY: help install sync run run-mocks test test-cov lint format typecheck hooks clean

help:  ## Show this help.
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: sync  ## Install dependencies (evaluator entry point).

sync:  ## Resolve and install dependencies via uv.
	uv sync --all-groups

run:  ## Run the FastAPI service (Phase 3+).
	uv run uvicorn app.main:app --reload --port 8000

run-mocks:  ## Run both mock services: workflow :9000 and storage :9100.
	@echo "Starting mocks — workflow :9000, storage :9100 (Ctrl-C stops both)"
	@trap 'kill %1 %2 2>/dev/null; exit 0' INT TERM; \
		uv run python -m mock_services.workflow_api & \
		uv run python -m mock_services.storage_api & \
		wait

test:  ## Run the test suite (without coverage gate).
	uv run pytest

test-cov:  ## Run tests with coverage; fails under 93%.
	uv run pytest --cov --cov-report=term-missing

lint:  ## Run ruff + flake8 checks (no autofix).
	uv run ruff check .
	uv run flake8 .

format:  ## Auto-fix with ruff.
	uv run ruff check --fix .
	uv run ruff format .

typecheck:  ## Run mypy.
	uv run mypy

hooks:  ## (Contributors only) Install pre-commit hooks. No-op if .git is absent.
	@if [ -d .git ]; then \
		uv run pre-commit install; \
	else \
		echo "No .git directory found - skipping pre-commit install."; \
		echo "This target is for contributors working in a cloned repo, not evaluators running from a zip."; \
	fi

clean:  ## Remove caches and build artefacts.
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
