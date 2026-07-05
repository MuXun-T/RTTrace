from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tool.formal_windows_common import default_archive_root, detect_repo_root, formal_parity_field_paths, json_load, json_write


GROUP_METRIC_PATHS = {
    "A_control_plane_first": [
        "required_metrics.scan_count",
        "required_metrics.seek_count",
        "required_metrics.window_span_total",
        "required_metrics.sidecar_lookup_count",
        "observations.closure_modes",
        "observations.proof_consumer_modes",
    ],
    "B_budget_pre_freeze": [
        "required_metrics.frontier_halt_reason",
        "required_metrics.projected_next_events",
        "required_metrics.projected_next_bytes",
        "required_metrics.reject_round_has_read",
    ],
    "C_degraded_audit": [
        "observations.exception_codes",
        "observations.all_consumer_modes_safe",
        "observations.all_package_contracts_valid",
        "observations.all_minimal_legal_packages_valid",
    ],
}
REQUIRED_COMPARE_FIELDS = (
    "contract_version",
    "run_scope",
    "input_contract.trace_sha256",
    "proof_group",
    "verdict",
)


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat()


def _get_path(payload: dict[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for token in dotted_path.split("."):
        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current


def _default_linux_manifest(repo_root: Path, archive_root: Path) -> Path:
    local = archive_root / "parity" / "linux_artifact_manifest.json"
    if local.exists():
        return local
    return repo_root / "docs" / "evidence_proof_archive_20260418" / "formal_10_4" / "parity" / "linux_artifact_manifest.json"


def _summary_path_from_linux_manifest(linux_manifest: dict[str, Any], group: str) -> Path | None:
    group_payload = dict((linux_manifest.get("groups") or {}).get(group) or {})
    path = group_payload.get("summary_path") or group_payload.get("path")
    return Path(str(path)).expanduser() if path else None


def _compare_group(group: str, linux_summary_path: Path, windows_summary_path: Path) -> dict[str, Any]:
    linux_summary = dict(json_load(linux_summary_path))
    windows_summary = dict(json_load(windows_summary_path))
    linux_fields = sorted(formal_parity_field_paths(linux_summary))
    windows_fields = sorted(formal_parity_field_paths(windows_summary))

    required_rows = []
    required_ok = True
    for field in REQUIRED_COMPARE_FIELDS:
        left = _get_path(linux_summary, field)
        right = _get_path(windows_summary, field)
        same = left == right
        required_ok = required_ok and same
        required_rows.append({"field": field, "linux": left, "windows": right, "same": same})

    metric_rows = []
    metric_diff_count = 0
    for metric_path in GROUP_METRIC_PATHS.get(group, []):
        left = _get_path(linux_summary, metric_path)
        right = _get_path(windows_summary, metric_path)
        same = left == right
        metric_diff_count += 0 if same else 1
        metric_rows.append({"metric": metric_path, "linux": left, "windows": right, "same": same})

    field_set_same = linux_fields == windows_fields
    if not required_ok or not field_set_same:
        status = "fail"
    elif metric_diff_count:
        status = "review"
    else:
        status = "pass"
    return {
        "group": group,
        "status": status,
        "linux_summary_path": str(linux_summary_path),
        "windows_summary_path": str(windows_summary_path),
        "required_rows": required_rows,
        "mandatory_field_set": {
            "same": field_set_same,
            "linux_field_count": len(linux_fields),
            "windows_field_count": len(windows_fields),
            "linux_only": sorted(set(linux_fields).difference(windows_fields)),
            "windows_only": sorted(set(windows_fields).difference(linux_fields)),
        },
        "metric_rows": metric_rows,
        "metric_diff_count": int(metric_diff_count),
    }


def build_report(*, linux_manifest_path: Path, windows_manifest_path: Path, output_path: Path) -> dict[str, Any]:
    missing_items: list[str] = []
    if not linux_manifest_path.exists():
        missing_items.append(f"linux manifest missing: {linux_manifest_path}")
    if not windows_manifest_path.exists():
        missing_items.append(f"windows manifest missing: {windows_manifest_path}")
    if missing_items:
        report = {
            "generated_at": _iso_now(),
            "status": "blocked",
            "linux_manifest": str(linux_manifest_path),
            "windows_manifest": str(windows_manifest_path),
            "missing_items": missing_items,
            "groups": [],
            "metric_diff_count": None,
            "mandatory_field_set_same": None,
            "ready_for_gate": False,
        }
        json_write(output_path, report)
        return report

    linux_manifest = dict(json_load(linux_manifest_path))
    windows_manifest = dict(json_load(windows_manifest_path))
    summary_paths = dict(windows_manifest.get("summary_paths") or windows_manifest.get("groups") or {})
    group_reports = []
    for group in ("A_control_plane_first", "B_budget_pre_freeze", "C_degraded_audit"):
        linux_summary_path = _summary_path_from_linux_manifest(linux_manifest, group)
        windows_summary_value = summary_paths.get(group)
        if linux_summary_path is None or not linux_summary_path.exists():
            missing_items.append(f"{group} linux summary missing: {linux_summary_path}")
            continue
        if not windows_summary_value:
            missing_items.append(f"{group} windows summary missing: <not set>")
            continue
        windows_summary_path = Path(str(windows_summary_value)).expanduser()
        if not windows_summary_path.exists() or not windows_summary_path.is_file():
            missing_items.append(f"{group} windows summary missing: {windows_summary_path}")
            continue
        group_reports.append(_compare_group(group, linux_summary_path, windows_summary_path))

    if missing_items:
        status = "blocked"
    elif any(row["status"] == "fail" for row in group_reports):
        status = "fail"
    elif any(row["status"] == "review" for row in group_reports):
        status = "review"
    else:
        status = "pass"
    metric_diff_count = sum(int(row.get("metric_diff_count") or 0) for row in group_reports)
    mandatory_field_set_same = bool(group_reports) and all(
        bool(dict(row.get("mandatory_field_set") or {}).get("same")) for row in group_reports
    )
    report = {
        "generated_at": _iso_now(),
        "status": status,
        "linux_manifest": str(linux_manifest_path),
        "windows_manifest": str(windows_manifest_path),
        "missing_items": missing_items,
        "groups": group_reports,
        "metric_diff_count": int(metric_diff_count) if group_reports else None,
        "mandatory_field_set_same": mandatory_field_set_same if group_reports else None,
        "ready_for_gate": status == "pass",
    }
    json_write(output_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_windows_formal_parity")
    repo_root = detect_repo_root()
    parser.add_argument("--archive-root", type=Path, default=default_archive_root(repo_root))
    parser.add_argument("--linux-manifest", type=Path)
    parser.add_argument("--windows-manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    archive_root = args.archive_root.expanduser().resolve()
    linux_manifest_path = (args.linux_manifest or _default_linux_manifest(repo_root, archive_root)).expanduser().resolve()
    windows_manifest_path = (args.windows_manifest or (archive_root / "windows_artifact_manifest.json")).expanduser().resolve()
    output_path = (args.output or (archive_root / "formal_parity_report.json")).expanduser().resolve()
    report = build_report(
        linux_manifest_path=linux_manifest_path,
        windows_manifest_path=windows_manifest_path,
        output_path=output_path,
    )
    print(f"status={report['status']} output={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
