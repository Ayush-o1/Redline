# Design Decisions

Short records of the choices that shaped Redline, and what was rejected.

## OpenAPI 3.x as the only input format

**Decision:** accept local OpenAPI 3.x YAML/JSON files only. No Swagger 2.0,
no URL fetching, no other API description formats (RAML, GraphQL SDL, etc).

**Why:** OpenAPI 3.x is the de facto standard for REST APIs and gave the
most value for the least parsing surface. URL fetching was deliberately
left out -- it adds a network dependency and an SSRF-shaped attack surface
(fetching and parsing whatever an arbitrary URL returns) for a feature that
isn't needed to demonstrate the core pipeline. A user can always download a
spec and point Redline at the local file.

## A hand-written OpenAPI loader instead of a validation library

**Decision:** `redline/openapi/loader.py` implements its own structural
checks rather than depending on `openapi-spec-validator` or similar.

**Why:** Redline only ever reads a specific, small subset of OpenAPI (paths,
operations, parameters, request bodies, responses, security schemes). A full
JSON-Schema-based validator would either be stricter than necessary (reject
specs Redline can perfectly well use) or produce generic, hard-to-act-on
error messages. A focused loader can say exactly "missing required field
`info.title`" instead of a JSON-schema diff.

## Two-stage generation: plan, then test cases

**Decision:** ask the model for a scenario list first (`TestPlan`), then a
second call turns the approved scenarios into full `TestCase` objects,
rather than asking for finished test cases in one shot.

**Why:** it is much cheaper for a human (or Redline's own logging) to review
"are these the right 6 scenarios" as six sentences than as six full JSON
test cases, and it keeps each prompt narrowly scoped. It also means a
generation failure at the planning stage (e.g. malformed JSON) is caught
before any test-case-shaped work is attempted.

## Structured output (Pydantic) instead of free-form text

**Decision:** every LLM response is parsed as JSON and validated against a
Pydantic model before it is used for anything.

**Why:** free-form text requires a second LLM call (or fragile regex) just
to figure out what the first call said. A schema gives a hard, mechanical
pass/fail boundary: either the JSON parses and validates, or it doesn't. This
is also what makes deterministic unit testing of the generation layer
possible at all (see `tests/unit/test_generator.py`) -- the tests assert on
Pydantic validation behavior, not on string matching.

## Data-driven execution instead of LLM-generated pytest source

**Decision:** the model produces `TestCase` JSON data. A single, fixed,
hand-written pytest file (`redline/execution/harness_test.py`) parametrizes
over that data and performs the HTTP request + assertions. The model never
writes a `.py` file.

**Why:** the alternative -- have the LLM write pytest test functions
directly -- means executing arbitrary model-authored code, which is a real
security problem for a tool whose entire job is to run untrusted-by-design
output. Sandboxing that properly (containers, seccomp, etc.) is out of scope
for a project this size. Treating test cases as *data* consumed by *trusted*
code removes the problem instead of mitigating it. The cost is a slightly
less flexible test format (assertions are a fixed vocabulary: status code,
field equals/exists/type, header exists, response-not-empty) -- a worthwhile
trade for eliminating code execution risk outright.

## pytest + subprocess execution

**Decision:** run the harness via `python -m pytest` in a subprocess (using
`pytest-json-report` for machine-readable output), not `pytest.main()`
in-process.

**Why:** pytest is the standard Python test runner and gives Redline
parametrization, fixtures, and a mature plugin ecosystem for free. Running
it as a subprocess means a hung or slow target API can't block the Redline
CLI process itself, and every run starts with a clean plugin/import state
(no leftover module-level state from a previous run in the same process).

## Deterministic validation, independent of the LLM

**Decision:** `redline/validation/validator.py` contains zero LLM calls. It
is a set of plain Python checks against the normalized `ApiModel`.

**Why:** validation is the trust boundary. If validation itself depended on
another LLM call, the system would have no non-LLM-dependent way to say
"this generated test is acceptable" -- the whole point of the validation
stage is to be the deterministic, reproducible check that generation is not.

## Mocked LLM in CI, real LLM only in a manual workflow

**Decision:** `ci.yml` runs on every push/PR using
`redline.generation.provider.MockProvider` with fixture responses. A
separate `integration.yml` (manual `workflow_dispatch` only) exercises a
real OpenAI key.

**Why:** CI must be free to run, fast, and not flaky because of an upstream
API outage or a expired key. A mocked provider makes the generation layer's
behavior (including malformed/partial/duplicate model output) fully
reproducible in tests.

## A capped, single-case repair loop

**Decision:** repair operates on one failing case at a time, receives only
that case + its specific error + endpoint context, and stops after
`--max-retries` attempts (default 2).

**Why:** an uncapped retry loop against a paid API is a cost and latency
risk with no guaranteed payoff. Sending only the minimal context (not the
full case list, not prior raw model output) keeps each repair prompt cheap
and keeps the model focused on the one concrete problem instead of
re-reasoning about the whole endpoint.

## Controlled test execution, not sandboxed arbitrary code

**Decision:** rather than building a sandbox (containers, restricted
subprocess, etc.) to safely run LLM-authored code, Redline structurally
removes the need for one (see "data-driven execution" above).

**Why:** building and maintaining a real sandbox is a substantial project on
its own, and easy to get subtly wrong. Not having arbitrary code to sandbox
in the first place is a stronger guarantee than a sandbox around code that
still shouldn't be trusted.
