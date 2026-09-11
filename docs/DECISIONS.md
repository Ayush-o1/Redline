# Design Decisions

Why Redline is built the way it is — what was chosen, why, and what it
cost. For *what* each piece does, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Design principles

A handful of rules the decisions below all follow:

1. **LLMs suggest; deterministic code decides.** The model never gets the
   final say on whether a test is valid or whether it passed.
2. **Generated tests are data before they are ever execution.** Nothing the
   model writes is interpreted as a program.
3. **External calls should never make CI flaky.** If it can rate-limit,
   time out, or answer differently each run, it doesn't belong in CI.
4. **Every retry has a limit.** No loop in the pipeline can run forever.
5. **A run should be explainable after the fact.** Every attempt — a
   generation call, a repair attempt — is recorded, not just the final
   outcome.

## Decisions

### OpenAPI 3.x, local files only

**Decision:** accept local OpenAPI 3.x YAML/JSON. No Swagger 2.0, no URL
fetching, no other description formats.

**Reason:** OpenAPI 3.x is the standard for REST APIs and gives the most
coverage for the least parsing work. Fetching specs from a URL was left out
on purpose — it adds a network dependency and lets Redline be pointed at an
arbitrary URL to fetch and parse, which isn't needed to demonstrate the
pipeline.

**Tradeoff:** a spec split across multiple files (external `$ref`) isn't
supported — only refs within the same document.

### A hand-written OpenAPI loader, not a validation library

**Decision:** `openapi/loader.py` implements its own structural checks
instead of depending on a general-purpose OpenAPI validator.

**Reason:** Redline only reads a specific subset of OpenAPI (paths,
parameters, request bodies, responses, security schemes). A generic
validator would produce generic, harder-to-act-on errors; a focused loader
can say exactly "missing required field `info.title`."

**Tradeoff:** it doesn't catch every way an OpenAPI document can be
invalid — only the ways that matter for what Redline reads.

### Two-stage generation: plan, then test cases

**Decision:** ask the model for a scenario list (`TestPlan`) first; a
second call turns approved scenarios into full `TestCase` JSON.

**Reason:** it's much easier to sanity-check "are these the right 6
scenarios" as six sentences than as six full JSON objects, and it keeps
each prompt narrowly scoped to one job.

**Tradeoff:** two LLM calls per endpoint instead of one — more latency and
cost per endpoint, in exchange for a coverage-review step and smaller,
more focused prompts.

### Structured output (Pydantic), never free-form text

**Decision:** every LLM response is parsed as JSON and checked against a
Pydantic model before it's used for anything.

**Reason:** a schema gives a hard, mechanical pass/fail: either it parses
and validates, or it doesn't. This is also what makes the generation layer
testable without a real model — tests assert on Pydantic behavior against
canned responses, not on fuzzy string matching.

**Tradeoff:** the model is constrained to a fixed shape — it can't express
an idea that shape doesn't allow for.

### Data-driven execution, not LLM-generated Python

**Decision:** the model produces `TestCase` JSON only. One fixed,
hand-written pytest file (`execution/harness_test.py`) reads that data and
performs the HTTP request and assertions. The model never writes a `.py`
file, and that file is never generated or modified.

**Reason:** the alternative — the LLM writes pytest source directly — means
running arbitrary model-authored code on every generation. Sandboxing that
properly is a real project on its own; not having code to sandbox in the
first place removes the risk instead of containing it.

**Tradeoff:** assertions are limited to a fixed vocabulary (status code,
field equals/exists/type, header exists, response-not-empty) instead of
arbitrary logic — less expressive, but everything in that vocabulary is
always safe to execute.

### pytest, run as a subprocess

**Decision:** run the harness via `python -m pytest` in a subprocess (with
the `pytest-json-report` plugin for machine-readable output), rather than
calling `pytest.main()` in the same process.

**Reason:** pytest gives parametrized execution and a mature plugin
ecosystem for free. A subprocess means a hung or slow target API can't
freeze the Redline CLI itself, and every run starts with a clean state.

**Tradeoff:** a small amount of process-startup overhead per run, which
doesn't matter at the scale Redline operates at.

### Deterministic validation, independent of the LLM

**Decision:** `validation/validator.py` makes zero LLM calls — it's plain
Python checks against the normalized API model.

**Reason:** validation is the trust boundary. If it depended on another LLM
call, there would be no non-LLM-dependent way to say "this test is
acceptable" — which defeats the point of having a boundary at all.

**Tradeoff:** validation can only check what's structurally
verifiable against the spec (does this endpoint/parameter exist, is this
status plausible) — it can't judge whether a test is a *good* test.

### Mocked LLM in CI; real LLM only in a manual workflow

**Decision:** `ci.yml` (every push/PR) uses `MockProvider` with fixture
responses. `integration.yml` (manual trigger only) uses a real OpenAI key.

**Reason:** CI needs to be free, fast, and deterministic. A real API call
on every push adds cost, adds latency, and can fail for reasons that have
nothing to do with the code (rate limits, an outage, a expired key).

**Tradeoff:** CI never actually proves the real LLM integration still
works end to end — that's what the manual workflow is for, run
deliberately rather than on every push.

### A capped, single-case repair loop

**Decision:** repair works on one failing case at a time, sending only that
case, its specific error, and the endpoint context — capped at
`--max-retries` (default 2).

**Reason:** an uncapped retry loop against a paid API is an open-ended cost
and latency risk. Sending only the minimal context keeps each repair
prompt cheap and focused on the one concrete problem.

**Tradeoff:** repair is per-case, not root-cause — if every case for an
endpoint fails for the same underlying reason, each is retried
individually instead of being diagnosed together.
