# Phase Notes

A per-phase decision log. One entry per phase capturing what shipped, what was
deferred, and any deviation from `plan.md`. Not a changelog — decisions only.

---

## Phase 1 — Scaffold

### Status

Complete. Gate satisfied: `make install && make test` both exit 0 on a clean checkout.

### What shipped

- **Build + deps** (`pyproject.toml`): project metadata, runtime deps (FastAPI,
  httpx, pydantic, pydantic-settings, structlog), OTel stack, dev deps (pytest,
  pytest-asyncio, pytest-cov, ruff, flake8 + plugin suite, mypy, pre-commit).
  Packaged via hatchling. `uv sync --all-groups` resolves everything.
- **Python pin**: `.python-version` set to 3.12. `uv` installs the interpreter
  if missing (used here — system had 3.10 only).
- **Directory layout** matches `architect.md` §13: `app/{api,core,ports,
  adapters,observability}`, `mock_services/{workflow_api,storage_api}`,
  `tests/{unit,integration}`. Every sub-package has an `__init__.py` with a
  one-line docstring so pydocstyle is satisfied.
- **Makefile**: `install`, `sync`, `run`, `run-mocks`, `test`, `test-cov`,
  `lint`, `format`, `typecheck`, `hooks`, `clean`. `run-mocks` is a stub for now.
- **pytest config** (in `pyproject.toml`): `asyncio_mode = "auto"`,
  `--strict-markers --strict-config`, `testpaths = ["tests"]`. Custom
  `integration` marker registered.
- **Coverage config**: `fail_under = 93`, branch coverage on, `app` and
  `mock_services` as the source. `exclude_also` list covers `TYPE_CHECKING`
  blocks, `...` stubs, `NotImplementedError`, and `pragma: no cover`.
- **Smoke test** (`tests/test_smoke.py`): 9 parametrised cases that import
  every declared package and assert `app.__version__` is a non-empty string.
  Writes the tests-from-contract pattern into place for later phases.
- **Ruff config**: Python 3.12 target, 100-char line length, rule set covers
  E/W/F/I/N/D/UP/B/C4/SIM/ANN/PT/TCH/RUF. Google-style docstrings enforced.
  Per-file relaxation for `tests/**` (docstring-per-function not required)
  and `mock_services/**` (scaffolding).
- **Flake8 config** (`.flake8`): runs in parallel to ruff with the user's
  explicit plugin set (bugbear, comprehensions, simplify, docstrings,
  annotations, pytest-style, type-checking, pep8-naming). Ignore list
  mirrors ruff so they don't fight.
- **mypy config**: `strict = true`, `disallow_untyped_defs`, files scoped to
  `app` and `mock_services`. Test files get `disallow_untyped_defs = false`.
- **Pre-commit** (`.pre-commit-config.yaml`): 11 hooks total — trailing
  whitespace, end-of-file-fixer, yaml/toml check, large-file + merge-conflict
  guards, mixed-line-ending, ruff (fix + format), flake8 (with additional
  plugin deps pinned), mypy (with pydantic deps pinned).
- **`.env.example`** with every config var from `architect.md` §11.
- **Stub `README.md`** so hatchling's metadata validation passes.

### Verification

- `make install` — green.
- `uv run pytest` — 9/9 passed.
- `uv run pytest --cov` — 100% of Phase 1 code covered; `fail_under=93` satisfied.
- `uv run ruff check .` — all checks passed.
- `uv run ruff format --check .` — 14/14 files already formatted.
- `uv run flake8 .` — exit 0.
- `uv run mypy` — no issues found in 9 source files.
- `pre-commit run --all-files` — all 11 hooks passed.

### Deferred (tracked for later phases)

- Full `README.md` is Phase 9. Current stub only satisfies hatchling.
- `run-mocks` Makefile target prints a note; real impl comes in Phase 5.
- No application code exists yet, so the coverage number is trivially 100%.
  Real coverage verification starts Phase 2 when the first non-`__init__`
  modules land.

### Deviations from `plan.md`

Phase 1 shipped several quality additions beyond what `plan.md` §Phase 1
specified. These were intentional, driven by explicit user requirements at
implementation time, and are logged here for audit:

| Addition | Why | Relation to plan |
|---|---|---|
| `pre-commit-config.yaml` with 11 hooks | User requested pre-commit hooks | plan.md §Phase 1 CUT listed pre-commit; user override takes precedence |
| `uv.lock` checked in | Result of `uv sync`, reproducible installs | plan.md §Phase 1 CUT listed lock files; lock produced automatically |
| `fail_under = 93` coverage gate | User required 93% coverage | plan.md only required tests to run |
| Flake8 plugin suite (bugbear, comprehensions, simplify, docstrings, annotations, pytest-style, type-checking, pep8-naming) | User requested "flake8 (all variations)" | plan.md only required flake8 via pre-commit |
| `mypy` strict on `app/` and `mock_services/` | User required type hints on every function | plan.md didn't specify the typechecker |
| Expanded Make targets (`test-cov`, `format`, `typecheck`, `hooks`) | Support the additional lint/type/coverage stack | plan.md listed `install run run-mocks test lint clean` |

After the first critique round, one fix was applied post-commit: `make
install` no longer depends on `make hooks`. An evaluator unzipping the
submission will not have a `.git` directory, so installing pre-commit
would have failed. `make hooks` is now a contributor-only target that
no-ops when `.git` is absent.

### Acknowledged but not acted on

- `fail_under = 93` is aggressive for early phases with few modules.
  Tracked here because it may need a temporary relaxation if Phase 2/3
  produces modules whose full test coverage lands in Phase 8. Current
  mitigation: `make test` does *not* enforce coverage (only `make test-cov`
  does), so the tight suite doesn't become a dev-loop friction point.

---

## Phase 2 — Domain model and Port contracts

### Status

Complete. No runtime gate for this phase (no service surface yet). Verified
via imports, 126 unit tests, and 100% coverage on every new module.

### What shipped

Domain and port contracts, plus the Protocol-satisfying fakes that Phase 3 will
wire as the `make run` default.

| Module | Surface |
|---|---|
| `app/core/models.py` | `ScreenshotRef`, `ScreenshotWithBytes` — both frozen, slotted dataclasses. |
| `app/core/errors.py` | `EnrichedQAError` base, `StorageFetchError(image_id, cause)`, `WorkflowUpstreamError(cause)`, `PartialFailureThresholdExceededError(failed, total)`. |
| `app/api/schemas.py` | `EnrichedQARequest` (UUID, `from_` aliased to `from`, window validator, 1-1024 char question), `Meta`, `EnrichedQAResponse`. |
| `app/ports/{workflow,storage,relevance}.py` | All three Protocols decorated `@runtime_checkable`. |
| `app/config.py` | `pydantic-settings` `Settings` model with all fields from `architect.md` §11. |
| `app/adapters/storage_fake.py` | `FakeScreenshotStorage` — dict-backed, has `missing` override, tracks `call_count`. |
| `app/adapters/relevance_fake.py` | `FakeRelevanceRanker` — deterministic SHA-256 ordering, respects `top_k`. |
| `app/adapters/workflow_fake.py` | `FakeWorkflowServicesClient` — configurable refs, canned answer, records `qa_calls` with a defensive copy. |

### Tests

126 unit tests across 8 files (`tests/unit/test_*.py`). Each file tests only
what its target promises via signature + docstring. Coverage enforced at 93%;
actual is 100% on the Phase 2 modules.

Adversarial / boundary cases wired in:

- **Schemas:** `from == to` rejected, `from > to` rejected, negative from/to rejected, `from = 0` accepted, empty question rejected, 1-char/1024-char accepted, 1025-char rejected, invalid UUID rejected. `Meta` negative count rejected. `populate_by_name` proven for both `"from"` and `"from_"`.
- **Errors:** subclass relationship, attribute preservation (identity-check on `cause`), message contains image id and cause, `total=0` constructor doesn't divide by zero.
- **Models:** frozen (FrozenInstanceError on attribute reassign), structural equality, hashability.
- **Ports:** `@runtime_checkable` confirmed; empty class fails `isinstance`; each fake passes `isinstance`.
- **Config:** defaults asserted field-by-field, env overrides checked (int/str/bool), case-insensitive env names, ratio boundary at 0.0 and 1.0, `PositiveInt` rejects zero/negative.
- **Fakes:** call counters, error paths, order preservation, defensive copy (caller mutating input list after `qa_answer` does not mutate recorded call).

### Verification

```
126 passed in 0.36s
Required test coverage of 93.0% reached. Total coverage: 100.00%
ruff: all checks passed
flake8: exit 0
mypy: Success — no issues found in 19 source files
```

### Deviations from plan.md

- **Class rename: `PartialFailureThresholdExceeded` → `PartialFailureThresholdExceededError`.** `pep8-naming` (`N818`) requires the `-Error` suffix. Preferred a rename over `# noqa` so the convention holds globally. Both `plan.md` and `architect.md` updated.
- **`EnrichedQAError` base class added** (not in `plan.md`). Gives handlers a single `except` clause for the whole domain hierarchy. Three-line addition, caught zero tests.
- **`# noqa: B042`** on `StorageFetchError.__init__` was lost to formatter stripping. Moved the waiver to `.flake8`'s `per-file-ignores` so the formatter can't touch it. Rationale: these exceptions are consumed at HTTP handler boundaries, never pickled or `copy.copy`'d.
- **`# noqa: TC003`** on `uuid.UUID` import in `app/api/schemas.py`. Pydantic resolves field annotations at runtime; moving `UUID` behind `TYPE_CHECKING` would break schema construction. All other `TC001/TC003` hits moved into `TYPE_CHECKING` blocks where annotations are genuinely lazy.
- **Test-layer discipline enforced.** Every test file reads only the signature + docstring of its target module. The test-writing process treated the implementation as opaque.

### Post-review fixes (commit `2037266`)

Codex adversarial review surfaced four issues addressed before starting Phase 3:

- `SettingsConfigDict(env_ignore_empty=True)` — blank optional values in `.env.example` (`FILTER_WORKERS=`, `OTEL_EXPORTER_OTLP_ENDPOINT=`) now fall back to defaults instead of failing integer parsing. Added `TestEnvFileLoading::test_env_example_loads_without_error`.
- `filter_workers: PositiveInt | None` — zero and negative values now rejected at load time (they'd crash `ProcessPoolExecutor(max_workers=...)` in Phase 6 otherwise).
- `Meta.errors` / `Meta.latency_ms` typed `dict[str, NonNegativeInt]` — negative counters or durations can no longer cross the HTTP boundary.
- `app/core/errors.py` module docstring: fixed `ExceededErrorError` double-rename artefact.
- `pyproject.toml`: removed ANN101/ANN102 from ruff's ignore list (rules deleted in modern ruff, emit noise warning when listed).

Net: **134 tests** (was 126), 100% coverage, all gates still green.

---

## Phase 3 — Vertical slice (with fakes)

### Status

Complete. **Gate passed.** `make run` + `curl` returns 200 with a deterministic answer produced by the fake pipeline end-to-end.

### What shipped

| Module | Surface |
|---|---|
| `app/deps.py` | `Ports` dataclass (workflow/storage/relevance bundle) + `get_settings`, `get_ports` providers. Default wiring uses the Phase 2 fakes with 3 demo refs so `curl` returns non-empty data. |
| `app/core/orchestrator.py` | `run(req, ports, config, request_id) -> EnrichedQAResponse`. Implements stream+filter, empty-window short-circuit, bounded-concurrency fetch, partial-failure counting + threshold raise, rank, QA, latency metering. Private helpers: `_stream_and_filter` (respects `ASSUME_SORTED_STREAM`), `_fetch_all` (asyncio semaphore). |
| `app/api/routes.py` | `POST /enriched-qa` handler. Generates `request_id` via `uuid4()`, injects `Ports`/`Settings` via `Depends`. |
| `app/main.py` | FastAPI factory `create_app()`, empty-body `lifespan` context (Phase 4 adds `httpx.AsyncClient`, Phase 6 adds `ProcessPoolExecutor`), and 4 exception handlers (RequestValidationError→400, PartialFailureThresholdExceededError→502, WorkflowUpstreamError→502, TimeoutError→504) all returning the `{error, detail, request_id}` envelope. |

### Tests

171 total (was 134 after Phase 2). New files:

- `tests/unit/test_orchestrator.py` — 22 tests covering happy path, empty window (incl. verifying *no* calls to storage/ranker/QA via call counters), boundary inclusivity (`timestamp == from` included, `== to` excluded), sorted-stream short-circuit vs unsorted drain (using a custom `_CountingWorkflow` fake that reports how many refs were drawn), partial failure below/at/above threshold, upstream-error propagation (stream + qa variants), ranker-order preservation in `qa_answer`, concurrency cap verified via a `_TrackingStorage` that records peak in-flight fetches.
- `tests/unit/test_routes.py` — 9 tests: literal `"from"` key round-trip, all four validation failure paths mapping to **400** (override of FastAPI's 422 default), `PartialFailureThresholdExceededError`→502 envelope, `WorkflowUpstreamError`→502 envelope, envelope-shape-is-exactly-`{error, detail, request_id}` assertion.
- `tests/unit/test_main.py` — 3 tests: `TimeoutError`→504 mapping, `lifespan` startup/shutdown via `with TestClient(app)`, `create_app()` returns a fresh instance each call.
- `tests/unit/test_deps.py` — 4 tests: `get_settings`/`get_ports` return expected types, cached singletons, demo refs match demo storage ids (prevents a silent drift that would make `curl` fail with a partial-failure error).

### Verification

```
make test       171 passed in 1.31s
make test-cov   100% coverage (gate 93%)
make lint       ruff + flake8 both clean
make typecheck  mypy clean (23 source files, strict)
pre-commit run --all-files  all 11 hooks pass
make run        started uvicorn on :8001
curl -sS -X POST http://localhost:8001/enriched-qa -d '{...}' -> HTTP 200 + demo answer
curl -sS -X POST ... -d '{"from": 10, "to": 10, ...}'         -> HTTP 400 + {error, detail, request_id}
```

### Deviations from plan.md

- Plan calls out `app/main.py` owning the `httpx.AsyncClient` "from Phase 4" inside `lifespan`. Phase 3 ships the `lifespan` *shape* but no resources — resources arrive in Phase 4. The docstring on `lifespan` spells this out so the handoff is explicit.
- Default demo refs live in `app/deps._demo_ports()` rather than baked into the fake classes. Keeps the fakes test-friendly (construct empty, populate inline) while still giving `make run` a meaningful payload.
- One inline `# noqa: TC001` on `from app.config import Settings` in `app/api/routes.py`. FastAPI resolves dependency-injected parameter annotations at runtime via `get_type_hints()`; moving `Settings` behind `TYPE_CHECKING` makes FastAPI treat `config` as a query parameter, which was caught by the first post-commit test run. Documented next to the noqa.
- Pre-commit mypy needed `fastapi` in `additional_dependencies` so decorator types resolve. Installed mypy was fine because `uv sync` ships the full runtime deps, but pre-commit's isolated env didn't, which mypy flagged as `Untyped decorator makes function … untyped`. Adding FastAPI, structlog, and pydantic-settings to the hook's env fixed it.

### Acknowledged but not acted on

- `RequestValidationError.errors()` is rendered via `str(...)` in the 400 envelope's `detail`. This produces a Python repr with quoted field names; not elegant. Replacing with a structured list is Phase 9 polish.

### Post-adversarial-review fixes (commit pending)

Two reviewers converged on the same Phase 3 boundary gaps — both blockers before Phase 4 lands real adapters. Four fixes applied in one pass:

1. **Ports moved to lifespan ownership.** `_DEFAULT_PORTS` module-global removed. `lifespan` now constructs `Settings` and calls `build_demo_ports()` and stashes both on `app.state`. `get_settings(request)` and `get_ports(request)` read from `request.app.state`. Phase 4's `httpx.AsyncClient` and Phase 6's `ProcessPoolExecutor` drop into the same lifespan `try/finally` without changing the dependency shape. Test `test_deps.py` rewritten to verify this and to assert `AttributeError` when lifespan hasn't run.
2. **Request-timeout budget enforced at the route boundary.** `post_enriched_qa` wraps `run(...)` in `async with asyncio.timeout(config.request_timeout_ms / 1000)`. Live smoke-tested: `request_timeout_ms=50` + a hung workflow returns **504 in 57ms** with the correct envelope. Two tests added in `test_boundary_contracts.py`: hung dependency → 504, fast request not cut off.
3. **`request_id` stashed on `request.state` before calling the orchestrator.** Exception handlers read `request.state.request_id` instead of generating a fresh uuid in the 400/502/504 paths. Test added: forced error returns an envelope whose `request_id` equals the fixed value produced by `uuid4` (monkeypatched to a known value), proving the handler reads what the route stashed — not a fresh uuid.
4. **Validation-error detail sanitised.** `_format_validation_errors` builds a compact `loc: msg` summary from `exc.errors()`, deliberately dropping the `input` field Pydantic includes. Before: caller's `project_id`, `from`, `to`, and `question` all reflected back in the 400 body. After: only field names and validation messages appear. Two tests: secret marker in the question body never appears anywhere in the 400 response, and `project_id` value `"not-a-uuid"` appears nowhere even though the field name does.

### Important structural constraint documented

Two modules (`app/api/routes.py`, `app/deps.py`) deliberately **do not** use `from __future__ import annotations`. FastAPI's dependency-analysis step classifies `Request`/`Settings`-typed parameters via class-identity checks; stringified annotations cause FastAPI to misclassify `request` as a query parameter (reproduced empirically before the fix). Per-file-ignores for `TC001/TC002/TC003` added to both `pyproject.toml` and `.flake8` so the lint rules don't push the imports back into `TYPE_CHECKING` blocks. Module docstrings spell this out to prevent future "harmless cleanup" commits from reintroducing the bug.

### Verification

- 179 tests pass (up from 171). New test file `tests/unit/test_boundary_contracts.py` (6 tests covering timeout, request-id correlation, input sanitisation).
- 100% coverage, gate 93%.
- ruff + flake8 + mypy all clean.
- Live uvicorn: normal `curl` → 200; bad window → 400 no input echo; hung port with `REQUEST_TIMEOUT_MS=50` → 504 in 57ms.

---

## Phase 4 — Real HTTP adapters

### Status

Complete. **Gate passed.** Three test surfaces:

1. Orchestrator unit tests (Protocol fakes) — unchanged, still green.
2. Adapter tests (`httpx.MockTransport`) — all new; verify URL shape, error mapping, NDJSON parsing.
3. Wire-up tests (`app.dependency_overrides`) — unchanged, still green.

Live smoke: `curl` against unreachable upstream returns **502 `workflow_upstream_failure`** with correct envelope.

### What shipped

| Module | Surface |
|---|---|
| `app/adapters/workflow_http.py` | `HttpxWorkflowServicesClient(client, base_url)`. `stream_project` opens `client.stream("GET", ...)` and iterates `aiter_lines()`, mapping `screenshot_url` → `image_id` at the boundary. `qa_answer` POSTs JSON and extracts the `answer` field as `str`. Malformed NDJSON lines are logged and skipped; any 4xx/5xx/transport error → `WorkflowUpstreamError`. |
| `app/adapters/storage_http.py` | `HttpxScreenshotStorageClient(client, base_url, global_semaphore)`. `get_image` acquires the injected process-wide semaphore inside the per-request semaphore held by the orchestrator. Any 4xx/5xx/transport error → `StorageFetchError(image_id, cause)`. |
| `app/core/orchestrator.py` | **Pre-fetch sampling.** New `_sample_uniform_over_window` bins refs into `max_rank_input` equal-width buckets over `[from, to)` and keeps the first per bucket. The orchestrator calls this between filter and fetch, so `images_considered` reflects the pre-sampling count while the partial-failure ratio is computed over the *sampled* set. We never fetch images we'd discard. |
| `app/deps.py` | New `build_http_ports(client, settings, global_semaphore)` factory. `build_demo_ports` kept for offline tests. |
| `app/main.py` | Lifespan now constructs the shared `httpx.AsyncClient` (with `Limits(100/50)` and a 30s read timeout) and a process-wide `asyncio.Semaphore(GLOBAL_FETCH_CONCURRENCY)`, stashes them on `app.state`, composes `Ports` via `build_http_ports`, and `aclose()`s the client on shutdown. |

### Tests

216 total (up from 179). New files:

- `tests/unit/test_workflow_http.py` — 18 tests: valid NDJSON parsing, `screenshot_url`→`image_id` mapping, malformed/missing-field/empty-line skipping, 5xx/4xx/transport-error → `WorkflowUpstreamError`, URL shape, qa_answer request body inspection + id-order preservation on the wire, non-JSON/missing-answer/non-string-answer responses → `WorkflowUpstreamError`, base-URL trailing-slash normalisation.
- `tests/unit/test_storage_http.py` — 9 tests: 200 returns bytes, 404/500/timeout/connect-error → `StorageFetchError`, URL pattern, **global semaphore caps peak concurrency at 3 for 20 concurrent fetches**, **semaphore released on error** (cap=1, two 500s then a success proves no deadlock).
- `tests/unit/test_orchestrator.py` extended with 11 new tests covering `_sample_uniform_over_window` (returns unchanged under/at limit, caps at max, preserves order, spreads across window, clustered input returns fewer, empty input, `max_input=1`, upper-boundary rounding) plus 3 tests on orchestrator integration: `images_considered` reflects pre-sample count, storage is called at most `MAX_RANK_INPUT` times, failure ratio is computed over the sampled total.

### Verification

```
make test       216 passed in 1.60s
make test-cov   100% coverage (gate 93%)
make lint       ruff + flake8 both clean
make typecheck  mypy strict clean (25 source files)
pre-commit run --all-files  all 11 hooks pass
live uvicorn   unreachable upstream -> 502 workflow_upstream_failure envelope
```

### Deviations from plan.md

- Per-hop timeouts simplified to a single client-level `httpx.Timeout(connect=5, read=30, write=10, pool=5)` rather than different timeouts per call site. Plan listed per-hop timeouts as SHOULD; the route-level `asyncio.timeout(REQUEST_TIMEOUT_MS)` is the hard budget anyway, and splitting httpx-level timeouts per call adds complexity for little gain.
- Pre-commit mypy env extended with `httpx`. Same pattern as Phase 3 adding fastapi/structlog/pydantic-settings — the hook's isolated env needs every lib with public types so decorator and attribute types resolve.

### Known gap (Phase 5 will resolve)

- `make run` without Phase 5's mock services running returns `502 workflow_upstream_failure` because the HTTP adapter can't reach `localhost:9000`/`:9100`. This is exactly what `plan.md` predicted and why Phase 5 brings up the separable mock services.

### Post-review fixes (commit pending)

Both adversarial reviewers converged on two real adapter bugs plus three polish items. All five fixed:

1. **[P1] Storage image_id URL-escaped.** `get_image` now builds the URL with `urllib.parse.quote(image_id, safe='')`. Probed inputs that would otherwise be unsafe: `"../escape.png"` no longer reshapes the path; `"img.png?token=secret"` no longer leaks into the query; `/`, `#`, space, `%`, `+`, `&`, and unicode are all percent-encoded. `StorageFetchError.image_id` still carries the *unescaped* original so logs and `meta.errors` reflect what the caller actually asked for. Nine new adversarial URL-encoding tests in `test_storage_http.py`.
2. **[P2] NDJSON parser strict-checks field types.** `_parse_ndjson_line` no longer calls `int(...)` / `str(...)`. It requires the row to be a JSON object; rejects `bool` (despite being an `int` subclass), `None`, floats, numeric strings, non-string `screenshot_url`, empty strings, and non-object payloads (arrays, strings). Eight new adversarial NDJSON tests in `test_workflow_http.py`.
3. **[P3] Two-pass sampling in `_sample_uniform_over_window`.** Pass 1 picks one ref per time bucket (temporal spread). Pass 2 fills remaining slots from cluster leftovers in stream order. Previously 500 refs in one bucket yielded 1 output; now yields `max_input`. Two new/rewritten tests assert both the spread-preserving behaviour and the budget-utilising fill.
4. **[P3] Lifespan test asserts real wiring.** Two new tests: one asserts `app.state.ports.{workflow,storage}` are the actual `HttpxWorkflowServicesClient` / `HttpxScreenshotStorageClient` (guards against a silent revert to demo fakes); another asserts `http_client.is_closed` is `True` after the `TestClient` context exits (guards against a missing `aclose()`). `global_fetch_semaphore` presence also asserted.
5. **[P3] README updated.** Removed Phase 1 scaffold wording. Current state now describes Phase 4: `make run` boots the real HTTP adapters, returns `502 workflow_upstream_failure` until Phase 5 mocks arrive, and the expected envelope is documented inline. Test entry points listed per file.

**Deliberately not acted on:** codex-1's suggestion to "default to `build_demo_ports` until Phase 5 mocks exist" or "ship the mocks in this change". The plan (`plan.md` Phase 4→5) intentionally split real adapters from mock services. Reverting to demo defaults would collapse that boundary; shipping mocks here would be a Phase 5 merge. Fix #5 is the right answer — document the intended state so evaluators aren't surprised.

### Verification

- 237 tests pass (up from 216; +21 new adversarial tests).
- 100% coverage, gate 93%.
- ruff + flake8 + mypy all clean.
- Live probe confirms: `get_image("../escape.png")` now hits `/images/..%2Fescape.png`; `get_image("img.png?token=secret")` hits `/images/img.png%3Ftoken%3Dsecret`; both no longer leak path/query semantics.
- NDJSON bodies like `{"timestamp": true, ...}` and `{"timestamp": 1, "screenshot_url": null}` now yield zero refs instead of `ScreenshotRef(timestamp=1, image_id="None")`.

---

## Phase 5 — Separable mock services + full-stack integration tests

### Status

Complete. **Gate passed.** `make run-mocks` + `make run` + `curl` returns **HTTP 200** with a deterministic answer produced by the real HTTP adapters hitting real uvicorn-backed mock services over loopback.

### What shipped

| Component | Purpose |
|---|---|
| `mock_services/storage_api/app.py` | FastAPI app: `GET /images/{image_id:path}`. The `:path` type captures encoded segments (incl. `%2F`→`/`). Any id starting with `missing-` returns 404 for deterministic partial-failure tests. |
| `mock_services/storage_api/__main__.py` | `python -m mock_services.storage_api` → uvicorn on 127.0.0.1:9100. |
| `mock_services/workflow_api/app.py` | FastAPI app with `create_app(refs=...)` factory. `GET /projects/{id}/stream` streams NDJSON with a 1ms tick per row (real streaming exercise); `?shuffle=true` permutes deterministically via `Random(int(project_id))`. `POST /qa/answer` echoes `"Q: <q> | IDs: <csv>"` preserving received order. |
| `mock_services/workflow_api/__main__.py` | `python -m mock_services.workflow_api` → uvicorn on 127.0.0.1:9000. |
| `make run-mocks` | Starts both services in parallel, traps Ctrl-C to kill both. |

### Tests

**+17 tests** (254 total). Split across three layers:

- `tests/unit/test_storage_mock.py` (5 tests): per-route mock behaviour via `httpx.ASGITransport`, including `{image_id:path}` decoding of `%2F`, `%3F`, and unicode.
- `tests/unit/test_workflow_mock.py` (5 tests): NDJSON row shape, `?shuffle=true` determinism per-project, `create_app(refs=...)` factory, QA-echo order preservation, `422` on empty question.
- `tests/integration/test_end_to_end.py` (7 tests, `@pytest.mark.integration`): the **real** stack — real FastAPI app, real HTTP adapters, real mock FastAPI apps, all wired via two layers of `httpx.ASGITransport` so every byte flows through httpx's request machinery without opening a socket.
  - `TestHappyPath` — default 10 refs, shape contract (exactly `{answer, meta}` with the 5 expected meta keys).
  - **`TestEncodedImageIds::test_ids_with_reserved_chars_round_trip`** — the headline adversarial case the reviewer asked for: 6 refs with `/`, `?`, space, `+`/`&`, and unicode image_ids. All round-trip through encode-decode-echo and appear verbatim in the final answer. Zero `storage_fetch_failed` entries — one drop would fail the test.
  - `TestPartialFailure` — 1/10 missing succeeds with `meta.errors = {"storage_fetch_failed": 1}`; 5/10 missing returns 502 with the exact failed/total counts in `detail`.
  - `TestStreamSortedAssumption` — `assume_sorted_stream=False` with out-of-order refs drains to EOF and still picks up the in-window rows.
  - `TestOrderPreservation` — two identical requests produce the same `| IDs: …` tail, proving ranker order propagates through the wire and back.

### Verification

```
make test            254 passed in 1.74s   (+17 vs Phase 4)
make test-cov        100% coverage (gate 93%)
make lint            ruff + flake8 clean
make typecheck       mypy strict clean (29 source files)
pre-commit run --all-files   all 11 hooks pass

Live stack (real uvicorn):
  $ make run-mocks &                           # workflow :9000, storage :9100
  $ uv run uvicorn app.main:app --port 8001 &  # service
  $ curl -X POST http://localhost:8001/enriched-qa -d '{...}'
  HTTP 200
  {"answer":"Q: what is happening? | IDs: img-005.png,img-006.png,...",
   "meta":{"request_id":"e07f...","images_considered":10,"images_relevant":10,
           "errors":{},"latency_ms":{"stream":21,"fetch":14,"rank":0,"qa":1,"total":37}}}
```

### Deviations from plan.md

- Integration tests use `httpx.ASGITransport` instead of launching uvicorn sub-processes. Runs in the same event loop as the test, zero socket overhead, ~5× faster. Still exercises every httpx encoding/header/streaming code path the real wire would. The real-uvicorn path is verified separately via the live smoke above.
- Pre-commit mypy env extended with `uvicorn[standard]` so the `__main__` modules type-check under the hook.

### Known ergonomic gap

- `make run-mocks` uses `trap ... ; wait` which requires bash-compatible shell semantics. On a system shell lacking job-control syntax the cleanup path won't fire. Not blocking — `make run-mocks` works on macOS/Linux zsh and bash.
