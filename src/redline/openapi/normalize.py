"""Convert a raw OpenAPI document into the structured redline.models.api.ApiModel.

Only local ($ref: "#/components/...") references are resolved. External file
or URL references are out of scope (see docs/DECISIONS.md) -- Redline targets
single-file OpenAPI documents, which covers the overwhelming majority of
hand-written and code-generated specs.
"""

from __future__ import annotations

from typing import Any

from redline.models.api import (
    ApiModel,
    Endpoint,
    HttpMethod,
    Parameter,
    ParameterLocation,
    RequestBody,
    RequestBodyField,
    ResponseSpec,
    SecurityRequirement,
)
from redline.openapi.loader import RedlineSpecError

_METHOD_MAP = {
    "get": HttpMethod.GET,
    "post": HttpMethod.POST,
    "put": HttpMethod.PUT,
    "patch": HttpMethod.PATCH,
    "delete": HttpMethod.DELETE,
}


def _resolve_ref(ref: str, document: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise RedlineSpecError(f"Unsupported external $ref (only local refs are supported): {ref}")
    node: Any = document
    for part in ref.lstrip("#/").split("/"):
        if not isinstance(node, dict) or part not in node:
            raise RedlineSpecError(f"Unresolvable $ref: {ref}")
        node = node[part]
    return node


def _deref(node: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    seen: set[str] = set()
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if ref in seen:
            raise RedlineSpecError(f"Circular $ref detected: {ref}")
        seen.add(ref)
        node = _resolve_ref(ref, document)
    return node


def _schema_type(schema: dict[str, Any]) -> str | None:
    t = schema.get("type")
    if t:
        return t
    if "properties" in schema:
        return "object"
    return None


def _extract_body_fields(
    schema: dict[str, Any], document: dict[str, Any]
) -> list[RequestBodyField]:
    schema = _deref(schema, document)
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields: list[RequestBodyField] = []
    for name, prop_schema in properties.items():
        prop_schema = _deref(prop_schema, document)
        fields.append(
            RequestBodyField(
                name=name,
                type=_schema_type(prop_schema),
                required=name in required,
                enum_values=[str(v) for v in prop_schema.get("enum", [])],
                description=prop_schema.get("description"),
            )
        )
    return fields


def _extract_parameters(
    raw_params: list[dict[str, Any]], document: dict[str, Any]
) -> list[Parameter]:
    params: list[Parameter] = []
    for raw in raw_params:
        raw = _deref(raw, document)
        schema = _deref(raw.get("schema", {}), document)
        try:
            location = ParameterLocation(raw.get("in"))
        except ValueError:
            # Skip parameter kinds Redline does not model (e.g. "cookie").
            continue
        params.append(
            Parameter(
                name=raw["name"],
                location=location,
                required=bool(raw.get("required", False)) or location == ParameterLocation.PATH,
                schema_type=_schema_type(schema),
                enum_values=[str(v) for v in schema.get("enum", [])],
                description=raw.get("description"),
                example=raw.get("example", schema.get("example")),
            )
        )
    return params


def _pick_content_type(content: dict[str, Any]) -> str | None:
    if "application/json" in content:
        return "application/json"
    return next(iter(content), None)


def _extract_request_body(
    raw_body: dict[str, Any] | None, document: dict[str, Any]
) -> RequestBody | None:
    if not raw_body:
        return None
    raw_body = _deref(raw_body, document)
    content = raw_body.get("content", {})
    content_type = _pick_content_type(content)
    if not content_type:
        return RequestBody(required=bool(raw_body.get("required", False)))
    media = content[content_type]
    schema = media.get("schema", {})
    fields = _extract_body_fields(schema, document) if schema else []
    return RequestBody(
        content_type=content_type,
        required=bool(raw_body.get("required", False)),
        fields=fields,
        example=media.get("example"),
    )


def _extract_responses(
    raw_responses: dict[str, Any], document: dict[str, Any]
) -> list[ResponseSpec]:
    responses: list[ResponseSpec] = []
    for status_code, raw_resp in raw_responses.items():
        raw_resp = _deref(raw_resp, document)
        content = raw_resp.get("content", {})
        content_type = _pick_content_type(content)
        schema_fields: list[str] = []
        if content_type:
            schema = _deref(content[content_type].get("schema", {}), document)
            schema_fields = list(schema.get("properties", {}).keys())
        responses.append(
            ResponseSpec(
                status_code=str(status_code),
                description=raw_resp.get("description"),
                content_type=content_type,
                schema_fields=schema_fields,
            )
        )
    return responses


def _extract_security(
    raw_security: list[dict[str, list[str]]] | None,
    document: dict[str, Any],
) -> list[SecurityRequirement]:
    if not raw_security:
        return []
    schemes = document.get("components", {}).get("securitySchemes", {})
    result: list[SecurityRequirement] = []
    for requirement in raw_security:
        for scheme_name in requirement:
            scheme = schemes.get(scheme_name, {})
            scheme_type = scheme.get("type")
            header_name = None
            header_scheme = None
            if scheme_type == "apiKey" and scheme.get("in") == "header":
                header_name = scheme.get("name")
            elif scheme_type == "http":
                header_name = "Authorization"
                header_scheme = scheme.get("scheme", "Bearer").capitalize()
            result.append(
                SecurityRequirement(
                    scheme_name=scheme_name,
                    scheme_type=scheme_type,
                    header_name=header_name,
                    header_scheme=header_scheme,
                )
            )
    return result


def normalize(document: dict[str, Any]) -> ApiModel:
    """Turn a raw (already structurally-validated) OpenAPI dict into an ApiModel."""
    info = document["info"]
    servers = document.get("servers", [])
    base_url = servers[0]["url"] if servers and "url" in servers[0] else None
    global_security = document.get("security")

    endpoints: list[Endpoint] = []
    for path, path_item in document["paths"].items():
        path_item = _deref(path_item, document)
        shared_params = _extract_parameters(path_item.get("parameters", []), document)

        for method_str, operation in path_item.items():
            if method_str.lower() not in _METHOD_MAP:
                continue
            method = _METHOD_MAP[method_str.lower()]
            op_params = _extract_parameters(operation.get("parameters", []), document)

            # Path-level parameters apply to every operation unless overridden by name+location.
            merged = {(p.name, p.location): p for p in shared_params}
            for p in op_params:
                merged[(p.name, p.location)] = p

            operation_id = operation.get("operationId") or f"{method.value.lower()}_{path}"
            security = operation.get("security", global_security)

            endpoints.append(
                Endpoint(
                    operation_id=operation_id,
                    path=path,
                    method=method,
                    summary=operation.get("summary"),
                    description=operation.get("description"),
                    parameters=list(merged.values()),
                    request_body=_extract_request_body(operation.get("requestBody"), document),
                    responses=_extract_responses(operation.get("responses", {}), document),
                    security=_extract_security(security, document),
                    tags=operation.get("tags", []),
                )
            )

    return ApiModel(
        title=info["title"],
        version=info["version"],
        base_url=base_url,
        description=info.get("description"),
        endpoints=endpoints,
    )
