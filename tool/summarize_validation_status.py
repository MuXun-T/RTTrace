from __future__ import annotations

import argparse
import json
from pathlib import Path


COLLECTOR_THROUGHPUT_THRESHOLD_EVENTS_PER_SEC = 1_000_000.0
EXPORT_FULL_SLA_THRESHOLD_SECONDS = 60.0
EXPORT_CLIPPED_SLA_THRESHOLD_SECONDS = 15.0
LONG_DURATION_FORMAL_THRESHOLD_SECONDS = 86_400.0


def _load_json(path: str) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _artifact_list(*paths: str | None) -> list[str]:
    return [path for path in paths if path]


def _limitation_list(node: dict[str, object]) -> list[str]:
    raw = node.get("limitations")
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


def _report_platform(node: dict[str, object]) -> str | None:
    platform_name = node.get("platform")
    if isinstance(platform_name, str) and platform_name.strip():
        return platform_name.strip().lower()
    return None


def _perf_required_keys() -> set[str]:
    return {
        "platform",
        "input_bytes",
        "input_scope",
        "acceptance_scope",
        "formal_input",
        "limitations",
        "first_screen_lt_10s",
        "peak_memory_lt_4gb",
        "first_screen_peak_memory_lt_4gb",
    }


def _perf_acceptance_node(report: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(report, dict):
        return {}
    node = report.get("desktop_perf_acceptance")
    if isinstance(node, dict):
        return node
    return {}


def _formal_input_node(node: dict[str, object]) -> dict[str, object]:
    formal_input = node.get("formal_input")
    if isinstance(formal_input, dict):
        return formal_input
    return {}


def _formal_input_complete(formal_input: dict[str, object]) -> bool:
    required = {
        "baseline_size_bytes",
        "candidate_size_bytes",
        "baseline_sha256",
        "candidate_sha256",
        "baseline_parse_ok",
        "candidate_parse_ok",
        "baseline_event_count",
        "candidate_event_count",
        "input_provenance",
        "formal_1gb_verified",
    }
    return required.issubset(formal_input)


def _formal_input_status(node: dict[str, object]) -> tuple[str, str]:
    formal_input = _formal_input_node(node)
    limitations = _limitation_list(node)
    if not _formal_input_complete(formal_input):
        return "manifest_incomplete", "formal input manifest incomplete"
    if formal_input.get("formal_1gb_verified") is True:
        return "formal_verified", "formal input verified"
    if formal_input.get("input_provenance") in {"public_rtos_derived", "public_rtos_seeded_padded"} or {
        "public_rtos_derived_input",
        "public_rtos_seeded_padded_input",
    }.intersection(limitations):
        return "public_rtos_prevalidation", "public RTOS derived input is suitable for pre-validation, but not final formal 1GB closure"
    if formal_input.get("input_provenance") == "synthetic_padded" or "synthetic_padded_input" in limitations:
        return "synthetic_padded", "synthetic padded input cannot satisfy formal 1GB validation"
    if "input_lt_1gb" in limitations:
        return "fixture_only", "input is smaller than 1GB"
    return "manifest_present_not_verified", "formal input manifest present but not verified"


def _perf_formal_ready(report: dict[str, object] | None) -> bool:
    if not isinstance(report, dict):
        return False
    node = _perf_acceptance_node(report)
    return report.get("mode") in {"desktop_perf_acceptance", "desktop_perf_acceptance_linux"} and _perf_required_keys().issubset(node)


def _perf_metric_state(report: dict[str, object] | None, metric_key: str) -> dict[str, object]:
    node = _perf_acceptance_node(report)
    if not _perf_formal_ready(report):
        return {
            "status": "missing",
            "platform": _report_platform(node),
            "input_scope": node.get("input_scope"),
            "verdict": node.get(metric_key),
            "notes": "desktop perf acceptance artifact is missing required fields",
        }

    formal_state, formal_notes = _formal_input_status(node)
    verdict = node.get(metric_key)
    pass_flag = verdict.get("pass") if isinstance(verdict, dict) else None
    if formal_state == "formal_verified" and pass_flag is True:
        status = "closed"
    elif formal_state == "formal_verified" and pass_flag is False:
        status = "failed"
    elif formal_state == "fixture_only":
        status = "fixture_only"
    else:
        status = "pending_external"
    return {
        "status": status,
        "platform": _report_platform(node),
        "input_scope": node.get("input_scope"),
        "verdict": verdict,
        "notes": formal_notes,
    }


def _perf_status_node(
    *,
    metric_key: str,
    linux_perf_path: str | None,
    windows_perf_path: str | None,
    linux_perf_report: dict[str, object] | None,
    windows_perf_report: dict[str, object] | None,
    fixture_note: str,
) -> dict[str, object]:
    linux_state = _perf_metric_state(linux_perf_report, metric_key)
    windows_state = _perf_metric_state(windows_perf_report, metric_key)
    states = {linux_state["status"], windows_state["status"]}

    if "closed" in states:
        status = "closed"
        remaining: list[str] = []
        notes = "已存在正式 1GB desktop perf artifact，当前门槛已留档。"
    elif "failed" in states:
        status = "failed"
        remaining = []
        notes = "已存在正式 1GB desktop perf artifact，但当前门槛未通过，需要继续优化或复测。"
    elif linux_state["status"] == "fixture_only" and windows_state["status"] == "fixture_only":
        status = "cross_platform_fixture_only"
        remaining = ["formal_1gb_desktop_input"]
        notes = "Linux 与 Windows 均已有 repo fixture 范围的 perf artifact，但仍不能替代正式 1GB 大输入验收。"
    elif linux_state["status"] == "fixture_only":
        status = "linux_formal_fixture_only"
        remaining = ["formal_1gb_desktop_input"]
        notes = fixture_note
    elif windows_state["status"] == "fixture_only":
        status = "windows_formal_fixture_only"
        remaining = ["formal_1gb_desktop_input"]
        notes = "Windows 已有 formal desktop perf artifact，但当前输入仍小于 1GB，不能替代正式大输入验收。"
    elif linux_state["status"] == "pending_external" or windows_state["status"] == "pending_external":
        status = "pending_external"
        remaining = ["formal_1gb_desktop_input"]
        notes = linux_state["notes"] if linux_state["status"] == "pending_external" else windows_state["notes"]
    else:
        status = "pending_external"
        remaining = ["formal_1gb_desktop_input"]
        notes = "当前尚未形成正式 1GB desktop perf artifact。"

    return {
        "status": status,
        "linux_artifacts": _artifact_list(linux_perf_path),
        "windows_artifacts": _artifact_list(windows_perf_path),
        "current_scope": linux_state["input_scope"] or windows_state["input_scope"],
        "windows_scope": windows_state["input_scope"],
        "linux_verdict": linux_state["verdict"],
        "windows_verdict": windows_state["verdict"],
        "remaining": remaining,
        "notes": notes,
    }


def _runtime_ready(runtime_report: dict[str, object] | None) -> bool:
    return bool(isinstance(runtime_report, dict) and runtime_report.get("runtime_contract_ok"))


def _runtime_error(
    runtime_report: dict[str, object] | None,
    env_report: dict[str, object] | None = None,
    preflight_report: dict[str, object] | None = None,
) -> str | None:
    ready = _runtime_ready(runtime_report)
    if isinstance(runtime_report, dict) and not ready:
        failure_summary = runtime_report.get("failure_summary")
        if isinstance(failure_summary, str) and failure_summary:
            return failure_summary
    if isinstance(env_report, dict):
        env_error = env_report.get("error")
        if isinstance(env_error, str) and env_error:
            return env_error
    if isinstance(preflight_report, dict) and not preflight_report.get("ready_for_runtime_smoke"):
        return "desktop preflight is not ready for runtime smoke"
    return None


def _collector_ready(report: dict[str, object] | None) -> bool:
    return bool(
        isinstance(report, dict)
        and report.get("status") == "ok"
        and isinstance(report.get("scenario"), dict)
    )


def _desktop_soak_state(report: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(report, dict):
        return {"ready": False, "duration_sec": None, "status": None}
    duration = _float_or_none(report.get("duration_sec"))
    status = report.get("status") if isinstance(report.get("status"), str) else None
    ready = bool(status == "ok" and duration is not None and duration > 0)
    return {"ready": ready, "duration_sec": duration, "status": status}


def _desktop_soak_24h_passed(state: dict[str, object]) -> bool:
    duration = _float_or_none(state.get("duration_sec"))
    status = state.get("status")
    return bool(status == "ok" and duration is not None and duration >= LONG_DURATION_FORMAL_THRESHOLD_SECONDS)


def _irrecoverable_error_state(report: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(report, dict):
        return {"observed": False, "duration_sec": None, "detected": None, "last_error_summary": None}
    duration = _float_or_none(report.get("duration_sec"))
    detected = (
        report.get("irrecoverable_error_detected")
        if isinstance(report.get("irrecoverable_error_detected"), bool)
        else None
    )
    last_summary = report.get("last_error_summary") if isinstance(report.get("last_error_summary"), str) else None
    observed = bool(duration is not None and duration > 0 and detected is not None)
    return {
        "observed": observed,
        "duration_sec": duration,
        "detected": detected,
        "last_error_summary": last_summary,
    }


def _compare_match(report: dict[str, object] | None) -> bool | None:
    if not isinstance(report, dict):
        return None
    match = report.get("match")
    if isinstance(match, bool):
        return match
    return None


def _float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _export_duration_metric(
    durations: dict[str, object],
    *,
    preferred_key: str,
    fallback_key: str,
) -> tuple[float | None, str | None, bool]:
    preferred_declared = preferred_key in durations
    preferred = _float_or_none(durations.get(preferred_key))
    if preferred_declared:
        return preferred, preferred_key if preferred is not None else None, True
    fallback = _float_or_none(durations.get(fallback_key))
    return fallback, fallback_key if fallback is not None else None, False


def _report_platform_from_environment(report: dict[str, object] | None) -> str | None:
    if not isinstance(report, dict):
        return None
    env = report.get("environment")
    if not isinstance(env, dict):
        return None
    platform_name = env.get("platform")
    if isinstance(platform_name, str) and platform_name.strip():
        return platform_name.strip().lower()
    return None


def _nfr_perf01_collector_throughput(
    *,
    collector_perf_path: str | None,
    collector_perf_report: dict[str, object] | None,
) -> dict[str, object]:
    if not isinstance(collector_perf_report, dict):
        return {
            "status": "missing",
            "artifacts": _artifact_list(collector_perf_path),
            "platform": None,
            "threshold_events_per_sec": COLLECTOR_THROUGHPUT_THRESHOLD_EVENTS_PER_SEC,
            "scenario_verdicts": [],
            "remaining": ["linux_collector_perf_baseline", "windows_collector_perf_baseline"],
            "notes": "collector perf baseline artifact 尚未导入。",
        }

    scenarios = collector_perf_report.get("scenarios")
    if not isinstance(scenarios, list):
        return {
            "status": "missing",
            "artifacts": _artifact_list(collector_perf_path),
            "platform": _report_platform_from_environment(collector_perf_report),
            "threshold_events_per_sec": COLLECTOR_THROUGHPUT_THRESHOLD_EVENTS_PER_SEC,
            "scenario_verdicts": [],
            "remaining": ["linux_collector_perf_baseline", "windows_collector_perf_baseline"],
            "notes": "collector perf baseline artifact 结构不完整。",
        }

    platform_name = _report_platform_from_environment(collector_perf_report)
    verdicts: list[dict[str, object]] = []
    all_pass = True
    for raw in scenarios:
        if not isinstance(raw, dict):
            continue
        scenario_name = raw.get("scenario")
        events_per_sec = _float_or_none(raw.get("events_per_sec"))
        pass_flag = bool(
            events_per_sec is not None
            and events_per_sec >= COLLECTOR_THROUGHPUT_THRESHOLD_EVENTS_PER_SEC
        )
        all_pass = all_pass and pass_flag
        verdicts.append(
            {
                "scenario": scenario_name,
                "events_per_sec": events_per_sec,
                "threshold_events_per_sec": COLLECTOR_THROUGHPUT_THRESHOLD_EVENTS_PER_SEC,
                "pass": pass_flag,
            }
        )

    if not verdicts:
        return {
            "status": "missing",
            "artifacts": _artifact_list(collector_perf_path),
            "platform": platform_name,
            "threshold_events_per_sec": COLLECTOR_THROUGHPUT_THRESHOLD_EVENTS_PER_SEC,
            "scenario_verdicts": [],
            "remaining": ["linux_collector_perf_baseline", "windows_collector_perf_baseline"],
            "notes": "collector perf baseline 未包含可用的 scenario 列表。",
        }

    if not all_pass:
        return {
            "status": "failed",
            "artifacts": _artifact_list(collector_perf_path),
            "platform": platform_name,
            "threshold_events_per_sec": COLLECTOR_THROUGHPUT_THRESHOLD_EVENTS_PER_SEC,
            "scenario_verdicts": verdicts,
            "remaining": [],
            "notes": "collector throughput 未达到门槛，需要优化或复测。",
        }

    if platform_name == "linux":
        status = "linux_evidence_only"
        remaining = ["windows_collector_perf_baseline"]
        notes = "Linux 侧已留档 collector throughput evidence；Windows 侧仍待补齐。"
    elif platform_name == "windows":
        status = "windows_evidence_only"
        remaining = ["linux_collector_perf_baseline"]
        notes = "Windows 侧已留档 collector throughput evidence；Linux 侧仍待补齐。"
    else:
        status = "evidence_only"
        remaining = ["linux_collector_perf_baseline", "windows_collector_perf_baseline"]
        notes = "collector throughput evidence 已留档，但平台信息不完整。"

    return {
        "status": status,
        "artifacts": _artifact_list(collector_perf_path),
        "platform": platform_name,
        "threshold_events_per_sec": COLLECTOR_THROUGHPUT_THRESHOLD_EVENTS_PER_SEC,
        "scenario_verdicts": verdicts,
        "remaining": remaining,
        "notes": notes,
    }


def _nfr_perf01_collector_throughput_aggregate(
    *,
    linux_collector_perf_path: str | None,
    linux_collector_perf_report: dict[str, object] | None,
    windows_collector_perf_path: str | None,
    windows_collector_perf_report: dict[str, object] | None,
) -> dict[str, object]:
    linux_node = _nfr_perf01_collector_throughput(
        collector_perf_path=linux_collector_perf_path,
        collector_perf_report=linux_collector_perf_report,
    )
    windows_node = _nfr_perf01_collector_throughput(
        collector_perf_path=windows_collector_perf_path,
        collector_perf_report=windows_collector_perf_report,
    )

    if linux_node.get("status") == "failed" or windows_node.get("status") == "failed":
        status = "failed"
        remaining: list[str] = []
        notes = "collector throughput evidence 已导入，但至少一侧未达到门槛。"
    else:
        pass_states = {"linux_evidence_only", "windows_evidence_only", "evidence_only"}
        linux_pass = linux_node.get("status") in pass_states
        windows_pass = windows_node.get("status") in pass_states
        platform_metadata_ok = (
            linux_node.get("platform") == "linux"
            and windows_node.get("platform") == "windows"
        )
        if linux_pass and windows_pass:
            if platform_metadata_ok:
                status = "closed"
                remaining = []
                notes = "Linux + Windows collector throughput evidence 已齐备且平台元信息正确，当前门槛已关闭。"
            else:
                status = "cross_platform_evidence_ready"
                remaining = ["collector_perf_platform_metadata"]
                notes = "Linux + Windows collector throughput evidence 已聚合，但平台元信息不完整或不一致，暂不判定 closed。"
        elif linux_pass:
            status = "linux_evidence_only"
            remaining = ["windows_collector_perf_baseline"]
            notes = "Linux 侧已留档 collector throughput evidence；Windows 侧仍待补齐。"
        elif windows_pass:
            status = "windows_evidence_only"
            remaining = ["linux_collector_perf_baseline"]
            notes = "Windows 侧已留档 collector throughput evidence；Linux 侧仍待补齐。"
        else:
            status = "missing"
            remaining = ["linux_collector_perf_baseline", "windows_collector_perf_baseline"]
            notes = "collector perf baseline artifact 尚未形成可用的双侧证据。"

    verdicts: list[dict[str, object]] = []
    for raw in linux_node.get("scenario_verdicts") or []:
        if isinstance(raw, dict):
            verdicts.append({**raw, "platform": "linux"})
    for raw in windows_node.get("scenario_verdicts") or []:
        if isinstance(raw, dict):
            verdicts.append({**raw, "platform": "windows"})

    artifacts: list[str] = []
    artifacts.extend(_artifact_list(linux_collector_perf_path))
    artifacts.extend(_artifact_list(windows_collector_perf_path))

    per_platform = {
        "linux": linux_node,
        "windows": windows_node,
    }
    if status == "closed":
        per_platform = {
            "linux": {
                **linux_node,
                "status": "closed",
                "remaining": [],
                "notes": "Linux 侧 collector throughput evidence 已纳入 cross-platform closed 聚合。",
            },
            "windows": {
                **windows_node,
                "status": "closed",
                "remaining": [],
                "notes": "Windows 侧 collector throughput evidence 已纳入 cross-platform closed 聚合。",
            },
        }

    return {
        "status": status,
        "artifacts": artifacts,
        "platform": (
            "cross_platform"
            if status in {"closed", "cross_platform_evidence_ready"}
            else (linux_node.get("platform") or windows_node.get("platform"))
        ),
        "threshold_events_per_sec": COLLECTOR_THROUGHPUT_THRESHOLD_EVENTS_PER_SEC,
        "scenario_verdicts": verdicts,
        "remaining": remaining,
        "notes": notes,
        "per_platform": per_platform,
    }


def _nfr_perf06_export_sla(
    *,
    desktop_perf_path: str | None,
    desktop_perf_report: dict[str, object] | None,
) -> dict[str, object]:
    if not isinstance(desktop_perf_report, dict):
        return {
            "status": "missing",
            "artifacts": _artifact_list(desktop_perf_path),
            "scope": {},
            "verdict": {},
            "remaining": ["desktop_perf_acceptance_artifact"],
            "notes": "desktop perf artifact 尚未导入，无法评估导出 SLA。",
        }

    durations = desktop_perf_report.get("durations")
    if not isinstance(durations, dict):
        return {
            "status": "missing",
            "artifacts": _artifact_list(desktop_perf_path),
            "scope": {},
            "verdict": {},
            "remaining": ["desktop_perf_acceptance_artifact"],
            "notes": "desktop perf artifact 未包含 durations，无法评估导出 SLA。",
        }

    export_write, export_write_source, export_v2_declared = _export_duration_metric(
        durations,
        preferred_key="export_write_seconds",
        fallback_key="export_full_seconds",
    )
    export_clipped_metric, export_clipped_source, export_clipped_v2_declared = _export_duration_metric(
        durations,
        preferred_key="export_clipped_write_seconds",
        fallback_key="export_clipped_seconds",
    )
    export_full = _float_or_none(durations.get("export_full_seconds"))
    export_normalize = _float_or_none(durations.get("export_normalize_seconds"))
    export_clipped_total = _float_or_none(durations.get("export_clipped_seconds"))
    export_clipped_write = _float_or_none(durations.get("export_clipped_write_seconds"))
    export_clipped_normalize = _float_or_none(durations.get("export_clipped_normalize_seconds"))
    desktop_perf = desktop_perf_report.get("desktop_perf") if isinstance(desktop_perf_report, dict) else {}
    export_probe_skipped = bool(desktop_perf.get("export_probe_skipped")) if isinstance(desktop_perf, dict) else False
    if export_write_source is None or export_clipped_source is None:
        if export_probe_skipped:
            notes = "desktop perf artifact 已声明 v2 export 计量，但当前 export write path 仍被跳过，无法评估导出 SLA。"
        elif export_v2_declared or export_clipped_v2_declared:
            notes = "desktop perf durations 使用 v2 export 合同，但缺少 export_write_seconds/export_clipped_write_seconds。"
        else:
            notes = "desktop perf durations 缺失导出耗时字段，无法评估导出 SLA。"
        return {
            "status": "missing",
            "artifacts": _artifact_list(desktop_perf_path),
            "scope": {},
            "verdict": {},
            "remaining": ["desktop_perf_acceptance_artifact"],
            "notes": notes,
        }

    acceptance = _perf_acceptance_node(desktop_perf_report)
    limitations = _limitation_list(acceptance) if acceptance else []
    formal_state, formal_notes = _formal_input_status(acceptance) if acceptance else ("manifest_incomplete", "formal input manifest incomplete")
    export_write_pass = export_write <= EXPORT_FULL_SLA_THRESHOLD_SECONDS
    clipped_pass = export_clipped_metric <= EXPORT_CLIPPED_SLA_THRESHOLD_SECONDS
    verdict = {
        "export_write_source": export_write_source,
        "export_write_seconds": export_write,
        "export_write_threshold_seconds": EXPORT_FULL_SLA_THRESHOLD_SECONDS,
        "export_write_pass": export_write_pass,
        "export_normalize_seconds": export_normalize,
        "export_full_seconds": export_full,
        "export_full_semantics": "observed_only_includes_normalize_seconds",
        "export_clipped_source": export_clipped_source,
        "export_clipped_metric_seconds": export_clipped_metric,
        "export_clipped_write_seconds": export_clipped_write,
        "export_clipped_normalize_seconds": export_clipped_normalize,
        "export_clipped_seconds": export_clipped_total,
        "export_clipped_threshold_seconds": EXPORT_CLIPPED_SLA_THRESHOLD_SECONDS,
        "export_clipped_pass": clipped_pass,
        "pass": bool(export_write_pass and clipped_pass),
    }
    scope = {
        "platform": _report_platform(acceptance) if acceptance else None,
        "input_bytes": acceptance.get("input_bytes") if acceptance else None,
        "input_scope": acceptance.get("input_scope") if acceptance else None,
        "acceptance_scope": acceptance.get("acceptance_scope") if acceptance else None,
        "formal_input_verified": (
            _formal_input_node(acceptance).get("formal_1gb_verified")
            if acceptance
            else None
        ),
        "limitations": limitations,
    }

    if formal_state == "fixture_only":
        return {
            "status": "fixture_only",
            "artifacts": _artifact_list(desktop_perf_path),
            "scope": scope,
            "verdict": verdict,
            "remaining": ["formal_1gb_desktop_input"],
            "notes": "当前仅为 repo fixture 范围导出耗时观测，不能替代 1GB 正式导出 SLA 验收。",
        }

    if formal_state != "formal_verified":
        return {
            "status": "pending_external",
            "artifacts": _artifact_list(desktop_perf_path),
            "scope": scope,
            "verdict": verdict,
            "remaining": ["formal_1gb_desktop_input"],
            "notes": formal_notes,
        }

    if verdict["pass"] is True:
        return {
            "status": "closed",
            "artifacts": _artifact_list(desktop_perf_path),
            "scope": scope,
            "verdict": verdict,
            "remaining": [],
            "notes": "导出 SLA 门槛已通过且输入范围满足正式要求。",
        }

    return {
        "status": "failed",
        "artifacts": _artifact_list(desktop_perf_path),
        "scope": scope,
        "verdict": verdict,
        "remaining": [],
        "notes": "导出 SLA 门槛未通过，需要优化或复测。",
    }


def _nfr_perf05_fps_status() -> dict[str, object]:
    return {
        "status": "missing_artifact",
        "artifacts": [],
        "remaining": ["formal_fps_report_artifact"],
        "notes": "仓库当前缺少独立 FPS formal report；待 WP-08-02 补齐。",
    }


def _normalize_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip().lower()
    return ""


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _render_qpa_platform(
    *,
    environment_node: dict[str, object],
    execution_node: dict[str, object],
    limitations: list[str],
) -> str | None:
    execution_qpa = execution_node.get("qt_qpa_platform")
    if isinstance(execution_qpa, str) and execution_qpa.strip():
        return execution_qpa.strip().lower()
    env_qpa = environment_node.get("qt_qpa_platform")
    if isinstance(env_qpa, str) and env_qpa.strip():
        return env_qpa.strip().lower()
    for item in limitations:
        lowered = _normalize_text(item)
        if lowered.startswith("qpa:") and len(lowered) > 4:
            return lowered[4:]
    return None


def _has_legacy_onscreen_evidence(
    *,
    execution_node: dict[str, object],
    notes: list[str],
) -> bool:
    if execution_node.get("onscreen_verified") is True:
        return True
    for note in notes:
        lowered = _normalize_text(note)
        if "onscreen execution detected" in lowered:
            return True
    return False


def _blocking_render_limitations(
    *,
    limitations: list[str],
    scope_supports_formal: bool,
    onscreen_verified: bool,
) -> list[str]:
    blocked: list[str] = []
    for item in limitations:
        lowered = _normalize_text(item)
        if not lowered:
            continue
        if lowered.startswith("qpa:"):
            # qpa:* is informational and should not be a hard block by itself.
            continue
        if lowered == "synthetic_workload" and scope_supports_formal and onscreen_verified:
            # Legacy reports may carry synthetic_workload even when notes/execution prove onscreen evidence.
            continue
        blocked.append(lowered)
    return blocked


def _nfr_perf05_render_fps(
    *,
    render_fps_path: str | None,
    render_fps_report: dict[str, object] | None,
) -> dict[str, object]:
    if not render_fps_path or not isinstance(render_fps_report, dict):
        return _nfr_perf05_fps_status()

    status = render_fps_report.get("status")
    if not isinstance(status, str):
        status = "error"
    status = status.strip().lower()

    workload = render_fps_report.get("workload")
    workload_node = workload if isinstance(workload, dict) else {}
    metrics = render_fps_report.get("metrics")
    metrics_node = metrics if isinstance(metrics, dict) else {}
    thresholds = render_fps_report.get("thresholds")
    thresholds_node = thresholds if isinstance(thresholds, dict) else {}
    verdict = render_fps_report.get("verdict")
    verdict_node = verdict if isinstance(verdict, dict) else {}
    environment = render_fps_report.get("environment")
    environment_node = environment if isinstance(environment, dict) else {}
    execution = render_fps_report.get("execution")
    execution_node = execution if isinstance(execution, dict) else {}
    notes = _string_list(render_fps_report.get("notes"))
    evidence_scope = _normalize_text(render_fps_report.get("evidence_scope"))

    raw_limitations = render_fps_report.get("limitations")
    limitations = [str(item) for item in raw_limitations] if isinstance(raw_limitations, list) else []
    limitation_set = {_normalize_text(item) for item in limitations}

    tile_count = int(workload_node.get("tile_count") or 0)
    target_fps = float(thresholds_node.get("target_fps") or 30.0)
    frame_budget_ms = float(thresholds_node.get("frame_budget_ms") or 16.0)
    fps_p95 = float(metrics_node.get("fps_p95") or 0.0)
    pass_flag = verdict_node.get("pass")
    pass_bool = bool(pass_flag) if isinstance(pass_flag, bool) else None
    qpa_platform = _render_qpa_platform(
        environment_node=environment_node,
        execution_node=execution_node,
        limitations=limitations,
    )
    offscreen = bool(
        qpa_platform == "offscreen"
        or "offscreen" in limitation_set
        or "qpa:offscreen" in limitation_set
    )
    legacy_scope = evidence_scope == ""
    formal_scope = evidence_scope == "formal_gui_onscreen"
    scope_supports_formal = bool(formal_scope or legacy_scope)
    legacy_onscreen_inferred = bool(
        legacy_scope
        and _has_legacy_onscreen_evidence(
            execution_node=execution_node,
            notes=notes,
        )
        and not offscreen
    )
    execution_onscreen = execution_node.get("onscreen_verified") is True
    onscreen_verified = bool(
        not offscreen and (execution_onscreen or legacy_onscreen_inferred)
    )
    blocking_limitations = _blocking_render_limitations(
        limitations=limitations,
        scope_supports_formal=scope_supports_formal,
        onscreen_verified=onscreen_verified,
    )

    if status != "ok":
        return {
            "status": "artifact_error",
            "artifacts": _artifact_list(render_fps_path),
            "workload": workload_node,
            "thresholds": {"target_fps": target_fps, "frame_budget_ms": frame_budget_ms},
            "metrics": metrics_node,
            "limitations": limitations,
            "blocking_limitations": blocking_limitations,
            "evidence_scope": evidence_scope or "legacy",
            "execution": {
                "qt_qpa_platform": qpa_platform,
                "onscreen_verified": onscreen_verified,
                "legacy_onscreen_inferred": legacy_onscreen_inferred,
            },
            "verdict": {"pass": False, "reason": status},
            "remaining": ["formal_fps_report_artifact"],
            "notes": "FPS report 存在但未成功生成，需在具备 GUI 依赖的环境复跑。",
        }

    remaining: list[str] = []
    formal_scale_covered = tile_count >= 100000
    if not formal_scale_covered:
        remaining.append("render_fps_100k_tiles")

    if not scope_supports_formal or not onscreen_verified or blocking_limitations:
        remaining.append("formal_gui_fps_report")

    if pass_bool is not True:
        remaining.append("fps_target_not_met_or_not_reported")

    # Never over-claim: only allow closed when scale is covered, verdict is pass,
    # scope supports formal evidence, onscreen evidence is verified, and no blocking limitations remain.
    if (
        formal_scale_covered
        and pass_bool is True
        and fps_p95 >= target_fps
        and scope_supports_formal
        and onscreen_verified
        and not blocking_limitations
    ):
        return {
            "status": "closed",
            "artifacts": _artifact_list(render_fps_path),
            "workload": workload_node,
            "thresholds": {"target_fps": target_fps, "frame_budget_ms": frame_budget_ms},
            "metrics": metrics_node,
            "limitations": limitations,
            "blocking_limitations": blocking_limitations,
            "evidence_scope": evidence_scope or "legacy",
            "execution": {
                "qt_qpa_platform": qpa_platform,
                "onscreen_verified": onscreen_verified,
                "legacy_onscreen_inferred": legacy_onscreen_inferred,
            },
            "verdict": {"target_fps": target_fps, "fps_p95": fps_p95, "pass": True},
            "remaining": [],
            "notes": "FPS 门槛已满足且证据范围满足正式要求。",
        }

    return {
        "status": "linux_evidence_only",
        "artifacts": _artifact_list(render_fps_path),
        "workload": workload_node,
        "thresholds": {"target_fps": target_fps, "frame_budget_ms": frame_budget_ms},
        "metrics": metrics_node,
        "limitations": limitations,
        "blocking_limitations": blocking_limitations,
        "evidence_scope": evidence_scope or "legacy",
        "execution": {
            "qt_qpa_platform": qpa_platform,
            "onscreen_verified": onscreen_verified,
            "legacy_onscreen_inferred": legacy_onscreen_inferred,
        },
        "verdict": {"target_fps": target_fps, "fps_p95": fps_p95, "pass": bool(fps_p95 >= target_fps)},
        "remaining": remaining or ["formal_fps_report_artifact"],
        "notes": "已生成 FPS 报告但仍受 limitations/规模范围约束，不能裁定为正式达标。",
    }


def _desktop_dense_blocker_status(
    *,
    desktop_dense_blocker_path: str | None,
    desktop_dense_blocker_report: dict[str, object] | None,
) -> dict[str, object] | None:
    if not desktop_dense_blocker_path or not isinstance(desktop_dense_blocker_report, dict):
        return None

    load_breakdown = desktop_dense_blocker_report.get("load_breakdown")
    load_breakdown_node = load_breakdown if isinstance(load_breakdown, dict) else {}
    error = desktop_dense_blocker_report.get("error")
    error_node = error if isinstance(error, dict) else {}
    scratch = desktop_dense_blocker_report.get("scratch")
    scratch_node = scratch if isinstance(scratch, dict) else {}
    memory_guard = desktop_dense_blocker_report.get("memory_guard")
    memory_guard_node = memory_guard if isinstance(memory_guard, dict) else {}
    traceback_text = error_node.get("traceback")
    traceback_value = str(traceback_text) if isinstance(traceback_text, str) else ""
    blocked_stage = desktop_dense_blocker_report.get("blocked_stage")
    last_completed_stage = desktop_dense_blocker_report.get("last_completed_stage")
    alignment_rescan_in_traceback = "_source_alignment_offsets" in traceback_value
    codec_chunk_copy_in_traceback = "chunk_payload = bytes(" in traceback_value
    normalize_rebuild_bundle_reload_in_traceback = (
        "raw_bundle = json_load(rebuild_path)" in traceback_value
        or (
            "_load_validated_package" in traceback_value
            and "_validate_package_refs" in traceback_value
            and "json.load(handle)" in traceback_value
        )
    )

    notes = "dense blocker artifact 已导入。"
    if blocked_stage == "export_write":
        notes = "dense blocker 当前固定在 export_write。"
        if alignment_rescan_in_traceback or codec_chunk_copy_in_traceback:
            notes = (
                "dense blocker 当前固定在 export_write；traceback 指向 source alignment 复扫链路，"
                "并继续经过 parser/codec.py 的 chunk bytes() 复制。"
            )
    elif blocked_stage == "export_normalize_sidecar":
        notes = "dense blocker 已前移到 export_normalize_sidecar。"
        if last_completed_stage == "export_write":
            notes = "dense blocker 已前移到 export_normalize_sidecar；export_write 已通过。"
        if normalize_rebuild_bundle_reload_in_traceback:
            notes = (
                "dense blocker 已前移到 export_normalize_sidecar；export_write 已通过，"
                "traceback 指向 json_load(rebuild_bundle.json) 回读链路。"
            )

    return {
        "status": desktop_dense_blocker_report.get("status"),
        "artifact": desktop_dense_blocker_path,
        "blocked_stage": blocked_stage,
        "last_completed_stage": last_completed_stage,
        "load_last_completed_stage": load_breakdown_node.get("last_completed_stage"),
        "error_type": error_node.get("type"),
        "scratch_fs_type": scratch_node.get("fs_type"),
        "memory_guard_limit_mb": memory_guard_node.get("limit_mb"),
        "alignment_rescan_in_traceback": alignment_rescan_in_traceback,
        "codec_chunk_copy_in_traceback": codec_chunk_copy_in_traceback,
        "normalize_rebuild_bundle_reload_in_traceback": normalize_rebuild_bundle_reload_in_traceback,
        "notes": notes,
    }


def _build_external_status(
    *,
    acceptance_path: str,
    desktop_runtime_path: str | None,
    desktop_perf_path: str | None,
    collector_soak_path: str | None,
    desktop_soak_path: str | None,
    irrecoverable_error_path: str | None,
    acceptance: dict[str, object],
    desktop_perf_report: dict[str, object] | None,
    collector_soak: dict[str, object] | None,
    desktop_soak: dict[str, object] | None,
    irrecoverable_error: dict[str, object] | None,
    windows_acceptance_path: str | None,
    windows_desktop_runtime_path: str | None,
    windows_desktop_perf_path: str | None,
    windows_collector_soak_path: str | None,
    windows_desktop_soak_path: str | None,
    windows_irrecoverable_error_path: str | None,
    windows_acceptance: dict[str, object] | None,
    windows_desktop_perf_report: dict[str, object] | None,
    windows_collector_soak: dict[str, object] | None,
    windows_desktop_soak: dict[str, object] | None,
    windows_irrecoverable_error: dict[str, object] | None,
    acceptance_compare_path: str | None,
    acceptance_compare: dict[str, object] | None,
    package_compare_path: str | None,
    package_compare: dict[str, object] | None,
) -> dict[str, object]:
    collector_scenario = collector_soak.get("scenario", {}) if isinstance(collector_soak, dict) else {}
    collector_duration = collector_scenario.get("duration_sec")
    windows_collector_scenario = (
        windows_collector_soak.get("scenario", {}) if isinstance(windows_collector_soak, dict) else {}
    )
    windows_collector_duration = windows_collector_scenario.get("duration_sec")
    short_soak_passed = bool(acceptance.get("consistency", {}).get("short_soak_passed"))
    windows_short_soak_passed = bool(
        isinstance(windows_acceptance, dict)
        and windows_acceptance.get("consistency", {}).get("short_soak_passed")
    )

    acceptance_compare_match = _compare_match(acceptance_compare)
    package_compare_match = _compare_match(package_compare)
    windows_artifacts = _artifact_list(
        windows_acceptance_path,
        windows_desktop_runtime_path,
        windows_desktop_perf_path,
    )
    compare_artifacts = _artifact_list(acceptance_compare_path, package_compare_path)
    missing_consistency = []
    if not windows_acceptance_path:
        missing_consistency.append("windows_acceptance_baseline")
    if not windows_desktop_runtime_path:
        missing_consistency.append("windows_desktop_runtime_report")
    if not windows_desktop_perf_path:
        missing_consistency.append("windows_desktop_perf_report")
    if acceptance_compare_path is None:
        missing_consistency.append("acceptance_baseline_compare")
    elif acceptance_compare_match is not True:
        missing_consistency.append("acceptance_baseline_compare_resolution")
    if package_compare_path is None:
        missing_consistency.append("normalized_package_compare")
    elif package_compare_match is not True:
        missing_consistency.append("normalized_package_compare_resolution")

    if not windows_artifacts:
        consistency_status = "linux_evidence_only"
        consistency_notes = "Linux 侧 evidence 已存在，但缺 Windows 同口径报告与双平台比较结果。"
    elif acceptance_compare_match is False or package_compare_match is False:
        consistency_status = "mismatch_detected"
        consistency_notes = "Windows 与 Linux 报告已生成，但比较结果存在差异，需要先定位不一致再收口。"
    elif not missing_consistency:
        consistency_status = "closed"
        consistency_notes = "Windows 与 Linux 同口径报告及比较结果已齐备。"
    elif all(
        path
        for path in (
            windows_acceptance_path,
            windows_desktop_runtime_path,
            windows_desktop_perf_path,
        )
    ):
        consistency_status = "cross_platform_reports_ready_pending_compare"
        consistency_notes = "Windows 与 Linux 报告均已齐备，但双平台比较结果仍待补齐。"
    else:
        consistency_status = "cross_platform_partial"
        consistency_notes = "Windows 侧已有部分报告，但尚未达到可完成双平台一致性比对的最小集合。"

    desktop_soak_state = _desktop_soak_state(desktop_soak)
    irrecoverable_state = _irrecoverable_error_state(irrecoverable_error)
    windows_desktop_soak_state = _desktop_soak_state(windows_desktop_soak)
    windows_irrecoverable_state = _irrecoverable_error_state(windows_irrecoverable_error)
    windows_24h_passed = _desktop_soak_24h_passed(windows_desktop_soak_state)

    long_duration_remaining = ["windows_desktop_soak_24h", "windows_irrecoverable_error_observation"]
    if windows_24h_passed:
        long_duration_remaining = [item for item in long_duration_remaining if item != "windows_desktop_soak_24h"]
    if windows_irrecoverable_state["observed"]:
        long_duration_remaining = [
            item for item in long_duration_remaining if item != "windows_irrecoverable_error_observation"
        ]

    if (short_soak_passed or collector_soak is not None) and windows_collector_soak is not None:
        long_duration_status = "cross_platform_short_soak_only"
        long_duration_notes = "Linux 与 Windows 均已有短时 soak / collector baseline，但尚未形成长时稳定性正式留档。"
    elif short_soak_passed or collector_soak is not None:
        long_duration_status = "linux_short_soak_only"
        long_duration_notes = "当前只有 Linux 短时 soak 与 collector baseline，尚未形成长时稳定性正式留档。"
    elif windows_short_soak_passed or windows_collector_soak is not None:
        long_duration_status = "windows_short_soak_only"
        long_duration_notes = "当前只有 Windows 短时 soak / collector baseline，尚未形成长时稳定性正式留档。"
    else:
        long_duration_status = "pending_external"
        long_duration_notes = "当前尚未形成长稳验证的基础产物。"

    linux_long_duration_ready = bool(
        desktop_soak_state["ready"]
        and irrecoverable_state["observed"]
        and irrecoverable_state["detected"] is False
    )
    linux_irrecoverable_detected = bool(
        irrecoverable_state["observed"]
        and irrecoverable_state["detected"] is True
    )
    windows_irrecoverable_detected = bool(
        windows_irrecoverable_state["observed"]
        and windows_irrecoverable_state["detected"] is True
    )
    if windows_irrecoverable_detected:
        # Observation exists and indicates a hard failure; do not claim stability.
        long_duration_status = "irrecoverable_error_detected"
        long_duration_notes = "Windows 24h 长稳观测已留档，但检测到不可恢复错误，需要先定位/修复再继续收口。"
    elif windows_24h_passed and windows_irrecoverable_state["observed"] and windows_irrecoverable_state["detected"] is False:
        long_duration_status = "closed"
        long_duration_notes = (
            "NFR-STAB-01 按当前项目级正式口径已闭环：Windows desktop long soak 达到 24h 且不可恢复错误观测为 false；"
            "collector soak 作为 supporting evidence 留档，不作为合同关闭门槛。"
        )
    elif linux_irrecoverable_detected:
        long_duration_status = "irrecoverable_error_detected"
        long_duration_notes = "Linux 长稳观测已留档，但检测到不可恢复错误，需要先定位/修复再继续收口。"
    elif linux_long_duration_ready and windows_collector_soak is not None:
        long_duration_status = "cross_platform_long_soak_evidence_ready"
        long_duration_notes = (
            "Linux 与 Windows 均已有长稳/collector supporting evidence；"
            "但 NFR-STAB-01 合同关闭仍以 Windows 24h + irrecoverable=false 的正式门槛判定。"
        )
    elif linux_long_duration_ready:
        long_duration_status = "linux_long_soak_evidence_ready_pending_windows"
        long_duration_notes = (
            "Linux 已具备桌面长稳与不可恢复错误观测留档；"
            "NFR-STAB-01 合同关闭仍需 Windows 24h long soak + irrecoverable=false。"
        )
    elif windows_desktop_soak_state["ready"] and not windows_24h_passed:
        long_duration_status = "windows_long_soak_duration_not_24h"
        long_duration_notes = "Windows desktop long soak 已有留档，但时长未达到 24h 正式门槛。"
    elif windows_24h_passed and not windows_irrecoverable_state["observed"]:
        long_duration_status = "windows_irrecoverable_observation_missing"
        long_duration_notes = "Windows 24h long soak 已有留档，但缺不可恢复错误观测结果。"

    external_status = {
        "desktop_1gb_first_screen_lt_10s": _perf_status_node(
            metric_key="first_screen_lt_10s",
            linux_perf_path=desktop_perf_path,
            windows_perf_path=windows_desktop_perf_path,
            linux_perf_report=desktop_perf_report,
            windows_perf_report=windows_desktop_perf_report,
            fixture_note="Linux 已有 formal desktop perf artifact，但当前输入仍小于 1GB，不能替代正式大输入验收。",
        ),
        "desktop_peak_memory_lt_4gb": _perf_status_node(
            metric_key="peak_memory_lt_4gb",
            linux_perf_path=desktop_perf_path,
            windows_perf_path=windows_desktop_perf_path,
            linux_perf_report=desktop_perf_report,
            windows_perf_report=windows_desktop_perf_report,
            fixture_note="Linux 峰值内存结果已留档，但当前仍属于 repo fixture 范围，不等价于正式 1GB 输入门槛。",
        ),
        "windows_linux_consistency": {
            "status": consistency_status,
            "linux_artifacts": _artifact_list(acceptance_path, desktop_runtime_path, desktop_perf_path, collector_soak_path),
            "windows_artifacts": windows_artifacts,
            "comparison_artifacts": compare_artifacts,
            "comparison": {
                "acceptance_baseline_match": acceptance_compare_match,
                "normalized_package_match": package_compare_match,
            },
            "remaining": missing_consistency,
            "notes": consistency_notes,
        },
        "long_duration_stability": {
            "status": long_duration_status,
            "linux_artifacts": _artifact_list(
                acceptance_path,
                collector_soak_path,
                desktop_soak_path,
                irrecoverable_error_path,
            ),
            "windows_artifacts": _artifact_list(
                windows_collector_soak_path,
                windows_desktop_soak_path,
                windows_irrecoverable_error_path,
            ),
            "current_scope": {
                "formal_contract": {
                    "platform": "windows",
                    "desktop_long_soak_threshold_seconds": LONG_DURATION_FORMAL_THRESHOLD_SECONDS,
                    "requires_irrecoverable_error_detected_false": True,
                },
                "linux_desktop_short_soak_passed": short_soak_passed,
                "linux_collector_soak_seconds": collector_duration,
                "linux_collector_soak_status": collector_soak.get("status") if isinstance(collector_soak, dict) else None,
                "linux_desktop_long_soak_seconds": desktop_soak_state["duration_sec"],
                "linux_desktop_long_soak_status": desktop_soak_state["status"],
                "linux_irrecoverable_error_observed_seconds": irrecoverable_state["duration_sec"],
                "linux_irrecoverable_error_detected": irrecoverable_state["detected"],
                "linux_irrecoverable_error_last_summary": irrecoverable_state["last_error_summary"],
                "windows_desktop_short_soak_passed": windows_short_soak_passed,
                "windows_collector_soak_seconds": windows_collector_duration,
                "windows_collector_soak_status": (
                    windows_collector_soak.get("status") if isinstance(windows_collector_soak, dict) else None
                ),
                "windows_collector_soak_supporting_only": True,
                "windows_desktop_long_soak_seconds": windows_desktop_soak_state["duration_sec"],
                "windows_desktop_long_soak_status": windows_desktop_soak_state["status"],
                "windows_desktop_long_soak_24h_passed": windows_24h_passed,
                "windows_irrecoverable_error_observed_seconds": windows_irrecoverable_state["duration_sec"],
                "windows_irrecoverable_error_detected": windows_irrecoverable_state["detected"],
                "windows_irrecoverable_error_last_summary": windows_irrecoverable_state["last_error_summary"],
            },
            "remaining": long_duration_remaining,
            "notes": long_duration_notes,
        },
    }
    return external_status


def _pending_keys(*sections: dict[str, dict[str, object]]) -> list[str]:
    pending: list[str] = []
    for section in sections:
        for key, value in section.items():
            if value.get("status") != "closed":
                pending.append(key)
    return pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="summarize_validation_status")
    parser.add_argument("--acceptance", required=True)
    parser.add_argument("--desktop-preflight", required=True)
    parser.add_argument("--desktop-env", required=True)
    parser.add_argument("--desktop-runtime")
    parser.add_argument("--desktop-perf")
    parser.add_argument("--desktop-dense-blocker")
    parser.add_argument("--collector-soak")
    parser.add_argument("--collector-perf")
    parser.add_argument("--windows-collector-perf")
    parser.add_argument("--desktop-soak")
    parser.add_argument("--irrecoverable-error")
    parser.add_argument("--render-fps")
    parser.add_argument("--windows-acceptance")
    parser.add_argument("--windows-desktop-runtime")
    parser.add_argument("--windows-desktop-perf")
    parser.add_argument("--windows-collector-soak")
    parser.add_argument("--windows-desktop-soak")
    parser.add_argument("--windows-irrecoverable-error")
    parser.add_argument("--acceptance-compare")
    parser.add_argument("--package-compare")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    acceptance = _load_json(args.acceptance)
    desktop_preflight = _load_json(args.desktop_preflight)
    desktop_env = _load_json(args.desktop_env)
    desktop_runtime = _load_json(args.desktop_runtime) if args.desktop_runtime else None
    desktop_perf = _load_json(args.desktop_perf) if args.desktop_perf else None
    desktop_dense_blocker = _load_json(args.desktop_dense_blocker) if args.desktop_dense_blocker else None
    collector_soak = _load_json(args.collector_soak) if args.collector_soak else None
    collector_perf = _load_json(args.collector_perf) if args.collector_perf else None
    windows_collector_perf = _load_json(args.windows_collector_perf) if args.windows_collector_perf else None
    desktop_soak = _load_json(args.desktop_soak) if args.desktop_soak else None
    irrecoverable_error = _load_json(args.irrecoverable_error) if args.irrecoverable_error else None
    render_fps = _load_json(args.render_fps) if args.render_fps else None
    windows_acceptance = _load_json(args.windows_acceptance) if args.windows_acceptance else None
    windows_desktop_runtime = _load_json(args.windows_desktop_runtime) if args.windows_desktop_runtime else None
    windows_desktop_perf = _load_json(args.windows_desktop_perf) if args.windows_desktop_perf else None
    windows_collector_soak = _load_json(args.windows_collector_soak) if args.windows_collector_soak else None
    windows_desktop_soak = _load_json(args.windows_desktop_soak) if args.windows_desktop_soak else None
    windows_irrecoverable_error = (
        _load_json(args.windows_irrecoverable_error) if args.windows_irrecoverable_error else None
    )
    acceptance_compare = _load_json(args.acceptance_compare) if args.acceptance_compare else None
    package_compare = _load_json(args.package_compare) if args.package_compare else None

    perf_required_keys = {
        "profile",
        "load_preview_seconds",
        "task_state_preview_seconds",
        "event_table_first_page_seconds",
        "lod2_first_window_seconds",
        "peak_memory_mb",
        "cache_eviction_count",
        "cache_budget_bytes",
        "cache_namespaces",
    }
    desktop_perf_node = desktop_perf.get("desktop_perf", {}) if isinstance(desktop_perf, dict) else {}
    windows_desktop_perf_node = (
        windows_desktop_perf.get("desktop_perf", {}) if isinstance(windows_desktop_perf, dict) else {}
    )

    runtime_ready = _runtime_ready(desktop_runtime)
    windows_runtime_ready = _runtime_ready(windows_desktop_runtime)
    runtime_error = _runtime_error(desktop_runtime, desktop_env, desktop_preflight)
    windows_runtime_error = _runtime_error(windows_desktop_runtime)

    desktop_perf_local_ready = bool(
        desktop_perf
        and desktop_perf.get("mode") in {"desktop_perf_baseline", "desktop_perf_acceptance", "desktop_perf_acceptance_linux"}
        and perf_required_keys.issubset(desktop_perf_node)
    )
    desktop_perf_linux_formal_ready = _perf_formal_ready(desktop_perf)
    desktop_perf_windows_formal_ready = _perf_formal_ready(windows_desktop_perf)
    collector_soak_linux_ready = _collector_ready(collector_soak)
    collector_soak_windows_ready = _collector_ready(windows_collector_soak)

    desktop_perf_acceptance = _perf_acceptance_node(desktop_perf)
    windows_desktop_perf_acceptance = _perf_acceptance_node(windows_desktop_perf)
    external_status = _build_external_status(
        acceptance_path=args.acceptance,
        desktop_runtime_path=args.desktop_runtime,
        desktop_perf_path=args.desktop_perf,
        collector_soak_path=args.collector_soak,
        desktop_soak_path=args.desktop_soak,
        irrecoverable_error_path=args.irrecoverable_error,
        acceptance=acceptance,
        desktop_perf_report=desktop_perf,
        collector_soak=collector_soak,
        desktop_soak=desktop_soak,
        irrecoverable_error=irrecoverable_error,
        windows_acceptance_path=args.windows_acceptance,
        windows_desktop_runtime_path=args.windows_desktop_runtime,
        windows_desktop_perf_path=args.windows_desktop_perf,
        windows_collector_soak_path=args.windows_collector_soak,
        windows_desktop_soak_path=args.windows_desktop_soak,
        windows_irrecoverable_error_path=args.windows_irrecoverable_error,
        windows_acceptance=windows_acceptance,
        windows_desktop_perf_report=windows_desktop_perf,
        windows_collector_soak=windows_collector_soak,
        windows_desktop_soak=windows_desktop_soak,
        windows_irrecoverable_error=windows_irrecoverable_error,
        acceptance_compare_path=args.acceptance_compare,
        acceptance_compare=acceptance_compare,
        package_compare_path=args.package_compare,
        package_compare=package_compare,
    )

    nfr_perf01 = (
        _nfr_perf01_collector_throughput_aggregate(
            linux_collector_perf_path=args.collector_perf,
            linux_collector_perf_report=collector_perf,
            windows_collector_perf_path=args.windows_collector_perf,
            windows_collector_perf_report=windows_collector_perf,
        )
        if args.windows_collector_perf
        else _nfr_perf01_collector_throughput(
            collector_perf_path=args.collector_perf,
            collector_perf_report=collector_perf,
        )
    )

    nfr_perf_status = {
        "NFR-PERF-01": nfr_perf01,
        "NFR-PERF-05": _nfr_perf05_render_fps(
            render_fps_path=args.render_fps,
            render_fps_report=render_fps,
        ),
        "NFR-PERF-06": _nfr_perf06_export_sla(
            desktop_perf_path=args.desktop_perf,
            desktop_perf_report=desktop_perf,
        ),
    }
    desktop_dense_blocker_status = _desktop_dense_blocker_status(
        desktop_dense_blocker_path=args.desktop_dense_blocker,
        desktop_dense_blocker_report=desktop_dense_blocker,
    )

    summary = {
        "artifacts": {
            "acceptance": args.acceptance,
            "desktop_preflight": args.desktop_preflight,
            "desktop_env": args.desktop_env,
            **({"desktop_runtime": args.desktop_runtime} if args.desktop_runtime else {}),
            **({"desktop_perf": args.desktop_perf} if args.desktop_perf else {}),
            **({"desktop_dense_blocker": args.desktop_dense_blocker} if args.desktop_dense_blocker else {}),
            **({"collector_soak": args.collector_soak} if args.collector_soak else {}),
            **({"collector_perf": args.collector_perf} if args.collector_perf else {}),
            **({"windows_collector_perf": args.windows_collector_perf} if args.windows_collector_perf else {}),
            **({"desktop_soak": args.desktop_soak} if args.desktop_soak else {}),
            **({"irrecoverable_error": args.irrecoverable_error} if args.irrecoverable_error else {}),
            **({"render_fps": args.render_fps} if args.render_fps else {}),
            **({"windows_acceptance": args.windows_acceptance} if args.windows_acceptance else {}),
            **({"windows_desktop_runtime": args.windows_desktop_runtime} if args.windows_desktop_runtime else {}),
            **({"windows_desktop_perf": args.windows_desktop_perf} if args.windows_desktop_perf else {}),
            **({"windows_collector_soak": args.windows_collector_soak} if args.windows_collector_soak else {}),
            **({"windows_desktop_soak": args.windows_desktop_soak} if args.windows_desktop_soak else {}),
            **(
                {"windows_irrecoverable_error": args.windows_irrecoverable_error}
                if args.windows_irrecoverable_error
                else {}
            ),
            **({"acceptance_compare": args.acceptance_compare} if args.acceptance_compare else {}),
            **({"package_compare": args.package_compare} if args.package_compare else {}),
        },
        "local_smoke_ready": bool(
            acceptance.get("consistency", {}).get("repeat_export_consistent")
            and acceptance.get("consistency", {}).get("repro_repeat_consistent")
            and acceptance.get("consistency", {}).get("compare_scope_consistent")
            and acceptance.get("consistency", {}).get("short_soak_passed")
        ),
        "desktop_preflight_ready": bool(desktop_preflight.get("ready_for_runtime_smoke")),
        "desktop_large_input_preflight_ready": bool(
            isinstance(desktop_preflight.get("large_input_preflight"), dict)
            and desktop_preflight["large_input_preflight"].get("ready_for_large_input_perf")
        ),
        "desktop_large_input_preflight_blocking_reasons": (
            desktop_preflight.get("large_input_preflight", {}).get("blocking_reasons")
            if isinstance(desktop_preflight.get("large_input_preflight"), dict)
            else []
        ),
        "desktop_env_ready": desktop_env.get("error") is None,
        "desktop_runtime_ready": runtime_ready,
        "desktop_runtime_error": runtime_error,
        "desktop_runtime_command": desktop_runtime.get("command") if isinstance(desktop_runtime, dict) else None,
        "desktop_runtime_executed": desktop_runtime.get("executed") if isinstance(desktop_runtime, dict) else None,
        "desktop_runtime_skipped": desktop_runtime.get("skipped") if isinstance(desktop_runtime, dict) else None,
        "windows_runtime_ready": windows_runtime_ready,
        "windows_runtime_error": windows_runtime_error,
        "windows_runtime_command": (
            windows_desktop_runtime.get("command") if isinstance(windows_desktop_runtime, dict) else None
        ),
        "windows_runtime_executed": (
            windows_desktop_runtime.get("executed") if isinstance(windows_desktop_runtime, dict) else None
        ),
        "windows_runtime_skipped": (
            windows_desktop_runtime.get("skipped") if isinstance(windows_desktop_runtime, dict) else None
        ),
        "desktop_perf_local_ready": desktop_perf_local_ready,
        "desktop_perf_linux_formal_ready": desktop_perf_linux_formal_ready,
        "desktop_perf_linux_first_screen_pass": (
            desktop_perf_acceptance.get("first_screen_lt_10s", {}).get("pass")
            if desktop_perf_linux_formal_ready
            else None
        ),
        "desktop_perf_linux_peak_memory_pass": (
            desktop_perf_acceptance.get("peak_memory_lt_4gb", {}).get("pass")
            if desktop_perf_linux_formal_ready
            else None
        ),
        "desktop_perf_windows_formal_ready": desktop_perf_windows_formal_ready,
        "desktop_perf_windows_first_screen_pass": (
            windows_desktop_perf_acceptance.get("first_screen_lt_10s", {}).get("pass")
            if desktop_perf_windows_formal_ready
            else None
        ),
        "desktop_perf_windows_peak_memory_pass": (
            windows_desktop_perf_acceptance.get("peak_memory_lt_4gb", {}).get("pass")
            if desktop_perf_windows_formal_ready
            else None
        ),
        "desktop_perf_profile": desktop_perf_node.get("profile") if desktop_perf_node else None,
        "windows_desktop_perf_profile": (
            windows_desktop_perf_node.get("profile") if windows_desktop_perf_node else None
        ),
        "collector_soak_linux_ready": collector_soak_linux_ready,
        "collector_soak_windows_ready": collector_soak_windows_ready,
        "acceptance_compare_match": _compare_match(acceptance_compare),
        "package_compare_match": _compare_match(package_compare),
        "external_status": external_status,
        "nfr_perf_status": nfr_perf_status,
        **({"desktop_dense_blocker": desktop_dense_blocker_status} if desktop_dense_blocker_status else {}),
        "external_pending": _pending_keys(external_status, nfr_perf_status),
        "notes": [
            "This summary aggregates current local artifacts and highlights what still requires external execution.",
            "Desktop runtime readiness is derived from a runtime report artifact, not only from preflight or env smoke.",
            "Desktop perf summary distinguishes local baseline readiness from formal acceptance report readiness.",
            "When a dense blocker artifact is supplied, the summary records its blocked stage as a first-class machine-readable status.",
            "Formal 1GB perf closure now requires a complete formal_input manifest and formal_input_verified=true.",
            "external_status records what Linux evidence already exists, what Windows artifacts have been imported, and what still remains open.",
            "Local desktop staged-load evidence can be aggregated here, but 1GB < 10s, < 4GB, Windows/Linux parity, and long-duration soak remain external until the corresponding artifacts are supplied.",
        ],
    }
    rendered = json.dumps(summary, indent=2, ensure_ascii=False)
    Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
