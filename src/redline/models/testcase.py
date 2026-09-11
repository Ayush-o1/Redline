"""The test-case contract.

This is the schema the LLM is asked to fill in. Nothing produced by the model
is trusted until it round-trips through these Pydantic models successfully
(see redline.validation.validator) -- a model instance is the only thing
allowed further into the pipeline.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class TestCategory(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    VALIDATION = "validation"
    BOUNDARY = "boundary"
    AUTHENTICATION = "authentication"
    NOT_FOUND = "not_found"
    SCHEMA = "schema"


class AssertionType(str, Enum):
    STATUS_CODE = "status_code"
    FIELD_EQUALS = "field_equals"
    FIELD_EXISTS = "field_exists"
    FIELD_TYPE = "field_type"
    HEADER_EXISTS = "header_exists"
    RESPONSE_NOT_EMPTY = "response_not_empty"


class Assertion(BaseModel):
    type: AssertionType
    field: str | None = None
    expected: object | None = None

    @model_validator(mode="after")
    def field_required_for_field_assertions(self) -> Assertion:
        needs_field = self.type in {
            AssertionType.FIELD_EQUALS,
            AssertionType.FIELD_EXISTS,
            AssertionType.FIELD_TYPE,
            AssertionType.HEADER_EXISTS,
        }
        if needs_field and not self.field:
            raise ValueError(f"assertion type {self.type} requires a 'field'")
        return self


class RequestSpec(BaseModel):
    path_params: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict | list | None = None


class TestCase(BaseModel):
    __test__ = False  # not a pytest test class, despite the name

    test_id: str
    endpoint: str
    method: str
    category: TestCategory
    name: str
    purpose: str
    preconditions: list[str] = Field(default_factory=list)
    request: RequestSpec
    expected_status: int
    assertions: list[Assertion] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("method")
    @classmethod
    def method_uppercase(cls, v: str) -> str:
        return v.upper()

    @field_validator("expected_status")
    @classmethod
    def status_in_range(cls, v: int) -> int:
        if not (100 <= v <= 599):
            raise ValueError(f"expected_status {v} is not a valid HTTP status code")
        return v

    def dedup_key(self) -> tuple:
        """Stable identity used for deduplication: method + path + category +
        a normalized view of the assertions (order-independent, values included).
        """
        assertions_key = tuple(
            sorted((a.type.value, a.field or "", repr(a.expected)) for a in self.assertions)
        )
        return (self.method, self.endpoint, self.category.value, assertions_key)


class TestPlan(BaseModel):
    """The output of the planning phase: structured scenarios, not yet code."""

    __test__ = False  # not a pytest test class, despite the name

    endpoint: str
    method: str
    scenarios: list[str] = Field(default_factory=list)
    notes: str | None = None


class GenerationAttempt(BaseModel):
    """One attempt (initial or repair) at producing a valid TestCase list for an endpoint."""

    attempt_number: int
    endpoint: str
    method: str
    raw_response: str
    success: bool
    error: str | None = None
    test_case_count: int = 0
