#!/usr/bin/env python3
"""Create an append-only, report-only P6 audit over the frozen P5 package."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
P5_COMMIT = "a6758f1068b99d921cdc0c5e02df82605ab3345f"
P5_TREE = "4fc20e150562b5a5e9ae461b9c5c8b6be6b30f78"
P5_REL = Path("docs/rtd_pilot/p5_execution_20260828_tools_ready/p5_7_freeze_candidate_20260914T181000Z")
REQUIRED = ("full_p5_capture_evidence_manifest.json", "p5_7_final_audit.json", "identity_hash_cross_case_audit.json", "lineage_episode_inventory.json", "p5_freeze_record_20260914T181500Z.json", "p5_final_freeze_candidate_record.json")

def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()

def ref(path: Path, role: str) -> dict[str, str]:
    if not path.is_file(): raise ValueError(f"missing source: {path}")
    return {"path": str(path.resolve()), "sha256": digest(path), "role": role}

def exclusive(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, payload.encode())
        os.fsync(fd)
    finally: os.close(fd)

def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ValueError(f"{path} is not an object")
    return value

def validate_inputs(p5: Path) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, str]]:
    files = {name: p5 / name for name in REQUIRED}
    if any(not path.is_file() for path in files.values()): raise ValueError("incomplete P5 freeze directory")
    freeze = load(files["p5_freeze_record_20260914T181500Z.json"])
    if freeze.get("decision") != "P5_FROZEN" or freeze.get("append_only") is not True: raise ValueError("P5 freeze decision mismatch")
    expected = {k: v["sha256"] for k, v in freeze["freeze_inputs"].items() if isinstance(v, dict) and "sha256" in v}
    for key, value in expected.items():
        path = p5 / Path(freeze["freeze_inputs"][key]["path"]).name
        if digest(path) != value: raise ValueError(f"stale P5 hash: {key}")
    for key in ("registry_sha256", "f1_profile_sha256", "f2_threshold_sha256", "f3_threshold_freeze_sha256", "wrong_entity_profile_sha256"):
        value = freeze["freeze_inputs"].get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value): raise ValueError(f"invalid frozen hash: {key}")
    manifest = load(files["full_p5_capture_evidence_manifest.json"])
    identity = load(files["identity_hash_cross_case_audit.json"])
    lineage = load(files["lineage_episode_inventory.json"])
    audit = load(files["p5_7_final_audit.json"])
    if audit.get("checks") != {"cross_case": True, "duplicate": True, "hash": True, "no_overwrite": True, "schema": True, "split": True, "stale_binding": True}: raise ValueError("P5 audit checks mismatch")
    if audit.get("gaps") != [] or manifest.get("gaps") != []: raise ValueError("P5 gaps are non-empty")
    captures = manifest.get("captures", [])
    if len(captures) != 63: raise ValueError("unexpected P5 capture count")
    ids = [c.get("capture_id") for c in captures]
    if len(ids) != len(set(ids)): raise ValueError("duplicate capture_id")
    pairs = [(c.get("registry_case_id"), c.get("capture_id")) for c in captures]
    if len(pairs) != len(set(pairs)): raise ValueError("duplicate registry/capture binding")
    raw_cases: dict[str, str] = {}
    for capture in captures:
        raw = capture.get("raw_sha256")
        case = capture.get("case_id")
        if raw and raw in raw_cases and raw_cases[raw] != case: raise ValueError("cross-case raw hash rebinding")
        if raw: raw_cases[raw] = case
        chain = capture.get("chain", {})
        if not {"CCM", "CIR", "CVR", "CaptureEpisode", "Ledger", "Observer/OAR", "lineage", "raw_hash"}.issubset(chain): raise ValueError("incomplete capture chain")
        for kind in ("lineage", "CaptureEpisode"):
            if not chain[kind].get("record_id") or not chain[kind].get("source_refs"): raise ValueError(f"incomplete {kind}")
        for source in capture.get("source_refs", []):
            source_path = Path(source["path"])
            if not source_path.is_file() or digest(source_path) != source.get("sha256"): raise ValueError("stale capture source_ref")
    return freeze, [ref(path, "P5 frozen input") for path in files.values()], {"captures": str(len(captures)), "cases": str(len({c.get('registry_case_id') for c in captures})), "identity_records": str(len(identity.get('captures', []))), "lineage_records": str(len(lineage.get('captures', [])))}

def build(output: Path, p5: Path) -> list[Path]:
    if output.exists(): raise ValueError("output directory already exists")
    freeze, refs, counts = validate_inputs(p5)
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    base = {"phase": "P6", "created_at": created, "append_only": True, "hardware_action": False, "p5_commit": P5_COMMIT, "p5_tree": P5_TREE, "truth_boundary": "P5 Observer/OAR/CVR and retained raw/hash/seal evidence only; D6/parser/diagnoser/Agent/screenshot excluded as Observer truth."}
    inventory = {"schema": "rtd-p6-frozen-input-inventory-v1", "record_type": "P6_FROZEN_INPUT_INVENTORY", "record_id": "record:p6-frozen-input-inventory-20260914T181500Z", **base, "source_refs": refs, "freeze_record_id": freeze["record_id"], "input_counts": counts, "identity": {"capture_ids_unique": True, "registry_capture_pairs_unique": True}, "lineage": {"required": True, "episode_required": True}}
    audit = {"schema": "rtd-p6-frozen-input-audit-v1", "record_type": "P6_FROZEN_INPUT_AUDIT", "record_id": "record:p6-frozen-input-audit-20260914T181500Z", **base, "source_refs": refs, "checks": {"schema": True, "hash": True, "duplicate": True, "cross_case": True, "stale_binding": True, "split": True, "lineage_episode": True, "no_overwrite": True}, "status": "PASS", "gaps": [], "identity": {"capture_ids_unique": True, "registry_capture_pairs_unique": True}, "lineage": {"required": True, "episode_required": True}}
    output.mkdir(parents=True)
    paths = [output / "p6_frozen_input_inventory.json", output / "p6_integrity_audit.json", output / "p6_execution_record.json"]
    exclusive(paths[0], inventory); exclusive(paths[1], audit)
    prior_refs = [ref(path, "P6 append-only record") for path in paths[:2]]
    execution = {"schema": "rtd-p6-execution-record-v1", "record_type": "P6_EXECUTION_RECORD", "record_id": "record:p6-execution-20260914T181500Z", **base, "source_refs": refs + prior_refs, "audit_record_id": audit["record_id"], "scope": "synthetic/report-only closeout; no new P6 capture or truth generation", "status": "COMPLETE", "historical_evidence_modified": False, "identity": {"capture_ids_unique": True}, "lineage": {"prior_records": [inventory["record_id"], audit["record_id"]]}}
    exclusive(paths[2], execution)
    prior_refs.append(ref(paths[2], "P6 append-only record"))
    manifest = {"schema": "rtd-p6-sha256-manifest-v1", "record_type": "P6_SHA256_MANIFEST", "record_id": "record:p6-sha256-manifest-20260914T181500Z", **base, "source_refs": refs + prior_refs, "records": [{"path": path.name, "sha256": digest(path)} for path in paths], "status": "SEALED_APPEND_ONLY", "identity": {"record_ids_unique": True}, "lineage": {"prior_records": [value["record_id"] for value in (inventory, audit, execution)]}}
    path = output / "p6_sha256_manifest.json"; exclusive(path, manifest); return paths + [path]

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--p5-dir", type=Path, default=ROOT / P5_REL); args = parser.parse_args()
    try: print(json.dumps({"files": [str(p) for p in build(args.output.resolve(), args.p5_dir.resolve())], "status": "PASS"}, sort_keys=True)); return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error: parser.error(str(error))
    return 2
if __name__ == "__main__": raise SystemExit(main())
