#!/usr/bin/env python3
"""Check a nonformal alternative-board preflight manifest; never validates a Capture."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from phase1_common import load_json, sha256

SCHEMA = "phase1-candidate-preflight-v1"
FINDINGS = {"physical_mapping", "pa9_tx", "ch340_path", "host_serial", "gpio_observer", "independent_transport"}
STATUSES = {"pending", "pass", "fail", "inconclusive"}
FORMAL_FIELDS = {"board_id", "pin_map_version", "capture_id", "session_id", "firmware_variant"}
UART_WITNESSES = {"undecided", "ch340_host_witness_v1", "pa9_independent_transport_witness_v1"}


def contains_formal_field(value: object) -> bool:
    if isinstance(value, dict):
        return bool(FORMAL_FIELDS & set(value)) or any(contains_formal_field(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_formal_field(item) for item in value)
    return False


def check(manifest: dict, base: Path) -> tuple[list[str], bool]:
    reasons: list[str] = []
    if manifest.get("schema_version") != SCHEMA:
        reasons.append("schema_version_mismatch")
    if manifest.get("preflight_scope") != "nonformal-electrical-link":
        reasons.append("preflight_scope_mismatch")
    if manifest.get("formal_capture_started") is not False:
        reasons.append("formal_capture_not_permitted")
    if manifest.get("freeze_requested") is not False:
        reasons.append("freeze_request_not_permitted")
    if not isinstance(manifest.get("candidate_label"), str) or not manifest["candidate_label"]:
        reasons.append("missing_candidate_label")
    if contains_formal_field(manifest):
        reasons.append("formal_capture_field_present")
    witness = manifest.get("selected_uart_witness")
    if witness not in UART_WITNESSES:
        reasons.append("uart_witness_invalid")

    target = manifest.get("target")
    if not isinstance(target, dict) or target.get("target") != "stm32f103ze" or target.get("probe_uid") != "0001A0000001":
        reasons.append("target_binding_mismatch")

    artifacts = manifest.get("artifacts")
    artifact_ids: set[str] = set()
    if not isinstance(artifacts, list):
        reasons.append("artifacts_invalid")
        artifacts = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            reasons.append("artifact_invalid")
            continue
        identifier, name, expected = artifact.get("id"), artifact.get("file"), artifact.get("sha256")
        if not isinstance(identifier, str) or not identifier or identifier in artifact_ids:
            reasons.append("artifact_id_invalid")
            continue
        artifact_ids.add(identifier)
        if not isinstance(name, str) or not name or not isinstance(expected, str) or len(expected) != 64:
            reasons.append("artifact_binding_invalid")
            continue
        path = Path(name)
        if not path.is_absolute():
            path = base / path
        if not path.is_file() or path.stat().st_size == 0:
            reasons.append("artifact_missing_or_empty")
        elif sha256(path) != expected:
            reasons.append("artifact_hash_mismatch")

    findings = manifest.get("findings")
    ready = False
    if not isinstance(findings, dict) or set(findings) != FINDINGS:
        reasons.append("findings_invalid")
    else:
        for name, finding in findings.items():
            if not isinstance(finding, dict) or finding.get("status") not in STATUSES:
                reasons.append("finding_status_invalid")
                continue
            evidence_ids = finding.get("evidence_ids")
            if not isinstance(evidence_ids, list) or not all(isinstance(item, str) for item in evidence_ids):
                reasons.append("finding_evidence_invalid")
            elif finding["status"] == "pass" and (not evidence_ids or not set(evidence_ids).issubset(artifact_ids)):
                reasons.append("passing_finding_missing_evidence")
        if not reasons and witness != "undecided":
            core = {"physical_mapping", "gpio_observer"}
            core_ready = all(findings[name]["status"] == "pass" for name in core)
            if witness == "ch340_host_witness_v1":
                ready = core_ready and findings["host_serial"]["status"] == "pass"
            else:
                ready = core_ready and findings["independent_transport"]["status"] == "pass"
    return sorted(set(reasons)), ready


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    reasons, ready = check(load_json(args.manifest), args.manifest.parent)
    if reasons:
        print("INVALID " + ",".join(reasons))
        return 2
    print("PREFLIGHT_READY_FOR_SEPARATE_FREEZE_REVIEW" if ready else "PREFLIGHT_PENDING")
    return 0


if __name__ == "__main__":
    sys.exit(main())
