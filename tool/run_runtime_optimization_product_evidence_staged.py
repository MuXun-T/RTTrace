from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import tool.run_runtime_optimization_product_evidence as runtime_optimization_product_evidence_tool

REPORT_VERSION = "runtime-optimization-product-evidence-staged-v1"
DEFAULT_DATE_TAG = "20260703"
DEFAULT_TRACE = (
    ROOT_DIR
    / "docs"
    / "evidence_proof_archive_20260416"
    / "formal_10_4"
    / "inputs"
    / "google_cluster_external_dense_1gb_formal_qualified.trace"
)
DEFAULT_OUTPUT_ROOT = ROOT_DIR / "tmp" / f"runtime_optimization_product_evidence_staged_{DEFAULT_DATE_TAG}"
DEFAULT_P3_MAIN_REPEAT = 3
DEFAULT_P4_PREBUILD_TIMEOUT_S = 7200.0
STAGED_REPORT_NAME = "staged_product_evidence_report.json"
STAGED_SUMMARY_NAME = "staged_product_evidence_summary.md"
HARNESS_REPORT_NAME = "product_evidence_report.json"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stage_report_path(stage_output_root: Path) -> Path:
    return stage_output_root / HARNESS_REPORT_NAME


def _stage_summary_path(stage_output_root: Path) -> Path:
    return stage_output_root / "product_evidence_summary.md"


def _build_stage_plans(
    *,
    trace: Path,
    output_root: Path,
    p3_main_repeat: int,
    p4_prebuild_timeout_s: float,
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []

    p3_verify_output_root = output_root / "p3_verify"
    plans.append(
        {
            "id": "p3_verify",
            "output_root": p3_verify_output_root,
            "report_path": _stage_report_path(p3_verify_output_root),
            "argv": [
                "--trace",
                str(trace),
                "--output-root",
                str(p3_verify_output_root),
                "--suite",
                "load_cache",
                "--repeat",
                "1",
            ],
        }
    )

    p3_main_output_root = output_root / "p3_main"
    plans.append(
        {
            "id": "p3_main",
            "output_root": p3_main_output_root,
            "report_path": _stage_report_path(p3_main_output_root),
            "argv": [
                "--trace",
                str(trace),
                "--output-root",
                str(p3_main_output_root),
                "--suite",
                "load_cache",
                "--repeat",
                str(p3_main_repeat),
                "--load-cache-mode",
                "cold_once_warm_repeat",
            ],
        }
    )

    p4_main_output_root = output_root / "p4_main"
    plans.append(
        {
            "id": "p4_main",
            "output_root": p4_main_output_root,
            "report_path": _stage_report_path(p4_main_output_root),
            "argv": [
                "--trace",
                str(trace),
                "--output-root",
                str(p4_main_output_root),
                "--suite",
                "background_prebuild",
                "--repeat",
                "1",
                "--prebuild-timeout-s",
                str(float(p4_prebuild_timeout_s)),
            ],
        }
    )
    return plans


def _stage_entry(
    plan: dict[str, Any],
    *,
    status: str,
    exit_code: int | None,
    status_reason: str | None,
    started_at: str | None = None,
    finished_at: str | None = None,
    elapsed_seconds: float | None = None,
    harness_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    harness_payload = dict(harness_report or {})
    return {
        "id": str(plan["id"]),
        "status": status,
        "exit_code": exit_code,
        "output_root": str(plan["output_root"]),
        "report_path": str(plan["report_path"]),
        "summary_path": str(_stage_summary_path(Path(plan["output_root"]))),
        "argv": list(plan["argv"]),
        "status_reason": status_reason,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": elapsed_seconds,
        "harness_status": harness_payload.get("status"),
        "harness_execution_status": harness_payload.get("execution_status"),
    }


def _load_stage_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(payload) if isinstance(payload, dict) else {}


def _stage_claim_present(report: dict[str, Any], *, suite: str, claim: str) -> bool:
    for item in list(report.get("claimable_speedups") or []):
        if dict(item).get("suite") == suite and dict(item).get("claim") == claim:
            return True
    return False


def _stage_status_by_id(stages: list[dict[str, Any]], stage_id: str) -> str:
    for stage in stages:
        if stage.get("id") == stage_id:
            return str(stage.get("status") or "missing")
    return "missing"


def _conclusion_status(*, claimable: bool, stage_status: str) -> str:
    if claimable:
        return "claimable"
    if stage_status in {"planned", "not_run"}:
        return stage_status
    return "not_claimable"


def _p3_open_cache_conclusion(stages: list[dict[str, Any]], stage_reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    stage_status = _stage_status_by_id(stages, "p3_main")
    report = dict(stage_reports.get("p3_main") or {})
    suite = dict(dict(report.get("suites") or {}).get("load_cache") or {})
    claim_evaluation = dict(suite.get("claim_evaluation") or {})
    claimable = (
        report.get("execution_status") == "completed"
        and bool(claim_evaluation.get("claimable"))
        and _stage_claim_present(
            report,
            suite="load_cache",
            claim="cached_desktop_open_load_median_reduced",
        )
    )
    if claimable:
        statement = "P3 证据满足条件，可宣称缓存命中后的桌面打开加载中位时间下降。"
    elif stage_status == "planned":
        statement = "P3 dry-run 未执行，当前不能宣称缓存命中后的桌面打开加载时间下降。"
    elif stage_status == "not_run":
        statement = "P3 主阶段未运行，不能宣称缓存命中后的桌面打开加载时间下降。"
    else:
        statement = "P3 证据不足，当前不可宣称缓存命中后的桌面打开加载时间下降。"
    return {
        "status": _conclusion_status(claimable=claimable, stage_status=stage_status),
        "claimable": claimable,
        "statement": statement,
        "source_stage": "p3_main",
    }


def _p4_click_wait_conclusion(stages: list[dict[str, Any]], stage_reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    stage_status = _stage_status_by_id(stages, "p4_main")
    report = dict(stage_reports.get("p4_main") or {})
    suite = dict(dict(report.get("suites") or {}).get("background_prebuild") or {})
    claim_evaluation = dict(suite.get("claim_evaluation") or {})
    claimable = (
        report.get("execution_status") == "completed"
        and bool(claim_evaluation.get("click_to_export_claimable"))
        and _stage_claim_present(
            report,
            suite="background_prebuild",
            claim="click_to_export_wait_reduced",
        )
    )
    if claimable:
        statement = "P4 证据满足条件，可宣称点击到导出完成的用户等待时间降低。"
    elif stage_status == "planned":
        statement = "P4 dry-run 未执行，当前不能宣称点击到导出完成的用户等待时间降低。"
    elif stage_status == "not_run":
        statement = "P4 未运行，不能宣称点击到导出完成的用户等待时间降低。"
    else:
        statement = "P4 证据不足，当前不可宣称点击到导出完成的用户等待时间降低。"
    return {
        "status": _conclusion_status(claimable=claimable, stage_status=stage_status),
        "claimable": claimable,
        "statement": statement,
        "source_stage": "p4_main",
    }


def _p5_1gb_conclusion() -> dict[str, Any]:
    return {
        "status": "not_run",
        "claimable": False,
        "statement": "验收达到，1GB 产品提速未充分证明。",
        "source_stage": None,
    }


def _evidence_status(*, execution_status: str, conclusions: dict[str, dict[str, Any]]) -> str:
    if execution_status != "completed":
        return execution_status
    conclusion_rows = [dict(item or {}) for item in conclusions.values()]
    if any(bool(item.get("claimable")) for item in conclusion_rows):
        if any(not bool(item.get("claimable")) for item in conclusion_rows):
            return "pass_with_noted_limits"
        return "pass_with_product_speedup_evidence"
    return "pass_with_noted_limits"


def _render_summary(report: dict[str, Any]) -> str:
    conclusions = dict(report.get("conclusions") or {})
    lines = [
        "# Runtime Optimization Product Evidence Staged",
        "",
        f"- execution_status: `{report['execution_status']}`",
        f"- evidence_status: `{report['evidence_status']}`",
        f"- trace: `{report['trace']}`",
        f"- output_root: `{report['output_root']}`",
        "",
        "## 阶段状态",
    ]
    for stage in list(report.get("stages") or []):
        lines.append(f"- `{stage.get('id', 'unknown')}`: `{stage.get('status', 'missing')}`")
    if report.get("stop_reason"):
        lines.extend(["", f"- stop_reason: {report['stop_reason']}"])
    lines.extend(
        [
            "",
            "## 结论",
            f"- P3 open cache: {dict(conclusions.get('p3_open_cache') or {}).get('statement', 'missing')}",
            f"- P4 click wait: {dict(conclusions.get('p4_click_wait') or {}).get('statement', 'missing')}",
            f"- P5 1GB: {dict(conclusions.get('p5_1gb') or {}).get('statement', 'missing')}",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_runtime_optimization_product_evidence_staged")
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--p3-main-repeat", type=int, default=DEFAULT_P3_MAIN_REPEAT)
    parser.add_argument("--p4-prebuild-timeout-s", type=float, default=DEFAULT_P4_PREBUILD_TIMEOUT_S)
    parser.add_argument("--skip-p4", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.p3_main_repeat < 1:
        parser.error("--p3-main-repeat must be >= 1")
    if float(args.p4_prebuild_timeout_s) <= 0.0:
        parser.error("--p4-prebuild-timeout-s must be > 0")

    trace = args.trace.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    stage_plans = _build_stage_plans(
        trace=trace,
        output_root=output_root,
        p3_main_repeat=int(args.p3_main_repeat),
        p4_prebuild_timeout_s=float(args.p4_prebuild_timeout_s),
    )
    stage_reports: dict[str, dict[str, Any]] = {}
    stages: list[dict[str, Any]] = []
    status = "planned" if args.dry_run else "completed"
    stop_reason = None
    stop_after_failure = False

    for plan in stage_plans:
        stage_id = str(plan["id"])
        if stage_id == "p4_main" and args.skip_p4:
            stages.append(
                _stage_entry(
                    plan,
                    status="not_run",
                    exit_code=None,
                    status_reason="skip_p4_requested",
                )
            )
            continue
        if args.dry_run:
            stages.append(
                _stage_entry(
                    plan,
                    status="planned",
                    exit_code=None,
                    status_reason="dry_run_only",
                )
            )
            continue
        if stop_after_failure:
            stages.append(
                _stage_entry(
                    plan,
                    status="not_run",
                    exit_code=None,
                    status_reason="stopped_after_prior_failure",
                )
            )
            continue

        plan_output_root = Path(plan["output_root"])
        plan_output_root.mkdir(parents=True, exist_ok=True)
        started_at = _iso_now()
        started_perf = time.perf_counter()
        exit_code = int(runtime_optimization_product_evidence_tool.main(list(plan["argv"])))
        finished_at = _iso_now()
        elapsed_seconds = round(time.perf_counter() - started_perf, 6)
        stage_report = _load_stage_report(Path(plan["report_path"]))
        stages.append(
            _stage_entry(
                plan,
                status="completed" if exit_code == 0 else "failed",
                exit_code=exit_code,
                status_reason=None if exit_code == 0 else "nonzero_exit_code",
                started_at=started_at,
                finished_at=finished_at,
                elapsed_seconds=elapsed_seconds,
                harness_report=stage_report,
            )
        )
        stage_reports[stage_id] = stage_report
        if exit_code != 0:
            status = "failed"
            if stage_id == "p3_verify":
                stop_after_failure = True
                stop_reason = "p3_verify 阶段失败，停止执行 p3_main 与 p4_main。"
            elif stage_id == "p3_main":
                stop_after_failure = True
                stop_reason = "p3_main 阶段失败，停止执行 p4_main。"
            else:
                stop_reason = "p4_main 阶段失败，P3 结论保留，P4 不可宣称。"

    conclusions = {
        "p3_open_cache": _p3_open_cache_conclusion(stages, stage_reports),
        "p4_click_wait": _p4_click_wait_conclusion(stages, stage_reports),
        "p5_1gb": _p5_1gb_conclusion(),
    }
    evidence_status = _evidence_status(execution_status=status, conclusions=conclusions)
    report = {
        "report_version": REPORT_VERSION,
        "generated_at": _iso_now(),
        "status": status,
        "execution_status": status,
        "evidence_status": evidence_status,
        "trace": str(trace),
        "output_root": str(output_root),
        "dry_run": bool(args.dry_run),
        "skip_p4": bool(args.skip_p4),
        "p3_main_repeat": int(args.p3_main_repeat),
        "p4_prebuild_timeout_s": float(args.p4_prebuild_timeout_s),
        "stages": stages,
        "stop_reason": stop_reason,
        "claim_policy": [
            "每个阶段都显式传入单 suite，避免误跑未授权套件，尤其不运行 P5。",
            "只有阶段 report execution_status=completed 且对应 claim/claimable 字段同时满足时，才允许产品提速宣称。",
            "dry-run、未运行、失败或 partial 一律不生成产品提速宣称。",
        ],
        "risk_notes": [
            "P3 cold_once_warm_repeat 只有 1 个 cold 样本，冷启动仅作为对照，不扩展为稳健统计结论。",
            "P4 repeat=1 只是单点方向性证据，不能视为稳定分布层面的充分证明。",
            "hot_metadata_only 不能等同完整 UI open，不可据此宣称完整打开路径提速。",
        ],
        "conclusions": conclusions,
        "p5_1gb_status": conclusions["p5_1gb"]["status"],
        "p5_statement": conclusions["p5_1gb"]["statement"],
    }

    report_path = output_root / STAGED_REPORT_NAME
    summary_path = output_root / STAGED_SUMMARY_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_path.write_text(_render_summary(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "summary_path": str(summary_path),
                "status": report["status"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
