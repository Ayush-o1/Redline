# Architecture

This document explains **how Redline works internally**: what each stage of
the pipeline does, why it exists, and what data moves between stages. For
*what Redline is and how to use it*, see the [README](../README.md). For
*why it was built this way instead of some other way*, see
[DECISIONS.md](DECISIONS.md).

## Overview

```
 OpenAPI spec (YAML/JSON)
        |
        v
 ┌─────────────────┐
 │  openapi/        │  loader.py    -- structural validation, clear errors
 │                  │  normalize.py -- raw dict -> ApiModel (Endpoint, Parameter, ...)
 └────────┬─────────┘
          v
 ┌─────────────────┐
 │  generation/     │  prompts.py   -- endpoint -> system/user prompt
 │  (per endpoint)  │  provider.py  -- LLMProvider (OpenAI | Mock)
 │                  │  generator.py -- raw text -> TestPlan -> list[TestCase]
 └────────┬─────────┘
          v
 ┌─────────────────┐
 │  validation/     │  validator.py -- deterministic checks against ApiModel
 │                  │  + deduplication by (method, path, category, assertions)
 └────────┬─────────┘
          v
 ┌─────────────────┐
 │  execution/      │  harness_test.py -- fixed pytest file: TestCase -> HTTP
 │                  │  runner.py       -- runs it as a subprocess
 └────────┬─────────┘
          v
 ┌─────────────────┐
 │  repair/         │  loop.py -- one failing case + its error -> the model
 │  (bounded)       │  -> re-validate -> re-run. Capped by --max-retries.
 └────────┬─────────┘
          v
 ┌─────────────────┐
 │  reporting/      │  report.py   -> reports/run-<id>.json
 │                  │  markdown.py -> reports/test-plan.md
 └──────────────────┘
```

`core/pipeline.py` is the only module that calls all of these in sequence
(per endpoint: plan → generate → validate → repair → execute → repair
again if needed). Every other module can be read and tested on its own.

## The stages

### 1. Input: an OpenAPI spec

**What:** the user supplies a local OpenAPI 3.x file (YAML or JSON).
**Why:** it's the one piece of ground truth the rest of the pipeline checks
generated tests against — without it, there's nothing to validate against.
**In:** a file path (`--spec examples/taskapi.yaml`).
**Out:** raw YAML/JSON, loaded into a Python dict.

### 2. Parsing & normalization (`redline/openapi/`)

**What:** `loader.py` checks the document has the fields Redline actually
needs (`openapi`, `info.title`, `info.version`, at least one operation) and
raises a specific error naming exactly what's missing or malformed.
`normalize.py` then walks the raw dict — resolving local `$ref` pointers to
reusable schemas — and builds a clean `ApiModel`: a list of `Endpoint`
objects, each with its parameters, request body fields, response codes, and
auth requirements.
**Why:** prompts and validation should never have to deal with raw OpenAPI
noise (`$ref` chains, optional fields in five different shapes) — they work
against one simple, predictable model instead.
**In:** the raw spec dict.
**Out:** an `ApiModel` (see `redline/models/api.py`).

### 3. Generation (`redline/generation/`)

**What:** for each endpoint, two LLM calls happen in sequence:
1. `generate_test_plan` asks for a short list of scenario *sentences*
   ("missing required field 'title' returns 400").
2. `generate_test_cases` turns the approved scenarios into full `TestCase`
   JSON (request, expected status, assertions), which is immediately parsed
   with Pydantic — anything that doesn't match the schema is dropped here,
   before it reaches validation.

**Why:** splitting plan from test cases means a human (or a quick glance at
the log) can sanity-check *coverage* — are these the right scenarios? —
before any structured test-case JSON is generated for them.
**In:** one `Endpoint`.
**Out:** a `TestPlan`, then a `list[TestCase]` (Pydantic model instances,
never raw text).

### 4. Validation (`redline/validation/`)

**What:** plain Python, no LLM call. Every `TestCase` is checked against
the real `ApiModel`: does the endpoint/method exist, are the path/query/body
parameters it references real, is the expected status code documented (or a
common status like 404/401/422), do any values contain `\r`/`\n`/NUL bytes
(a header-injection guard). Cases with identical
`(method, path, category, assertions)` are also collapsed to one.
**Why:** this is the trust boundary. The model can *suggest* a test; this
stage decides whether it's actually usable — independently of whatever the
model claims about itself.
**In:** `list[TestCase]` + the `ApiModel`.
**Out:** a `ValidationResult` — the cases that passed, and a specific
rejection reason for each one that didn't.

### 5. Execution (`redline/execution/`)

**What:** validated cases are written to a JSON file and handed to
`harness_test.py` — a fixed pytest file, never generated or modified by the
model — which parametrizes one test function over them, sends the real HTTP
request, and checks the assertions. `runner.py` invokes this as a
`python -m pytest` subprocess (using the `pytest-json-report` plugin) and
parses the result back into a structured `ExecutionResult`.
**Why:** the model never gets to run anything — it only produces data that
a fixed, reviewed test file consumes. Running pytest as a subprocess (not
calling it in-process) means a slow or hanging target API can't freeze the
Redline CLI, and every run starts with a clean state.
**In:** validated `list[TestCase]`, a target (`--target-base-url` or `--demo-app`).
**Out:** an `ExecutionResult` (pass/fail/error per test id, with the failure message).

### 6. Repair (`redline/repair/`)

**What:** for a case that failed — either rejected by validation or failed
during execution — Redline sends the model exactly that one case, its one
specific error, and the endpoint context, and asks for a fix. The fix goes
through the *same* validation as any generated case (and, for an
execution failure, is actually re-run to confirm it now passes) before
being accepted. Capped at `--max-retries` (default 2); every attempt is
recorded whether it succeeds or not.
**Why:** so failures get one bounded, targeted chance to be fixed instead
of being silently dropped or retried forever.
**In:** one failing `TestCase` + its error message.
**Out:** a fixed `TestCase`, or nothing if the retry budget runs out.

### 7. Reporting (`redline/reporting/`)

**What:** `report.py` builds a `RunReport` (per-endpoint counts, every
generation attempt, every repair attempt, a final PASSED/FAILED/NO_TESTS
status) and writes it to `reports/run-<id>.json`. `markdown.py` renders the
accepted (and rejected) test cases into `reports/test-plan.md` for a human
to read without opening any JSON.
**Why:** every number printed to the terminal comes from this one report —
there's no separate place where a count gets recomputed and could drift out
of sync with what actually happened.
**In:** the results of every stage above.
**Out:** a terminal summary, `run-<id>.json`, `test-plan.md`.

### 8. CI (`.github/workflows/`)

**What:** `ci.yml` runs lint, type checks, the full test suite, and a CLI
smoke test on every push — using `MockProvider` in place of a real LLM
call, so no API key is needed. `integration.yml` is a separate,
manually-triggered workflow that runs the same pipeline with a real
`OPENAI_API_KEY`.
**Why:** CI needs to be fast, free, and deterministic — a real LLM call on
every push would make it slow, costly, and occasionally fail for reasons
that have nothing to do with a code change.
**In:** every push/PR (`ci.yml`), or a manual trigger (`integration.yml`).
**Out:** a pass/fail check on the PR.

## Failure handling summary

| Failure point                          | Handled by                          | Outcome                                     |
|-----------------------------------------|--------------------------------------|----------------------------------------------|
| Spec file missing / malformed YAML/JSON | `openapi/loader.py`                  | `RedlineSpecError` naming the exact problem   |
| Spec missing required OpenAPI fields    | `openapi/loader.py`                  | `RedlineSpecError` naming the missing field   |
| Model returns non-JSON text             | `generation/generator.py`            | `GenerationError`; endpoint skipped, logged   |
| Model JSON doesn't match the schema     | `generation/generator.py` (Pydantic) | that case is dropped; valid ones are kept     |
| Case references a nonexistent endpoint  | `validation/validator.py`            | rejected, eligible for repair                 |
| Case passes validation, fails execution | `execution/` + `repair/`             | repair attempted, capped at `--max-retries`   |
| pytest itself cannot run                | `execution/runner.py`                | `ExecutionError` with captured stdout/stderr  |
