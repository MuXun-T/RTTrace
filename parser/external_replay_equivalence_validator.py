"""Direct P7.4 reproduction and deterministic result equivalence for P7.5."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

from parser.external_layered_validation_models import EquivalenceResult, ReproductionResult
from parser.external_package_replay_adapter import reference_only_report, replay_case_input
from parser.external_semantic_replay import replay
from parser.external_semantic_replay_report import canonical_report
from parser.external_validation_intake import REFERENCE_ONLY_CASES, ValidationIntake, intake_case


_REPORT_ROOT = "tests/python/fixtures/external_validation/replay/reports"
EQUIVALENCE_FACT_FIELDS = (
    "actual_identity",
    "actual_output",
    "advisor_invocation_count",
    "comparison_attempted",
    "comparison_completed",
    "comparison_matched",
    "comparison_profile_id",
    "comparison_profile_identity",
    "comparison_profile_version",
    "expected_identity",
    "feedback_invocation_count",
    "hardware_validation",
    "invariants",
    "llm_invocation_count",
    "mismatch_classes",
    "network_invocation_count",
    "normalized_event_count",
    "package_identity",
    "package_mutation_count",
    "package_open_result",
    "primary_reason",
    "raw_trace_mutation_count",
    "reason_codes",
    "replay_attempted",
    "replay_profile_id",
    "replay_state",
    "shell_invocation_count",
    "source_identity",
    "source_mutation_count",
    "subprocess_invocation_count",
    "timestamp_regressions",
    "trace_identity",
)


@dataclass(frozen=True)
class ReplayEquivalenceValidation:
    case_id: str
    frozen_report_bytes: bytes
    reproduced_report_bytes: bytes
    reproduction_result: ReproductionResult
    equivalence_result: EquivalenceResult

    @property
    def frozen_report(self) -> dict[str, object]:
        return _object(self.frozen_report_bytes, "frozen P7.4 report")

    @property
    def reproduced_report(self) -> dict[str, object]:
        return _object(self.reproduced_report_bytes, "reproduced P7.4 report")


def _object(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _direct_replay(case_id: str) -> dict[str, object]:
    if case_id in REFERENCE_ONLY_CASES:
        return reference_only_report(case_id)
    return replay(replay_case_input(case_id))


def _mismatch_classes(report: Mapping[str, object]) -> tuple[str, ...]:
    values = report.get("mismatches")
    if not isinstance(values, list):
        raise ValueError("P7.4 mismatches are invalid")
    classes: list[str] = []
    for value in values:
        if not isinstance(value, Mapping) or not isinstance(value.get("mismatch_class"), str):
            raise ValueError("P7.4 mismatch class is invalid")
        classes.append(value["mismatch_class"])
    return tuple(classes)


def _fact(report: Mapping[str, object], name: str) -> object:
    return _mismatch_classes(report) if name == "mismatch_classes" else report.get(name)


def _equivalence(frozen: Mapping[str, object], reproduced: Mapping[str, object], canonical_equal: bool) -> EquivalenceResult:
    mismatches = [name for name in EQUIVALENCE_FACT_FIELDS if _fact(frozen, name) != _fact(reproduced, name)]
    if not canonical_equal:
        mismatches.append("canonical_report_bytes")
    return EquivalenceResult(not mismatches, len(EQUIVALENCE_FACT_FIELDS), tuple(sorted(mismatches)))


def reproduce_and_compare_p7_4(case_id: str) -> ReplayEquivalenceValidation:
    intake: ValidationIntake = intake_case(case_id)
    report_path = f"{_REPORT_ROOT}/{case_id}.json"
    frozen_bytes = intake.raw(report_path)
    frozen = _object(frozen_bytes, "frozen P7.4 report")
    if canonical_report(frozen) != frozen_bytes:
        raise ValueError("frozen P7.4 report is not canonical")
    try:
        reproduced = _direct_replay(case_id)
        reproduced_bytes = canonical_report(reproduced)
        intake.assert_unchanged()
    except Exception:
        intake.assert_unchanged()
        raise
    frozen_sha256 = hashlib.sha256(frozen_bytes).hexdigest()
    reproduced_sha256 = hashlib.sha256(reproduced_bytes).hexdigest()
    canonical_equal = frozen_bytes == reproduced_bytes
    return ReplayEquivalenceValidation(
        case_id,
        frozen_bytes,
        reproduced_bytes,
        ReproductionResult(True, canonical_equal, frozen_sha256 == reproduced_sha256, reproduced_sha256),
        _equivalence(frozen, reproduced, canonical_equal),
    )
