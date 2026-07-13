"""No-follow, snapshotting intake for frozen P7.5 validation inputs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Mapping

from parser.external_case_package_evidence import read_regular_no_follow


INVENTORY_PATH = "docs/phase7_external_trace_sources/source_inventory.json"
OPENED_REPORT_PATH = "tests/python/fixtures/external_validation/packages/reports/opened.json"
PROFILE_PATH = "tests/python/fixtures/external_validation/replay/comparison_profiles/p7.4-freertos-semantic-exact-v1.json"
_SOURCE_ROOT = "tests/python/fixtures/external_validation/sources"
_REPLAY_ROOT = "tests/python/fixtures/external_validation/replay"

ACQUIRED_CASES = {
    "freertos_btf_1core": ("freertos_btf_trace", "example.btf"),
    "freertos_vcd_1core": ("freertos_btf_trace", "example.vcd"),
    "freertos_btf_4cores": ("freertos_btf_trace", "example-4cores.btf"),
    "freertos_btf_50k": ("freertos_btf_trace", "example-50k.btf"),
}
REFERENCE_ONLY_CASES = {
    "zephyr": ("zephyr_pipeline", "SOURCE_ACQUISITION_BLOCKED"),
    "zephelin": ("zephelin_optional", "SOURCE_EXTERNAL_REFERENCE_ONLY"),
}


@dataclass(frozen=True)
class FrozenInputSnapshot:
    logical_path: str
    byte_count: int
    sha256: str


@dataclass(frozen=True)
class ValidationIntake:
    case_id: str
    acquired: bool
    input_bytes: tuple[tuple[str, bytes], ...]
    before_snapshots: tuple[FrozenInputSnapshot, ...]

    def raw(self, logical_path: str) -> bytes:
        for path, value in self.input_bytes:
            if path == logical_path:
                return value
        raise ValueError("frozen input is not part of this intake")

    @property
    def logical_paths(self) -> tuple[str, ...]:
        return tuple(path for path, _ in self.input_bytes)

    def assert_unchanged(self) -> None:
        if snapshot_inputs(self.logical_paths) != self.before_snapshots:
            raise ValueError("frozen validation input mutated")


def _snapshot(logical_path: str) -> tuple[FrozenInputSnapshot, bytes]:
    raw = read_regular_no_follow(logical_path)
    return FrozenInputSnapshot(logical_path, len(raw), hashlib.sha256(raw).hexdigest()), raw


def snapshot_inputs(logical_paths: tuple[str, ...]) -> tuple[FrozenInputSnapshot, ...]:
    return tuple(_snapshot(path)[0] for path in logical_paths)


def case_input_paths(case_id: str) -> tuple[str, ...]:
    if case_id in ACQUIRED_CASES:
        source_id, raw_name = ACQUIRED_CASES[case_id]
        return (
            INVENTORY_PATH,
            f"{_SOURCE_ROOT}/{source_id}/SOURCE.md",
            f"{_SOURCE_ROOT}/{source_id}/LICENSE",
            f"{_SOURCE_ROOT}/{source_id}/checksums.sha256",
            f"{_SOURCE_ROOT}/{source_id}/raw/{raw_name}",
            f"tests/python/fixtures/external_validation/packages/per_case/{case_id}/package_manifest.json",
            OPENED_REPORT_PATH,
            f"tests/python/fixtures/external_validation/packages/reports/per_case/{case_id}.json",
            f"tests/python/fixtures/external_validation/evidence_bindings/{case_id}.json",
            f"{_REPLAY_ROOT}/expected/{case_id}.expected.json",
            PROFILE_PATH,
            f"{_REPLAY_ROOT}/reports/{case_id}.json",
        )
    if case_id in REFERENCE_ONLY_CASES:
        source_id, _ = REFERENCE_ONLY_CASES[case_id]
        return (
            INVENTORY_PATH,
            f"{_SOURCE_ROOT}/{source_id}/SOURCE.md",
            f"{_SOURCE_ROOT}/{source_id}/LICENSE",
            f"{_SOURCE_ROOT}/{source_id}/checksums.sha256",
            f"{_REPLAY_ROOT}/reports/{case_id}.json",
        )
    raise ValueError("unsupported P7.5 case")


def intake_case(case_id: str) -> ValidationIntake:
    paths = case_input_paths(case_id)
    captured = tuple((path, _snapshot(path)) for path in paths)
    before = tuple(snapshot for _, (snapshot, _) in captured)
    value = ValidationIntake(case_id, case_id in ACQUIRED_CASES, tuple((path, raw) for path, (_, raw) in captured), before)
    value.assert_unchanged()
    return value


def input_objects(intake: ValidationIntake, logical_paths: tuple[str, ...]) -> Mapping[str, bytes]:
    if tuple(sorted(logical_paths)) != tuple(sorted(set(logical_paths))):
        raise ValueError("requested frozen inputs are duplicated")
    return {path: intake.raw(path) for path in logical_paths}
