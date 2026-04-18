# Implementation Plan

## Context

The brief is a **3-hour bounded** challenge. The architecture in `architect.md` is sound but larger than 3 hours of build. Without explicit prioritisation the likely failure mode is beautiful abstractions over a half-wired pipeline.

Operating principle for every phase:

> **Vertical slice first.** Get request → response flowing end-to-end with fakes before any real HTTP or process-pool work. Then swap fakes for real adapters one lane at a time.

Every phase below uses three buckets:

- **MUST** — blockers. If missing the submission is incomplete.
- **SHOULD** — expected quality bar. Cut only under real time pressure.
- **CUT** — drop silently and list under "Known deferrals" in the README.

There are **decision gates** after Phase 3, Phase 5, and Phase 7 — if a gate fails, stop adding features and fix the slice.

---

## Phase 1 — Scaffold

### MUST

- `pyproject.toml` pinning the full dependency set so later phases don't require re-installs:
  - Runtime: `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic>=2`, `pydantic-settings`, `structlog`.
  - OTel: `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-httpx`.
  - Test: `pytest`, `pytest-asyncio`, `anyio`. Adapter tests use `httpx.MockTransport` directly — no extra dependency.
- `[tool.pytest.ini_options]` with `asyncio_mode = "auto"` and `addopts = "--no-header -q"`. Add a **placeholder** `tests/test_smoke.py::test_placeholder` that asserts `True` so `pytest` does not exit with code 5 while the suite is empty.
- Directory layout per `architect.md` §13: `app/{api,core,ports,adapters,observability}`, `mock_services/{workflow_api,storage_api}`, `tests/{unit,integration}`.
- `Makefile` with: `install`, `run`, `run-mocks`, `test`, `lint`, `clean`.
- `.python-version` or equivalent pinning Python 3.12.

### SHOULD

- `.env.example` with every config variable from `architect.md` §11.
- `.gitignore` covering `__pycache__`, `.venv`, `.env`, `.pytest_cache`.

### CUT

- Pre-commit hooks, lock files, CI config.

### Definition of done

- `make install && make test` both exit 0 on a clean checkout. The placeholder test proves the harness runs.

---

## Phase 2 — Domain model and Port contracts

The idea is to lock every interface in code before anything starts calling it. Once these types are written, the rest is fill-in-the-blanks.

### MUST

- `app/core/models.py` — internal domain types that never cross the wire:
  - `ScreenshotRef(timestamp: int, image_id: str)` — frozen dataclass. Sampling uses this, not the bytes-carrying type, so timestamps are preserved when we downsample before fetching.
  - `ScreenshotWithBytes(ref: ScreenshotRef, data: bytes)` — frozen dataclass. Keeps the timestamp with the bytes so the ranker (and any future ranker that cares) can weight by recency.
- `app/core/errors.py` — port-level exception hierarchy used by adapters and handled by the orchestrator:
  - `StorageFetchError(image_id: str, cause: Exception)` — single-image fetch failure (404, timeout, 5xx).
  - `WorkflowUpstreamError(cause: Exception)` — stream or qa_answer failure.
  - `PartialFailureThresholdExceeded(failed: int, total: int)` — raised by the orchestrator when `>MAX_FETCH_FAILURE_RATIO` fetches fail.
- `app/api/schemas.py` — the wire schemas live here, not in `core`. This matches `architect.md` §13 and keeps Pydantic out of the domain types:
  - `EnrichedQARequest` with `from_: int = Field(alias="from", ge=0)`, `to: int = Field(ge=0)`, `project_id: UUID`, `question: Annotated[str, Field(min_length=1, max_length=1024)]`, `model_validator(mode="after")` enforcing `from_ < to`. `model_config = ConfigDict(populate_by_name=True)` so both `from` and `from_` work.
  - `EnrichedQAResponse` with `answer: str` and `meta: Meta`.
  - `Meta` with `request_id: str`, `images_considered: int`, `images_relevant: int`, `errors: dict[str, int]`, `latency_ms: dict[str, int]`.
- `app/ports/workflow.py` — `WorkflowServicesClient` Protocol. `qa_answer(...)` returns `str` directly (drop the separate `Answer` wrapper — the value *is* a string).
- `app/ports/storage.py` — `ScreenshotStorageClient` Protocol; `get_image` raises `StorageFetchError` on any non-2xx or transport failure.
- `app/ports/relevance.py` — `RelevanceRanker` Protocol; signature `async def rank(screenshots: list[ScreenshotWithBytes], question: str, top_k: int) -> list[str]`.
- `app/config.py` — `pydantic-settings` `BaseSettings` model with every field the pipeline needs: `WORKFLOW_API_URL`, `STORAGE_BASE_URL`, `MAX_CONCURRENT_FETCHES`, `GLOBAL_FETCH_CONCURRENCY`, `MAX_RELEVANT_IMAGES`, `MAX_RANK_INPUT`, `MAX_FETCH_FAILURE_RATIO`, `ASSUME_SORTED_STREAM`, `FILTER_WORKERS`, `REQUEST_TIMEOUT_MS`, `OTEL_EXPORTER_OTLP_ENDPOINT`. Defaults match `architect.md` §11. Using `pydantic-settings` from the start avoids a two-step migration later — the env-parsing and boolean-coercion gotchas you'd hit during a dataclass→`BaseSettings` swap are more expensive than the tiny extra dependency cost now.

### SHOULD

- `app/adapters/storage_fake.py` — in-memory dict-backed fake implementing `ScreenshotStorageClient`. Configurable miss-set so partial-failure tests can force `StorageFetchError`.
- `app/adapters/relevance_fake.py` — deterministic ranker (e.g. sorted by `sha256(image_id + question)`, take first `top_k`). Usable in unit tests without the process pool.
- `app/adapters/workflow_fake.py` — constructor takes a list of `ScreenshotRef` and a canned answer string.

### CUT

- A `Clock` port. Time filtering uses stream timestamps, not wall clock. `architect.md` already dropped it.
- A separate `Answer` value object. `str` is enough.

### Definition of done

- `python -c "from app.ports.workflow import WorkflowServicesClient; ..."` imports cleanly for all three ports, the two dataclasses, three errors, and three schemas.
- All fakes satisfy their Protocol (a small `conftest.py` helper that calls each method catches structural drift).

---

## Phase 3 — Vertical slice (with fakes)

This is the most important phase. The moment this works, the architecture is proven.

### MUST

- `app/core/orchestrator.py`:
  - `async def run(req: EnrichedQARequest, ports: Ports, config: Config, request_id: str) -> EnrichedQAResponse`. `request_id` is passed in, not generated here — the handler owns it so the same value appears in logs, spans, and `Meta` in Phase 7.
  - **Stream handling** — consume `ports.workflow.stream_project`, filter rows to `[from_, to)`. If `config.ASSUME_SORTED_STREAM=True` (default), short-circuit as soon as `timestamp >= to`. If `False`, drain to EOF. This matches `architect.md` §4 and keeps the fallback explicit.
  - **Empty-stream early return** — after filtering, if zero `ScreenshotRef` remain, return 200 with `answer=""`, zero counts, empty `errors`, and skip fetch/rank/QA entirely. This is also what prevents the partial-failure divide-by-zero.
  - **Partial-failure counting** — `storage_fetch_failed: int` counter. Each `StorageFetchError` is caught and incremented, the request continues. After the fetch phase, if `total_fetches > 0` **and** `failed / total_fetches > config.MAX_FETCH_FAILURE_RATIO`, raise `PartialFailureThresholdExceeded`. Below the threshold, continue with what succeeded and put the count in `meta.errors["storage_fetch_failed"]`.
  - **Bounded concurrency** — per-request `asyncio.Semaphore(config.MAX_CONCURRENT_FETCHES)` (default 25).
  - **Ranker** — call `ports.relevance.rank(images, question, config.MAX_RELEVANT_IMAGES)`.
  - **QA** — call `ports.workflow.qa_answer(question, ids)`.
  - **Response** — build `Meta` with `request_id`, `images_considered`, `images_relevant`, `errors`, `latency_ms`.
- `app/api/routes.py` — `POST /enriched-qa` handler that:
  - Generates `request_id = str(uuid4())` on entry (Phase 7 moves this to a middleware; see the handoff note in that phase).
  - Calls the orchestrator with the request, dependencies, config, and `request_id`.
  - Installs exception handlers that **all return the same envelope**: `{"error": "<slug>", "detail": "<human message>", "request_id": "<id>"}` per `architect.md` §7. Mappings: `RequestValidationError` → 400 `error="invalid_request"`, `PartialFailureThresholdExceeded` → 502 `error="storage_partial_failure"`, `WorkflowUpstreamError` → 502 `error="workflow_upstream_failure"`, `asyncio.TimeoutError` → 504 `error="request_timeout"`. (FastAPI's default for validation is 422; the handler overrides it to 400 so the error contract matches `architect.md` §7.)
- `app/main.py` — FastAPI app with a `lifespan` context. **Phase 3 defines only the lifespan shape** — no resources owned yet, since fakes don't need them. Phase 4 adds the shared `httpx.AsyncClient`, Phase 6 adds the `ProcessPoolExecutor`. Both close cleanly on shutdown. Establishing the `lifespan` hook in Phase 3 means Phase 4 and Phase 6 add lines, not scaffolding.
- `app/deps.py` — dependency providers. **Default wiring for `make run` in Phase 3 is the fake adapters from Phase 2** (`workflow_fake`, `storage_fake`, `relevance_fake`). Phase 4 swaps the default to the real httpx/process-pool adapters. Tests override via `app.dependency_overrides`.
- `tests/unit/test_orchestrator_happy_path.py` — orchestrator + all fakes → expected answer, checks `images_considered`, `images_relevant`, and absence of `errors`.
- `make run` starts the app with fake wiring; `curl -d '{"project_id":"...","from":1,"to":2,"question":"q"}' http://localhost:8000/enriched-qa` returns 200 with a JSON body produced by the fakes.

### SHOULD

- `meta.latency_ms` populated per phase using `time.perf_counter()` (keys: `stream`, `fetch`, `rank`, `qa`, `total`).
- Test for empty time window returns 200 with `images_considered=0`.

### CUT

- Streaming response (SSE/chunked). Brief does not require it.
- Auth, rate limiting, CORS.

### Gate (critical)

**Can the service accept a request and return a deterministic answer end-to-end using only fakes, with at least the happy-path and empty-window tests green?** If no, stop. Do not start Phase 4.

---

## Phase 4 — Real HTTP adapters

### MUST

- `app/adapters/workflow_http.py`:
  - Shared `httpx.AsyncClient` injected via dependency.
  - `stream_project` uses `client.stream("GET", url)` + `aiter_lines()` + `json.loads` per line. Maps upstream `screenshot_url` → `ScreenshotRef.image_id` at the adapter boundary (`architect.md` §3).
  - `qa_answer` posts JSON, returns the `answer` field as `str`.
  - Malformed NDJSON lines: logged and skipped, not raised. (This behaviour is **adapter-owned**, not orchestrator-owned; tests live at the adapter layer, not the core.)
  - Raises `WorkflowUpstreamError` on any non-2xx or transport failure.
- `app/adapters/storage_http.py`:
  - `get_image(image_id)` hits `{STORAGE_BASE_URL}/images/{image_id}`; returns `bytes` or raises `StorageFetchError`. 404s, 5xx, and timeouts all map to `StorageFetchError`.
- `app/config.py` is already complete from Phase 2 — no replacement needed. Verify env vars override defaults correctly (a single test that instantiates `Settings()` with a patched env proves it).
- **Shared `httpx.AsyncClient`** — constructed in the Phase 3 `lifespan` hook and closed on shutdown. Use `httpx.Limits(max_connections=100, max_keepalive_connections=50)` — architect §6 treats this as one of the three required back-pressure layers, so the plan treats it as MUST too.
- **Process-wide storage semaphore** — `asyncio.Semaphore(config.GLOBAL_FETCH_CONCURRENCY)` (default 100) held in app state and acquired by every storage fetch *in addition to* the per-request one. Without it, 10 concurrent requests × 25 in-flight fetches each saturates the pool; the global cap is what bounds total storage load across requests. Architect §6 explicitly requires it, so it is MUST.
- **Pre-fetch sampling.** In `app/core/orchestrator.py`, after streaming-and-filtering but before fetching, truncate the `list[ScreenshotRef]` to at most `config.MAX_RANK_INPUT`. Sample **uniformly over the time window** (partition the `[from, to)` range into `MAX_RANK_INPUT` buckets, take one ref from each non-empty bucket). This keeps timestamps because we sample `ScreenshotRef`, not `ScreenshotWithBytes`. And critically — we don't fetch images we'd discard.

### SHOULD

- Per-hop timeouts: stream timeout longer than per-request image fetch timeout.
- Overall request budget enforced via `asyncio.timeout(REQUEST_TIMEOUT_MS/1000)` wrapping the whole handler.
- Dedicated test proving the global semaphore caps peak concurrency (mock storage records concurrent connections). The semaphore itself is MUST; the *test* is SHOULD because lower-fidelity tests already exercise the pool through the pipeline.

### CUT

- Retries with backoff. Callers of this service can retry; internal retries double-bill the budget.

### Gate

Three separate test surfaces must all be green — mixing them is a common architectural smell.

1. **Orchestrator unit tests** (from Phase 3) stay on Protocol fakes, nothing changes.
2. **Adapter tests** (`tests/unit/test_workflow_http.py`, `test_storage_http.py`) use `httpx.MockTransport` to verify URL shapes, error mapping to `StorageFetchError`/`WorkflowUpstreamError`, and malformed-NDJSON skip behaviour.
3. **Wire-up test** (`tests/unit/test_routes.py`) uses `app.dependency_overrides` to inject fakes into the real FastAPI app and posts via `httpx.AsyncClient` (ASGI transport).

---

## Phase 5 — Separable mock services

### MUST

- `mock_services/storage_api/app.py` — minimal FastAPI app on port 9100 (explicit brief requirement).
  - Single route: `GET /images/{image_id}` returning deterministic bytes (`f"fake-image::{image_id}".encode()`).
- `mock_services/workflow_api/app.py` — FastAPI app on port 9000. Promoted from SHOULD because Phase 5's gate and the Phase 8 integration test both depend on it; leaving it optional is a contradiction.
  - `GET /projects/{id}/stream` yielding NDJSON with a short interval so streaming is genuinely exercised. Rows are sorted by `timestamp` ascending by default (the assumption our orchestrator relies on); a query param `?shuffle=true` permutes them so the fallback path can be tested.
  - `POST /qa/answer` returning a deterministic answer built from `question` + `image_ids` **in the order received** (no sort). Preserving order is what lets tests detect accidental re-ordering between ranker output and the QA call.
- Both services runnable standalone via `python -m mock_services.<name>` and together via `make run-mocks`.

### SHOULD

- `/images/{image_id}` returns **404 for any `image_id` starting with `missing-`**. A server-side prefix convention works with the real adapter unchanged — the adapter never needs to add query params, and integration tests just include `missing-*` ids in the stream to drive partial-failure thresholds deterministically.

### CUT

- `fake-gcs-server` via Docker. In-process FastAPI is fine for this brief.
- Realistic cadence jitter (fixed short interval is enough to exercise streaming).

### Gate (critical)

**Can the real app talk to the real mock services and return the same answer the fake-backed tests produced?** If no, the HTTP contract has a bug — diagnose before continuing.

### Definition of done

- `make run-mocks` starts both mock services; `curl http://localhost:9100/images/img1` returns bytes; `curl http://localhost:9000/projects/<uuid>/stream` returns NDJSON.

---

## Phase 6 — CPU-bound relevance ranker

### MUST

- `app/adapters/relevance_cpu.py`:
  - `ProcessPoolExecutor(max_workers=config.FILTER_WORKERS)` owned by the FastAPI `lifespan` context (see Phase 3). Shut down cleanly on app stop. `max_tasks_per_child` deliberately omitted — it's useful production hardening but adds multiprocessing-lifecycle variability that's not worth debugging in a 3-hour build. README notes worker recycling as a production deferral. README also notes Linux/macOS as the target platform — Windows requires `spawn` start method but the top-level function signature already works.
  - `rank(...)` uses `asyncio.get_running_loop().run_in_executor(pool, _rank_sync, ...)`.
  - `_rank_sync` is a **top-level function** (picklable) doing a deterministic CPU-shaped operation — e.g., hash each `(image_id, question)` pair with SHA-256, pick the `top_k` with the lowest hash prefix. No real ML.
  - Input arriving here is already at most `MAX_RANK_INPUT` because Phase 4 samples before fetching. The ranker enforces the bound defensively but does not re-sample.

### SHOULD

- Test that the ranker returns at most `top_k` ids and that the order is stable for the same `(images, question, top_k)` input.
- Test that the worker actually runs in a child process (`multiprocessing.current_process().name != "MainProcess"` inside `_rank_sync` via a test hook).

### CUT

- Measuring event-loop lag under CPU load. Documented but not implemented.
- Real embedding / CLIP / anything that needs weights.

### Gate

- None beyond Phase 4's gate still passing.

---

## Phase 7 — Observability

### SHOULD

- `app/observability/tracing.py` — OTel SDK init with `OTLPSpanExporter` if `OTEL_EXPORTER_OTLP_ENDPOINT` is set, otherwise `ConsoleSpanExporter`.
- Auto-instrument `FastAPIInstrumentor` and `HTTPXClientInstrumentor`.
- Manual spans at: `enriched_qa.handler`, `workflow.stream`, `storage.fetch_batch`, `relevance.rank`, `workflow.qa_answer`. Each span has relevant attributes (project_id, images_considered, etc.).
- `app/observability/middleware.py` — request_id middleware. **Takes over `request_id` generation from the route.** The middleware generates or reads the `X-Request-Id` header, writes it to `request.state.request_id`, binds it to the current span and to `structlog.contextvars`. Phase 3's route is updated in this phase to read `request.state.request_id` instead of calling `uuid4()` — single source of truth from here on.
- `app/observability/logging.py` — structlog JSON formatter; single configure() call.

### CUT

- Metrics SDK with OTLP metrics exporter. Spans + logs are enough for a submission.
- Prometheus scrape endpoint.
- Log correlation to traces via `trace_id`/`span_id` injection beyond what auto-instrumentation provides.

### Gate

- Running a request prints a structured log line with `request_id` and emits at least five spans to the console.

---

## Phase 8 — Tests

### MUST

Tests are split by layer. Don't test adapter concerns via the orchestrator or vice-versa — the critique called this out and the split below prevents it.

**Orchestrator unit tests** (`tests/unit/test_orchestrator_*.py`), Protocol fakes only:
- happy path (images considered > 0, images relevant > 0, answer returned).
- empty stream → 200 with `images_considered == 0`.
- boundary inclusivity: `timestamp == from_` included; `timestamp == to` excluded.
- storage partial failure **below** threshold → `meta.errors["storage_fetch_failed"] > 0` and the request succeeds.
- storage failure **above** threshold → `PartialFailureThresholdExceeded` raised.
- empty time window (zero refs after filtering) → 200 with `answer=""`, `images_considered=0`, and **no calls to storage/ranker/QA** (verified by asserting the fakes' call counters stayed at zero).
- stream-sorted assumption: when `assume_sorted_stream=True`, the orchestrator stops pulling after the first `timestamp >= to` (verify via a counter on the fake).
- stream-unsorted fallback: with `assume_sorted_stream=False`, an out-of-order row past `to` does not prematurely end the stream.

**Adapter unit tests** (`tests/unit/test_workflow_http.py`, `test_storage_http.py`), using `httpx.MockTransport`:
- Workflow: malformed NDJSON line is skipped and logged; valid rows after it are still yielded.
- Workflow: `screenshot_url` upstream field maps to `ScreenshotRef.image_id`.
- Workflow: 5xx from stream or qa → `WorkflowUpstreamError`.
- Storage: 404 / 5xx / timeout → `StorageFetchError`.

**Wire-up tests** (`tests/unit/test_routes.py`), FastAPI TestClient with `dependency_overrides`:
- POST with body using literal `"from"` (not `"from_"`) returns 200 — catches alias bugs.
- POST with `from == to` returns **400** (the exception handler overrides FastAPI's default 422).
- POST with missing `question` returns 400.
- `PartialFailureThresholdExceeded` maps to 502.

**Integration test** (`tests/integration/test_end_to_end.py`):
- Spin up `mock_services.storage_api` and `mock_services.workflow_api` via a pytest fixture. Point the real httpx adapters at them. Assert the full request→answer flow works and `meta` is populated.

### SHOULD

- Ranker determinism test (`tests/unit/test_ranker.py`).
- Ranker-in-child-process test (verify `_rank_sync` executes outside MainProcess).
- Burst test: 10 concurrent requests against the mock stack; assert all succeed.
- Global-storage-semaphore test: mock storage counts peak concurrent connections; assert it never exceeds `GLOBAL_FETCH_CONCURRENCY`.

### CUT

- Event-loop lag measurement.
- Property-based tests.
- Load test beyond "10 concurrent requests succeed".

### Gate

- `make test` green. Every MUST test above must be green specifically.

---

## Phase 9 — Polish

### MUST

- `README.md` covering:
  - What the service does (one paragraph).
  - Install + run in under 5 commands.
  - How to run tests.
  - Design decisions: why ports/adapters, why ProcessPoolExecutor, response format.
  - Assumptions (stream sorted by timestamp, `[from, to)` window, partial-failure threshold).
  - Known deferrals: everything under **CUT** that was actually cut.
  - Submission notes (answers to the three Mimica follow-up questions).
- Re-read `architect.md` §14 open questions; write answers in the README.

### CUT

- Inline architecture diagrams as images; they live in `docs/`.
- Full API reference (OpenAPI docs are generated by FastAPI).

---

## Build order summary

Minimal ship order if everything under MUST is done:

1. Scaffold.
2. Models + Protocols + fakes.
3. Orchestrator + FastAPI endpoint + first happy-path unit test. **Gate.**
4. Real httpx adapters + config.
5. Mock storage (required by brief) + mock workflow.  **Gate.**
6. CPU ranker in process pool.
7. Observability (structlog + OTel console).  **Gate.**
8. Remaining tests.
9. README + submission.

---

## Ruthless cut order if time runs short

Cut in this order only. Everything above the line is still required for a credible submission.

1. Burst and concurrency tests (beyond the smoke integration test).
2. OTel spans (keep structlog + request_id).
3. Global-storage-semaphore test (keep the implementation, drop the test proving it works).
4. Ranker-in-child-process test (keep the pool, drop the assertion about which process it ran in).
5. Latency breakdown in `meta` (keep `total` only).
6. `pydantic-settings` for config; replace with a handful of `os.getenv` calls guarded by type casts.
7. Process-wide storage semaphore (keep per-request; note as a known limitation under bursty load).

─── below this line is not a credible submission ───

8. Separable mock services (both).
9. Real httpx adapters.
10. Ports as Protocols (the whole point of the brief).
11. Partial-failure policy (silent data loss → confident wrong answers).
12. End-to-end integration test.

If you find yourself considering anything below the line, stop and re-read the brief.

---

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| FastAPI returns 422 for validation instead of the documented 400 | high | exception handler for `RequestValidationError` → 400 in `main.py`; wire-up test posts `from == to` and asserts 400 |
| `ProcessPoolExecutor` pickle cost at 500 images | medium | pre-fetch sampling caps input at `MAX_RANK_INPUT`; we never pickle more than that |
| httpx `aiter_text` vs `aiter_lines` mistake (buffers) | medium | use `aiter_lines`; integration test with slow mock |
| Pydantic v2 `from_` alias subtle at the FastAPI boundary | medium | wire-up test posts literal `"from"`; `populate_by_name=True` so both work |
| Leaked `httpx.AsyncClient` / `ProcessPoolExecutor` on reload or test exit | medium | both owned by FastAPI `lifespan`; close in the `finally` branch |
| Windows multiprocessing `spawn` not pickling closures | low | ranker is a top-level function; document Linux/macOS target in README |
| `asyncio.timeout` cannot kill worker mid-pickle | medium | bound input size instead of relying on timeout for CPU work |
| Mock storage + real adapter uses `http://` | low | httpx client config accepts plain HTTP in dev |
| Pytest exits 5 with an empty suite and `make test` fails | low | placeholder `test_smoke.py::test_placeholder` in Phase 1 |
| Adapter concerns leak into orchestrator tests (NDJSON parsing, HTTP status mapping) | medium | test layering in Phase 8 is explicit about which file tests what |

---

## Submission checklist

- [ ] `make test` is green on a clean clone.
- [ ] `make run-mocks` in one terminal + `make run` in another + `curl` returns a valid answer.
- [ ] README documents setup, tests, and the three Mimica questions (time spent, what I'd add, feedback).
- [ ] Known deferrals listed with rationale.
- [ ] `.env.example` present; no real secrets in the repo.
- [ ] Zip excludes `.venv`, `__pycache__`, `.pytest_cache`, `.git`.
