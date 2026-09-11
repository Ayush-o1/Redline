"""Loading and structural validation of OpenAPI documents.

We intentionally implement a small, dependency-free OpenAPI 3.x loader rather
than pulling in a full OpenAPI validation library: Redline only needs the
subset of the spec described in docs/ARCHITECTURE.md, and a focused loader
gives us precise, actionable error messages (see RedlineSpecError below)
instead of a wall of generic JSON-schema errors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


class RedlineSpecError(Exception):
    """Raised when an OpenAPI document is missing or structurally invalid."""


def load_raw_spec(spec_path: str | Path) -> dict[str, Any]:
    """Load a local YAML or JSON OpenAPI document into a plain dict."""
    path = Path(spec_path)
    if not path.exists():
        raise RedlineSpecError(f"Spec file not found: {path}")
    if not path.is_file():
        raise RedlineSpecError(f"Spec path is not a file: {path}")

    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()

    try:
        if suffix in (".yaml", ".yml"):
            data = yaml.safe_load(text)
        elif suffix == ".json":
            data = json.loads(text)
        else:
            # Fall back to sniffing: try JSON first, then YAML.
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = yaml.safe_load(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise RedlineSpecError(f"Could not parse {path} as YAML or JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise RedlineSpecError(f"{path} does not contain a JSON/YAML object at the top level")

    validate_spec_structure(data)
    return data


def validate_spec_structure(data: dict[str, Any]) -> None:
    """Check the minimal structural requirements Redline relies on.

    Raises RedlineSpecError with a specific, human-readable message on the
    first problem found (mirroring section 34 of the project brief: errors
    must name the exact missing field, not just say "invalid").
    """
    openapi_version = data.get("openapi")
    if not openapi_version:
        raise RedlineSpecError("Invalid OpenAPI document: missing required field 'openapi'")
    if not str(openapi_version).startswith("3."):
        raise RedlineSpecError(
            f"Unsupported OpenAPI version '{openapi_version}': Redline supports OpenAPI 3.x only"
        )

    info = data.get("info")
    if not isinstance(info, dict):
        raise RedlineSpecError("Invalid OpenAPI document: missing required field 'info'")
    if not info.get("title"):
        raise RedlineSpecError("Invalid OpenAPI document: missing required field 'info.title'")
    if not info.get("version"):
        raise RedlineSpecError("Invalid OpenAPI document: missing required field 'info.version'")

    paths = data.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise RedlineSpecError(
            "Invalid OpenAPI document: 'paths' is missing or empty -- there is nothing to test"
        )

    valid_methods = {"get", "post", "put", "patch", "delete"}
    found_operation = False
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            raise RedlineSpecError(f"Invalid OpenAPI document: path item '{path}' is not an object")
        for method in path_item:
            if method.lower() in valid_methods:
                found_operation = True

    if not found_operation:
        raise RedlineSpecError(
            "Invalid OpenAPI document: no supported HTTP operations "
            f"({', '.join(sorted(valid_methods))}) found under any path"
        )
