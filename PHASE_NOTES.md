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
