from __future__ import annotations

import json

import pytest
import yaml

from redline.openapi.loader import RedlineSpecError, load_raw_spec, validate_spec_structure


def test_load_valid_yaml_spec(taskapi_spec_path):
    data = load_raw_spec(taskapi_spec_path)
    assert data["info"]["title"] == "Task API"


def test_load_valid_json_spec(tmp_path, taskapi_spec_path):
    data = load_raw_spec(taskapi_spec_path)
    json_path = tmp_path / "spec.json"
    json_path.write_text(json.dumps(data))
    reloaded = load_raw_spec(json_path)
    assert reloaded["info"]["title"] == "Task API"


def test_missing_file_raises(tmp_path):
    with pytest.raises(RedlineSpecError, match="not found"):
        load_raw_spec(tmp_path / "does-not-exist.yaml")


def test_missing_openapi_field():
    with pytest.raises(RedlineSpecError, match="openapi"):
        validate_spec_structure(
            {"info": {"title": "x", "version": "1"}, "paths": {"/x": {"get": {}}}}
        )


def test_unsupported_openapi_version():
    with pytest.raises(RedlineSpecError, match="Unsupported OpenAPI version"):
        validate_spec_structure(
            {"openapi": "2.0", "info": {"title": "x", "version": "1"}, "paths": {"/x": {"get": {}}}}
        )


def test_missing_info_title():
    with pytest.raises(RedlineSpecError, match="info.title"):
        validate_spec_structure(
            {"openapi": "3.0.3", "info": {"version": "1"}, "paths": {"/x": {"get": {}}}}
        )


def test_missing_paths():
    with pytest.raises(RedlineSpecError, match="paths"):
        validate_spec_structure(
            {"openapi": "3.0.3", "info": {"title": "x", "version": "1"}, "paths": {}}
        )


def test_no_operations_found():
    with pytest.raises(RedlineSpecError, match="no supported HTTP operations"):
        validate_spec_structure(
            {
                "openapi": "3.0.3",
                "info": {"title": "x", "version": "1"},
                "paths": {"/x": {"parameters": []}},
            }
        )


def test_malformed_yaml_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("openapi: 3.0.3\ninfo: [unterminated")
    with pytest.raises(RedlineSpecError, match="Could not parse"):
        load_raw_spec(bad)


def test_top_level_not_object(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.dump(["not", "an", "object"]))
    with pytest.raises(RedlineSpecError, match="top level"):
        load_raw_spec(bad)
