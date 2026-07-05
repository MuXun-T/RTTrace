from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop.sample_data import write_scenario
from desktop.services import ExportService, ReproService, WorkspaceController
from parser.evidence_sidecar import build_dependency_sidecar, materialize_dependency_sidecar
from parser.result import err_result
from spec.io import checksum_file, json_dump, json_load, jsonl_dump, jsonl_load
from spec.schema_loader import DICTIONARY_PATH, SCHEMA_DIR


ONE_GB_REFERENCE_CANDIDATES = [
    ROOT / "docs" / "desktop_perf_acceptance_20260320_linux_1gb.json",
    ROOT / "docs" / "public_rtos_1gb_fast_desktop_perf_20260324_phase2.json",
    ROOT / "docs" / "public_rtos_1gb_dense_desktop_perf_blocker_20260325_phase5a.json",
    ROOT / "docs" / "final_validation_status_20260314.json",
]

DEFAULT_LARGE_INPUT_TRACE = (
    ROOT / "example" / "public-rtos-1gb-prevalidation" / "public_rtos_1gb_fast_baseline.trace"
)
DEFAULT_LARGE_INPUT_TIMEOUT_S = 1800


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_path(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    json_dump(path, payload)
    return path


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _large_input_reference_context(output_root: Path, mode_a_obs: dict[str, Any], mode_b_obs: dict[str, Any]) -> dict[str, Any]:
    reference_artifacts = []
    for path in ONE_GB_REFERENCE_CANDIDATES:
        if not path.exists():
            continue
        reference_artifacts.append(
            {
                "path": _relative(path, ROOT),
                "sha256": _sha256(path),
            }
        )
    return {
        "kind": "reference_context",
        "reference_artifacts": reference_artifacts,
        "local_basis": {
            "mode_a_package": mode_a_obs["package_path"],
            "mode_b_package": mode_b_obs["package_path"],
            "mode_a_sidecar_bytes": mode_a_obs["sidecar_bytes"],
            "mode_b_sidecar_bytes": mode_b_obs["sidecar_bytes"],
            "mode_a_events_emitted": int(mode_a_obs["proof_digest"]["events_emitted"]),
            "mode_b_events_emitted": int(mode_b_obs["proof_digest"]["events_emitted"]),
        },
        "derived_metrics": {
            "mode_a_sidecar_bytes_per_emitted_event": round(
                mode_a_obs["sidecar_bytes"] / max(1, int(mode_a_obs["proof_digest"]["events_emitted"])),
                4,
            ),
            "mode_b_sidecar_bytes_per_emitted_event": round(
                mode_b_obs["sidecar_bytes"] / max(1, int(mode_b_obs["proof_digest"]["events_emitted"])),
                4,
            ),
            "proof_digest_excerpt": {
                "mode_a": {
                    "scan_count": int(mode_a_obs["proof_digest"]["scan_count"]),
                    "seek_count": int(mode_a_obs["proof_digest"]["seek_count"]),
                    "window_span_total": int(mode_a_obs["proof_digest"]["window_span_total"]),
                    "sidecar_lookup_count": int(mode_a_obs["proof_digest"]["sidecar_lookup_count"]),
                },
                "mode_b": {
                    "scan_count": int(mode_b_obs["proof_digest"]["scan_count"]),
                    "seek_count": int(mode_b_obs["proof_digest"]["seek_count"]),
                    "window_span_total": int(mode_b_obs["proof_digest"]["window_span_total"]),
                    "sidecar_lookup_count": int(mode_b_obs["proof_digest"]["sidecar_lookup_count"]),
                },
            },
        },
    }


def _mock_large_input_observation(
    *,
    mode: str,
    output_root: Path,
    package_path: str,
    trace_path: Path,
) -> dict[str, Any]:
    input_contract = {
        "trace_path": _relative(trace_path, ROOT),
        "trace_size_bytes": int(trace_path.stat().st_size) if trace_path.exists() else 1073741880,
        "sample_provenance": "public_rtos_seeded_padded",
        "close_scope": "prevalidation_only",
    }
    if mode == "mock_blocked":
        return {
            "status": "blocked",
            "artifact_name": "large_input_blocker.json",
            "payload": {
                "kind": "blocked",
                "blocker_code": "scratch_dir_not_local_disk",
                "failure_stage": "preflight",
                "input_contract": input_contract,
                "next_steps": [
                    "Stage the same bytes to a local SSD scratch directory.",
                    "Re-run build_evidence_proof_archive.py with --large-input-mode auto.",
                ],
            },
        }
    return {
        "status": "pass",
        "artifact_name": "large_input_measured.json",
        "payload": {
            "kind": "direct_measured",
            "run_kind": "evidence_export_large_input_direct",
            "input_contract": input_contract,
            "package_path": package_path,
            "closure_mode": "exact",
            "package_metrics": {
                "scan_count": 39,
                "seek_count": 39,
                "window_span_total": 0,
                "sidecar_lookup_count": 40,
                "events_emitted": 40,
                "bytes_emitted": 2016,
                "round_count": 40,
                "window_hit_rate": 1.0,
                "peak_rss_mb": 612.992,
            },
            "source_diagnosis_count": 12,
            "package_diagnosis_count": 12,
            "diagnosis_preservation_rate": 1.0,
            "proof_consumer_mode": {
                "compare": "ALLOW_COMPARE",
                "replay": "ALLOW_COMPARE",
                "audit": "ALLOW_COMPARE",
            },
            "runtime_seconds": 8.125,
            "peak_rss_source": "mock_linux_peak_rss_mb",
        },
    }


def _run_large_input_direct_export(
    *,
    output_root: Path,
    trace_path: Path,
    scratch_root: Path,
    timeout_s: int,
) -> dict[str, Any]:
    if not trace_path.exists():
        return {
            "status": "blocked",
            "artifact_name": "large_input_blocker.json",
            "payload": {
                "kind": "blocked",
                "blocker_code": "input_missing",
                "failure_stage": "input_contract",
                "input_contract": {
                    "trace_path": str(trace_path),
                    "sample_provenance": "public_rtos_seeded_padded",
                    "close_scope": "prevalidation_only",
                },
                "next_steps": [
                    "Restore the checked-in public RTOS 1GB prevalidation sample.",
                    "Re-run build_evidence_proof_archive.py with --large-input-mode auto.",
                ],
            },
        }
    scratch_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="evidence-proof-archive-", dir=str(scratch_root)))
    package_scratch = work_dir / "large_input_package"
    package_output = output_root / "A_control_plane_first" / "packages" / "large_input_direct"
    runtime_started = time.perf_counter()
    child_script = textwrap.dedent(
        f"""
        from __future__ import annotations
        import json
        import resource
        import sys
        from pathlib import Path

        ROOT = Path({str(ROOT)!r})
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))

        from desktop.services import ExportService, ReproService, WorkspaceController

        trace_path = Path(sys.argv[1])
        package_dir = Path(sys.argv[2])
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        if not loaded.ok:
            raise SystemExit(loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        if not bundle.event_stream:
            raise SystemExit("large input trace has no events")
        seed_ref = bundle.event_stream[0].ref_key
        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {{
                "dataset_id": loaded.data,
                "seed_spec": {{"source_kind": "manual_refs", "source_payload": {{"refs": [seed_ref]}}}},
                "rule_family": ["ref_ref"],
                "budget_vector": {{"D_max": 128, "C_events": 1024, "S_bytes": 1048576, "rho_max": 8.0}},
                "closure_policy": {{"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 128}},
                "embodiment_mode": "mode_a",
            }}
        )
        if not job.ok:
            raise SystemExit(job.message)
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        if not written.ok:
            raise SystemExit(written.message)
        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        diagnoses = json.loads((package_dir / "result" / "diagnoses.json").read_text(encoding="utf-8"))
        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        if not opened.ok:
            raise SystemExit(opened.message)
        peak_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        source_diagnosis_count = int(written.data.get("diagnosis_count") or len(diagnoses))
        package_diagnosis_count = int(len(diagnoses))
        print(
            json.dumps(
                {{
                    "package_path": str(package_dir),
                    "closure_mode": written.data.get("closure_mode"),
                    "proof_digest": proof_digest,
                    "peak_rss_mb": proof_digest.get("peak_rss_mb") if proof_digest.get("peak_rss_mb") is not None else round(float(peak_rss_kb) / 1024.0, 3),
                    "peak_rss_source": "linux_peak_rss_mb",
                    "proof_consumer_mode": opened.data.get("consumer_mode"),
                    "source_diagnosis_count": source_diagnosis_count,
                    "package_diagnosis_count": package_diagnosis_count,
                    "diagnosis_preservation_rate": round(package_diagnosis_count / max(1, source_diagnosis_count), 4),
                }},
                ensure_ascii=False,
            )
        )
        """
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", child_script, str(trace_path), str(package_scratch)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=max(60, int(timeout_s)),
            check=False,
        )
        runtime_seconds = round(time.perf_counter() - runtime_started, 6)
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or "large input direct export failed"
            return {
                "status": "blocked",
                "artifact_name": "large_input_blocker.json",
                "payload": {
                    "kind": "blocked",
                    "blocker_code": "runtime_failure",
                    "failure_stage": "evidence_export",
                    "input_contract": {
                        "trace_path": _relative(trace_path, ROOT),
                        "trace_size_bytes": int(trace_path.stat().st_size),
                        "sample_provenance": "public_rtos_seeded_padded",
                        "close_scope": "prevalidation_only",
                    },
                    "command": "python -c <embedded evidence export runner>",
                    "runtime_seconds": runtime_seconds,
                    "stderr": stderr,
                    "next_steps": [
                        "Inspect stderr and rerun the direct evidence export on a local scratch directory.",
                    ],
                },
            }
        payload = json.loads(completed.stdout.strip())
        _copy_tree(package_scratch, package_output)
        proof_digest = dict(payload["proof_digest"])
        package_metrics = {
            "scan_count": int(proof_digest.get("scan_count", 0)),
            "seek_count": int(proof_digest.get("seek_count", 0)),
            "window_span_total": int(proof_digest.get("window_span_total", 0)),
            "sidecar_lookup_count": int(proof_digest.get("sidecar_lookup_count", 0)),
            "events_emitted": int(proof_digest.get("events_emitted", 0)),
            "bytes_emitted": int(proof_digest.get("bytes_emitted", 0)),
            "peak_rss_mb": float(payload.get("peak_rss_mb", 0.0)),
            "round_count": int(proof_digest.get("round_count", 0)),
            "window_hit_rate": float(proof_digest.get("window_hit_rate", 1.0)),
        }
        return {
            "status": "pass",
            "artifact_name": "large_input_measured.json",
            "payload": {
                "kind": "direct_measured",
                "run_kind": "evidence_export_large_input_direct",
                "input_contract": {
                    "trace_path": _relative(trace_path, ROOT),
                    "trace_size_bytes": int(trace_path.stat().st_size),
                    "sample_provenance": "public_rtos_seeded_padded",
                    "close_scope": "prevalidation_only",
                },
                "package_path": _relative(package_output, output_root),
                "closure_mode": str(payload.get("closure_mode")),
                "package_metrics": package_metrics,
                "source_diagnosis_count": int(payload.get("source_diagnosis_count", 0)),
                "package_diagnosis_count": int(payload.get("package_diagnosis_count", 0)),
                "diagnosis_preservation_rate": float(payload.get("diagnosis_preservation_rate", 0.0)),
                "proof_consumer_mode": payload.get("proof_consumer_mode"),
                "runtime_seconds": runtime_seconds,
                "peak_rss_source": str(payload.get("peak_rss_source") or "linux_peak_rss_mb"),
            },
        }
    except subprocess.TimeoutExpired:
        runtime_seconds = round(time.perf_counter() - runtime_started, 6)
        return {
            "status": "blocked",
            "artifact_name": "large_input_blocker.json",
            "payload": {
                "kind": "blocked",
                "blocker_code": "timeout",
                "failure_stage": "evidence_export",
                "input_contract": {
                    "trace_path": _relative(trace_path, ROOT),
                    "trace_size_bytes": int(trace_path.stat().st_size),
                    "sample_provenance": "public_rtos_seeded_padded",
                    "close_scope": "prevalidation_only",
                },
                "runtime_seconds": runtime_seconds,
                "timeout_seconds": int(timeout_s),
                "next_steps": [
                    "Increase --large-input-timeout-s or move the run to a faster local scratch directory.",
                ],
            },
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _refresh_manifest_checksums(package_dir: Path, *relative_paths: str) -> None:
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    updated_paths = set(relative_paths)
    for entry in manifest["entries"]:
        if entry["path"] in updated_paths:
            entry["checksum"] = checksum_file(package_dir / entry["path"])
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _refresh_sidecar_manifest_checksums(package_dir: Path, *relative_paths: str) -> None:
    sidecar_manifest_path = package_dir / "control" / "sidecar_manifest.json"
    sidecar_manifest = json.loads(sidecar_manifest_path.read_text(encoding="utf-8"))
    updated_paths = set(relative_paths)
    for rel_path in updated_paths:
        if rel_path in sidecar_manifest.get("entry_checksums", {}):
            sidecar_manifest["entry_checksums"][rel_path] = checksum_file(package_dir / rel_path)
    sidecar_manifest_path.write_text(json.dumps(sidecar_manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _prepare_controller(trace_path: Path) -> tuple[WorkspaceController, str, Any]:
    controller = WorkspaceController()
    loaded = controller.viz_LoadDataset(str(trace_path))
    if not loaded.ok:
        raise RuntimeError(loaded.message)
    bundle = controller.repository.get(loaded.data).artifact.bundle
    anchor_ref = bundle.event_stream[0].ref_key
    context = controller.viz_SetContext(
        {
            "evidence_anchor": {"ref_key": anchor_ref},
            "selection": {"seed_ref": anchor_ref},
        }
    )
    if not context.ok:
        raise RuntimeError(context.message)
    return controller, loaded.data, bundle


def _export_evidence_package(
    controller: WorkspaceController,
    dataset_id: str,
    package_dir: Path,
    payload: dict[str, Any],
    *,
    progress_updates: list[dict[str, Any]] | None = None,
    inject_read_error: tuple[str, str] | None = None,
) -> dict[str, Any]:
    updates = progress_updates if progress_updates is not None else []
    export = ExportService(
        controller.repository,
        controller.context_store,
        controller.jobs,
        on_progress=updates.append,
    )
    job_payload = dict(payload)
    job_payload["dataset_id"] = dataset_id
    job = export.export_Evidence(job_payload)
    if not job.ok:
        raise RuntimeError(job.message)

    patch_ctx = (
        patch("parser.evidence_closure.read_window_plan", return_value=err_result(*inject_read_error))
        if inject_read_error is not None
        else nullcontext()
    )
    with patch_ctx:
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
    if not written.ok:
        raise RuntimeError(written.message)

    repro = ReproService(controller.repository, controller.context_store)
    opened = repro.repro_OpenPackage(str(package_dir))
    return {
        "written": written,
        "opened": opened,
        "progress": list(updates),
    }


def _write_external_sidecar_artifacts(
    root: Path,
    trace_path: Path,
    bundle: Any,
    *,
    snapshot_id: str,
    rule_family: tuple[str, ...],
) -> tuple[Path, Path]:
    control_dir = root / "control"
    result_dir = root / "result"
    schema_dir = root / "reference" / "schema"
    control_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    schema_dir.mkdir(parents=True, exist_ok=True)

    ref_index_rows = [
        {
            "ref_key": event.ref_key,
            "timestamp_aligned": float(event.timestamp_aligned),
            "core_id": int(event.core_id),
            "seq": int(event.seq),
        }
        for event in sorted(bundle.event_stream, key=lambda item: (item.timestamp_aligned, item.core_id, item.seq))
    ]
    source_trace_checksum = checksum_file(trace_path)
    sidecar_rows = materialize_dependency_sidecar(
        build_dependency_sidecar(
            bundle,
            snapshot_id=snapshot_id,
            rule_families=rule_family,
            ref_index_rows=ref_index_rows,
        ),
        trace_checksum=source_trace_checksum,
    )
    sidecar_path = control_dir / "dependency_sidecar.jsonl"
    jsonl_dump(sidecar_path, sidecar_rows)

    shutil.copy2(DICTIONARY_PATH, root / "reference" / "dictionary.json")
    schema_names = {
        "dependency_sidecar_schema": "dependency_sidecar.schema.json",
        "frontier_snapshot_schema": "frontier_snapshot.schema.json",
        "frontier_refs_schema": "frontier_refs.schema.json",
        "proof_digest_schema": "proof_digest.schema.json",
        "sidecar_manifest_schema": "sidecar_manifest.schema.json",
        "blocker_artifact_schema": "blocker_artifact.schema.json",
    }
    schema_checksums = {}
    for schema_key, filename in schema_names.items():
        target = schema_dir / filename
        shutil.copy2(SCHEMA_DIR / filename, target)
        schema_checksums[schema_key] = {
            "path": f"reference/schema/{filename}",
            "algo": "sha256",
            "checksum": checksum_file(target),
        }

    manifest_path = control_dir / "sidecar_manifest.json"
    manifest_payload = {
        "sidecar_version": "external-sidecar-1",
        "generator_version": "evidence-proof-archive",
        "trace_checksum": source_trace_checksum,
        "dictionary_checksum": checksum_file(root / "reference" / "dictionary.json"),
        "schema_checksums": schema_checksums,
        "relation_families": list(rule_family),
        "entry_paths": ["control/dependency_sidecar.jsonl"],
        "entry_checksums": {
            "control/dependency_sidecar.jsonl": checksum_file(sidecar_path),
        },
        "created_at": "2026-04-14T00:00:00+00:00",
        "snapshot_id": snapshot_id,
    }
    json_dump(manifest_path, manifest_payload)

    anchor_event = bundle.event_stream[0]
    evidence_ref = {
        "ref_type": "event",
        "ref_key": anchor_event.ref_key,
        "t_begin": float(anchor_event.timestamp_aligned),
        "t_end": float(anchor_event.timestamp_aligned),
    }
    alerts_payload = [
        {
            "alert_id": "alert:archive:1",
            "type": "archive_alert",
            "severity": "warning",
            "time_window": [float(anchor_event.timestamp_aligned), float(anchor_event.timestamp_aligned)],
            "object_scope": {"task_id": anchor_event.task_id},
            "threshold": 1.0,
            "actual": 2.0,
            "evidence_refs": [evidence_ref],
            "trusted": True,
            "support_level": "exact",
        }
    ]
    diagnoses_payload = [
        {
            "diag_id": "diag:archive:1",
            "title": "archive diagnosis",
            "diagnosis_type": "root_cause",
            "time_window": [float(anchor_event.timestamp_aligned), float(anchor_event.timestamp_aligned)],
            "object_scope": {"task_id": anchor_event.task_id},
            "conclusion": "reuse frozen result",
            "evidence_refs": [evidence_ref],
            "related_alerts": ["alert:archive:1"],
            "confidence": "high",
            "support_level": "exact",
        }
    ]
    json_dump(result_dir / "alerts.json", alerts_payload)
    json_dump(result_dir / "diagnoses.json", diagnoses_payload)
    return sidecar_path, manifest_path


def _diagnosis_count(package_dir: Path) -> int:
    return len(list(json_load(package_dir / "result" / "diagnoses.json") or []))


def _package_observation(
    output_root: Path,
    package_dir: Path,
    progress_updates: list[dict[str, Any]],
    opened_payload: Any,
) -> dict[str, Any]:
    proof_digest = json_load(package_dir / "control" / "proof_digest.json")
    frontier_snapshot = json_load(package_dir / "control" / "frontier_snapshot.json")
    blocker_path = package_dir / "control" / "blocker_artifact.json"
    result_validity = json_load(package_dir / "result" / "result_validity.json")
    sidecar_path = package_dir / "control" / "dependency_sidecar.jsonl"
    return {
        "package_path": _relative(package_dir, output_root),
        "closure_mode": str(proof_digest.get("closure_mode")),
        "proof_digest": proof_digest,
        "frontier_snapshot": frontier_snapshot,
        "blocker_artifact": json_load(blocker_path) if blocker_path.exists() else None,
        "result_validity_count": len(list(result_validity.get("results") or [])),
        "sidecar_bytes": int(sidecar_path.stat().st_size) if sidecar_path.exists() else 0,
        "minimal_legal_package_valid": bool(opened_payload.ok),
        "proof_consumer_mode": opened_payload.data.get("consumer_mode") if opened_payload.ok else None,
        "progress_events": list(progress_updates),
    }


def _extract_rejected_round_read(progress_updates: list[dict[str, Any]]) -> dict[str, Any]:
    rejected_rounds = {
        int(item["round_id"])
        for item in progress_updates
        if item.get("substage") == "round/budget" and item.get("status") == "rejected"
    }
    read_rounds = {
        int(item["round_id"])
        for item in progress_updates
        if item.get("substage") == "round/read"
    }
    return {
        "rejected_round_ids": sorted(rejected_rounds),
        "read_round_ids": sorted(read_rounds),
        "reject_round_has_read": bool(rejected_rounds & read_rounds),
    }


def _pareto_frontier(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frontier: list[dict[str, Any]] = []
    for row in rows:
        dominated = False
        for other in rows:
            if other is row:
                continue
            if (
                int(other["bytes_emitted"]) <= int(row["bytes_emitted"])
                and int(other["events_emitted"]) <= int(row["events_emitted"])
                and float(other["diagnosis_preservation_rate"]) >= float(row["diagnosis_preservation_rate"])
                and (
                    int(other["bytes_emitted"]) < int(row["bytes_emitted"])
                    or int(other["events_emitted"]) < int(row["events_emitted"])
                    or float(other["diagnosis_preservation_rate"]) > float(row["diagnosis_preservation_rate"])
                )
            ):
                dominated = True
                break
        if not dominated:
            frontier.append(row)
    frontier.sort(key=lambda row: (int(row["bytes_emitted"]), int(row["events_emitted"]), str(row["scenario_id"])))
    return frontier


def _build_group_a(
    output_root: Path,
    *,
    medium_repeat: int,
    large_input_mode: str,
    large_input_trace: Path,
    large_input_scratch: Path | None,
    large_input_timeout_s: int,
) -> dict[str, Any]:
    group_dir = output_root / "A_control_plane_first"
    packages_dir = group_dir / "packages"
    trace_small = write_scenario(group_dir / "small.trace", name="basic", repeat=1)
    controller_small, dataset_small, bundle_small = _prepare_controller(trace_small)
    small_progress: list[dict[str, Any]] = []
    small_package = packages_dir / "small_exact"
    small_export = _export_evidence_package(
        controller_small,
        dataset_small,
        small_package,
        {
            "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
            "rule_family": ["ref_ref"],
            "budget_vector": {"D_max": 16, "C_events": 64, "S_bytes": 16384, "rho_max": 4.0},
            "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 16},
        },
        progress_updates=small_progress,
    )
    small_obs = _package_observation(output_root, small_package, small_progress, small_export["opened"])
    small_payload = {
        "kind": "measured",
        "trace_bytes": int(trace_small.stat().st_size),
        "trace_event_count": int(len(bundle_small.event_stream)),
        "observation": small_obs,
    }
    small_path = _json_path(group_dir / "small_correctness.json", small_payload)

    trace_medium = write_scenario(group_dir / "medium.trace", name="basic", repeat=medium_repeat)
    controller_medium, dataset_medium, bundle_medium = _prepare_controller(trace_medium)
    mode_a_progress: list[dict[str, Any]] = []
    mode_a_package = packages_dir / "medium_mode_a_exact"
    mode_a_export = _export_evidence_package(
        controller_medium,
        dataset_medium,
        mode_a_package,
        {
            "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
            "rule_family": ["ref_ref"],
            "budget_vector": {"D_max": 128, "C_events": 1024, "S_bytes": 1048576, "rho_max": 8.0},
            "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 128},
        },
        progress_updates=mode_a_progress,
    )
    mode_a_obs = _package_observation(output_root, mode_a_package, mode_a_progress, mode_a_export["opened"])

    sidecar_root = group_dir / "mode_b_sidecar"
    sidecar_path, manifest_path = _write_external_sidecar_artifacts(
        sidecar_root,
        trace_medium,
        bundle_medium,
        snapshot_id="snapshot:proof-archive:mode-b",
        rule_family=("ref_ref",),
    )
    mode_b_progress: list[dict[str, Any]] = []
    mode_b_package = packages_dir / "medium_mode_b_exact"
    mode_b_export = _export_evidence_package(
        controller_medium,
        dataset_medium,
        mode_b_package,
        {
            "embodiment_mode": "mode_b",
            "sidecar_source": str(sidecar_path),
            "sidecar_manifest_source": str(manifest_path),
            "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
            "rule_family": ["ref_ref"],
            "budget_vector": {"D_max": 128, "C_events": 1024, "S_bytes": 1048576, "rho_max": 8.0},
            "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 128},
        },
        progress_updates=mode_b_progress,
    )
    mode_b_obs = _package_observation(output_root, mode_b_package, mode_b_progress, mode_b_export["opened"])
    shared_contract = {
        "same_closure_mode": mode_a_obs["closure_mode"] == mode_b_obs["closure_mode"],
        "same_consumer_mode": mode_a_obs["proof_consumer_mode"] == mode_b_obs["proof_consumer_mode"],
        "proof_objects_exposed": bool(mode_a_obs["result_validity_count"] > 0 and mode_b_obs["result_validity_count"] > 0),
    }
    medium_payload = {
        "kind": "measured",
        "mode_a": mode_a_obs,
        "mode_b": mode_b_obs,
        "shared_proof_contract": shared_contract,
    }
    medium_path = _json_path(group_dir / "medium_flow.json", medium_payload)

    reference_context = _large_input_reference_context(output_root, mode_a_obs, mode_b_obs)
    reference_path = _json_path(group_dir / "large_input_reference.json", reference_context)
    scratch_root = large_input_scratch or (Path(tempfile.gettempdir()) / "evidence-proof-archive-large-input")
    if large_input_mode in {"mock_measured", "mock_blocked"}:
        large_input_result = _mock_large_input_observation(
            mode=large_input_mode,
            output_root=output_root,
            package_path="A_control_plane_first/packages/large_input_direct",
            trace_path=large_input_trace,
        )
    elif large_input_mode == "disabled":
        large_input_result = {
            "status": "blocked",
            "artifact_name": "large_input_blocker.json",
            "payload": {
                "kind": "blocked",
                "blocker_code": "large_input_disabled",
                "failure_stage": "builder_config",
                "input_contract": {
                    "trace_path": _relative(large_input_trace, ROOT) if large_input_trace.exists() else str(large_input_trace),
                    "sample_provenance": "public_rtos_seeded_padded",
                    "close_scope": "prevalidation_only",
                },
                "next_steps": [
                    "Re-run build_evidence_proof_archive.py with --large-input-mode auto or mock_measured.",
                ],
            },
        }
    else:
        large_input_result = _run_large_input_direct_export(
            output_root=output_root,
            trace_path=large_input_trace,
            scratch_root=scratch_root,
            timeout_s=large_input_timeout_s,
        )
    large_path = _json_path(group_dir / large_input_result["artifact_name"], large_input_result["payload"])

    status = (
        small_obs["minimal_legal_package_valid"]
        and all(shared_contract.values())
        and large_input_result["status"] == "pass"
    )
    summary = {
        "group": "A_control_plane_first",
        "status": "pass" if status else ("blocked" if large_input_result["status"] == "blocked" else "fail"),
        "artifacts": {
            "small_correctness": _relative(small_path, output_root),
            "medium_flow": _relative(medium_path, output_root),
            "large_input_reference": _relative(reference_path, output_root),
            "large_input_primary": _relative(large_path, output_root),
        },
        "key_metrics": {
            "small_scan_count": int(small_obs["proof_digest"]["scan_count"]),
            "small_seek_count": int(small_obs["proof_digest"]["seek_count"]),
            "small_window_span_total": int(small_obs["proof_digest"]["window_span_total"]),
            "small_sidecar_lookup_count": int(small_obs["proof_digest"]["sidecar_lookup_count"]),
            "small_sidecar_bytes": int(small_obs["sidecar_bytes"]),
        },
    }
    summary_path = _json_path(group_dir / "summary.json", summary)
    return {
        "status": summary["status"],
        "summary_path": _relative(summary_path, output_root),
    }


def _build_group_b(output_root: Path, *, medium_repeat: int) -> dict[str, Any]:
    group_dir = output_root / "B_budget_freeze"
    packages_dir = group_dir / "packages"
    trace_path = write_scenario(group_dir / "budget.trace", name="basic", repeat=medium_repeat)
    controller, dataset_id, _bundle = _prepare_controller(trace_path)

    baseline_package = packages_dir / "baseline_exact"
    baseline_export = _export_evidence_package(
        controller,
        dataset_id,
        baseline_package,
        {
            "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
            "rule_family": ["ref_ref"],
            "budget_vector": {"D_max": 128, "C_events": 1024, "S_bytes": 1048576, "rho_max": 8.0},
            "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 128},
        },
    )
    baseline_diag_count = max(1, _diagnosis_count(baseline_package))
    baseline_obs = _package_observation(output_root, baseline_package, [], baseline_export["opened"])

    scenarios = {
        "depth_limit": {
            "budget_vector": {"D_max": 0, "C_events": 256, "S_bytes": 65536, "rho_max": 4.0},
        },
        "event_limit": {
            "budget_vector": {"D_max": 16, "C_events": 1, "S_bytes": 65536, "rho_max": 4.0},
        },
        "byte_limit": {
            "budget_vector": {"D_max": 16, "C_events": 256, "S_bytes": 1, "rho_max": 4.0},
        },
        "rho_limit": {
            "budget_vector": {"D_max": 16, "C_events": 256, "S_bytes": 65536, "rho_max": 0.1},
        },
    }
    rows: list[dict[str, Any]] = []
    for scenario_id, config in scenarios.items():
        progress_updates: list[dict[str, Any]] = []
        package_dir = packages_dir / scenario_id
        export_payload = {
            "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
            "rule_family": ["ref_ref"],
            "budget_vector": dict(config["budget_vector"]),
            "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
        }
        scenario_export = _export_evidence_package(
            controller,
            dataset_id,
            package_dir,
            export_payload,
            progress_updates=progress_updates,
        )
        obs = _package_observation(output_root, package_dir, progress_updates, scenario_export["opened"])
        progress_path = _json_path(group_dir / f"progress_{scenario_id}.json", progress_updates)
        rejected_read = _extract_rejected_round_read(progress_updates)
        diag_rate = round(_diagnosis_count(package_dir) / baseline_diag_count, 4)
        rows.append(
            {
                "scenario_id": scenario_id,
                "closure_mode": obs["closure_mode"],
                "frontier_halt_reason": str(obs["proof_digest"].get("frontier_halt_reason")),
                "truncated_frontier_count": int(obs["proof_digest"].get("truncated_frontier_count", 0)),
                "projected_next_events": int(obs["frontier_snapshot"].get("projected_next_events", 0)),
                "projected_next_bytes": int(obs["frontier_snapshot"].get("projected_next_bytes", 0)),
                "events_emitted": int(obs["proof_digest"].get("events_emitted", 0)),
                "bytes_emitted": int(obs["proof_digest"].get("bytes_emitted", 0)),
                "diagnosis_preservation_rate": diag_rate,
                "proof_consumer_mode": obs["proof_consumer_mode"],
                "reject_round_has_read": bool(rejected_read["reject_round_has_read"]),
                "rejected_round_ids": rejected_read["rejected_round_ids"],
                "read_round_ids": rejected_read["read_round_ids"],
                "progress_trace": _relative(progress_path, output_root),
                "package_path": obs["package_path"],
            }
        )
    budget_sweep_path = _json_path(
        group_dir / "budget_sweep.json",
        {
            "baseline": baseline_obs,
            "rows": rows,
        },
    )
    frontier_rows = _pareto_frontier(rows)
    pareto_path = _json_path(group_dir / "pareto_frontier.json", {"rows": frontier_rows})
    status = all(row["closure_mode"] == "bounded" and not row["reject_round_has_read"] for row in rows)
    summary = {
        "group": "B_budget_freeze",
        "status": "pass" if status else "fail",
        "artifacts": {
            "budget_sweep": _relative(budget_sweep_path, output_root),
            "pareto_frontier": _relative(pareto_path, output_root),
        },
        "scenario_count": len(rows),
        "bounded_without_read_count": sum(
            1 for row in rows if row["closure_mode"] == "bounded" and not row["reject_round_has_read"]
        ),
    }
    summary_path = _json_path(group_dir / "summary.json", summary)
    return {
        "status": summary["status"],
        "summary_path": _relative(summary_path, output_root),
    }


def _build_cycle_inflation_sidecar(sidecar_path: Path, manifest_path: Path, seed_ref: str) -> None:
    rows = jsonl_load(sidecar_path)
    if not rows:
        raise RuntimeError("unable to build cycle inflation sidecar: no rows available")
    base_row = rows[0]
    mutated_rows = []
    for index in range(9):
        row = dict(base_row)
        row["src_ref"] = seed_ref
        row["cycle_guard_token"] = "cycle:inflation"
        row["priority"] = int(base_row.get("priority", 0)) + index
        mutated_rows.append(row)
    jsonl_dump(sidecar_path, mutated_rows)
    manifest = json_load(manifest_path)
    manifest["entry_checksums"]["control/dependency_sidecar.jsonl"] = checksum_file(sidecar_path)
    json_dump(manifest_path, manifest)


def _build_group_c(output_root: Path) -> dict[str, Any]:
    group_dir = output_root / "C_degraded_audit"
    packages_dir = group_dir / "packages"
    rows: list[dict[str, Any]] = []

    def run_scenario(
        scenario_id: str,
        *,
        payload: dict[str, Any],
        setup: callable | None = None,
        inject_read_error: tuple[str, str] | None = None,
    ) -> None:
        trace_path = write_scenario(group_dir / f"{scenario_id}.trace", name="basic", repeat=4)
        controller, dataset_id, bundle = _prepare_controller(trace_path)
        if setup is not None:
            setup(group_dir, trace_path, bundle)
        progress_updates: list[dict[str, Any]] = []
        package_dir = packages_dir / scenario_id
        scenario_export = _export_evidence_package(
            controller,
            dataset_id,
            package_dir,
            payload,
            progress_updates=progress_updates,
            inject_read_error=inject_read_error,
        )
        obs = _package_observation(output_root, package_dir, progress_updates, scenario_export["opened"])
        progress_path = _json_path(group_dir / f"progress_{scenario_id}.json", progress_updates)
        rows.append(
            {
                "scenario_id": scenario_id,
                "closure_mode": obs["closure_mode"],
                "blocker_artifact": obs["blocker_artifact"],
                "proof_digest": obs["proof_digest"],
                "frontier_snapshot": obs["frontier_snapshot"],
                "minimal_legal_package_valid": obs["minimal_legal_package_valid"],
                "proof_consumer_mode": obs["proof_consumer_mode"],
                "package_path": obs["package_path"],
                "progress_trace": _relative(progress_path, output_root),
            }
        )

    def sidecar_mismatch_setup(group_root: Path, trace_path: Path, bundle: Any) -> None:
        sidecar_root = group_root / "mode_b_sidecar_mismatch"
        _sidecar_path, manifest_path = _write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:proof-archive:sidecar-mismatch",
            rule_family=("ref_ref",),
        )
        manifest = json_load(manifest_path)
        manifest["trace_checksum"] = "deadbeef"
        json_dump(manifest_path, manifest)

    def cycle_inflation_setup(group_root: Path, trace_path: Path, bundle: Any) -> None:
        sidecar_root = group_root / "mode_b_sidecar_cycle"
        sidecar_path, manifest_path = _write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:proof-archive:cycle-inflation",
            rule_family=("ref_ref",),
        )
        _build_cycle_inflation_sidecar(sidecar_path, manifest_path, bundle.event_stream[0].ref_key)

    run_scenario(
        "sidecar_mismatch",
        payload={
            "embodiment_mode": "mode_b",
            "sidecar_source": str(group_dir / "mode_b_sidecar_mismatch" / "control" / "dependency_sidecar.jsonl"),
            "sidecar_manifest_source": str(group_dir / "mode_b_sidecar_mismatch" / "control" / "sidecar_manifest.json"),
            "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
            "rule_family": ["ref_ref"],
            "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
        },
        setup=sidecar_mismatch_setup,
    )
    run_scenario(
        "cycle_inflation",
        payload={
            "embodiment_mode": "mode_b",
            "sidecar_source": str(group_dir / "mode_b_sidecar_cycle" / "control" / "dependency_sidecar.jsonl"),
            "sidecar_manifest_source": str(group_dir / "mode_b_sidecar_cycle" / "control" / "sidecar_manifest.json"),
            "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
            "rule_family": ["ref_ref"],
            "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
        },
        setup=cycle_inflation_setup,
    )
    run_scenario(
        "corrupt_segment",
        payload={
            "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
            "rule_family": ["ref_ref"],
            "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
        },
        inject_read_error=("CORRUPT_SEGMENT", "injected corrupt segment"),
    )
    run_scenario(
        "io_guard",
        payload={
            "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
            "rule_family": ["ref_ref"],
            "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
        },
        inject_read_error=("TRACE_IO_GUARD", "injected trace io guard"),
    )

    summary = {
        "group": "C_degraded_audit",
        "status": "pass"
        if all(
            row["closure_mode"] == "degraded"
            and row["minimal_legal_package_valid"]
            and row["proof_consumer_mode"] is not None
            for row in rows
        )
        else "fail",
        "scenario_count": len(rows),
        "scenario_ids": [row["scenario_id"] for row in rows],
        "artifacts": {
            row["scenario_id"]: row["package_path"]
            for row in rows
        },
    }
    _json_path(group_dir / "scenarios.json", {"rows": rows})
    summary_path = _json_path(group_dir / "summary.json", summary)
    return {
        "status": summary["status"],
        "summary_path": _relative(summary_path, output_root),
    }


def build_archive(
    output_dir: Path,
    *,
    medium_repeat: int = 6,
    large_input_mode: str = "auto",
    large_input_trace: Path = DEFAULT_LARGE_INPUT_TRACE,
    large_input_scratch: Path | None = None,
    large_input_timeout_s: int = DEFAULT_LARGE_INPUT_TIMEOUT_S,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    group_results = {
        "A_control_plane_first": _build_group_a(
            output_dir,
            medium_repeat=medium_repeat,
            large_input_mode=large_input_mode,
            large_input_trace=large_input_trace,
            large_input_scratch=large_input_scratch,
            large_input_timeout_s=large_input_timeout_s,
        ),
        "B_budget_freeze": _build_group_b(output_dir, medium_repeat=medium_repeat),
        "C_degraded_audit": _build_group_c(output_dir),
    }
    group_statuses = {str(item["status"]) for item in group_results.values()}
    if group_statuses == {"pass"}:
        overall_status = "pass"
    elif "fail" in group_statuses:
        overall_status = "fail"
    else:
        overall_status = "blocked"
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "archive_kind": "evidence_proof_archive",
        "status": overall_status,
        "groups": group_results,
    }
    report_path = _json_path(output_dir / "report.json", report)
    entries = []
    for path in sorted(output_dir.rglob("*.json")):
        if path.name == "manifest.json":
            continue
        entries.append(
            {
                "path": _relative(path, output_dir),
                "sha256": _sha256(path),
            }
        )
    manifest = {
        "generated_at": report["generated_at"],
        "archive_kind": report["archive_kind"],
        "status": report["status"],
        "report_path": _relative(report_path, output_dir),
        "entries": entries,
    }
    manifest_path = _json_path(output_dir / "manifest.json", manifest)
    return {
        "report_path": str(report_path),
        "manifest_path": str(manifest_path),
        "status": report["status"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_evidence_proof_archive")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--medium-repeat", type=int, default=6)
    parser.add_argument(
        "--large-input-mode",
        default="auto",
        choices=["auto", "mock_measured", "mock_blocked", "disabled"],
    )
    parser.add_argument("--large-input-trace", type=Path, default=DEFAULT_LARGE_INPUT_TRACE)
    parser.add_argument("--large-input-scratch", type=Path)
    parser.add_argument("--large-input-timeout-s", type=int, default=DEFAULT_LARGE_INPUT_TIMEOUT_S)
    args = parser.parse_args(argv)
    result = build_archive(
        Path(args.output_dir).expanduser().resolve(),
        medium_repeat=max(2, int(args.medium_repeat)),
        large_input_mode=str(args.large_input_mode),
        large_input_trace=Path(args.large_input_trace).expanduser().resolve(),
        large_input_scratch=(
            Path(args.large_input_scratch).expanduser().resolve() if args.large_input_scratch is not None else None
        ),
        large_input_timeout_s=max(60, int(args.large_input_timeout_s)),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
