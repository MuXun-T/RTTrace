from __future__ import annotations

import argparse
import json
import signal
import shutil
import sys
import time
import tracemalloc
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop.clipped_completeness import (
    build_analysis_snapshot,
    build_anchor_rows,
    compare_snapshots,
    render_summary,
)
from desktop.repository import DatasetRecord
from desktop.services import ExportService, PARSER_VERSION, ReproService, WorkspaceController
from metric.core import MetricConfig, alert_Evaluate, diag_Generate, metric_Compute, metric_Ingest, metric_Init
from parser import load_dataset_with_timings, prs_Prescan
from parser.models import DatasetArtifact, GlobalHeader, RebuildBundle, ResourceGraph
from parser.parser_process_agent import ParserProcessAgent
from spec.io import json_dump, json_load, serialize


FULL_BASELINE_JSON = "full_baseline.json"
CLIPPED_TARGET_JSON = "clipped_target.json"
COMPLETENESS_REPORT_JSON = "full_trace_vs_clipped_trace_completeness_report.json"
FAILURE_REPORT_JSON = "clipped_completeness_failure_report.json"


def main(argv: list[str] | None = None) -> int:
    run_state = _new_run_state()
    output_dir: Path | None = None
    restore_signal_handlers = None
    parser = argparse.ArgumentParser(prog="run_clipped_completeness_check")
    parser.add_argument("--input")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--time-window", nargs=2, type=float, metavar=("BEGIN", "END"))
    parser.add_argument("--filter-json")
    parser.add_argument("--selection-json")
    parser.add_argument("--metric-config-json")
    parser.add_argument("--package-dir")
    parser.add_argument("--reuse-clipped-package")
    parser.add_argument("--full-baseline-json")
    parser.add_argument(
        "--baseline-generation-mode",
        choices=("materialized", "source_backed_window_scan"),
        default="materialized",
    )
    parser.add_argument("--diff-sample-limit", type=int, default=20)

    try:
        _begin_stage(run_state, "parse_args")
        args = parser.parse_args(argv)
        _complete_stage(run_state)

        output_dir = Path(args.output_dir)
        package_dir = Path(args.reuse_clipped_package or args.package_dir or (output_dir / "clipped_package"))
        run_state["baseline_generation_mode"] = args.baseline_generation_mode
        run_state["package_dir"] = str(package_dir)
        restore_signal_handlers = _install_termination_handlers(run_state)
        return _run_clipped_completeness(args, parser, output_dir, package_dir, run_state)
    except SystemExit as exc:
        if output_dir is not None:
            _best_effort_write_failure_state(output_dir, run_state, exc)
        raise
    except Exception as exc:
        if output_dir is not None:
            _best_effort_write_failure_state(output_dir, run_state, exc)
        raise
    finally:
        if restore_signal_handlers is not None:
            try:
                restore_signal_handlers()
            except Exception:
                pass


def _run_clipped_completeness(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    output_dir: Path,
    package_dir: Path,
    run_state: dict[str, Any],
) -> int:
    _begin_stage(run_state, "validate_args")

    if not args.full_baseline_json and not args.input:
        parser.error("--input is required unless --full-baseline-json is provided")
    if not args.full_baseline_json and not args.time_window:
        parser.error("--time-window is required unless --full-baseline-json is provided")
    if args.full_baseline_json and not args.reuse_clipped_package and not args.input:
        parser.error("--input is required to export a new clipped package when --reuse-clipped-package is absent")
    _complete_stage(run_state)

    input_path = Path(args.input) if args.input else None

    _begin_stage(run_state, "prepare_output")
    output_dir.mkdir(parents=True, exist_ok=True)
    _complete_stage(run_state)

    loaded_full_baseline = None
    if args.full_baseline_json:
        run_state["artifacts"]["input_full_baseline_json"] = str(Path(args.full_baseline_json))
        _begin_stage(run_state, "load_full_baseline")
        loaded_full_baseline = json_load(args.full_baseline_json)
        _complete_stage(run_state)

    _begin_stage(run_state, "build_scope")
    inherited_scope = dict((loaded_full_baseline or {}).get("comparison_scope") or {})
    time_window = (
        [float(args.time_window[0]), float(args.time_window[1])]
        if args.time_window
        else list(inherited_scope.get("time_window") or [])
    )
    if len(time_window) != 2:
        parser.error("--time-window is required when --full-baseline-json does not contain comparison_scope.time_window")
    scope = {
        "time_window": [float(time_window[0]), float(time_window[1])],
        "filter": _json_arg(args.filter_json, "--filter-json")
        if args.filter_json is not None
        else dict(inherited_scope.get("filter") or {}),
        "selection": _json_arg(args.selection_json, "--selection-json")
        if args.selection_json is not None
        else dict(inherited_scope.get("selection") or {}),
        "metric_config": _json_arg(args.metric_config_json, "--metric-config-json")
        if args.metric_config_json is not None
        else dict(inherited_scope.get("metric_config") or {}),
    }
    metric_config = MetricConfig(**scope["metric_config"])
    _complete_stage(run_state)

    tracemalloc.start()
    controller = WorkspaceController()
    dataset_id: str | None = None
    if input_path is not None:
        _begin_stage(run_state, "load_dataset")
        dataset_id = _load_dataset_for_baseline(
            controller,
            input_path,
            args.baseline_generation_mode,
            comparison_scope=scope,
        )
        context_result = controller.viz_SetContext(
            {
                "time_window": tuple(scope["time_window"]),
                "filter": scope["filter"],
                "selection": scope["selection"],
                "dataset_role": "single",
            }
        )
        if not context_result.ok:
            raise SystemExit(context_result.message)
        _apply_metric_config(controller, dataset_id, metric_config)
        _complete_stage(run_state)

    _begin_stage(run_state, "build_full_baseline")
    if loaded_full_baseline is not None:
        full_snapshot = loaded_full_baseline
    else:
        if dataset_id is None or input_path is None:
            raise SystemExit("full baseline generation requires --input")
        full_snapshot = _snapshot_from_dataset(
            controller=controller,
            dataset_id=dataset_id,
            snapshot_kind="full_baseline",
            input_path=input_path,
            comparison_scope=scope,
            metric_config=metric_config,
            package_path=None,
            ref_index=[],
            meta={},
            manifest={},
            analysis_context=controller.context_store.get().persisted_dict(),
            export_observation={},
            baseline_generation_mode=args.baseline_generation_mode,
        )
    run_state["baseline_generation_mode"] = str(
        full_snapshot.get("baseline_generation_mode") or args.baseline_generation_mode
    )
    _complete_stage(run_state)

    _begin_stage(run_state, "write_full_baseline")
    json_dump(output_dir / FULL_BASELINE_JSON, full_snapshot)
    run_state["artifacts"]["full_baseline_json"] = FULL_BASELINE_JSON
    _complete_stage(run_state)
    _write_run_status(
        output_dir,
        status="full_baseline_ready",
        current_stage=run_state["current_stage"],
        last_completed_stage=run_state["last_completed_stage"],
        full_baseline_json=FULL_BASELINE_JSON,
        package_dir=str(package_dir),
        baseline_generation_mode=run_state["baseline_generation_mode"],
    )

    export = ExportService(controller.repository, controller.context_store, controller.jobs)
    export_observation: dict[str, Any] = {}
    if args.reuse_clipped_package:
        _begin_stage(run_state, "load_reused_package_metadata")
        raw_meta = json_load(package_dir / "meta.json")
        raw_manifest = json_load(package_dir / "manifest.json")
        export_observation.update(_reused_package_export_observation(package_dir, raw_meta, raw_manifest))
        _complete_stage(run_state)
    else:
        _begin_stage(run_state, "export_clipped")
        if dataset_id is None:
            raise SystemExit("clipped package export requires --input")
        if package_dir.exists() and package_dir.resolve().is_relative_to(output_dir.resolve()):
            shutil.rmtree(package_dir)
        job = export.export_Clipped(
            {
                "dataset_id": dataset_id,
                "time_window": tuple(scope["time_window"]),
                "filter": scope["filter"],
                "selection": scope["selection"],
                "dataset_role": "single",
            }
        )
        if not job.ok:
            raise SystemExit(job.message)
        _complete_stage(run_state)

        _begin_stage(run_state, "write_package")
        write_started = time.perf_counter()
        written = export.export_WritePackage(str(job.data["job_id"]), str(package_dir))
        write_seconds = time.perf_counter() - write_started
        if not written.ok:
            raise SystemExit(written.message)
        run_state["artifacts"]["package_dir"] = str(package_dir)
        raw_meta = dict(written.data.get("meta") or {})
        raw_manifest = dict(written.data.get("manifest") or {})
        export_observation.update(
            {
                "write_seconds": round(write_seconds, 9),
                "write_timings": dict(written.data.get("write_timings") or {}),
                "write_mode": written.data.get("write_mode"),
                "event_count": written.data.get("event_count"),
            }
        )
        _complete_stage(run_state)

    _begin_stage(run_state, "normalize_package")
    normalize_started = time.perf_counter()
    normalized_result = export.export_NormalizePackage(str(package_dir))
    normalize_seconds = time.perf_counter() - normalize_started
    if not normalized_result.ok:
        raise SystemExit(normalized_result.message)
    normalized = normalized_result.data
    export_observation["normalize_seconds"] = round(normalize_seconds, 9)
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    export_observation.setdefault("python_current_alloc_mb", round(current_bytes / (1024 * 1024), 6))
    export_observation.setdefault("python_peak_alloc_mb", round(peak_bytes / (1024 * 1024), 6))
    for key, value in _os_peak_observation().items():
        export_observation.setdefault(key, value)
    _complete_stage(run_state)

    _begin_stage(run_state, "open_package")
    repro = ReproService(controller.repository, controller.context_store, controller.jobs)
    opened = repro.repro_OpenPackage(str(package_dir))
    if not opened.ok:
        raise SystemExit(opened.message)
    _complete_stage(run_state)

    _begin_stage(run_state, "restore_context")
    restored = repro.repro_RestoreContext(None)
    if not restored.ok:
        raise SystemExit(restored.message)
    _complete_stage(run_state)

    _begin_stage(run_state, "load_clipped_dataset")
    clipped_dataset = repro.repro_LoadAsDataset("single")
    if not clipped_dataset.ok:
        raise SystemExit(clipped_dataset.message)
    _apply_metric_config(controller, str(clipped_dataset.data), metric_config)
    _complete_stage(run_state)

    _begin_stage(run_state, "build_clipped_snapshot")
    clipped_snapshot = _snapshot_from_dataset(
        controller=controller,
        dataset_id=str(clipped_dataset.data),
        snapshot_kind="clipped_target",
        input_path=input_path or _snapshot_input_path(full_snapshot),
        comparison_scope=scope,
        metric_config=metric_config,
        package_path=package_dir,
        ref_index=list(normalized.get("ref_index") or []),
        meta=raw_meta,
        manifest=raw_manifest,
        analysis_context=dict(normalized.get("analysis_context") or controller.context_store.get().persisted_dict()),
        export_observation=export_observation,
        result_validity=normalized.get("result_validity"),
        baseline_generation_mode=str(full_snapshot.get("baseline_generation_mode") or args.baseline_generation_mode),
        input_sha256=(full_snapshot.get("source") or {}).get("input_sha256") if loaded_full_baseline is not None else None,
        consumer_mode=normalized.get("consumer_mode"),
        consumer_mode_explain=normalized.get("consumer_mode_explain"),
    )
    _complete_stage(run_state)

    _begin_stage(run_state, "compare_snapshots")
    report = compare_snapshots(
        full_snapshot,
        clipped_snapshot,
        diff_sample_limit=args.diff_sample_limit,
    )
    _complete_stage(run_state)

    _begin_stage(run_state, "write_report_artifacts")
    rerun_command = " ".join(_quote_arg(item) for item in sys.argv)
    summary = render_summary(report, rerun_command=rerun_command)

    json_dump(output_dir / CLIPPED_TARGET_JSON, clipped_snapshot)
    run_state["artifacts"]["clipped_target_json"] = CLIPPED_TARGET_JSON
    json_dump(output_dir / COMPLETENESS_REPORT_JSON, report)
    run_state["artifacts"]["report_json"] = COMPLETENESS_REPORT_JSON
    (output_dir / "summary.md").write_text(summary, encoding="utf-8")
    run_state["artifacts"]["summary_md"] = "summary.md"
    _complete_stage(run_state)
    _write_run_status(
        output_dir,
        status="completed",
        current_stage=run_state["current_stage"],
        last_completed_stage=run_state["last_completed_stage"],
        full_baseline_json=FULL_BASELINE_JSON,
        clipped_target_json=CLIPPED_TARGET_JSON,
        report_json=COMPLETENESS_REPORT_JSON,
        summary_md="summary.md",
        package_dir=str(package_dir),
        baseline_generation_mode=run_state["baseline_generation_mode"],
        report_status=str(report.get("status")),
    )
    print(json.dumps({"status": report["status"], "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0 if report["status"] in {"strict_pass", "bounded_pass"} else 2


def _snapshot_from_dataset(
    *,
    controller: WorkspaceController,
    dataset_id: str,
    snapshot_kind: str,
    input_path: Path | None,
    comparison_scope: dict[str, Any],
    metric_config: MetricConfig,
    package_path: Path | None,
    ref_index: list[dict[str, Any]],
    meta: dict[str, Any],
    manifest: dict[str, Any],
    analysis_context: dict[str, Any],
    export_observation: dict[str, Any],
    result_validity: Any | None = None,
    baseline_generation_mode: str = "materialized",
    input_sha256: str | None = None,
    consumer_mode: Any | None = None,
    consumer_mode_explain: Any | None = None,
) -> dict[str, Any]:
    if snapshot_kind == "full_baseline" and baseline_generation_mode == "source_backed_window_scan":
        return _source_backed_window_snapshot(
            controller=controller,
            dataset_id=dataset_id,
            input_path=input_path,
            comparison_scope=comparison_scope,
            metric_config=metric_config,
            analysis_context=analysis_context,
            baseline_generation_mode=baseline_generation_mode,
        )
    record = controller.repository.get(dataset_id)
    session_result = metric_Init(metric_config)
    if not session_result.ok:
        raise RuntimeError(session_result.message)
    session = session_result.data
    ingested = metric_Ingest(session, record.artifact.bundle)
    if not ingested.ok:
        raise RuntimeError(ingested.message)
    t_begin, t_end = comparison_scope["time_window"]
    filters = comparison_scope.get("filter") or {}
    metrics_result = metric_Compute(session, float(t_begin), float(t_end), filters)
    if not metrics_result.ok:
        raise RuntimeError(metrics_result.message)
    alerts_result = alert_Evaluate(session, float(t_begin), float(t_end), filters)
    if not alerts_result.ok:
        raise RuntimeError(alerts_result.message)
    diagnoses_result = diag_Generate(session, float(t_begin), float(t_end), alerts_result.data or [])
    if not diagnoses_result.ok:
        raise RuntimeError(diagnoses_result.message)
    alerts = serialize(alerts_result.data or [])
    diagnoses = serialize(diagnoses_result.data or [])
    result_validity, consumer_mode, consumer_mode_explain = _completeness_result_contract(
        snapshot_kind=snapshot_kind,
        alerts=alerts,
        diagnoses=diagnoses,
        result_validity=result_validity,
        consumer_mode=consumer_mode,
        consumer_mode_explain=consumer_mode_explain,
    )
    anchors = build_anchor_rows(dict(analysis_context or {}), alerts, diagnoses)
    return build_analysis_snapshot(
        snapshot_kind=snapshot_kind,
        input_path=input_path,
        dataset_id=record.artifact.dataset_id,
        parser_version=PARSER_VERSION,
        comparison_scope=comparison_scope,
        metrics=metrics_result.data or [],
        alerts=alerts,
        diagnoses=diagnoses,
        anchors=anchors,
        rebuild_bundle=record.artifact.bundle.to_dict(),
        ref_index=ref_index,
        meta=meta,
        manifest=manifest,
        package_path=package_path,
        analysis_context=analysis_context,
        result_validity=result_validity,
        export_observation=export_observation,
        baseline_generation_mode=baseline_generation_mode,
        input_sha256=input_sha256,
        consumer_mode=consumer_mode,
        consumer_mode_explain=consumer_mode_explain,
    )


def _completeness_result_contract(
    *,
    snapshot_kind: str,
    alerts: list[dict[str, Any]],
    diagnoses: list[dict[str, Any]],
    result_validity: Any | None,
    consumer_mode: Any | None,
    consumer_mode_explain: Any | None,
) -> tuple[Any | None, Any | None, Any | None]:
    if snapshot_kind != "clipped_target" or result_validity is not None:
        return result_validity, consumer_mode, consumer_mode_explain

    blocking_objects = _non_exact_result_objects(alerts, diagnoses)
    if not blocking_objects:
        return result_validity, consumer_mode, consumer_mode_explain

    synthesized_result_validity = {"results": _result_validity_rows_for_subset(alerts, diagnoses)}
    final_consumer_mode = consumer_mode
    if final_consumer_mode is None:
        final_consumer_mode = {
            "compare": "REFERENCE_ONLY",
            "replay": "REFERENCE_ONLY",
            "audit": "REFERENCE_ONLY",
        }
    final_consumer_mode_explain = consumer_mode_explain
    if final_consumer_mode_explain is None:
        final_consumer_mode_explain = {
            "final_mode": final_consumer_mode,
            "decision_reasons": [
                "non_exact_support_level_present",
                "recomputed_subset_present",
            ],
            "blocking_objects": blocking_objects,
        }
    return synthesized_result_validity, final_consumer_mode, final_consumer_mode_explain


def _result_validity_rows_for_subset(
    alerts: list[dict[str, Any]],
    diagnoses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for alert in alerts:
        alert_id = alert.get("alert_id")
        if alert_id is None:
            continue
        rows.append(
            {
                "path": "result/alerts.json",
                "category": "result",
                "object_kind": "alert",
                "object_id": str(alert_id),
                "validity_scope": "evidence_subset",
                "derivation_mode": "recomputed_subset",
                "notes": "Completeness check recomputed this result from the clipped evidence subset.",
            }
        )
    for diagnosis in diagnoses:
        diag_id = diagnosis.get("diag_id")
        if diag_id is None:
            continue
        rows.append(
            {
                "path": "result/diagnoses.json",
                "category": "result",
                "object_kind": "diagnosis",
                "object_id": str(diag_id),
                "validity_scope": "evidence_subset",
                "derivation_mode": "recomputed_subset",
                "notes": "Completeness check recomputed this result from the clipped evidence subset.",
            }
        )
    return rows


def _non_exact_result_objects(
    alerts: list[dict[str, Any]],
    diagnoses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for alert in alerts:
        if not _is_non_exact_support(alert):
            continue
        alert_id = alert.get("alert_id")
        if alert_id is not None:
            objects.append(
                {
                    "object_kind": "alert",
                    "object_id": str(alert_id),
                    "support_level": str(alert.get("support_level") or "").strip(),
                }
            )
    for diagnosis in diagnoses:
        if not _is_non_exact_support(diagnosis):
            continue
        diag_id = diagnosis.get("diag_id")
        if diag_id is not None:
            objects.append(
                {
                    "object_kind": "diagnosis",
                    "object_id": str(diag_id),
                    "support_level": str(diagnosis.get("support_level") or "").strip(),
                }
            )
    return objects


def _is_non_exact_support(row: dict[str, Any]) -> bool:
    support = str((row or {}).get("support_level") or "exact").strip().lower()
    return bool(support and support != "exact")


def _source_backed_window_snapshot(
    *,
    controller: WorkspaceController,
    dataset_id: str,
    input_path: Path | None,
    comparison_scope: dict[str, Any],
    metric_config: MetricConfig,
    analysis_context: dict[str, Any],
    baseline_generation_mode: str,
) -> dict[str, Any]:
    record = controller.repository.get(dataset_id)
    t_begin, t_end = comparison_scope["time_window"]
    filters = comparison_scope.get("filter") or {}
    export = ExportService(controller.repository, controller.context_store, controller.jobs)
    export_bundle = export._build_export_bundle(record, "clipped", (float(t_begin), float(t_end)), filters)
    session_result = metric_Init(metric_config)
    if not session_result.ok:
        raise RuntimeError(session_result.message)
    session = session_result.data
    ingest_result = metric_Ingest(session, export_bundle)
    if not ingest_result.ok:
        raise RuntimeError(ingest_result.message)
    metrics_result = metric_Compute(session, float(t_begin), float(t_end), filters)
    if not metrics_result.ok:
        raise RuntimeError(metrics_result.message)
    alerts_result = alert_Evaluate(session, float(t_begin), float(t_end), filters)
    if not alerts_result.ok:
        raise RuntimeError(alerts_result.message)
    diagnoses_result = diag_Generate(session, float(t_begin), float(t_end), alerts_result.data or [])
    if not diagnoses_result.ok:
        raise RuntimeError(diagnoses_result.message)
    metrics = metrics_result.data or []
    alert_rows = serialize(alerts_result.data or [])
    diagnosis_rows = serialize(diagnoses_result.data or [])
    anchors = build_anchor_rows(dict(analysis_context or {}), alert_rows, diagnosis_rows)
    return build_analysis_snapshot(
        snapshot_kind="full_baseline",
        input_path=input_path,
        dataset_id=record.artifact.dataset_id,
        parser_version=PARSER_VERSION,
        comparison_scope=comparison_scope,
        metrics=metrics,
        alerts=alert_rows,
        diagnoses=diagnosis_rows,
        anchors=anchors,
        rebuild_bundle=export_bundle.to_dict(),
        ref_index=[],
        meta={},
        manifest={},
        package_path=None,
        analysis_context=analysis_context,
        export_observation={},
        baseline_generation_mode=baseline_generation_mode,
    )


def _load_dataset_for_baseline(
    controller: WorkspaceController,
    input_path: Path,
    mode: str,
    *,
    comparison_scope: dict[str, Any],
) -> str:
    if mode == "source_backed_window_scan":
        return _load_source_backed_window_seed(controller, input_path, comparison_scope)
    loaded = controller.viz_LoadDataset(str(input_path))
    if not loaded.ok:
        raise SystemExit(loaded.message)
    return str(loaded.data)


def _load_source_backed_window_seed(
    controller: WorkspaceController,
    input_path: Path,
    comparison_scope: dict[str, Any],
) -> str:
    prescan = prs_Prescan(input_path, include_task_state_preview=False)
    if not prescan.ok:
        raise SystemExit(prescan.message)
    preview = dict(prescan.data or {})
    header = GlobalHeader(**dict(preview.get("header") or {}))
    dataset_id = str(preview.get("dataset_id") or header.run_id or input_path.stem or "trace")
    seed_bundle = RebuildBundle(
        bundle_id=f"bundle:{dataset_id}:source-backed-seed",
        dataset_id=dataset_id,
        event_stream=[],
        task_states=[],
        exec_slices=[],
        resource_graph=ResourceGraph(),
        irq_spans=[],
        untrusted_windows=[],
        rebuild_rev=1,
        capability_flags={},
        header=header,
    )
    seed_artifact = DatasetArtifact(
        dataset_id=dataset_id,
        source=str(input_path),
        header=header,
        bundle=seed_bundle,
        dictionary_info={},
    )
    seed_record = DatasetRecord(artifact=seed_artifact, metric_session=metric_Init().data)
    export = ExportService(controller.repository, controller.context_store, controller.jobs)
    t_begin, t_end = comparison_scope["time_window"]
    filters = dict(comparison_scope.get("filter") or {})
    with tempfile.TemporaryDirectory(prefix="clipped-completeness-window-seed-") as temp_dir:
        temp_root = Path(temp_dir)
        event_trace_path = temp_root / "window.trace"
        ref_index_path = temp_root / "ref_index.json"
        seed_export_bundle = export._build_export_bundle(
            seed_record,
            "clipped",
            (float(t_begin), float(t_end)),
            filters,
        )
        event_summary_result = export._source_backed_event_summary(
            seed_record,
            seed_export_bundle,
            export_window=(float(t_begin), float(t_end)),
            export_filter=filters,
            required_event_ref_keys=set(),
            event_trace_path=event_trace_path,
            ref_index_path=ref_index_path,
            write_timings={},
        )
        if not event_summary_result.ok:
            raise SystemExit(event_summary_result.message)
        loaded_window = ParserProcessAgent(job_id="clipped-window-isolated").parse_rebuild(
            event_trace_path,
            artifact_policy={
                "artifact_dir": str(temp_root / "parser_process"),
                "materialize_event_stream": True,
                "load_artifact": True,
                "retain_artifact": False,
            },
        )
        if not loaded_window.ok:
            raise SystemExit(loaded_window.message)
        window_artifact = loaded_window.data["artifact"]
        window_artifact.source = str(input_path)
    registered = controller._register_artifact(window_artifact)
    if not registered.ok:
        raise SystemExit(registered.message)
    return str(registered.data)


def _new_run_state() -> dict[str, Any]:
    return {
        "current_stage": "not_started",
        "last_completed_stage": None,
        "baseline_generation_mode": None,
        "package_dir": None,
        "artifacts": {},
    }


def _begin_stage(run_state: dict[str, Any], stage: str) -> None:
    run_state["current_stage"] = stage


def _complete_stage(run_state: dict[str, Any]) -> None:
    run_state["last_completed_stage"] = run_state.get("current_stage")


def _install_termination_handlers(run_state: dict[str, Any]):
    previous_handlers: dict[signal.Signals, Any] = {}

    def handle_signal(signum: int, _frame: Any) -> None:
        signal_name = signal.Signals(signum).name
        run_state["termination_signal"] = int(signum)
        run_state["termination_signal_name"] = signal_name
        raise SystemExit(128 + int(signum))

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, handle_signal)
    except (OSError, RuntimeError, ValueError):
        for signum, previous in previous_handlers.items():
            try:
                signal.signal(signum, previous)
            except (OSError, RuntimeError, ValueError):
                pass
        return lambda: None

    def restore() -> None:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)

    return restore


def _best_effort_write_failure_state(output_dir: Path, run_state: dict[str, Any], exc: BaseException) -> None:
    try:
        _write_failure_state(output_dir, run_state, exc)
    except Exception:
        pass


def _write_failure_state(output_dir: Path, run_state: dict[str, Any], exc: BaseException) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = _artifact_refs(output_dir, run_state)
    failure_payload = _failure_payload(run_state, exc)
    failure_report = {
        "kind": "clipped_completeness_execution_failure",
        **failure_payload,
        "artifacts": artifacts,
    }

    report_write_error: dict[str, str] | None = None
    try:
        json_dump(output_dir / FAILURE_REPORT_JSON, failure_report)
        artifacts["failure_report_json"] = FAILURE_REPORT_JSON
    except Exception as report_exc:
        report_write_error = {
            "error_type": type(report_exc).__name__,
            "error_message": str(report_exc),
        }

    status_payload = {
        **failure_payload,
        **artifacts,
        "artifacts": artifacts,
        "failure_report_json": artifacts.get("failure_report_json"),
    }
    if report_write_error is not None:
        status_payload["failure_report_write_error"] = report_write_error
    _write_run_status(output_dir, **status_payload)


def _failure_payload(run_state: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    signal_name = run_state.get("termination_signal_name")
    status = "terminated" if signal_name else "failed"
    error_type = str(signal_name or type(exc).__name__)
    error_message = f"received {signal_name}" if signal_name else _exception_message(exc)
    return {
        "status": status,
        "current_stage": run_state.get("current_stage"),
        "failure_stage": run_state.get("current_stage"),
        "last_completed_stage": run_state.get("last_completed_stage"),
        "exit_code": _exit_code(exc),
        "error_type": error_type,
        "error_message": error_message,
        "baseline_generation_mode": run_state.get("baseline_generation_mode"),
        "package_dir": run_state.get("package_dir"),
    }


def _artifact_refs(output_dir: Path, run_state: dict[str, Any]) -> dict[str, Any]:
    artifacts = dict(run_state.get("artifacts") or {})
    for key, filename in (
        ("full_baseline_json", FULL_BASELINE_JSON),
        ("clipped_target_json", CLIPPED_TARGET_JSON),
        ("report_json", COMPLETENESS_REPORT_JSON),
        ("summary_md", "summary.md"),
    ):
        if (output_dir / filename).is_file():
            artifacts.setdefault(key, filename)
    package_dir = run_state.get("package_dir")
    if package_dir:
        artifacts.setdefault("package_dir", package_dir)
    return artifacts


def _exit_code(exc: BaseException) -> int:
    if not isinstance(exc, SystemExit):
        return 1
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


def _exception_message(exc: BaseException) -> str:
    if isinstance(exc, SystemExit):
        return "" if exc.code is None else str(exc.code)
    return str(exc)


def _write_run_status(output_dir: Path, **payload: Any) -> None:
    json_dump(
        output_dir / "run_status.json",
        {
            "status": payload.pop("status"),
            **payload,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )


def _snapshot_input_path(snapshot: dict[str, Any]) -> Path | None:
    raw_path = (snapshot.get("source") or {}).get("input_path")
    if not raw_path:
        return None
    return Path(str(raw_path))


def _apply_metric_config(controller: WorkspaceController, dataset_id: str, metric_config: MetricConfig) -> None:
    record = controller.repository.get(dataset_id)
    session_result = metric_Init(metric_config)
    if not session_result.ok:
        raise RuntimeError(session_result.message)
    ingest_result = metric_Ingest(session_result.data, record.artifact.bundle)
    if not ingest_result.ok:
        raise RuntimeError(ingest_result.message)
    record.metric_session = session_result.data


def _json_arg(raw: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"{label} must be a JSON object")
    return payload


def _os_peak_observation() -> dict[str, Any]:
    try:
        import resource
    except ImportError:
        return {"os_peak_supported": False, "os_peak": {}}
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "os_peak_supported": True,
        "os_peak": {
            "ru_maxrss": usage.ru_maxrss,
        },
    }


def _reused_package_export_observation(
    package_dir: Path,
    raw_meta: dict[str, Any],
    raw_manifest: dict[str, Any],
) -> dict[str, Any]:
    for payload in (raw_meta, raw_manifest):
        for key in ("export_resource_observation", "export_observation"):
            value = payload.get(key)
            if isinstance(value, dict):
                return dict(value)
    target_path = package_dir.parent / "clipped_target.json"
    if not target_path.is_file():
        return {}
    try:
        clipped_target = json_load(target_path)
    except (OSError, ValueError, TypeError):
        return {}
    if not _snapshot_package_matches(clipped_target, package_dir, target_path.parent):
        return {}
    value = clipped_target.get("export_resource_observation")
    return dict(value) if isinstance(value, dict) else {}


def _snapshot_package_matches(snapshot: dict[str, Any], package_dir: Path, snapshot_dir: Path) -> bool:
    raw_package = (snapshot.get("source") or {}).get("package_path")
    if not raw_package:
        return True
    package_text = str(raw_package)
    candidates = [Path(package_text)]
    if not Path(package_text).is_absolute():
        candidates.append(ROOT / package_text)
        candidates.append(snapshot_dir / package_text)
    try:
        expected = package_dir.resolve()
    except OSError:
        expected = package_dir
    for candidate in candidates:
        try:
            if candidate.resolve() == expected:
                return True
        except OSError:
            continue
    return False


def _quote_arg(value: str) -> str:
    if not value:
        return "''"
    if all(ch.isalnum() or ch in "._/-=:" for ch in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
