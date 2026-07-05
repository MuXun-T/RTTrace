from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = ROOT / "docs" / "traceability_matrix.json"
DEFAULT_TC_MATRIX = ROOT / "docs" / "acceptance_case_matrix_20260329.json"
DEFAULT_FINAL_STATUS = ROOT / "docs" / "final_validation_status_20260314.json"
DEFAULT_REQUIREMENTS_DOC = ROOT / "document" / "需求规格说明书_MVP_v1.3_回放对比复现对齐版.md"
DEFAULT_OUTPUT = ROOT / "docs" / "traceability_matrix.json"
DEFAULT_READINESS = ROOT / "docs" / "final_acceptance_readiness_20260314.md"
DEFAULT_CHECKLIST = ROOT / "docs" / "external_validation_checklist_20260314.md"

FR_ROW_PATTERN = re.compile(r"^\|\s*(FR-[A-Z]+-\d{2})\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|$")
NFR_ROW_PATTERN = re.compile(r"^\|\s*(NFR-[A-Z]+-\d{2})\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|$")
TC_TOKEN_PATTERN = re.compile(r"TC-[A-Z]+-\d{2}(?:~\d{2})?")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_disallowed_artifact(path: str) -> bool:
    lowered = path.lower()
    return "stage" in lowered or "phase" in lowered


def _unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _build_final_artifact_whitelist(
    final_status: dict[str, Any],
    final_status_path: Path,
    readiness_path: Path,
    checklist_path: Path,
) -> set[str]:
    candidates: list[str] = []
    candidates.extend(str(item) for item in (final_status.get("artifacts") or {}).values())
    candidates.extend(
        [
            str(final_status_path.relative_to(ROOT)),
            str(readiness_path.relative_to(ROOT)),
            str(checklist_path.relative_to(ROOT)),
        ]
    )
    whitelist: set[str] = set()
    for rel_path in candidates:
        if _is_disallowed_artifact(rel_path):
            continue
        if not (ROOT / rel_path).exists():
            continue
        whitelist.add(rel_path)
    return whitelist


def _filter_authoritative(paths: list[str], whitelist: set[str]) -> list[str]:
    filtered = [item for item in paths if item in whitelist and not _is_disallowed_artifact(item)]
    return _unique_keep_order(filtered)


def _expand_tc_token(token: str) -> list[str]:
    if "~" not in token:
        return [token]
    start, end = token.split("~", 1)
    prefix = start[:-2]
    start_num = int(start[-2:])
    end_num = int(end[-2:])
    if end_num < start_num:
        return [start]
    return [f"{prefix}{value:02d}" for value in range(start_num, end_num + 1)]


def _parse_fr_to_tc_mapping(requirements_doc: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    fr_to_tc: dict[str, list[str]] = {}
    fr_design_refs: dict[str, list[str]] = {}
    for line in requirements_doc.read_text(encoding="utf-8").splitlines():
        matched = FR_ROW_PATTERN.match(line.strip())
        if not matched:
            continue
        fr_id = matched.group(1)
        design_raw = matched.group(2)
        tc_raw = matched.group(3)
        design_refs = re.findall(r"`([^`]+)`", design_raw)
        tc_tokens = TC_TOKEN_PATTERN.findall(tc_raw)
        expanded: list[str] = []
        for token in tc_tokens:
            expanded.extend(_expand_tc_token(token))
        fr_to_tc[fr_id] = _unique_keep_order(expanded)
        fr_design_refs[fr_id] = design_refs
    return fr_to_tc, fr_design_refs


def _parse_nfr_design_refs(requirements_doc: Path) -> dict[str, list[str]]:
    nfr_design_refs: dict[str, list[str]] = {}
    for line in requirements_doc.read_text(encoding="utf-8").splitlines():
        matched = NFR_ROW_PATTERN.match(line.strip())
        if not matched:
            continue
        nfr_id = matched.group(1)
        design_raw = matched.group(2)
        nfr_design_refs[nfr_id] = re.findall(r"`([^`]+)`", design_raw)
    return nfr_design_refs


def _tc_primary_covered(case: dict[str, Any]) -> bool:
    if case.get("status") != "covered":
        return False
    primary = [
        item
        for item in (case.get("evidence") or [])
        if isinstance(item, dict) and item.get("role") == "primary"
    ]
    return bool(primary)


def _tc_artifact_refs(case: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for item in case.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "artifact":
            continue
        ref = item.get("ref")
        if isinstance(ref, str) and ref:
            refs.append(ref)
    return refs


def _fr_family(fr_id: str) -> str:
    return fr_id.split("-")[1]


def _fr_default_artifacts(fr_id: str, artifacts: dict[str, str]) -> list[str]:
    family = _fr_family(fr_id)
    if family == "COL":
        keys = ["collector_perf", "collector_soak"]
    elif family in {"CMP"}:
        keys = ["acceptance_compare", "package_compare", "acceptance", "windows_acceptance"]
    elif family in {"VIZ"}:
        keys = ["desktop_perf", "windows_desktop_perf", "acceptance"]
    elif family in {"EXP"}:
        keys = ["acceptance", "package_compare"]
    else:
        keys = ["acceptance", "desktop_perf"]
    return [artifacts[key] for key in keys if key in artifacts]


def _evaluate_tc_group(tc_ids: list[str], tc_cases: dict[str, dict[str, Any]]) -> tuple[bool, list[str], list[str]]:
    missing = [tc_id for tc_id in tc_ids if tc_id not in tc_cases]
    not_covered = [
        tc_id
        for tc_id in tc_ids
        if tc_id in tc_cases and not _tc_primary_covered(tc_cases[tc_id])
    ]
    closed = not missing and not not_covered
    return closed, missing, not_covered


def _final_status_closed(node: dict[str, Any] | None) -> bool:
    if not isinstance(node, dict):
        return False
    return node.get("status") == "closed"


def _build_requirements_section(
    seed_matrix: dict[str, Any],
    tc_matrix: dict[str, Any],
    final_status: dict[str, Any],
    fr_to_tc: dict[str, list[str]],
    fr_design_refs_from_doc: dict[str, list[str]],
    whitelist: set[str],
    final_status_path: Path,
    readiness_path: Path,
) -> dict[str, Any]:
    seed_requirements = seed_matrix.get("requirements") or {}
    artifacts = final_status.get("artifacts") or {}
    tc_cases = {item["tc_id"]: item for item in (tc_matrix.get("tc_cases") or []) if isinstance(item, dict)}
    common_artifacts = [
        str(final_status_path.relative_to(ROOT)),
        str(readiness_path.relative_to(ROOT)),
    ]

    requirements: dict[str, Any] = {}
    for fr_id, seed_entry in seed_requirements.items():
        tc_ids = fr_to_tc.get(fr_id, [])
        closed, missing, not_covered = _evaluate_tc_group(tc_ids, tc_cases)

        tc_artifacts: list[str] = []
        for tc_id in tc_ids:
            case = tc_cases.get(tc_id)
            if case is not None:
                tc_artifacts.extend(_tc_artifact_refs(case))

        authoritative_artifacts = _filter_authoritative(
            tc_artifacts + _fr_default_artifacts(fr_id, artifacts) + common_artifacts,
            whitelist,
        )

        remaining: list[str] = []
        if missing:
            remaining.extend([f"missing_tc:{tc_id}" for tc_id in missing])
        if not_covered:
            remaining.extend([f"uncovered_tc:{tc_id}" for tc_id in not_covered])
        if closed and not authoritative_artifacts:
            remaining.append("no_authoritative_artifact")
            closed = False

        requirements[fr_id] = {
            "design_refs": list(seed_entry.get("design_refs") or fr_design_refs_from_doc.get(fr_id) or []),
            "implementation_refs": list(seed_entry.get("implementation_refs") or []),
            "tests": list(seed_entry.get("tests") or []),
            "status": "closed" if closed else "gap",
            "evidence_level": "implemented_and_regressed" if closed else "external_evidence_required",
            "remaining": [] if closed else remaining,
            "authoritative_artifacts": authoritative_artifacts,
            "derived_from": {
                "tc_ids": tc_ids,
                "missing_tc_ids": missing,
                "not_covered_tc_ids": not_covered,
                "tc_matrix_ref": str(DEFAULT_TC_MATRIX.relative_to(ROOT)),
            },
        }
    return requirements


def _nfr_artifacts(artifacts: dict[str, str], keys: list[str], common: list[str], whitelist: set[str]) -> list[str]:
    refs = [artifacts[key] for key in keys if key in artifacts]
    refs.extend(common)
    return _filter_authoritative(refs, whitelist)


def _build_non_functional_section(
    seed_matrix: dict[str, Any],
    tc_matrix: dict[str, Any],
    final_status: dict[str, Any],
    whitelist: set[str],
    final_status_path: Path,
    readiness_path: Path,
    checklist_path: Path,
    nfr_design_refs_from_doc: dict[str, list[str]],
) -> dict[str, Any]:
    seed_non_functional = seed_matrix.get("non_functional") or {}
    tc_cases = {item["tc_id"]: item for item in (tc_matrix.get("tc_cases") or []) if isinstance(item, dict)}
    artifacts = final_status.get("artifacts") or {}
    external = final_status.get("external_status") or {}
    nfr_perf = final_status.get("nfr_perf_status") or {}
    common = [
        str(final_status_path.relative_to(ROOT)),
        str(readiness_path.relative_to(ROOT)),
        str(checklist_path.relative_to(ROOT)),
    ]

    def tc_rule(
        nfr_id: str,
        tc_ids: list[str],
        artifact_keys: list[str],
        evidence_level_closed: str,
        extra_checks: list[tuple[bool, str]] | None = None,
    ) -> dict[str, Any]:
        closed, missing, not_covered = _evaluate_tc_group(tc_ids, tc_cases)
        remaining: list[str] = []
        if missing:
            remaining.extend([f"missing_tc:{tc_id}" for tc_id in missing])
        if not_covered:
            remaining.extend([f"uncovered_tc:{tc_id}" for tc_id in not_covered])

        checks = extra_checks or []
        for passed, reason in checks:
            if not passed:
                closed = False
                remaining.append(reason)

        authoritative_artifacts = _nfr_artifacts(artifacts, artifact_keys, common, whitelist)
        if closed and not authoritative_artifacts:
            closed = False
            remaining.append("no_authoritative_artifact")

        seed_entry = seed_non_functional.get(nfr_id) or {}
        return {
            "implementation_refs": list(seed_entry.get("implementation_refs") or []),
            "tests": list(seed_entry.get("tests") or []),
            "design_refs": list(nfr_design_refs_from_doc.get(nfr_id) or []),
            "status": "closed" if closed else "gap",
            "evidence_level": evidence_level_closed if closed else "external_evidence_required",
            "remaining": [] if closed else remaining,
            "authoritative_artifacts": authoritative_artifacts,
            "derived_from": {
                "rule_type": "tc_matrix_plus_final_artifacts",
                "tc_ids": tc_ids,
                "extra_checks": [reason for passed, reason in checks if not passed],
                "tc_matrix_ref": str(DEFAULT_TC_MATRIX.relative_to(ROOT)),
            },
        }

    def final_rule(
        nfr_id: str,
        status_source: tuple[str, str],
        artifact_keys: list[str],
        evidence_level_closed: str,
        extra_checks: list[tuple[bool, str]] | None = None,
    ) -> dict[str, Any]:
        node_group, node_key = status_source
        group = final_status.get(node_group) or {}
        node = group.get(node_key)
        closed = _final_status_closed(node)
        remaining: list[str] = []
        if not closed:
            remaining.append(f"{node_group}.{node_key}.status!=closed")
        checks = extra_checks or []
        for passed, reason in checks:
            if not passed:
                closed = False
                remaining.append(reason)

        authoritative_artifacts = _nfr_artifacts(artifacts, artifact_keys, common, whitelist)
        if closed and not authoritative_artifacts:
            closed = False
            remaining.append("no_authoritative_artifact")

        seed_entry = seed_non_functional.get(nfr_id) or {}
        return {
            "implementation_refs": list(seed_entry.get("implementation_refs") or []),
            "tests": list(seed_entry.get("tests") or []),
            "design_refs": list(nfr_design_refs_from_doc.get(nfr_id) or []),
            "status": "closed" if closed else "gap",
            "evidence_level": evidence_level_closed if closed else "external_evidence_required",
            "remaining": [] if closed else remaining,
            "authoritative_artifacts": authoritative_artifacts,
            "derived_from": {
                "rule_type": "final_validation_status",
                "status_source": f"{node_group}.{node_key}.status",
                "extra_checks": [reason for passed, reason in checks if not passed],
                "final_status_ref": str(final_status_path.relative_to(ROOT)),
            },
        }

    non_functional: dict[str, Any] = {}
    for nfr_id in seed_non_functional.keys():
        if nfr_id == "NFR-PERF-01":
            non_functional[nfr_id] = final_rule(
                nfr_id,
                ("nfr_perf_status", "NFR-PERF-01"),
                ["collector_perf", "windows_collector_perf"],
                "closed_with_authoritative_artifacts",
            )
            continue
        if nfr_id == "NFR-PERF-02":
            perf01_closed = _final_status_closed((nfr_perf.get("NFR-PERF-01") or {}))
            non_functional[nfr_id] = tc_rule(
                nfr_id,
                ["TC-COL-04", "TC-COL-05"],
                ["collector_perf", "collector_soak"],
                "implemented_and_regressed",
                extra_checks=[(perf01_closed, "nfr_perf_status.NFR-PERF-01.status!=closed")],
            )
            continue
        if nfr_id == "NFR-PERF-03":
            non_functional[nfr_id] = final_rule(
                nfr_id,
                ("external_status", "desktop_1gb_first_screen_lt_10s"),
                ["desktop_preflight", "desktop_perf"],
                "linux_formal_closed",
            )
            continue
        if nfr_id == "NFR-PERF-04":
            non_functional[nfr_id] = final_rule(
                nfr_id,
                ("external_status", "desktop_peak_memory_lt_4gb"),
                ["desktop_preflight", "desktop_perf"],
                "linux_formal_closed",
            )
            continue
        if nfr_id == "NFR-PERF-05":
            non_functional[nfr_id] = final_rule(
                nfr_id,
                ("nfr_perf_status", "NFR-PERF-05"),
                ["render_fps"],
                "closed_with_authoritative_artifacts",
            )
            continue
        if nfr_id == "NFR-PERF-06":
            non_functional[nfr_id] = final_rule(
                nfr_id,
                ("nfr_perf_status", "NFR-PERF-06"),
                ["desktop_perf"],
                "linux_formal_closed",
            )
            continue
        if nfr_id == "NFR-STAB-01":
            non_functional[nfr_id] = final_rule(
                nfr_id,
                ("external_status", "long_duration_stability"),
                [
                    "collector_soak",
                    "desktop_soak",
                    "irrecoverable_error",
                    "windows_collector_soak",
                    "windows_desktop_soak",
                    "windows_irrecoverable_error",
                ],
                "closed_with_authoritative_artifacts",
            )
            continue
        if nfr_id == "NFR-CONS-01":
            non_functional[nfr_id] = final_rule(
                nfr_id,
                ("external_status", "windows_linux_consistency"),
                [
                    "acceptance",
                    "windows_acceptance",
                    "desktop_perf",
                    "windows_desktop_perf",
                    "windows_desktop_runtime",
                    "acceptance_compare",
                    "package_compare",
                ],
                "closed_with_authoritative_artifacts",
                extra_checks=[
                    (bool(final_status.get("acceptance_compare_match")), "acceptance_compare_match!=true"),
                    (bool(final_status.get("package_compare_match")), "package_compare_match!=true"),
                ],
            )
            continue
        if nfr_id == "NFR-CONS-02":
            non_functional[nfr_id] = tc_rule(
                nfr_id,
                ["TC-PRS-04"],
                ["acceptance", "acceptance_compare"],
                "implemented_and_regressed",
            )
            continue
        if nfr_id == "NFR-CONS-03":
            non_functional[nfr_id] = tc_rule(
                nfr_id,
                ["TC-PRS-02", "TC-COL-08"],
                ["acceptance", "windows_acceptance", "acceptance_compare"],
                "implemented_and_regressed",
            )
            continue
        if nfr_id == "NFR-CONS-04":
            non_functional[nfr_id] = tc_rule(
                nfr_id,
                ["TC-RPR-01", "TC-RPR-02"],
                ["acceptance", "windows_acceptance", "acceptance_compare"],
                "implemented_and_regressed",
            )
            continue
        if nfr_id == "NFR-CONS-05":
            non_functional[nfr_id] = tc_rule(
                nfr_id,
                ["TC-CMP-02", "TC-CMP-03"],
                ["acceptance_compare", "package_compare"],
                "implemented_and_regressed",
                extra_checks=[
                    (bool(final_status.get("acceptance_compare_match")), "acceptance_compare_match!=true"),
                    (bool(final_status.get("package_compare_match")), "package_compare_match!=true"),
                ],
            )
            continue
        if nfr_id == "NFR-COMPAT-01":
            non_functional[nfr_id] = tc_rule(
                nfr_id,
                ["TC-FMT-03", "TC-FMT-04", "TC-FMT-06"],
                ["acceptance"],
                "implemented_and_regressed",
            )
            continue

        seed_entry = seed_non_functional.get(nfr_id) or {}
        non_functional[nfr_id] = {
            "implementation_refs": list(seed_entry.get("implementation_refs") or []),
            "tests": list(seed_entry.get("tests") or []),
            "design_refs": list(nfr_design_refs_from_doc.get(nfr_id) or []),
            "status": "gap",
            "evidence_level": "external_evidence_required",
            "remaining": ["unmapped_nfr_rule"],
            "authoritative_artifacts": _nfr_artifacts(artifacts, [], common, whitelist),
            "derived_from": {
                "rule_type": "unmapped",
                "final_status_ref": str(final_status_path.relative_to(ROOT)),
            },
        }

    return non_functional


def build_traceability_matrix(
    *,
    seed_path: Path,
    tc_matrix_path: Path,
    final_status_path: Path,
    requirements_doc_path: Path,
    readiness_path: Path,
    checklist_path: Path,
) -> dict[str, Any]:
    seed = _load_json(seed_path)
    tc_matrix = _load_json(tc_matrix_path)
    final_status = _load_json(final_status_path)

    fr_to_tc, fr_design_refs_from_doc = _parse_fr_to_tc_mapping(requirements_doc_path)
    nfr_design_refs_from_doc = _parse_nfr_design_refs(requirements_doc_path)

    whitelist = _build_final_artifact_whitelist(
        final_status,
        final_status_path,
        readiness_path,
        checklist_path,
    )

    meta = dict(seed.get("meta") or {})
    meta["summary_ref"] = str(final_status_path.relative_to(ROOT))
    meta["readiness_ref"] = str(readiness_path.relative_to(ROOT))
    meta["checklist_ref"] = str(checklist_path.relative_to(ROOT))
    meta["tc_matrix_ref"] = str(tc_matrix_path.relative_to(ROOT))
    meta["generator"] = "tool/build_traceability_matrix.py"
    meta["derivation_rules_version"] = "2026-03-30-v1"

    requirements = _build_requirements_section(
        seed,
        tc_matrix,
        final_status,
        fr_to_tc,
        fr_design_refs_from_doc,
        whitelist,
        final_status_path,
        readiness_path,
    )
    non_functional = _build_non_functional_section(
        seed,
        tc_matrix,
        final_status,
        whitelist,
        final_status_path,
        readiness_path,
        checklist_path,
        nfr_design_refs_from_doc,
    )

    return {
        "meta": meta,
        "requirements": requirements,
        "non_functional": non_functional,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild traceability matrix from TC matrix + final status.")
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--tc-matrix", type=Path, default=DEFAULT_TC_MATRIX)
    parser.add_argument("--final-status", type=Path, default=DEFAULT_FINAL_STATUS)
    parser.add_argument("--requirements-doc", type=Path, default=DEFAULT_REQUIREMENTS_DOC)
    parser.add_argument("--readiness", type=Path, default=DEFAULT_READINESS)
    parser.add_argument("--checklist", type=Path, default=DEFAULT_CHECKLIST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    matrix = build_traceability_matrix(
        seed_path=args.seed,
        tc_matrix_path=args.tc_matrix,
        final_status_path=args.final_status,
        requirements_doc_path=args.requirements_doc,
        readiness_path=args.readiness,
        checklist_path=args.checklist,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
