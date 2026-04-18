# Enriched QA Service

Async REST service that enriches a QA endpoint with relevant screenshots from a project time window.

> Phase 1 scaffold. Implementation is proceeding phase-by-phase per [plan.md](plan.md).
> Architecture lives in [architect.md](architect.md); diagrams in [docs/](docs/).

## Quickstart

```bash
make install   # uv sync + pre-commit install
make test      # run pytest
make lint      # ruff + flake8
make typecheck # mypy strict
```

Python 3.12 required. Dependencies are managed with [`uv`](https://docs.astral.sh/uv/).
