# Redline

**AI-assisted API test planning, generation, and validation.**

Redline takes an OpenAPI specification, uses an LLM to generate structured
test plans and test cases for it, checks every generated case against the
API schema, and runs the ones that pass with pytest. The key design choice:
**the LLM produces test data, not executable code** — a fixed test harness
is what actually runs the requests, so nothing the model writes is ever
executed as a program.

## Why Redline

Writing API tests by hand is repetitive, especially the negative and
edge-case scenarios (missing fields, bad enums, wrong auth, not-found IDs)
that matter most for catching bugs. An LLM is good at proposing that kind of
coverage quickly. It is not good at guaranteeing the result is correct.

Redline's answer is to let the LLM *suggest* tests while keeping every
decision about whether a test is valid, and whether it passed, in
deterministic application code:

```
LLM suggests  →  application validates  →  pytest decides pass/fail
```

If a test fails, Redline can ask the model to fix just that one case, with
just the one error message, up to a fixed retry limit — not regenerate
everything and hope.

## How it works

```
OpenAPI spec
     │
     ▼
Parse & normalize          (redline/openapi -- turns raw YAML/JSON into a
     │                       clean internal model: endpoints, parameters,
     │                       request bodies, response codes)
     ▼
Generate test plan          (LLM call #1 -- a short list of scenario
     │                       sentences, e.g. "missing 'title' returns 400")
     ▼
Generate test cases         (LLM call #2 -- turns approved scenarios into
     │                       structured JSON matching a fixed schema)
     ▼
Validate                    (no LLM -- plain Python checks against the
     │                       real API: does the endpoint/parameter exist,
     │                       is the status code plausible, etc.)
     ▼
Run with pytest             (real HTTP requests, real assertions)
     │
     ▼
Repair failed cases         (bounded retries, one case + one error at a time)
     │
     ▼
Report results              (terminal summary + JSON + markdown)
```

## Why not let the LLM write Python directly?

This is the design decision the rest of the project is built around.

An earlier, more obvious design would have the model write a pytest file
directly. That was rejected for a simple reason: it means **running
arbitrary code that a language model wrote**, on every generation. Even a
well-behaved model occasionally hallucinates a wrong import, an infinite
loop, or a call it shouldn't make — and there's no way to validate a chunk
of Python before running it the way you can validate structured data before
using it.

Instead, the model only ever produces JSON matching a fixed schema (a
`TestCase`: endpoint, method, request, expected status, assertions). That
JSON is:

1. **Parsed and schema-checked** with Pydantic — malformed output is
   rejected before it goes anywhere.
2. **Validated against the real API** — does the endpoint exist, are the
   parameters real, is the expected status code plausible.
3. **Executed by one fixed, hand-written pytest file** (`harness_test.py`)
   that turns the validated data into an HTTP request and checks the
   assertions. This file is part of Redline's own source code — the model
   never writes it and never modifies it.

The trade-off is a smaller vocabulary of what a test can check (status
code, field equals/exists/type, header exists, response-not-empty) instead
of arbitrary logic. That trade is deliberate: a fixed, small vocabulary that
is always safe to execute beats an expressive one that isn't.

## Key features

- OpenAPI 3.x (YAML/JSON) parsing with specific, actionable error messages
- Two-stage LLM generation: a test **plan** (scenarios) before any test case JSON
- Structured, schema-validated test cases — no free-form LLM text is trusted
- Deterministic validation against the real API model (endpoint, method, parameters, status code, header-injection checks)
- Duplicate test cases collapsed automatically
- Real execution via pytest, against a live API or the bundled offline demo app
- A bounded, per-case repair loop, with every attempt recorded
- Machine-readable JSON reports and a human-readable markdown test plan
- A CLI with five commands, each mapping to one pipeline stage
- CI that runs on every push without needing a paid API key

## Architecture

Redline is organized as one small package per pipeline stage:

```
src/redline/
├── cli/          command-line entry point (click)
├── config/       settings, loaded from environment variables
├── openapi/      parses a spec and normalizes it into a clean internal model
├── models/       the shared data contracts (Pydantic): API model, TestCase, TestPlan
├── generation/   builds prompts, calls the LLM, parses its response
├── validation/   deterministic checks + deduplication (no LLM)
├── execution/    the fixed pytest harness + the subprocess runner
├── repair/       the bounded, per-case retry loop
├── reporting/    turns a run into a JSON report and a markdown test plan
└── core/         pipeline.py, which calls the stages above in order
```

Full explanation of each stage, what goes in and what comes out, is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The reasoning behind each
major choice (why pytest, why mock the LLM in CI, why a capped retry loop)
is in [docs/DECISIONS.md](docs/DECISIONS.md).

## Project structure

```
demo_app/     a small in-memory FastAPI app for a fully offline demo
examples/     a sample OpenAPI 3.x spec (taskapi.yaml) used by that demo
tests/
  unit/         one concern per file, LLM calls always mocked
  integration/  the full pipeline against the demo app, LLM calls mocked
  fixtures/     sample specs + canned LLM responses (valid, malformed, ...)
docs/         ARCHITECTURE.md and DECISIONS.md
reports/      where generated JSON/markdown reports are written (gitignored)
```

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,demo]"

# Parse and validate a spec -- no LLM call, no network
redline validate --spec examples/taskapi.yaml
```

```bash
# Run the full pipeline against the bundled offline demo app.
# plan/generate/full call a real LLM, so this needs a key -- see .env.example
export OPENAI_API_KEY=sk-...
redline full --spec examples/taskapi.yaml --demo-app
```

`--demo-app` runs everything in-process against `demo_app/app.py`, a small
FastAPI Task API seeded with one task — no separate server to start. Omit it
and pass `--target-base-url` instead to point Redline at any real API.

| Command | What it does |
|---|---|
| `redline validate --spec <file>` | Parse a spec, list discovered endpoints. No LLM call. |
| `redline plan --spec <file>` | Print an LLM-generated test plan (scenario list) per endpoint. |
| `redline generate --spec <file>` | Generate + validate test cases for every endpoint; writes `test-plan.md`. Does not execute. |
| `redline run --cases <file.json> [--demo-app]` | Execute a previously generated, validated test-case file with pytest. |
| `redline full --spec <file> [--demo-app]` | The entire pipeline: plan → generate → validate → execute → repair → report. |

Every command exits non-zero on failure, so it composes into CI or a
pre-merge check. Settings (API key, retry count, timeout, output directory)
are environment variables with sensible defaults — see
[.env.example](.env.example).

## Example

Given this endpoint in `examples/taskapi.yaml`:

```yaml
/tasks/{task_id}:
  get:
    parameters:
      - name: task_id
        in: path
        required: true
        schema: { type: integer }
        example: 1
    responses:
      "200": { description: The requested task }
      "404": { description: Task not found }
      "401": { description: Missing or invalid API key }
```

Redline first generates a **test plan** (`redline plan`) — a short list of
scenarios, not code yet:

```json
{
  "scenarios": [
    "fetching an existing task returns 200 with the task body",
    "fetching a task id that does not exist returns 404",
    "request without a valid API key returns 401"
  ]
}
```

Then it turns each approved scenario into a structured **test case**
(`redline generate`):

```json
{
  "test_id": "TC-001",
  "endpoint": "/tasks/{task_id}",
  "method": "GET",
  "category": "positive",
  "request": { "path_params": { "task_id": "1" } },
  "expected_status": 200,
  "assertions": [
    { "type": "status_code", "expected": 200 },
    { "type": "field_equals", "field": "id", "expected": 1 }
  ]
}
```

After validation, `redline run` executes it for real and reports the
outcome (`PASSED TC-001` or a specific assertion failure) — never LLM
output on its own, always something that actually ran.

## Testing

```bash
pip install -e ".[dev,demo]"
ruff check src tests      # lint
mypy src                  # type check
pytest -v                 # unit + integration tests
```

**84 automated tests** (78 unit, 6 integration), **86% line coverage**.
Every test mocks the LLM — `MockProvider` returns canned responses from
`tests/fixtures/mock_llm_responses/` (valid, malformed, partial, duplicate,
and repair-needed model output) — so the suite costs nothing and never
depends on network access.

Integration tests run the *entire* pipeline for real (OpenAPI parsing,
deterministic validation, pytest execution against the in-process demo app)
with only the LLM call mocked. This includes both repair paths: a case
rejected by validation getting fixed, and a case that passes validation but
is factually wrong (asserts the wrong status code) getting caught and fixed
after actually failing execution.

## Demo results

Running `redline full --spec examples/taskapi.yaml --demo-app` with a
scripted provider standing in for the LLM (to keep this reproducible and
free) against all 6 endpoints of the bundled demo app:

```
Tests generated:   12
Validated:         12
Executed:          12
Passed:            12
Repair attempts:   0
Final status:      PASSED
```

**This is a sample run against a mocked LLM, not a measured accuracy
number.** It shows the pipeline mechanics work end-to-end; it says nothing
about how a real model performs at scale — see [Limitations](#limitations).

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push
and pull request: lint, type check, the full test suite, and a CLI smoke
test, on Python 3.10 and 3.12. It never calls a real LLM — CI uses the same
`MockProvider` the test suite uses — so a paid API key is never required
and an OpenAI outage can never break the build.

[`.github/workflows/integration.yml`](.github/workflows/integration.yml) is
a separate workflow, triggered manually only, that runs the pipeline against
a real OpenAI key for anyone who wants to check that path. It never runs
automatically.

## Safety and security

- **LLM output is never executed as code** — only as validated data, read by a fixed pytest file. See [Why not let the LLM write Python](#why-not-let-the-llm-write-python-directly).
- **No secrets are sent to the model.** Prompts include the auth header *name*, never a real credential. A test case that needs valid auth uses the literal placeholder `AUTH_TOKEN_PLACEHOLDER`; the real value is substituted in only at execution time, from an environment variable.
- **Every generated case is validated before it runs** — unknown endpoints, methods, or parameters are rejected first.
- **Header/parameter values containing `\r`, `\n`, or a NUL byte are rejected**, as a guard against request/header injection.
- **Retries are bounded** by `--max-retries` (default 2) — no unbounded retry path exists.
- **API keys live in environment variables only.** `.env` is git-ignored; `.env.example` ships with empty values.

This is a set of concrete, testable safeguards, not a formal security audit.

## Limitations

Stated plainly:

- **One LLM provider.** Only OpenAI is implemented (`LLM_PROVIDER=openai`).
- **No real-LLM accuracy number.** The metrics above use a mocked provider (see [Testing](#testing)) — nobody has measured what fraction of *real* model output passes validation across a large set of specs.
- **Local specs only.** External `$ref`s (another file or a URL) aren't resolved — only refs within the same document.
- **A fixed assertion vocabulary** (status code, field equals/exists/type, header exists, response-not-empty) — a deliberate trade-off, not an oversight (see [above](#why-not-let-the-llm-write-python-directly)).
- **No multi-step test chaining.** Every test case is independent; there's no "create a resource, then use its id in the next test."
- **Repair is per-case, not root-cause.** If every case for an endpoint fails for the same reason, each is retried individually rather than diagnosed together.

## Future work

- A second LLM provider behind the existing `LLMProvider` interface.
- External `$ref` support (specs split across multiple files).
- A real evaluation harness measuring validity/pass/repair rates against a real model, across a larger set of specs.
- Multi-step scenarios (create → use the returned id → verify).
