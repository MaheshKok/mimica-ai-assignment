# Enriched QA Service

Async REST service that enriches a QA endpoint with relevant screenshots from a project time window.

> **Current state: Phase 4 complete.** Real `httpx`-backed adapters for the Workflow Services API
> and S3-compatible storage are wired behind the `Ports` abstraction, with pre-fetch sampling,
> per-request + process-wide back-pressure, and a per-request timeout budget.
> Phase 5 (separable mock services under `mock_services/`) is next.
> Architecture lives in [architect.md](architect.md); phase-by-phase execution in
> [plan.md](plan.md); per-phase decision log in [PHASE_NOTES.md](PHASE_NOTES.md).

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

## Running the service

```bash
make run       # starts uvicorn on :8000 with the real HTTP adapters wired
```

**Expected behaviour today (end of Phase 4):** the service starts, but any
`POST /enriched-qa` will return `502 workflow_upstream_failure` because the
adapters try to reach `http://localhost:9000` (Workflow) and
`http://localhost:9100` (Storage), which are not yet running — Phase 5
brings the separable mock services online. A real 502 response looks like:

```json
{
  "error": "workflow_upstream_failure",
  "detail": "workflow upstream error: All connection attempts failed",
  "request_id": "<uuid>"
}
```

This is the expected shape of the error envelope (architect.md §7) and proves
the HTTP adapters, the route timeout, and the `request_id` stashing are all
wired correctly end-to-end. Phase 5 will flip this to `200 OK`.

The target URLs are configurable via environment variables — see `.env.example`
(`WORKFLOW_API_URL`, `STORAGE_BASE_URL`) — so you can point at any compatible
service.

## Testing

```bash
make test                             # fast — full suite on fakes and MockTransport, no network
make test-cov                         # same, with the 93% branch-coverage gate enforced

uv run pytest tests/unit/test_orchestrator.py -v     # core pipeline
uv run pytest tests/unit/test_workflow_http.py -v    # HTTP adapter (via httpx.MockTransport)
uv run pytest tests/unit/test_storage_http.py -v     # HTTP adapter (via httpx.MockTransport)
uv run pytest tests/unit/test_routes.py -v           # FastAPI wire-up via dependency_overrides
uv run pytest tests/unit/test_boundary_contracts.py  # timeout, request-id, input sanitisation
```

## Contributors

If you're working in a cloned repo and want the local commit-time guardrails:

```bash
make hooks     # installs pre-commit hooks - no-op if .git is absent
```
