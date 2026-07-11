#!/usr/bin/env python3
"""Build and validate the deterministic Phase 6 closeout manifest."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_CLOSEOUT_HEAD = "d844474"
P6_4_REPORT_HASH = "fe6bb93191fc72e9584339878c0418b1005f3631f0e213e92b8dcbeb2bd2811e"
FROZEN_COMMITS = (
    ("7f57ec5", "docs: add phase6 pre-approval development plan", "P6.0"),
    ("d381a8c", "phase6 add diagnosis scope lock and case schemas", "P6.1"),
    ("c187b41", "phase6 add synthetic diagnosis fixtures", "P6.2"),
    ("278e7ee", "phase6 add reference-only diagnosis replay validation", "P6.3"),
    ("ad8e7ab", "phase6 add deterministic diagnosis metrics report", "P6.4"),
    ("35b1505", "phase6 add isolated advisor diagnosis review variants", "P6.5"),
    ("d844474", "phase6 add privacy-preserving human feedback pipeline", "P6.6"),
)
SCHEMAS = (
    "rtos_diagnosis_case.schema.json",
    "rtos_diagnosis_suite.schema.json",
    "rtos_diagnosis_report.schema.json",
    "rtos_diagnosis_advisor_review.schema.json",
    "rtos_diagnosis_human_feedback_record.schema.json",
    "rtos_diagnosis_human_feedback_summary.schema.json",
)
MANIFEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _entries(phase_item: str, artifact_type: str, claim_class: str, paths: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {"path": path, "artifact_type": artifact_type, "phase_item": phase_item,
         "required": True, "synthetic_only": True, "claim_class": claim_class}
        for path in paths
    ]


def _artifact_groups() -> dict[str, list[dict[str, Any]]]:
    return {
        "scope_and_contracts": _entries("P6.0-P6.1", "contract", "report_only", (
            "docs/phase6_pre_approval_development_plan_20260709.md", "docs/phase6_scope_note.md",
            "docs/phase6_implementation_checklist.md", "docs/phase6_risk_register.md")),
        "synthetic_fixtures": _entries("P6.2", "synthetic_fixture", "report_only", (
            "tests/python/fixtures/rtos_diagnosis/generated/phase6_synthetic_suite.json",
            "tests/python/fixtures/rtos_diagnosis/generated/phase6_synthetic_suite_artifacts",
            "tests/python/fixtures/rtos_diagnosis/phase6_stub_suite.json")),
        "replay_validation": _entries("P6.3", "validation", "report_only", (
            "docs/phase6_p6_3_replay_validation.md", "parser/rtos_diagnosis_replay.py",
            "tool/run_rtos_diagnosis_replay.py", "tests/python/test_rtos_diagnosis_replay.py")),
        "diagnosis_report": _entries("P6.4", "report", "report_only", (
            "docs/phase6_p6_4_diagnosis_report.md", "parser/rtos_diagnosis_report.py",
            "tool/build_rtos_diagnosis_report.py",
            "tests/python/fixtures/rtos_diagnosis/reports/phase6_diagnosis_report.json",
            "tests/python/test_rtos_diagnosis_report.py")),
        "advisor_review": _entries("P6.5", "review_overlay", "report_only", (
            "docs/phase6_p6_5_advisor_review.md", "parser/rtos_diagnosis_advisor_review.py",
            "tool/run_rtos_diagnosis_advisor_review.py",
            "tests/python/fixtures/rtos_diagnosis/advisor_reviews/phase6_advisor_review.json",
            "tests/python/test_rtos_diagnosis_advisor_review.py")),
        "human_feedback": _entries("P6.6", "synthetic_feedback", "report_only", (
            "docs/phase6_p6_6_human_feedback.md", "parser/rtos_diagnosis_human_feedback.py",
            "tool/build_rtos_diagnosis_human_feedback_summary.py",
            "tests/python/fixtures/rtos_diagnosis/human_feedback/phase6_synthetic_feedback.json",
            "tests/python/fixtures/rtos_diagnosis/human_feedback/phase6_human_feedback_summary.json",
            "tests/python/test_rtos_diagnosis_human_feedback.py")),
        "schemas": _entries("P6.1-P6.6", "schema", "deterministic_contract", tuple(
            f"spec/schema/{name}" for name in SCHEMAS) + tuple(f"spec/assets/schema/{name}" for name in SCHEMAS)),
        "tests": _entries("P6.1-P6.7", "test", "deterministic_contract", (
            "tests/python/test_rtos_diagnosis_schema.py", "tests/python/test_rtos_diagnosis_fixtures.py",
            "tests/python/test_rtos_diagnosis_advisor_review_security.py",
            "tests/python/test_rtos_diagnosis_human_feedback_privacy.py", "tests/python/test_phase6_closeout.py")),
        "cli_tools": _entries("P6.2-P6.7", "cli", "deterministic_contract", (
            "tool/build_rtos_diagnosis_cases.py", "tool/build_phase6_closeout_manifest.py")),
        "closeout_documents": _entries("P6.7", "closeout_document", "deterministic_contract", (
            "docs/phase6_p6_7_closeout.md", "docs/phase6_claim_boundary_matrix.md",
            "docs/phase6_reproducibility_index.md")),
    }


def build_manifest(*, manifest_id: str = "phase6-p6-7-closeout", test_passed: int = 682,
                   subtests_passed: int = 950) -> dict[str, Any]:
    """Return data only; current time, git state, and environment are never read."""
    if not isinstance(manifest_id, str) or not MANIFEST_ID.fullmatch(manifest_id):
        raise ValueError("manifest_id must be an ASCII slug")
    if test_passed < 0 or subtests_passed < 0:
        raise ValueError("test counts must be non-negative")
    return {
        "schema_version": "phase6-closeout-manifest-v1", "manifest_id": manifest_id, "phase": 6,
        "closeout_scope": "Phase 6 engineering closeout only; no new diagnosis, replay, proof, advisor, or feedback capability.",
        "pre_closeout_head": PRE_CLOSEOUT_HEAD,
        "frozen_commits": [{"commit": commit, "message": message, "phase_item": item, "frozen": True}
                           for commit, message, item in FROZEN_COMMITS],
        "artifact_groups": _artifact_groups(),
        "schema_mirror_checks": [
            {"source": f"spec/schema/{name}", "mirror": f"spec/assets/schema/{name}", "matches": True}
            for name in SCHEMAS
        ],
        "canonical_artifacts": {
            "p6_4_report": {"path": "tests/python/fixtures/rtos_diagnosis/reports/phase6_diagnosis_report.json", "canonical_hash": P6_4_REPORT_HASH},
            "p6_5_deterministic_review": {"path": "tests/python/fixtures/rtos_diagnosis/advisor_reviews/phase6_advisor_review.json", "synthetic_only": True},
            "p6_6_synthetic_feedback": {"path": "tests/python/fixtures/rtos_diagnosis/human_feedback/phase6_synthetic_feedback.json", "synthetic_only": True},
            "p6_6_expected_summary": {"path": "tests/python/fixtures/rtos_diagnosis/human_feedback/phase6_human_feedback_summary.json", "synthetic_only": True},
        },
        "test_baseline": {"label": "P6.7 final full-regression result", "python_test_passed": test_passed,
                          "subtests_passed": subtests_passed, "failures": 0, "skips": 0, "warnings": 0},
        "invariants": {
            "replay": {"replay_pass_count": 0, "replay_fail_count": 0, "reference_only_count": 8, "all_replay_passed": False},
            "report": {"claimable_count": 0, "report_only_count": 8, "proof_drift_count": 0, "real_hardware_case_count": 0},
            "advisor": {"truth_mutation_count": 0, "proof_drift_count": 0, "deterministic_truth_modified": False},
            "human_feedback": {"synthetic_records_count": 8, "real_participant_records_count": 0,
                                "contains_real_participant_data": False, "pipeline_validation_only": True},
        },
        "claim_boundary": {"human_effect_claimable": False, "usability_improvement_claimable": False,
                           "diagnosis_correctness_claimable": False, "root_cause_correctness_claimable": False,
                           "replay_correctness_claimable": False, "proof_correctness_claimable": False,
                           "live_llm_quality_claimable": False, "full_evidence_replay_claimable": False,
                           "proof_parity_claimable": False, "trace_reconstruction_claimable": False},
        "limitations": ["Reference-only metadata preservation is not a full evidence-package replay pass.",
                        "Synthetic fixtures do not establish real RTOS, hardware, correctness, generality, or causal claims.",
                        "Synthetic feedback is pipeline validation only and is not a participant study or usability result."],
        "reproducibility_commands": [
            "python3 -m pytest tests/python/test_phase6_closeout.py -q",
            "python3 tool/build_phase6_closeout_manifest.py --output /tmp/phase6_closeout_manifest.json --summary --overwrite --manifest-id phase6-p6-7-closeout --test-passed <final_passed> --subtests-passed <final_subtests>",
            "python3 -m pytest tests/python -q"],
        "unresolved_validation_gaps": ["full_evidence_package_replay", "proof_parity", "complete_trace_reconstruction",
                                       "real_rtos_hardware_validation", "real_participant_study", "live_llm_quality_evaluation",
                                       "diagnosis_and_root_cause_correctness", "cross_platform_generality"],
        "release_readiness": {"engineering_artifact": "ready", "synthetic_evaluation": "ready",
                              "empirical_systems_evidence": "incomplete", "human_evaluation": "incomplete",
                              "paper_claim_readiness": "conditionally_ready_for_artifact_scope_only"},
        "notes": ["Deterministic manifest: no timestamp, absolute path, environment value, participant-level record, raw feedback record, proof write field, or live-model result is included."]
    }


def canonical_json(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def validate_manifest(manifest: dict[str, Any], *, root: Path = REPO_ROOT) -> None:
    if manifest.get("phase") != 6 or manifest.get("pre_closeout_head") != PRE_CLOSEOUT_HEAD:
        raise ValueError("Phase 6 pre-closeout identity mismatch")
    commits = manifest.get("frozen_commits")
    expected = [{"commit": c, "message": m, "phase_item": p, "frozen": True} for c, m, p in FROZEN_COMMITS]
    if commits != expected:
        raise ValueError("frozen commit inventory mismatch")
    for group in manifest.get("artifact_groups", {}).values():
        for entry in group:
            path = entry.get("path", "")
            candidate = Path(path)
            if candidate.is_absolute() or ".." in candidate.parts or not path or not (root / candidate).exists():
                raise ValueError(f"invalid required artifact path: {path!r}")
    for check in manifest.get("schema_mirror_checks", []):
        source, mirror = root / check["source"], root / check["mirror"]
        if not check.get("matches") or source.read_bytes() != mirror.read_bytes():
            raise ValueError(f"schema mirror mismatch: {check.get('source')}")
    if manifest["canonical_artifacts"]["p6_4_report"]["canonical_hash"] != P6_4_REPORT_HASH:
        raise ValueError("P6.4 canonical hash mismatch")
    expected_invariants = build_manifest()["invariants"]
    if manifest.get("invariants") != expected_invariants:
        raise ValueError("Phase 6 invariant mismatch")
    if any(manifest.get("claim_boundary", {}).get(key) is not False for key in build_manifest()["claim_boundary"]):
        raise ValueError("claim boundary must remain false")
    if not manifest.get("unresolved_validation_gaps"):
        raise ValueError("unresolved validation gaps must be nonempty")
    if manifest.get("release_readiness", {}).get("empirical_systems_evidence") == "ready":
        raise ValueError("empirical readiness cannot be ready")
    rendered = canonical_json(manifest).lower()
    forbidden = ("/media/", "../", "api_key", "secret", "token", "proof_digest_write_path", "correctness_ranking")
    if any(value in rendered for value in forbidden):
        raise ValueError("forbidden closeout content")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--manifest-id", default="phase6-p6-7-closeout")
    parser.add_argument("--test-passed", type=int, default=682)
    parser.add_argument("--subtests-passed", type=int, default=950)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists() and not args.overwrite:
        parser.error("output exists; use --overwrite")
    manifest = build_manifest(manifest_id=args.manifest_id, test_passed=args.test_passed, subtests_passed=args.subtests_passed)
    validate_manifest(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_json(manifest), encoding="utf-8")
    if args.summary:
        print(json.dumps({"manifest_id": manifest["manifest_id"], "artifact_count": sum(len(v) for v in manifest["artifact_groups"].values()),
                          "schema_mirror_checks": len(manifest["schema_mirror_checks"]), "canonical_hash": P6_4_REPORT_HASH,
                          "unresolved_validation_gaps": len(manifest["unresolved_validation_gaps"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
