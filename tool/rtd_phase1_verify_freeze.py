#!/usr/bin/env python3
"""Read-only verifier for the Phase 1 owner-approved freeze."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPPLEMENT = ROOT / "hardware/rtd_pilot/phase1_freeze_supplements/20260730T103123Z_alientek_elite_v2"
RECEIPT = SUPPLEMENT / "phase1_final_freeze_receipt.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def repo_path(name: str) -> Path:
    path = (ROOT / name).resolve()
    if ROOT not in path.parents or not path.is_file():
        raise ValueError(f"missing or unsafe repository path: {name}")
    return path


def verify() -> dict:
    checks: list[str] = []
    errors: list[str] = []
    try:
        receipt = load(RECEIPT)
        expected = {
            "freeze_status": "FROZEN",
            "review_status": "SELF_REVIEW_COMPLETED",
            "approval_status": "OWNER_APPROVED",
            "platform_selection_status": "SELECTED",
            "data_release_rights": "PASS",
        }
        for key, value in expected.items():
            if receipt.get(key) != value:
                errors.append(f"receipt {key} is not {value}")
        for entry in receipt.get("bindings", []) + receipt.get("historical_evidence_unchanged", []):
            path = repo_path(entry["file"])
            if digest(path) != entry["sha256"]:
                errors.append(f"hash mismatch: {entry['file']}")
        checks.append("receipt bindings and historical evidence hashes")

        supplement = load(repo_path(next(x["file"] for x in receipt["bindings"] if x["role"] == "frozen supplement manifest")))
        if any(supplement.get(key) != value for key, value in {
            "status": "FROZEN", "p1_final_freeze_status": "FROZEN",
            "platform_selection_status": "SELECTED", "data_release_status": "PASS",
        }.items()):
            errors.append("supplement state is not frozen")
        for entry in supplement.get("immutable_inputs", []) + supplement.get("additional_h3_review_inputs", []):
            path = repo_path(entry["file"])
            if digest(path) != entry["sha256"]:
                errors.append(f"supplement evidence hash mismatch: {entry['file']}")
        for entry in supplement.get("decision_inputs", {}).values():
            path = repo_path(entry["file"])
            if digest(path) != entry["sha256"]:
                errors.append(f"supplement decision hash mismatch: {entry['file']}")
        approval = supplement["owner_self_review_approval"]
        if digest(repo_path(approval["file"])) != approval["sha256"] or approval["status"] != "OWNER_APPROVED":
            errors.append("owner self-review approval mismatch")
        log = supplement["final_regression"]
        if digest(SUPPLEMENT / log["log_file"]) != log["log_sha256"] or log["result"] != "38 tests passed":
            errors.append("final regression log mismatch")
        checks.append("supplement state and final regression log")

        scorecard = load(repo_path(next(x["file"] for x in receipt["bindings"] if x["role"] == "H3 scorecard machine record")))
        items = scorecard.get("items", [])
        required = {"item_id", "mandatory_item", "result", "evidence_refs", "evidence_hashes", "platform_identity", "firmware_or_config_identity", "review_method", "reviewer", "review_date", "limitations"}
        if len(items) != 11 or any(item.get("result") != "PASS" or not required.issubset(item) for item in items):
            errors.append("H3 scorecard is not eleven complete PASS entries")
        if scorecard.get("configuration_identity", {}).get("ELF_sha256") != "574d91f7a3841ec4fde19355b8836ae5d61dd92cf2051d13e3ae294154c0895e":
            errors.append("H3 scorecard ELF identity mismatch")
        h3 = load(repo_path(next(x["file"] for x in supplement["immutable_inputs"] if x["role"] == "H3 evidence manifest")))
        preflash = load(repo_path(next(x["file"] for x in supplement["additional_h3_review_inputs"] if x["role"] == "H3 pre-capture metadata snapshot")))
        identity = scorecard["configuration_identity"]
        if identity["platform_identity"] != h3.get("board_id") or identity["ELF_sha256"] != preflash.get("elf_sha256"):
            errors.append("H3 board or ELF identity chain mismatch")
        checks.append("eleven-item H3 scorecard and exact ELF identity")

        raw_by_capture = {item["capture"]: item for item in supplement.get("external_raw_dsview", [])}
        for item in receipt.get("external_raw_dsview", []):
            raw = raw_by_capture.get(item["capture"])
            if not raw or raw["sha256"] != item["sha256"] or digest(Path(raw["file"])) != item["sha256"]:
                errors.append(f"external raw capture mismatch: {item['capture']}")
        checks.append("external raw DSView hashes")
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    return {"valid": not errors, "checks": checks, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify()
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.report:
        with args.report.open("x", encoding="utf-8") as target:
            target.write(text)
    print(text, end="")
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
