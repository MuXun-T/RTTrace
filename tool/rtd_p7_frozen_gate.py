#!/usr/bin/env python3
"""Fail-closed, append-only P7 gate over the sealed P6/P5 inputs."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
P6_COMMIT = "f3f0bcd89261f5eb418cd7fc50b633d3f54d181d"
P6_TREE = "4ab59342d64777b5b2f4ac514d910320197be7ff"
P6_FREEZE = Path("docs/rtd_pilot/p6_freeze_20260914T194500Z")
P6_AUDIT = Path("docs/rtd_pilot/p6_post_freeze_audit_20260914T182500Z")
EXPECTED = {
    "docs/rtd_pilot/p6_freeze_20260914T194500Z/p6_regression_and_freeze_record.json": "27be214484854277ee996e7b10e6dc0deec7339a17a761883c0082822d6947b0",
    "docs/rtd_pilot/p6_freeze_20260914T194500Z/p6_duplicate_run_disposition.json": "3be44b81ced52d23939e51aeb020676139934fc8f8cd3d78d1d7f0b1f5994c83",
    "docs/rtd_pilot/p6_post_freeze_audit_20260914T182500Z/p6_execution_record.json": "932191eae77ed86252ca11ba0ae5d19fe813a3270e9c6b9534545310a76aa800",
    "docs/rtd_pilot/p6_post_freeze_audit_20260914T182500Z/p6_frozen_input_inventory.json": "3715fed34c26f760940c104a50e7ce7bd95b14483ee434f7900bcd4cf1dbd526",
    "docs/rtd_pilot/p6_post_freeze_audit_20260914T182500Z/p6_integrity_audit.json": "e83671174064e0b3d17db65deffad9705bbd4c28c954bdbf45360db2f9cb5a7b",
    "docs/rtd_pilot/p6_post_freeze_audit_20260914T182500Z/p6_sha256_manifest.json": "28228f82171b8d353e4811e95321f11a6749cea2061e7a8b82c99f6efc3966de",
}

def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()

def exclusive(path: Path, value: Any) -> None:
    payload = (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, payload); os.fsync(fd)
    finally:
        os.close(fd)

def source_ref(rel: str, role: str) -> dict[str, str]:
    path = ROOT / rel
    if not path.is_file():
        raise ValueError(f"missing source: {rel}")
    actual = digest(path)
    if EXPECTED.get(rel) and actual != EXPECTED[rel]:
        raise ValueError(f"stale P6 hash: {rel}")
    return {"path": rel, "sha256": actual, "role": role}

def record_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name

def validate_inputs() -> list[dict[str, str]]:
    refs = [source_ref(path, "canonical P6 frozen input") for path in EXPECTED]
    integrity = json.loads((ROOT / "docs/rtd_pilot/p6_post_freeze_audit_20260914T182500Z/p6_integrity_audit.json").read_text())
    if integrity.get("status") != "PASS" or integrity.get("gaps") != []:
        raise ValueError("P6 integrity audit is not PASS")
    if integrity.get("checks", {}).get("hash") is not True:
        raise ValueError("P6 hash check is not PASS")
    # Reuse the frozen P5 validator; it is read-only and cannot promote truth.
    spec = importlib.util.spec_from_file_location("p6audit", ROOT / "tool/rtd_p6_frozen_input_audit.py")
    module = importlib.util.module_from_spec(spec); assert spec.loader
    spec.loader.exec_module(module)
    module.validate_inputs(ROOT / module.P5_REL)
    return refs

def build(output: Path) -> list[Path]:
    if output.exists():
        raise ValueError("output directory already exists")
    refs = validate_inputs()
    output.mkdir(parents=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    base = {"phase": "P7", "schema_version": "rtd-p7-v1", "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "append_only": True, "hardware_action": False, "identity": {"gate_id": "rtd-pilot-p7-frozen-gate", "p6_commit": P6_COMMIT, "p6_tree": P6_TREE}, "truth_boundary": "Observer truth remains P5 independent Observer/OAR/CVR only; synthetic/report-only and prohibited tooling are non-truth.", "episode": None, "episode_status": "not_applicable_no_capture"}
    blocked_id = f"record:p7-blocked-{stamp}"
    blocked = {"schema": "rtd-p7-blocked-record-v1", "record_type": "P7_BLOCKED", "record_id": blocked_id, **base, "status": "BLOCKED", "source_refs": refs, "lineage": {"parent_record_id": "record:p6-execution-20260914T181500Z", "episode": None}, "evaluation_split": "not_applicable_no_eligible_cases", "blockers": ["independent Observer truth hierarchy absent from P6 freeze", "Phase 5 Case hierarchy and eligible diagnostic outputs absent"], "conclusion": "P6 is intact and synthetic/report-only; P7 baseline/evaluation preconditions are absent."}
    paths = [output / "p7_blocked_record.json"]
    exclusive(paths[0], blocked)
    exec_id = f"record:p7-execution-{stamp}"
    execution = {"schema": "rtd-p7-execution-record-v1", "record_type": "P7_EXECUTION_RECORD", "record_id": exec_id, **base, "source_refs": refs + [{"path": record_path(paths[0]), "sha256": digest(paths[0]), "role": "P7 blocked record"}], "lineage": {"parent_record_id": blocked_id}, "blocked_record_id": blocked_id, "blocked_record_sha256": digest(paths[0]), "status": "BLOCKED"}
    paths.append(output / "p7_execution_record.json"); exclusive(paths[-1], execution)
    reg_id = f"record:p7-regression-{stamp}"
    regression = {"schema": "rtd-p7-regression-record-v1", "record_type": "P7_REGRESSION_RECORD", "record_id": reg_id, **base, "source_refs": [{"path": record_path(p), "sha256": digest(p), "role": "P7 record"} for p in paths], "lineage": {"parent_record_id": exec_id, "prior_records": [blocked_id, exec_id]}, "checks": {"schema": True, "hash": True, "duplicate": True, "cross_case": True, "stale_binding": True, "split": True, "lineage_episode": True, "no_overwrite": True}, "status": "BLOCKED"}
    paths.append(output / "p7_regression_record.json"); exclusive(paths[-1], regression)
    manifest_path = output / "p7_sha256_manifest.json"
    closeout = output.parent / f"p7_closeout_{stamp}.md"
    fd = os.open(closeout, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, (f"# RTD-PILOT P7 closeout\n\nStatus: **BLOCKED**\n\nManifest: `{manifest_path.name}`\n\nNo hardware action was performed.\n").encode()); os.fsync(fd)
    finally: os.close(fd)
    manifest_id = f"record:p7-manifest-{stamp}"
    all_files = paths + [closeout]
    manifest = {"schema": "rtd-p7-sha256-manifest-v1", "record_type": "P7_SHA256_MANIFEST", "record_id": manifest_id, **base, "source_refs": [{"path": p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.name, "sha256": digest(p), "role": "P7 record"} for p in all_files], "records": [{"path": p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.name, "sha256": digest(p)} for p in all_files], "lineage": {"parent_record_id": reg_id, "prior_records": [blocked_id, exec_id, reg_id]}, "status": "SEALED_APPEND_ONLY"}
    exclusive(manifest_path, manifest)
    return paths + [manifest_path]

def disposition(output: Path, invalid_manifest: Path, invalid_closeout: Path,
                canonical_manifest: Path, duplicate_manifest: Path) -> list[Path]:
    if output.exists():
        raise ValueError("output directory already exists")
    refs = []
    for path, role in ((invalid_manifest, "invalid non-manifested run"),
                       (invalid_closeout, "invalid absolute-path closeout"),
                       (canonical_manifest, "canonical blocked run"),
                       (duplicate_manifest, "duplicate blocked run")):
        if not path.is_file():
            raise ValueError(f"missing disposition source: {path}")
        refs.append({"path": path.relative_to(ROOT).as_posix(), "sha256": digest(path), "role": role})
    output.mkdir(parents=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    record = {"schema": "rtd-p7-disposition-record-v1", "record_type": "P7_CORRECTION_AND_SUPERSEDES",
              "record_id": f"record:p7-disposition-{stamp}", "phase": "P7", "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
              "append_only": True, "hardware_action": False, "status": "RETAINED_HISTORY",
              "identity": {"gate_id": "rtd-pilot-p7-frozen-gate", "p6_commit": P6_COMMIT, "p6_tree": P6_TREE},
              "episode": None, "episode_status": "not_applicable_no_capture", "evaluation_split": "not_applicable_no_eligible_cases",
              "truth_boundary": "No disposition record is Observer truth; synthetic/report-only and prohibited tooling remain non-truth.",
              "source_refs": refs, "lineage": {"parent_record_id": "record:p6-execution-20260914T181500Z", "episode": None},
              "correction_of": "record:p7-manifest-20260914T124818851913Z",
              "supersedes": "record:p7-manifest-20260914T125345798955Z",
              "canonical_record": "record:p7-manifest-20260914T125324583725Z",
              "dispositions": [{"record_id": "record:p7-manifest-20260914T124818851913Z", "status": "INVALID_NON_MANIFESTED", "reason": "closeout used an absolute path and was outside the manifest"}, {"record_id": "record:p7-manifest-20260914T125345798955Z", "status": "SUPERSEDED_NON_MANIFESTED", "reason": "duplicate blocked run; earlier compliant run is canonical"}]}
    record_path = output / "p7_correction_record.json"; exclusive(record_path, record)
    manifest = {"schema": "rtd-p7-sha256-manifest-v1", "record_type": "P7_SHA256_MANIFEST", "record_id": f"record:p7-disposition-manifest-{stamp}", "phase": "P7", "append_only": True, "hardware_action": False, "status": "SEALED_APPEND_ONLY", "identity": record["identity"], "episode": None, "episode_status": "not_applicable_no_capture", "source_refs": [{"path": record_path.name, "sha256": digest(record_path), "role": "P7 disposition record"}], "records": [{"path": record_path.name, "sha256": digest(record_path)}], "lineage": {"parent_record_id": record["record_id"]}}
    manifest_path = output / "p7_sha256_manifest.json"; exclusive(manifest_path, manifest)
    return [record_path, manifest_path]

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--disposition", nargs=4, type=Path, metavar=("INVALID_MANIFEST", "INVALID_CLOSEOUT", "CANONICAL_MANIFEST", "DUPLICATE_MANIFEST"))
    try:
        args = parser.parse_args()
        files = disposition(args.output.resolve(), *(path.resolve() for path in args.disposition)) if args.disposition else build(args.output.resolve())
        print(json.dumps({"status": "BLOCKED", "files": [str(p) for p in files]}, sort_keys=True)); return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.error(str(error)); return 2

if __name__ == "__main__": raise SystemExit(main())
