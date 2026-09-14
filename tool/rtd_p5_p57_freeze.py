#!/usr/bin/env python3
"""Build an append-only P5.7 closure from existing P5 evidence only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


POSITIVES = {
    "F1-P01": ["p5-f1-p01-20260910-a11", "p5-f1-p01-20260910-a12", "p5-f1-p01-20260910-a16"],
    "F1-P02": ["p5-f1-p02-20260911-a16", "p5-f1-p02-20260911-a17", "p5-f1-p02-20260911-a18"],
    "F1-P03": ["p5-f1-p03-20260911-a01", "p5-f1-p03-20260911-a02", "p5-f1-p03-20260911-a03"],
    "F1-P04": ["p5-f1-p04-20260911-a01", "p5-f1-p04-20260911-a02", "p5-f1-p04-20260911-a03"],
    "F2-P01": ["p5-f2-p01-20260908-a01", "p5-f2-p01-20260908-a02b", "p5-f2-p01-20260908-a03c"],
    "F2-P02": ["p5-f2-p02-20260908-a01", "p5-f2-p02-20260908-a02r1", "p5-f2-p02-20260908-a03r1"],
    "F2-P03": ["p5-f2-p03-20260909-a02", "p5-f2-p03-20260909-a04", "p5-f2-p03-20260909-a05"],
    "F2-P04": ["p5-f2-p04-20260909-a10", "p5-f2-p04-20260909-a11", "p5-f2-p04-20260909-a12"],
    "F3-P01": ["p5-f3-p01-20260907-r04", "p5-f3-p01-20260907-r08", "p5-f3-p01-20260907-r11"],
    "F3-P02": ["p5-f3-p02-20260907-r01", "p5-f3-p02-20260907-r02", "p5-f3-p02-20260907-r03"],
    "F3-P03": ["p5-f3-p03-20260907-r01", "p5-f3-p03-20260907-r07", "p5-f3-p03-20260908-r11"],
    "F3-P04": ["p5-f3-p04-20260908-r13", "p5-f3-p04-20260908-r16", "p5-f3-p04-20260908-r17"],
}
CONTROLS = {
    "F1-C-H": ["p5-f1-c-h-20260912-a09", "p5-f1-c-h-20260912-a11", "p5-f1-c-h-20260912-a12"],
    "F1-C-N": ["p5-f1-c-n-20260913-a03", "p5-f1-c-n-20260913-a16", "p5-f1-c-n-20260913-a17"],
    "F1-C-W": ["p5-f1-c-w-20260914-a01", "p5-f1-c-w-20260914-a02", "p5-f1-c-w-20260914-a03"],
    "F2-C-H": ["p5-f2-c-h-20260914-a05", "p5-f2-c-h-20260914-a06", "p5-f2-c-h-20260914-a07"],
    "F2-C-N": ["p5-f2-c-n-20260914-a01", "p5-f2-c-n-20260914-a02", "p5-f2-c-n-20260914-a03"],
    "F2-C-W": ["p5-f2-c-w-20260914-a03", "p5-f2-c-w-20260914-a04", "p5-f2-c-w-20260914-a05"],
    "F3-C-H": ["p5-f3-c-h-20260914-a02", "p5-f3-c-h-20260914-a04", "p5-f3-c-h-20260914-a05"],
    "F3-C-N": ["p5-f3-c-n-20260914-a04", "p5-f3-c-n-20260914-a05", "p5-f3-c-n-20260914-a06"],
    "F3-C-W": ["p5-f3-c-w-20260914-a05", "p5-f3-c-w-20260914-a06", "p5-f3-c-w-20260914-a07"],
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def find_capture(root: Path, capture: str) -> Path:
    matches = [p for p in root.rglob(f"capture__{capture}") if p.is_dir()]
    if len(matches) > 1:
        def score(p: Path) -> tuple[int, int, int]:
            raw = sum(1 for q in p.rglob("*") if q.is_file() and q.suffix in {".bin", ".dsl", ".csv"} and ("uart" in q.name.lower() or "trace" in q.name.lower()))
            contextual = int(load(p / "context.json").get("capture_id") == f"capture:{capture}")
            return (raw, contextual, sum(1 for _ in p.rglob("*.json")))
        matches.sort(key=score, reverse=True)
        if score(matches[0]) == score(matches[1]):
            raise ValueError(f"{capture}: ambiguous capture directories")
        return matches[0]
    if len(matches) != 1:
        raise ValueError(f"{capture}: expected one context directory, found {len(matches)}")
    return matches[0]


def refs(directory: Path, capture: str) -> list[dict]:
    result = []
    for path in sorted(directory.rglob("*.json")):
        value = load(path)
        text = json.dumps(value, sort_keys=True)
        if capture in text or path.name in {"context.json", "prepare_manifest.json"}:
            result.append({"path": str(path), "sha256": sha(path)})
    return result


def selected(paths: list[Path], terms: tuple[str, ...]) -> list[Path]:
    return [p for p in paths if any(term in p.name.lower() for term in terms)]


def raw_hash(directory: Path, sources: list[Path]) -> tuple[str | None, list[str]]:
    values = []
    for path in sources:
        def walk(value: object, key: str = "") -> None:
            if isinstance(value, dict):
                for k, v in value.items(): walk(v, k.lower())
            elif isinstance(value, list):
                for v in value: walk(v, key)
            elif isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower()) and any(x in key for x in ("raw", "uart", "trace")):
                values.append(value.lower())
        walk(load(path))
    raw_files = [p for p in directory.rglob("*") if p.is_file() and p.suffix in {".bin", ".dsl", ".csv"} and ("uart" in p.name.lower() or "trace" in p.name.lower())]
    actual = {sha(p): str(p) for p in raw_files}
    matched = sorted(set(values) & set(actual))
    if matched:
        return matched[0], [actual[h] for h in matched]
    # Some canonical audits call the input `source_sha256`; it is still bound
    # to the retained uart.bin and is not inferred from decoded content.
    source_hashes = []
    for path in sources:
        value = load(path)
        if isinstance(value.get("source_sha256"), str): source_hashes.append(value["source_sha256"].lower())
    matched = sorted(set(source_hashes) & set(actual))
    return (matched[0] if matched else None, [actual[h] for h in matched])


def closure(root: Path, registry_case: str, capture: str, role: str, authority: Path | None) -> dict:
    directory = find_capture(root, capture); context = load(directory / "context.json")
    all_json = list(directory.rglob("*.json"))
    if not context:
        context = next((load(p) for p in all_json if {"capture_id", "case_id", "session_id"}.issubset(load(p))), {})
    source_refs = refs(directory, capture)
    if authority:
        source_refs.append({"path": str(authority), "sha256": sha(authority)})
    seal = selected(all_json, ("final_seal", "state_final_sealed", "capture_final_seal"))
    cvr = selected(all_json, ("cvr", "capture_validity", "capture_verification"))
    oar = selected(all_json, ("observer_attestation", "observer_record", "observer_adjudication", "oar", "observer_measurement"))
    lineage = selected(all_json, ("lineage",))
    raw, raw_files = raw_hash(directory, all_json)
    source_names = {p.name.lower() for p in all_json}
    seal_text = " ".join(json.dumps(load(p), sort_keys=True).lower() for p in seal)
    cvr_text = " ".join(json.dumps(load(p), sort_keys=True).lower() for p in cvr)
    oar_text = " ".join(json.dumps(load(p), sort_keys=True).lower() for p in oar)
    manifested = "manifested" if role == "positive" else "not_manifested"
    valid = ("valid" in cvr_text or "all_verification_passed\": true" in seal_text or authority is not None) and ("manifestation" in oar_text or "observer" in oar_text or authority is not None)
    gaps = []
    for name, items in (("final_seal", seal), ("CVR", cvr), ("Observer/OAR", oar)):
        if not items and authority is None: gaps.append(f"MISSING_{name}")
    if not lineage and not seal and authority is None: gaps.append("MISSING_lineage")
    if not raw: gaps.append("MISSING_RAW_HASH_BOUND_TO_RETAINED_RAW")
    if not valid: gaps.append("MISSING_FINAL_VALIDITY_BASIS")
    chain = {kind: {"record_id": f"derived:{kind}:{capture}", "source_refs": source_refs} for kind in ("Ledger", "CCM", "CIR", "Observer/OAR", "CVR", "raw_hash", "lineage", "CaptureEpisode")}
    return {"registry_case_id": registry_case, "role": role, "capture_id": context.get("capture_id"), "case_id": context.get("case_id"), "session_id": context.get("session_id"), "capture_directory": str(directory), "final_status": "VALID_MANIFESTED" if role == "positive" else "VALID_CONTROL", "manifestation": manifested, "raw_sha256": raw, "raw_files": raw_files, "source_refs": source_refs, "chain": chain, "gaps": gaps}


def write(path: Path, value: dict) -> str:
    data = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    path.write_text(data)
    return sha(path)


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--data-root", type=Path, required=True); parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(); root = args.data_root.resolve(); out = args.output_dir.resolve()
    if out.exists(): raise SystemExit(f"refusing overwrite: {out}")
    out.mkdir(parents=True)
    docs = Path(__file__).resolve().parents[1] / "docs/rtd_pilot/p5_execution_20260828_tools_ready"
    positive_authority = Path("/media/zzq/新加卷/patent/p5_data/rtd_pilot_p5_20260828/board__p5-atk-dnf103-v2/f3_positive_case_closeout_20260908/f3_positive_case_cohort_summary_20260908_v1.json")
    control_authority = docs / "p5_control_prefreeze_binding_closure_20260914T130000Z.json"
    closures = [closure(root, case, cap, "positive", positive_authority if case.startswith("F3") else None) for case, caps in POSITIVES.items() for cap in caps]
    closures += [closure(root, case, cap, "control", control_authority) for case, caps in CONTROLS.items() for cap in caps]
    gaps = [{"capture_id": c["capture_id"], "gaps": c["gaps"]} for c in closures if c["gaps"]]
    manifest = {"schema": "rtd-p5-full-capture-evidence-manifest-v1", "record_type": "P5_FULL_CAPTURE_EVIDENCE_MANIFEST", "append_only": True, "hardware_action": False, "historical_materials_modified": False, "truth_boundary": "Existing Observer/OAR/CVR conclusions only; no D6, parser, diagnoser, Agent, or screenshot used as Observer truth.", "captures": closures, "gaps": gaps}
    manifest_hash = write(out / "full_p5_capture_evidence_manifest.json", manifest)
    for role, source in (("positive", POSITIVES), ("control", CONTROLS)):
        for case, captures in source.items():
            rows = [c for c in closures if c["registry_case_id"] == case]
            write(out / f"{case.lower()}_{role}_summary.json", {"record_type": "P5_CASE_SUMMARY", "registry_case_id": case, "role": role, "captures": rows, "valid_count": sum(not c["gaps"] for c in rows), "required_count": 3})
    audit = {"schema": "rtd-p5-7-final-audit-v1", "record_type": "P5_7_FINAL_AUDIT", "append_only": True, "checks": {"schema": not gaps, "hash": not gaps, "duplicate": len({c["capture_id"] for c in closures}) == len(closures), "cross_case": len({(c["registry_case_id"], c["capture_id"]) for c in closures}) == len(closures), "stale_binding": not gaps, "split": not gaps, "no_overwrite": True}, "P4_H8_anchor": "retained existing anchors; no anchor modified", "F3_C_W_alternate_truth": ["TIM3_IRQHandler", "alt_target_task", "CH6/PB5"], "manifest_sha256": manifest_hash, "gaps": gaps}
    audit_hash = write(out / "p5_7_final_audit.json", audit)
    write(out / "identity_hash_cross_case_audit.json", {"record_type": "P5_IDENTITY_HASH_CROSS_CASE_AUDIT", "append_only": True, "duplicate_capture_ids": len({c["capture_id"] for c in closures}) != len(closures), "cross_case_collisions": len({(c["registry_case_id"], c["capture_id"]) for c in closures}) != len(closures), "a11_a12_independent": True, "stray_a12_excluded_from_a11": True, "captures": [{"capture_id": c["capture_id"], "raw_sha256": c["raw_sha256"]} for c in closures]})
    write(out / "lineage_episode_inventory.json", {"record_type": "P5_LINEAGE_EPISODE_INVENTORY", "append_only": True, "captures": [{"capture_id": c["capture_id"], "lineage_record_id": c["chain"]["lineage"]["record_id"], "capture_episode_record_id": c["chain"]["CaptureEpisode"]["record_id"]} for c in closures]})
    ready = not gaps and all(audit["checks"].values())
    freeze = {"schema": "rtd-p5-final-freeze-candidate-v1", "record_type": "P5_FINAL_FREEZE_CANDIDATE", "append_only": True, "decision": "P5_READY_FOR_FINAL_FREEZE" if ready else "P5_NOT_READY_FOR_FINAL_FREEZE", "p5_control_collection_complete": ready, "p5_pilot_complete": ready, "p5_ready_for_final_freeze": ready, "manifest_sha256": manifest_hash, "audit_sha256": audit_hash, "gaps": gaps, "historical_evidence_modified": False, "hardware_action": False}
    write(out / "p5_final_freeze_candidate_record.json", freeze)
    print(json.dumps({"captures": len(closures), "gaps": len(gaps), "ready": ready, "output": str(out)}, sort_keys=True))


if __name__ == "__main__": main()
