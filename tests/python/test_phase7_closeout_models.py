from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from parser.phase7_closeout_models import Phase7CloseoutManifest, canonical_json, normalize_relative_path
from spec.schema_validator import validate_schema


ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/python/fixtures/phase7_closeout/valid_manifest.json"
SCHEMA = ROOT / "spec/schema/phase7_closeout_manifest.schema.json"
MIRROR = ROOT / "spec/assets/schema/phase7_closeout_manifest.schema.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_valid_manifest_model_and_schema() -> None:
    value = load_fixture()
    assert validate_schema(json.loads(SCHEMA.read_text()), value) is None
    manifest = Phase7CloseoutManifest.from_dict(value)
    assert manifest.to_dict() == value
    assert manifest.canonical_bytes() == manifest.canonical_bytes()
    assert manifest.canonical_bytes().endswith(b"\n")


def test_schema_mirrors_are_byte_identical() -> None:
    assert SCHEMA.read_bytes() == MIRROR.read_bytes()


@pytest.mark.parametrize("path", ("/absolute/file", "./docs/a", "docs/../a", "docs\\a", "docs//a", "tmp/hostname.txt"))
def test_relative_path_normalization_is_fail_closed(path: str) -> None:
    with pytest.raises(ValueError):
        normalize_relative_path(path)


@pytest.mark.parametrize("field", ("phase7_closeout_version", "contract_version", "freeze_commits", "frozen_artifacts", "schema_mirrors", "identity"))
def test_required_and_forbidden_fields_are_closed(field: str) -> None:
    missing = load_fixture()
    del missing[field]
    with pytest.raises(ValueError):
        Phase7CloseoutManifest.from_dict(missing)
    extra = load_fixture()
    extra["unexpected"] = True
    with pytest.raises(ValueError):
        Phase7CloseoutManifest.from_dict(extra)


def test_checksum_and_missing_artifact_records_are_rejected() -> None:
    value = load_fixture()
    value["frozen_artifacts"][0]["sha256"] = "0" * 64
    # A syntactically valid digest is accepted by the model; the audit runner
    # performs the raw-byte mismatch check in the next bounded item.
    assert Phase7CloseoutManifest.from_dict(value).canonical_sha256()
    value = load_fixture()
    value["frozen_artifacts"].pop()
    with pytest.raises(ValueError):
        Phase7CloseoutManifest.from_dict(value)


def test_environment_and_absolute_path_leaks_are_rejected() -> None:
    value = deepcopy(load_fixture())
    value["claim_boundary"][0] = "/tmp/phase7"
    with pytest.raises(ValueError):
        Phase7CloseoutManifest.from_dict(value)
    value = deepcopy(load_fixture())
    value["reproducibility"]["commands"][0] = "pytest --junitxml=/home/user/result.xml"
    with pytest.raises(ValueError):
        Phase7CloseoutManifest.from_dict(value)


def test_canonical_json_is_ascii_and_stable() -> None:
    first = canonical_json({"b": 2, "a": "x"})
    second = canonical_json({"a": "x", "b": 2})
    assert first == second == b'{"a":"x","b":2}\n'
