"""Structured internal representation of an API, produced by the OpenAPI normalizer.

Keeping this separate from the raw OpenAPI document means the LLM prompt layer
(see redline.generation.prompts) never has to deal with $ref pointers, vendor
extensions, or other OpenAPI noise -- it only ever sees these clean models.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class ParameterLocation(str, Enum):
    PATH = "path"
    QUERY = "query"
    HEADER = "header"


class Parameter(BaseModel):
    name: str
    location: ParameterLocation
    required: bool = False
    schema_type: str | None = None
    enum_values: list[str] = Field(default_factory=list)
    description: str | None = None
    example: object | None = None


class RequestBodyField(BaseModel):
    name: str
    type: str | None = None
    required: bool = False
    enum_values: list[str] = Field(default_factory=list)
    description: str | None = None


class RequestBody(BaseModel):
    content_type: str = "application/json"
    required: bool = False
    fields: list[RequestBodyField] = Field(default_factory=list)
    example: object | None = None


class ResponseSpec(BaseModel):
    status_code: str
    description: str | None = None
    content_type: str | None = None
    schema_fields: list[str] = Field(default_factory=list)


class SecurityRequirement(BaseModel):
    scheme_name: str
    scheme_type: str | None = None  # e.g. "http", "apiKey", "oauth2"
    header_name: str | None = None  # concrete header to set, when determinable
    header_scheme: str | None = None  # e.g. "Bearer" for http bearer auth


class Endpoint(BaseModel):
    """One (path, method) operation, fully normalized."""

    operation_id: str
    path: str
    method: HttpMethod
    summary: str | None = None
    description: str | None = None
    parameters: list[Parameter] = Field(default_factory=list)
    request_body: RequestBody | None = None
    responses: list[ResponseSpec] = Field(default_factory=list)
    security: list[SecurityRequirement] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @property
    def requires_auth(self) -> bool:
        return len(self.security) > 0

    @property
    def required_parameters(self) -> list[Parameter]:
        return [p for p in self.parameters if p.required]

    @property
    def path_parameters(self) -> list[Parameter]:
        return [p for p in self.parameters if p.location == ParameterLocation.PATH]


class ApiModel(BaseModel):
    """Normalized representation of an entire API definition."""

    title: str
    version: str
    base_url: str | None = None
    description: str | None = None
    endpoints: list[Endpoint] = Field(default_factory=list)

    def find_endpoint(self, path: str, method: str) -> Endpoint | None:
        method_upper = method.upper()
        for ep in self.endpoints:
            if ep.path == path and ep.method.value == method_upper:
                return ep
        return None
