#!/usr/bin/env python3
"""Fail closed on H3 alignment-session artifact and hash-chain mismatches."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from phase1_common import sha256, write_new_json


SHA256 = re.compile(r"^[0-9a-f]{64}$")
RAW_REQUIRED = {
    "dsview_dsl", "dsview_csv", "dsview_screenshot", "uart_raw",
    "uart_reader_ready", "gate_ready", "gate_verified", "gate_receipt",
    "v2_contract_binding",
}
DERIVED_REQUIRED = {
    "observer_normalized", "observer_summary", "observer_epochs",
    "clock_analysis", "clock_analysis_main_recompute",
}


def load_object(path: Path, errors: list[str], label: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} cannot be read: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{label} must be a JSON object")
        return None
    return value


def local_path(base: Path, value: object, errors: list[str], label: str) -> Path | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{label}.file is absent")
        return None
    candidate = (base / value).resolve()
    if base.resolve() not in candidate.parents or not candidate.is_file():
        errors.append(f"{label}.file does not resolve inside the session: {value}")
        return None
    return candidate


def artifact_path(entry: object, base: Path, errors: list[str], label: str) -> tuple[Path | None, str | None]:
    if not isinstance(entry, dict):
        errors.append(f"{label} must be an artifact object")
        return None, None
    expected = entry.get("sha256")
    if not isinstance(expected, str) or not SHA256.fullmatch(expected):
        errors.append(f"{label}.sha256 must be exactly 64 lowercase hex characters")
        return None, None
    if "file" in entry:
        path = local_path(base, entry.get("file"), errors, label)
    elif "external_path" in entry:
        external = entry.get("external_path")
        path = Path(external) if isinstance(external, str) and external else None
        if path is None or not path.is_file():
            errors.append(f"{label}.external_path does not resolve: {external}")
            return None, None
    else:
        errors.append(f"{label} needs file or external_path")
        return None, None
    if path is not None:
        actual = sha256(path)
        if actual != expected:
            errors.append(f"{label}.sha256 does not match {path}")
        expected_bytes = entry.get("bytes")
        if expected_bytes is not None and (not isinstance(expected_bytes, int) or expected_bytes != path.stat().st_size):
            errors.append(f"{label}.bytes does not match {path}")
    return path, expected


def value_at(value: dict[str, Any] | None, path: tuple[str, ...]) -> object:
    current: object = value
    for field in path:
        if not isinstance(current, dict):
            return None
        current = current.get(field)
    return current


def equal(errors: list[str], label: str, expected: object, actual: object) -> None:
    if expected != actual:
        errors.append(f"{label} does not match")


def verify(manifest_path: Path, root: Path) -> dict[str, object]:
    errors: list[str] = []
    manifest = load_object(manifest_path, errors, "session manifest")
    if manifest is None:
        return {"valid": False, "errors": errors}
    if manifest.get("schema_version") != "phase1-h3-alignment-session-v2":
        errors.append("session manifest schema_version is invalid")

    base = manifest_path.parent.resolve()
    raw = manifest.get("raw_artifacts")
    derived = manifest.get("derived_artifacts")
    if not isinstance(raw, dict):
        raw = {}
        errors.append("raw_artifacts is absent")
    if not isinstance(derived, dict):
        derived = {}
        errors.append("derived_artifacts is absent")
    missing_raw = RAW_REQUIRED - set(raw)
    missing_derived = DERIVED_REQUIRED - set(derived)
    if missing_raw:
        errors.append(f"raw_artifacts is missing: {','.join(sorted(missing_raw))}")
    if missing_derived:
        errors.append(f"derived_artifacts is missing: {','.join(sorted(missing_derived))}")

    artifacts: dict[str, tuple[Path | None, str | None]] = {}
    for label, entry in raw.items():
        artifacts[label] = artifact_path(entry, base, errors, f"raw_artifacts.{label}")
    for label, entry in derived.items():
        if isinstance(entry, dict) and "sha256" in entry:
            artifacts[label] = artifact_path(entry, base, errors, f"derived_artifacts.{label}")

    def data(label: str) -> dict[str, Any] | None:
        path = artifacts.get(label, (None, None))[0]
        return load_object(path, errors, label) if path is not None else None

    uart_hash = artifacts.get("uart_raw", (None, None))[1]
    gate = data("gate_receipt")
    analysis = data("clock_analysis")
    recompute = data("clock_analysis_main_recompute")
    summary = data("observer_summary")
    observer_hash = artifacts.get("observer_normalized", (None, None))[1]
    epochs_hash = artifacts.get("observer_epochs", (None, None))[1]
    dsl_hash = artifacts.get("dsview_dsl", (None, None))[1]
    csv_hash = artifacts.get("dsview_csv", (None, None))[1]
    equal(errors, "gate_receipt.uart_sha256", uart_hash, value_at(gate, ("uart_sha256",)))
    equal(errors, "clock_analysis.input_hashes.uart_log_sha256", uart_hash, value_at(analysis, ("input_hashes", "uart_log_sha256")))
    equal(errors, "clock_analysis_main_recompute.input_hashes.uart_log_sha256", uart_hash, value_at(recompute, ("input_hashes", "uart_log_sha256")))
    equal(errors, "clock_analysis.input_hashes.calibration_csv_sha256", observer_hash, value_at(analysis, ("input_hashes", "calibration_csv_sha256")))
    equal(errors, "clock_analysis.input_hashes.observer_epochs_sha256", epochs_hash, value_at(analysis, ("input_hashes", "observer_epochs_sha256")))
    equal(errors, "observer_summary.observer_file_sha256", dsl_hash, value_at(summary, ("observer_file_sha256",)))
    equal(errors, "observer_summary.normalized_observer_file_sha256", observer_hash, value_at(summary, ("normalized_observer_file_sha256",)))
    equal(errors, "observer_summary.source_csv_sha256", csv_hash, value_at(summary, ("source_csv_sha256",)))

    binding_path, binding_hash = artifacts.get("v2_contract_binding", (None, None))
    binding = load_object(binding_path, errors, "v2_contract_binding") if binding_path is not None else None
    manifest_binding = manifest.get("v2_contract_binding")
    if not isinstance(manifest_binding, dict):
        errors.append("v2_contract_binding is absent from the manifest")
        manifest_binding = {}
    if binding is not None:
        binding_fields = {
            "decision_id": "v2_decision_id",
            "effective_not_before_utc": "v2_effective_not_before_utc",
            "v2_contract_path": "v2_contract_path",
            "v2_contract_sha256": "v2_contract_sha256",
        }
        for manifest_field, binding_field in binding_fields.items():
            equal(errors, f"manifest v2_contract_binding.{manifest_field}", binding.get(binding_field), manifest_binding.get(manifest_field))
        equal(errors, "manifest independence.binding_sha256", binding_hash, value_at(manifest, ("independence", "binding_sha256")))
        contract_path = binding.get("v2_contract_path")
        if isinstance(contract_path, str) and contract_path:
            candidate = (root / contract_path).resolve()
            if root.resolve() not in candidate.parents or not candidate.is_file():
                errors.append("v2 contract path does not resolve inside the repository")
            else:
                equal(errors, "v2 contract SHA-256", binding.get("v2_contract_sha256"), sha256(candidate))
        else:
            errors.append("v2 contract path is absent")

    independence = manifest.get("independence")
    if not isinstance(independence, dict):
        errors.append("independence is absent")
    else:
        reset_path = local_path(base, independence.get("reset_receipt"), errors, "independence.reset_receipt")
        expected_reset_hash = independence.get("reset_receipt_sha256")
        if not isinstance(expected_reset_hash, str) or not SHA256.fullmatch(expected_reset_hash):
            errors.append("independence.reset_receipt_sha256 must be exactly 64 lowercase hex characters")
        elif reset_path is not None and sha256(reset_path) != expected_reset_hash:
            errors.append("independence.reset_receipt_sha256 does not match")

    return {"valid": not errors, "errors": errors}


def audit_receipt(manifest_path: Path, root: Path, validated_at_utc: str) -> dict[str, object]:
    errors: list[str] = []
    manifest = load_object(manifest_path, errors, "session manifest")
    result = verify(manifest_path, root)
    return {
        "schema_version": "phase1-h3-integrity-audit-receipt-v1",
        "h3_alignment_session_id": manifest.get("h3_alignment_session_id") if manifest else None,
        "validated_at_utc": validated_at_utc,
        "session_manifest": {"file": manifest_path.name, "sha256": sha256(manifest_path)},
        "verification_control": {
            "repo_path": "hardware/rtd_pilot/scripts/verify_h3_alignment_session.py",
            "sha256": sha256(Path(__file__).resolve()),
        },
        "result": result,
    }


def verify_audit_receipt(receipt_path: Path, root: Path) -> dict[str, object]:
    errors: list[str] = []
    receipt = load_object(receipt_path, errors, "integrity audit receipt")
    if receipt is None:
        return {"valid": False, "errors": errors}
    if receipt.get("schema_version") != "phase1-h3-integrity-audit-receipt-v1":
        errors.append("integrity audit receipt schema_version is invalid")
    manifest_ref = receipt.get("session_manifest")
    if not isinstance(manifest_ref, dict):
        errors.append("integrity audit receipt session_manifest is absent")
        manifest_ref = {}
    manifest_path = local_path(receipt_path.parent.resolve(), manifest_ref.get("file"), errors, "integrity audit receipt session_manifest")
    expected_manifest_hash = manifest_ref.get("sha256")
    if not isinstance(expected_manifest_hash, str) or not SHA256.fullmatch(expected_manifest_hash):
        errors.append("integrity audit receipt session_manifest.sha256 is invalid")
    elif manifest_path is not None and sha256(manifest_path) != expected_manifest_hash:
        errors.append("integrity audit receipt session_manifest.sha256 does not match")
    control = receipt.get("verification_control")
    if not isinstance(control, dict):
        errors.append("integrity audit receipt verification_control is absent")
        control = {}
    control_path = control.get("repo_path")
    expected_control_hash = control.get("sha256")
    if not isinstance(control_path, str) or not control_path:
        errors.append("integrity audit receipt verification_control.repo_path is invalid")
    else:
        candidate = (root / control_path).resolve()
        if root.resolve() not in candidate.parents or not candidate.is_file():
            errors.append("integrity audit receipt verification_control path is invalid")
        elif not isinstance(expected_control_hash, str) or not SHA256.fullmatch(expected_control_hash):
            errors.append("integrity audit receipt verification_control.sha256 is invalid")
        elif sha256(candidate) != expected_control_hash:
            errors.append("integrity audit receipt verification_control.sha256 does not match")
    recorded_result = receipt.get("result")
    current_result = verify(manifest_path, root) if manifest_path is not None else {"valid": False, "errors": ["session manifest unavailable"]}
    if recorded_result != current_result:
        errors.append("integrity audit receipt result does not match current verification")
    return {"valid": not errors, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, nargs="?")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--write-audit-receipt", type=Path)
    parser.add_argument("--validated-at-utc")
    parser.add_argument("--verify-audit-receipt", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.verify_audit_receipt:
        if args.manifest or args.write_audit_receipt or args.validated_at_utc:
            parser.error("--verify-audit-receipt cannot be combined with manifest or write options")
        result = verify_audit_receipt(args.verify_audit_receipt, root)
    else:
        if args.manifest is None:
            parser.error("manifest is required unless --verify-audit-receipt is used")
        result = verify(args.manifest, root)
        if args.write_audit_receipt:
            if not args.validated_at_utc:
                parser.error("--validated-at-utc is required with --write-audit-receipt")
            write_new_json(args.write_audit_receipt, audit_receipt(args.manifest, root, args.validated_at_utc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
