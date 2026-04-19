# Enriched QA Service

Async REST service that enriches a QA endpoint with relevant screenshots from a
project time window.

> **Current state: Phase 7 complete.** Observability is live end-to-end.
> Every request is stamped with an `X-Request-Id` (honouring inbound headers or
> minting a UUID4), bound to `structlog` contextvars, and set as an attribute
> on the current OpenTelemetry span. The orchestrator emits five manual spans
> (`enriched_qa.handler`, `workflow.stream`, `storage.fetch_batch`,
> `relevance.rank`, `workflow.qa_answer`) nested under the auto-instrumented
> FastAPI server span, with `httpx` client spans auto-attached for every
> upstream call. Spans export to `OTEL_EXPORTER_OTLP_ENDPOINT` when set and fall
> back to a console exporter for local runs. Logs are JSON-rendered with
> timestamp, level, event, and bound `request_id`. The relevance ranker still
> runs on a lifespan-owned `ProcessPoolExecutor` from Phase 6; broken or
> shut-down pools surface as HTTP 503 (`relevance_ranker_unavailable`).
> Architecture lives in [architect.md](architect.md); phase-by-phase execution
> in [plan.md](plan.md); per-phase decision log in [PHASE_NOTES.md](PHASE_NOTES.md).

## Quickstart (evaluators)

```bash
make install   # uv sync - installs all dependencies
make test      # run pytest (all suites)
make test-cov  # run pytest with 93% coverage gate
make lint      # ruff + flake8
make typecheck # mypy strict
```

Python 3.12 required. Dependencies are managed with
[`uv`](https://docs.astral.sh/uv/). `make install` does **not** install git
hooks, so it works from a zipped submission without a `.git` directory.

## Running the live stack

Two terminals:

```bash
# terminal 1 — starts both mocks with PID tracking, readiness probes,
# and fail-fast cleanup. Ctrl-C stops both.
make run-mocks    # workflow :9000, storage :9100

# terminal 2 — starts the real service
make run          # :8000
```

Then POST to `/enriched-qa`:

```bash
curl -s -X POST http://localhost:8000/enriched-qa \
  -H 'Content-Type: application/json' \
  -d '{
        "project_id":"00000000-0000-0000-0000-000000000001",
        "from":1700000000,
        "to":1700001000,
        "question":"what is happening?"
      }' | jq .
```

Expected `200 OK` response:

```json
{
  "answer": "Q: what is happening? | IDs: img-005.png,img-006.png,...",
  "meta": {
    "request_id": "<uuid>",
    "images_considered": 10,
    "images_relevant": 10,
    "errors": {},
    "latency_ms": {"stream": 21, "fetch": 14, "rank": 0, "qa": 1, "total": 37}
  }
}
```

The target URLs are configurable via `WORKFLOW_API_URL` and `STORAGE_BASE_URL`
— see `.env.example` — so you can point the service at any compatible
deployment. The mock ports can be overridden via `WORKFLOW_PORT` /
`STORAGE_PORT` env vars before `make run-mocks`.

### Troubleshooting: mocks not running

If you hit `make run` without `make run-mocks` first, the HTTP adapters cannot
reach the upstream and every request returns a **502 `workflow_upstream_failure`**
envelope:

```json
{
  "error": "workflow_upstream_failure",
  "detail": "workflow upstream error: All connection attempts failed",
  "request_id": "<uuid>"
}
```

Seeing this shape means the route timeout, `request_id` correlation, and error
envelope are all wired correctly — only the upstream is absent. Start
`make run-mocks` in a second terminal and retry.

## Testing

```bash
make test       # full suite; ASGITransport integration + one live-socket test
make test-cov   # same, with the 93% branch-coverage gate enforced

uv run pytest tests/unit/test_orchestrator.py -v         # core pipeline
uv run pytest tests/unit/test_workflow_http.py -v        # HTTP adapter (httpx.MockTransport)
uv run pytest tests/unit/test_storage_http.py -v         # HTTP adapter (httpx.MockTransport)
uv run pytest tests/unit/test_routes.py -v               # FastAPI wire-up via overrides
uv run pytest tests/unit/test_boundary_contracts.py      # timeout, request-id, sanitisation
uv run pytest tests/integration/test_end_to_end.py       # component-level, ASGITransport
uv run pytest tests/integration/test_live_stack.py       # real sockets + subprocess mocks
```

The integration suite is split into two layers. `test_end_to_end.py` uses
`httpx.ASGITransport` for fast component-level coverage of encoding,
partial-failure handling, and order preservation. `test_live_stack.py` spawns
the mocks and the app as real `uvicorn` subprocesses on ephemeral ports and
exercises the full production wiring — shared `httpx.AsyncClient` lifespan,
`build_http_ports`, and TCP between every hop. One case in the live stack
pipes app stdout to a file and asserts a JSON log line appears carrying the
inbound `X-Request-Id`, proving the observability pipeline is fully wired.

## Observability

Set `OTEL_EXPORTER_OTLP_ENDPOINT=https://your-collector:4317` to ship spans
over OTLP/gRPC. Leave it unset and spans stream to stdout via the console
exporter — useful for `make run` without standing up infrastructure. The
service name on every resource is `enriched-qa-service`.

- **Request correlation**: the service honours an inbound `X-Request-Id`
  header (empty and whitespace-only values are ignored), otherwise mints a
  UUID4. The id is echoed in the response header, stamped on the response
  body's `meta.request_id` (and on any error envelope), bound to every log
  line emitted during the request, and set as the `request_id` attribute on
  the current OTel span.
- **Spans**: five manual spans — `enriched_qa.handler`, `workflow.stream`,
  `storage.fetch_batch`, `relevance.rank`, `workflow.qa_answer` — nest under
  the auto-instrumented FastAPI server span. `httpx` client spans are
  attached automatically for every upstream call.
- **Logs**: structured JSON, one object per line — `event`, `level`,
  `timestamp` (ISO-8601 UTC), plus any bound contextvars and handler-supplied
  fields. Uvicorn's access log flows through the same stdout.

## Contributors

If you're working in a cloned repo and want the local commit-time guardrails:

```bash
make hooks     # installs pre-commit hooks - no-op if .git is absent
```
