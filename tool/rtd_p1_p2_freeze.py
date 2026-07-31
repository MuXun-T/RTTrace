#!/usr/bin/env python3
"""Issue or verify the owner-authorized Phase 1/2 freeze receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEGACY_RECEIPT = ROOT / "docs/rtd_pilot/contracts/p1_p2_final_freeze_receipt_20260731.json"
RECEIPT = ROOT / "docs/rtd_pilot/contracts/p1_p2_final_freeze_receipt_20260731_v2.json"
H3_AUDIT = ROOT / "docs/rtd_pilot/feasibility/phase1_h3_alignment_final_audit_v3_20260731.json"
H3_SCORECARD = ROOT / "docs/rtd_pilot/feasibility/phase1_h3_scorecard_v3.json"
H3_SESSION_RECEIPTS = (
    "hardware/rtd_pilot/h3_alignment_sessions/h3-alignment-20260731T110847Z-session01-v2/integrity_audit_receipt.json",
    "hardware/rtd_pilot/h3_alignment_sessions/h3-alignment-20260731T112454Z-session02-v2/integrity_audit_receipt.json",
    "hardware/rtd_pilot/h3_alignment_sessions/h3-alignment-20260731T120337Z-session03-recapture01-v2/integrity_audit_receipt.json",
)
PHASE2_SCHEMA_NAMES = (
    "rtd_capture_capability_manifest.schema.json",
    "rtd_capture_integrity_record.schema.json",
    "rtd_capture_validity_record.schema.json",
    "rtd_case_definition.schema.json",
    "rtd_collector_config_snapshot.schema.json",
    "rtd_injection_ledger.schema.json",
    "rtd_ledger_session_seal.schema.json",
    "rtd_observer_record.schema.json",
    "rtd_raw_artifact_inventory.schema.json",
)
BINDINGS = (
    ("Phase 1 hardware evidence tree", "hardware/rtd_pilot", "tree"),
    ("Phase 1 feasibility evidence tree", "docs/rtd_pilot/feasibility", "tree"),
    ("Phase 1 H3 score verifier", "tool/rtd_phase1_verify_h3_corrective.py", "file"),
    ("Phase 1 freeze verifier", "tool/rtd_phase1_verify_freeze.py", "file"),
    ("Phase 1 H3 verifier test", "tests/python/test_rtd_phase1_verify_h3_corrective.py", "file"),
    ("Phase 2 owner authorization", "docs/rtd_pilot/contracts/phase2_owner_authorization.md", "file"),
    ("Phase 2 contract reference", "docs/rtd_pilot/contracts/phase2_contract_reference.md", "file"),
    ("Phase 2 contract examples", "docs/rtd_pilot/contracts/phase2_contract_examples.md", "file"),
    ("Phase 2 migration retention", "docs/rtd_pilot/contracts/phase2_migration_retention.md", "file"),
    ("Phase 2 freeze inventory", "docs/rtd_pilot/contracts/phase2_freeze_inventory.md", "file"),
    ("Phase 2 independent review", "docs/rtd_pilot/contracts/phase2_final_independent_review.md", "file"),
    ("Phase 2 regression baseline", "docs/rtd_pilot/contracts/phase2_regression_baseline_disposition.json", "file"),
    ("Phase 2 regression disposition", "docs/rtd_pilot/contracts/phase2_regression_baseline_disposition.md", "file"),
    ("Phase 2 contracts", "spec/rtd_pilot_contracts.py", "file"),
    ("Phase 2 schema loader", "spec/schema_loader.py", "file"),
    ("Phase 2 schema validator", "spec/schema_validator.py", "file"),
    ("Phase 2 contract tests", "tests/python/test_rtd_pilot_contracts.py", "file"),
    ("Phase 2 contract CLI tests", "tests/python/test_rtd_pilot_contract_cli.py", "file"),
    ("Phase 2 external binding tests", "tests/python/test_external_case_evidence_binding_models.py", "file"),
    ("Phase 2 config adapter", "tool/rtd_adapt_collector_config.py", "file"),
    ("Phase 2 ledger writer", "tool/rtd_append_ledger.py", "file"),
    ("Phase 2 ledger seal writer", "tool/rtd_seal_ledger.py", "file"),
    ("Phase 2 capture validator", "tool/rtd_validate_capture_bundle.py", "file"),
    ("Phase 2 ledger validator", "tool/rtd_validate_ledger.py", "file"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def repository_path(value: str) -> Path:
    candidate = (ROOT / value).resolve()
    if ROOT not in candidate.parents and candidate != ROOT:
        raise ValueError(f"unsafe repository path: {value}")
    if not candidate.exists():
        raise ValueError(f"missing required path: {value}")
    return candidate


def digest_tree(path: Path) -> str:
    if path.is_file():
        return sha256(path)
    digest = hashlib.sha256()
    files = sorted(
        item for item in path.rglob("*")
        if item.is_file()
        and item.suffix != ".pyc"
        and not any(part in {"__pycache__", ".pytest_cache"} for part in item.relative_to(path).parts)
    )
    if not files:
        raise ValueError(f"empty evidence tree: {path.relative_to(ROOT)}")
    for item in files:
        if item.is_symlink():
            raise ValueError(f"symlink is not permitted in evidence tree: {item}")
        relative = item.relative_to(ROOT).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(sha256(item).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def run(command: list[str], expected_returncode: int = 0, expected_text: str | None = None) -> str:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    text = result.stdout + result.stderr
    if result.returncode != expected_returncode:
        raise ValueError(f"command failed ({result.returncode}): {' '.join(command)}\n{text}")
    if expected_text is not None and expected_text not in text:
        raise ValueError(f"command missing expected result {expected_text!r}: {' '.join(command)}\n{text}")
    return text


def validate_live() -> list[str]:
    audit = load_json(H3_AUDIT)
    scorecard = load_json(H3_SCORECARD)
    if audit.get("alignment_status") != "PASS_V2_PROSPECTIVE":
        raise ValueError("Phase 1 H3 audit is not passing")
    if audit.get("available_aligned_h3_sessions") != 3 or audit.get("required_independent_h3_sessions") != 3:
        raise ValueError("Phase 1 H3 audit does not retain three independent sessions")
    if audit.get("gate_decision", {}).get("timer_alignment_score") != 2:
        raise ValueError("Phase 1 H3 timer score is not 2")
    if scorecard.get("status") != "PHASE1_CORRECTIVE_READY_TO_FREEZE":
        raise ValueError("Phase 1 v3 scorecard is not ready to freeze")

    checks = ["Phase 1 H3 v3 status and score"]
    run([sys.executable, "tool/rtd_phase1_verify_h3_corrective.py", "--scorecard", str(H3_SCORECARD.relative_to(ROOT))])
    checks.append("Phase 1 H3 scorecard verifier")
    for receipt in H3_SESSION_RECEIPTS:
        run([sys.executable, "hardware/rtd_pilot/scripts/verify_h3_alignment_session.py", "--root", ".", "--verify-audit-receipt", receipt])
    checks.append("three H3 session integrity receipts")

    for name in PHASE2_SCHEMA_NAMES:
        source = repository_path(f"spec/schema/{name}")
        mirror = repository_path(f"spec/assets/schema/{name}")
        if source.read_bytes() != mirror.read_bytes():
            raise ValueError(f"Phase 2 schema mirror mismatch: {name}")
    checks.append("nine Phase 2 schema mirrors")

    focused = [
        "tests/python/test_rtd_phase1_verify_h3_corrective.py",
        "hardware/rtd_pilot/tests/test_tools.py",
        "tests/python/test_rtd_pilot_contracts.py",
        "tests/python/test_rtd_pilot_contract_cli.py",
        "tests/python/test_external_case_evidence_binding_models.py",
    ]
    run([sys.executable, "-m", "pytest", "-q", *focused])
    checks.append("Phase 1 and Phase 2 focused tests")
    run([sys.executable, "-m", "pytest", "-q", "tests/python/test_rtd_pilot_contracts.py", "-k", "truth"])
    checks.append("Phase 2 truth-boundary tests")
    run(
        [sys.executable, "-m", "pytest", "-q", "tests/python/test_online_channel.py::OnlineChannelTests::test_serial_channel_matches_offline_dataset"],
        expected_returncode=1,
        expected_text="invalid trace magic",
    )
    checks.append("recorded inherited serial regression sentinel")
    return checks


def receipt_payload() -> dict[str, Any]:
    bindings = []
    for role, relative, kind in BINDINGS:
        path = repository_path(relative)
        if kind not in {"file", "tree"}:
            raise ValueError(f"invalid binding kind: {kind}")
        if kind == "file" and not path.is_file():
            raise ValueError(f"expected file: {relative}")
        if kind == "tree" and not path.is_dir():
            raise ValueError(f"expected tree: {relative}")
        bindings.append({"role": role, "path": relative, "kind": kind, "sha256": digest_tree(path)})
    return {
        "schema_version": "rtd-p1-p2-final-freeze-receipt-v2",
        "status": "FROZEN",
        "authority": "workspace operator explicit instruction: freeze and submit P1 and P2",
        "phase1": {
            "status": "FROZEN",
            "h3_timer_alignment_score": 2,
            "supersedes_ready_to_freeze_record": str(H3_AUDIT.relative_to(ROOT)),
            "audit": str(H3_AUDIT.relative_to(ROOT)),
            "audit_sha256": sha256(H3_AUDIT),
            "scorecard": str(H3_SCORECARD.relative_to(ROOT)),
            "scorecard_sha256": sha256(H3_SCORECARD),
        },
        "phase2": {
            "status": "FROZEN",
            "supersedes_preparatory_inventory": "docs/rtd_pilot/contracts/phase2_freeze_inventory.md",
            "scope": "offline contracts, validators, schemas, tools, and example-only records only",
            "formal_case": "NOT_CREATED",
        },
        "phase3": {"status": "NOT_AUTHORIZED"},
        "control": {"path": str(Path(__file__).relative_to(ROOT)), "sha256": sha256(Path(__file__).resolve())},
        "superseded_preliminary_receipt": {
            "path": str(LEGACY_RECEIPT.relative_to(ROOT)),
            "sha256": sha256(LEGACY_RECEIPT),
            "reason": "v1 bound generated Python cache files and is retained but excluded from the final freeze control",
        },
        "bindings": bindings,
    }


def verify_receipt(path: Path) -> dict[str, object]:
    errors: list[str] = []
    try:
        receipt = load_json(path)
        if receipt.get("schema_version") != "rtd-p1-p2-final-freeze-receipt-v2":
            errors.append("receipt schema_version is invalid")
        if receipt.get("status") != "FROZEN":
            errors.append("receipt status is not FROZEN")
        if receipt.get("phase1", {}).get("status") != "FROZEN" or receipt.get("phase1", {}).get("h3_timer_alignment_score") != 2:
            errors.append("Phase 1 freeze status or H3 score is invalid")
        if receipt.get("phase2", {}).get("status") != "FROZEN" or receipt.get("phase2", {}).get("formal_case") != "NOT_CREATED":
            errors.append("Phase 2 freeze status or Case boundary is invalid")
        if receipt.get("phase3", {}).get("status") != "NOT_AUTHORIZED":
            errors.append("Phase 3 boundary is invalid")
        control = receipt.get("control", {})
        if control.get("path") != str(Path(__file__).relative_to(ROOT)) or control.get("sha256") != sha256(Path(__file__).resolve()):
            errors.append("freeze control hash does not match")
        expected = receipt_payload()
        for key in ("phase1", "phase2", "phase3", "superseded_preliminary_receipt", "bindings"):
            if receipt.get(key) != expected.get(key):
                errors.append(f"receipt {key} does not match current frozen inputs")
        if not errors:
            validate_live()
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        errors.append(str(exc))
    return {"valid": not errors, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="issue the write-once receipt")
    parser.add_argument("--verify", action="store_true", help="verify the existing receipt")
    args = parser.parse_args()
    if args.write == args.verify:
        parser.error("use exactly one of --write or --verify")
    if args.write:
        checks = validate_live()
        if RECEIPT.exists():
            raise FileExistsError(f"refusing to overwrite existing receipt: {RECEIPT}")
        RECEIPT.write_text(json.dumps(receipt_payload(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result: dict[str, object] = {"valid": True, "checks": checks, "receipt": str(RECEIPT.relative_to(ROOT))}
    else:
        result = verify_receipt(RECEIPT)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
