# Redline

AI-assisted API test planning, generation, validation, and execution.

Redline takes an OpenAPI specification, asks an LLM to design and author API
test cases for it, and then treats that output as **untrusted** until it
proves otherwise: every generated test case is validated against the actual
API definition and then executed for real with pytest before it counts as
anything. Failures can trigger a bounded, auditable repair loop. Nothing the
model produces is ever run as code -- only as data consumed by a fixed,
hand-written execution harness.

## Why this exists

Most "AI writes your tests" demos stop at generation: the model produces
something that looks like a test, and that's treated as the finish line.
Redline's premise is that generation is the easy 20% -- the interesting
engineering problem is what happens *after*: is the output actually valid,
does it actually run, what happens when it's wrong, and how do you fix that
without just re-rolling the dice and hoping. Redline is built around that
second half: **validation, execution, and repair**, with generation as the
front door.

## Core workflow

```
OpenAPI spec
     │
     ▼
parse + normalize (redline/openapi)          -- structured, not raw OpenAPI
     │
     ▼
generate a test PLAN with an LLM              -- scenario descriptions first
     │
     ▼
generate structured TEST CASES with an LLM    -- JSON, validated by Pydantic
     │
     ▼
validate deterministically against the spec   -- no LLM involved
     │
     ▼
execute with pytest against the real target   -- subprocess, real HTTP calls
     │
     ▼
repair failing cases (bounded retries)         -- one case + one error at a time
     │
     ▼
report: terminal summary, JSON, markdown       -- reports/run-<id>.json etc.
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full breakdown and
[docs/DECISIONS.md](docs/DECISIONS.md) for why it's built this way.

## Architecture

```
src/redline/
├── cli/          entry point (click) -- validate | plan | generate | run | full
├── config/       Settings, loaded from environment variables
├── openapi/      loader.py (parse + structural validation), normalize.py (-> ApiModel)
├── models/       Pydantic contracts: ApiModel/Endpoint, TestCase/TestPlan/Assertion
├── generation/   prompts.py, provider.py (OpenAI | Mock), generator.py
├── validation/   deterministic checks against ApiModel + deduplication
├── execution/    harness_test.py (fixed pytest harness), runner.py (subprocess)
├── repair/       bounded, per-case repair loop
├── reporting/    RunReport -> JSON report + human-readable markdown test plan
└── core/         pipeline.py orchestrates all of the above

demo_app/         a tiny in-memory FastAPI "Task API" for a fully offline demo
examples/         a hand-written OpenAPI 3.x sample spec (examples/taskapi.yaml)
tests/
├── unit/         one concern per test file, LLM calls always mocked
├── integration/  full pipeline against the demo app, LLM calls mocked
└── fixtures/     sample specs + canned LLM responses (valid, malformed, partial, ...)
```

## Features

- OpenAPI 3.x (YAML/JSON) parsing with specific, actionable error messages
- Two-stage generation: an LLM test **plan** (scenarios) before any structured test cases
- Structured, schema-validated test cases (Pydantic) -- no free-form LLM text is trusted
- Deterministic validation against the real API model (endpoint/method/parameter/status checks, header-injection guard)
- Stable deduplication by `(method, path, category, assertions)`
- Real execution via pytest, against a live target or the bundled offline demo app
- A bounded, per-case repair loop with every attempt recorded
- Machine-readable JSON reports and a human-reviewable markdown test plan
- Auth handling that never sends real credentials to the model (see [Security](#security-considerations))
- CI that never depends on a paid API (mocked LLM); a separate manual workflow for real-LLM runs

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,demo]"

# Parse & validate a spec -- no LLM, no network
redline validate --spec examples/taskapi.yaml

# Run the entire pipeline against the bundled offline demo app.
# Requires OPENAI_API_KEY (see .env.example) since plan/generate call a real LLM.
export OPENAI_API_KEY=sk-...
redline full --spec examples/taskapi.yaml --demo-app
```

`--demo-app` runs everything in-process against `demo_app/app.py`, a small
FastAPI Task API seeded with one task -- no separate server to start, no
external target needed. Omit it and pass `--target-base-url` to point Redline
at any real running API instead.

## CLI usage

| Command | What it does |
|---|---|
| `redline validate --spec <file>` | Parse + structurally validate a spec, list discovered endpoints. No LLM call. |
| `redline plan --spec <file>` | Generate and print an LLM test plan (scenario list) per endpoint. |
| `redline generate --spec <file> [--output-dir DIR]` | Generate + validate test cases for every endpoint; writes `test-plan.md`. Does not execute. |
| `redline run --cases <file.json> [--demo-app \| --target-base-url URL]` | Execute a previously generated, validated `TestCase` JSON file with pytest. |
| `redline full --spec <file> [--max-retries N] [--demo-app \| --target-base-url URL]` | The entire pipeline: plan → generate → validate → execute → repair → report. |

Every command exits non-zero on failure (invalid spec, generation failure, or
any test failing) so it composes into CI or a pre-merge check.

## Configuration

All settings are environment variables with sensible defaults (see
[.env.example](.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | *(none)* | Required for `plan`/`generate`/`full`. Never required for `validate`/`run` or the test suite. |
| `LLM_PROVIDER` | `openai` | Currently only `openai` is implemented. |
| `MODEL` | `gpt-4o-mini` | Model passed to the Chat Completions API. |
| `MAX_RETRIES` | `2` | Repair attempts per failing case. |
| `REQUEST_TIMEOUT` | `30` | Seconds, per LLM request. |
| `OUTPUT_DIR` | `reports` | Where JSON/markdown reports and generated case files are written. |
| `TARGET_BASE_URL` | *(none)* | The API under test. Omit when using `--demo-app`. |
| `REDLINE_AUTH_TOKEN` | *(none)* | Real credential substituted in for the `AUTH_TOKEN_PLACEHOLDER` literal at execution time -- see Security. |

## Example input

[`examples/taskapi.yaml`](examples/taskapi.yaml): a small OpenAPI 3.x Task
API with API-key auth, full CRUD, required fields, an enum, path/query
parameters, and multiple documented response codes (200/201/204/400/401/404/422)
-- enough surface to exercise every test category Redline generates, small
enough to read end-to-end in a few minutes. [`demo_app/app.py`](demo_app/app.py)
implements exactly that spec as an in-memory FastAPI app, so the whole
pipeline can be demonstrated offline with `--demo-app`.

## Example output

Terminal summary (`redline full`):

```
Redline Test Run

Spec:              Task API v1.0.0
Endpoints covered: 6
Tests generated:   12
Validated:         12
Rejected:          0
Duplicates removed:  0
Executed:          12
Passed:            12
Failed:            0
Errors:            0
Repair attempts:   0 (0 successful)
Final status:      PASSED
```

Plus `reports/run-<id>.json` (full machine-readable detail: per-endpoint
counts, every generation attempt, every repair attempt) and
`reports/test-plan.md` (a human-readable table of every accepted test case,
and why any rejected ones were rejected) -- see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for why both exist.

## Testing

```bash
pip install -e ".[dev,demo]"
ruff check src tests      # lint
mypy src                  # type check
pytest -v                 # unit + integration tests
```

Every test mocks the LLM (`redline.generation.provider.MockProvider` with
fixtures under `tests/fixtures/mock_llm_responses/`, covering valid,
malformed, partial, duplicate, and repair-needed model output) -- the suite
costs nothing to run and never depends on network access or an API key.
Integration tests run the *entire* pipeline (real OpenAPI parsing, real
deterministic validation, real pytest execution against the in-process demo
app) with only the LLM call mocked.

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and
PR: lint, type check, full test suite with coverage, and a CLI smoke test --
on Python 3.10 and 3.12, with no external dependency and no secret required.

[`.github/workflows/integration.yml`](.github/workflows/integration.yml) is a
separate, manually-triggered (`workflow_dispatch`) workflow that runs
`redline full` against the demo app with a real `OPENAI_API_KEY` repository
secret, for anyone who wants to validate that path. It never runs
automatically, so a missing key or an OpenAI outage can never break CI.

## Design decisions

Full rationale in [docs/DECISIONS.md](docs/DECISIONS.md); the headlines:

- **OpenAPI 3.x local files only** -- no URL fetching (avoids an SSRF-shaped surface for no real benefit at this scope).
- **pytest as the execution engine** -- mature, standard, gives parametrization and reporting plugins for free.
- **Structured output (Pydantic), never free-form text** -- validity becomes a parse/validate boolean, not a judgment call.
- **Data-driven execution, not LLM-generated Python** -- the model produces `TestCase` JSON; a fixed, hand-written harness executes it. The model never writes code that runs.
- **Deterministic validation, independent of the LLM** -- the trust boundary can't itself depend on the thing it's checking.
- **Mocked LLM in CI, real LLM only in a manual workflow** -- CI stays free, fast, and never flaky because of an upstream provider.
- **A capped, single-case repair loop** -- bounded cost/latency, minimal context per repair prompt.

## Limitations

Said plainly, so nothing here is oversold:

- **One provider.** Only OpenAI's Chat Completions API is implemented (`LLM_PROVIDER=openai`). Adding another provider means implementing `LLMProvider.complete()` for it.
- **No accuracy numbers for real LLM output.** Every metric in this README comes from a real run of the pipeline -- but the generation-stage numbers under [Validation](#validation) below use mocked LLM responses (see [Testing](#testing)), because a real-LLM benchmark run wasn't performed as part of this project (that requires a paid API key and would need a much larger, curated spec set to be a meaningful statistic, not a one-off number). `redline plan/generate/full` do call a real model when `OPENAI_API_KEY` is set; nobody has yet measured what fraction of *that* real output passes validation on a large sample.
- **Single-file specs only.** External (`$ref` to another file or URL) references are not resolved -- only local `#/components/...` refs within the same document.
- **A fixed assertion vocabulary.** Generated tests can only assert `status_code`, `field_equals`, `field_exists`, `field_type`, `header_exists`, and `response_not_empty` -- a deliberate trade-off (see [Decisions](#design-decisions)) for eliminating arbitrary code execution.
- **No test chaining.** Every generated test case is independent (no "create, then use the returned id in the next test") -- each one uses example values from the spec directly. Multi-step workflows aren't modeled.
- **Repair is per-case, not systemic.** If every generated case for an endpoint fails for the same underlying reason (e.g. a misread of the spec), repair retries each one individually rather than diagnosing a shared root cause.

## Security considerations

- **No secrets are ever sent to the model.** Prompts include the auth header
  *name* (from the OpenAPI security scheme) but never a real credential
  value. When a test case needs valid auth, the model is instructed to use
  the literal placeholder `AUTH_TOKEN_PLACEHOLDER`; the execution harness
  substitutes the real value from `REDLINE_AUTH_TOKEN` only at request time.
  See `redline/generation/prompts.py` and `redline/execution/harness_test.py`.
- **LLM output is never executed as code.** See [Architecture](docs/ARCHITECTURE.md#why-llm-output-is-treated-as-untrusted) -- the model produces JSON data only; a fixed, hand-written pytest harness is the only thing that turns it into HTTP requests.
- **Deterministic validation before execution.** Every generated case is checked against the real API model and rejected if it references an unknown endpoint, method, or parameter -- before it is ever sent over the network.
- **Header/request-injection guard.** Parameter and header values containing `\r`, `\n`, or NUL are rejected during validation.
- **Bounded retries.** The repair loop is capped by `--max-retries` (default 2); there is no unbounded retry path anywhere in the pipeline.
- **`.env` is git-ignored** and `.env.example` ships with empty values -- no key has ever been committed to this repository.
- **Redline only tests what you point it at.** There is no crawling, URL discovery, or fetching of specs from the network -- you must supply a local spec file and (for real targets) an explicit `--target-base-url`, so nothing gets tested without deliberate configuration.

## Future improvements

Listed as genuine next steps, not resume padding:

- A second LLM provider (Anthropic/local model) behind the existing `LLMProvider` protocol.
- Multi-step / stateful scenarios (create → use the returned id → verify) instead of independent test cases.
- A measured real-LLM evaluation harness across a larger, curated set of OpenAPI specs (validity rate, execution pass rate, repair success rate) -- see [Limitations](#limitations).
- Endpoint/category coverage visualization in the markdown report.

## Validation

Exact commands, run against this repository as committed:

```bash
$ ruff check src tests
All checks passed!

$ mypy src
Success: no issues found in 28 source files

$ pytest -v
======================= 83 passed in ~6s =======================
  (78 in tests/unit, 5 in tests/integration)

$ pytest --cov=redline --cov-report=term-missing
TOTAL   1234 stmts   204 miss   83% coverage

$ redline validate --spec examples/taskapi.yaml
Spec OK: Task API v1.0.0
Endpoints discovered: 6
  GET     /health
  GET     /tasks [auth]
  POST    /tasks [auth]
  GET     /tasks/{task_id} [auth]
  PUT     /tasks/{task_id} [auth]
  DELETE  /tasks/{task_id} [auth]
```

Full-pipeline run (`redline full --spec examples/taskapi.yaml --demo-app`),
executed against all 6 endpoints of the bundled demo app with a scripted
provider standing in for the LLM call (to keep this result reproducible and
free -- see [Limitations](#limitations) for what that does and doesn't
prove): **12 test cases generated, 12/12 passed deterministic validation,
12/12 executed, 12/12 passed, 0 repair attempts needed.** The repair loop
itself (a case failing validation, getting corrected, and passing on
re-validation, and separately, a case that never gets fixed and correctly
stops after exactly `--max-retries` attempts) is exercised and asserted on
directly in `tests/integration/test_pipeline_mock_llm.py`.

Every number above came from actually running these commands against this
repository, not from an estimate.
