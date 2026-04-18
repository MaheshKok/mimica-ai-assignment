# Enriched QA Service

Async REST service that enriches a QA endpoint with relevant screenshots from a project time window.

> Phase 1 scaffold. Implementation is proceeding phase-by-phase per [plan.md](plan.md).
> Architecture lives in [architect.md](architect.md); diagrams in [docs/](docs/).

## Quickstart (evaluators)

```bash
make install   # uv sync - installs all dependencies
make test      # run pytest
make test-cov  # run pytest with 93% coverage gate
make lint      # ruff + flake8
make typecheck # mypy strict
```

Python 3.12 required. Dependencies are managed with [`uv`](https://docs.astral.sh/uv/).
`make install` does **not** install git hooks, so it works from a zipped submission
without a `.git` directory.

## Contributors

If you're working in a cloned repo and want the local commit-time guardrails:

```bash
make hooks     # installs pre-commit hooks - no-op if .git is absent
```
