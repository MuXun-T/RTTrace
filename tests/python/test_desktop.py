from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import subprocess
import sys
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.context import ContextStore
from desktop.app.cli import main as cli_main
from desktop.package_writer import write_evidence_control_plane
from desktop.repository import DatasetRecord, DatasetRepository
from desktop.sample_data import build_scenario, write_scenario
from desktop.services import BackgroundJobManager, CompareService, ExportService, ReplayService, ReproService, WorkspaceController
from metric.core import metric_Ingest, metric_Init
from parser.evidence_sidecar import build_dependency_sidecar, build_sidecar_segment_manifest, materialize_dependency_sidecar, partition_dependency_sidecar_segments
from parser.evidence_models import FrontierSnapshot, ProofDigest, evd_RecomputeProofHash
from parser.openai_advisor_client import OpenAIAdvisorClientResult
from parser import encode_trace, load_dataset, load_dataset_with_timings, prs_Prescan
from parser.parser_process_agent import build_parser_cache_bindings
from parser.runtime_advisor import RuntimeLoadPlan
from parser.runtime_optimization_gate import ValidationGateResult
from parser.models import Alert, Diagnosis, EventTableQuery, EvidenceRef, ResourceGraph, TaskStateQuery, UntrustedWindow, dataclass_to_dict
from parser.result import Result, err_result, ok_result
from parser.telemetry import TelemetryHistoryStore, TelemetryReportAgent
from spec.events import event_id_for
from spec.io import checksum_file, json_dump, jsonl_dump
from spec.schema_loader import DICTIONARY_PATH, SCHEMA_DIR, load_dictionary


FORMAL_COMPARE_SCOPE_FIELDS = {
    "baseline_id",
    "candidate_id",
    "aligned_time_window",
    "filter",
    "dimensions",
    "metric_ids",
    "bucket_size",
    "evidence_policy",
    "scope_id",
}
FORMAL_DIFF_SUMMARY_FIELDS = {
    "scope",
    "metric_changes",
    "alert_changes",
    "hotspot_changes",
    "interval_changes",
    "task_changes",
    "core_changes",
    "resource_changes",
    "irq_changes",
    "trust_summary",
}
FORMAL_TRUST_SUMMARY_FIELDS = {
    "trusted",
    "baseline_untrusted_window_count",
    "candidate_untrusted_window_count",
    "unified_untrusted_window_count",
    "baseline_reason_codes",
    "candidate_reason_codes",
}


def _summary_metric_rows(summary: dict[str, object]) -> list[dict[str, object]]:
    return list(summary["metric_changes"])


def _summary_dimensions(summary: dict[str, object]) -> list[str]:
    return list(summary["scope"]["dimensions"])


class DesktopServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._qt_app = None

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _cold_preview_runtime_load_plan(self, *, background_sidecar_prebuild: bool = False) -> RuntimeLoadPlan:
        return RuntimeLoadPlan(
            plan_version="runtime-load-plan-v1",
            load_mode="cold_preview",
            index_build_mode="minimal",
            materialize_event_stream=False,
            try_parser_artifact_reuse=False,
            try_sidecar_index_reuse=False,
            background_sidecar_prebuild=background_sidecar_prebuild,
            reasons=["unit test"],
        )

    def test_cli_module_entrypoint_executes_main(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "desktop.app.cli", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("usage:", proc.stdout.lower())
        self.assertIn("export-evidence", proc.stdout)

    def _write_shifted_trace(self, output_path: Path, offset: int) -> Path:
        events = []
        for item in build_scenario(name="basic"):
            events.append(
                {
                    **item,
                    "timestamp": int(item["timestamp"]) + offset,
                    "payload": dict(item["payload"]),
                }
            )
        return encode_trace(output_path, events, producer_ver="python-sim-0.1")

    def _dictionary_copy(self) -> dict[str, object]:
        return json.loads(json.dumps(load_dictionary()))

    def _rename_event(self, raw_dictionary: dict[str, object], event_id: int, event_name: str) -> dict[str, object]:
        for item in raw_dictionary["event_defs"]:
            if item["event_id"] == event_id:
                item["event_name"] = event_name
                return raw_dictionary
        raise AssertionError(f"event_id {event_id} not found")

    def _append_event_definition(
        self,
        raw_dictionary: dict[str, object],
        event_id: int,
        event_name: str,
        payload_fields: list[str],
        domain: str = "task",
    ) -> dict[str, object]:
        raw_dictionary["event_defs"].append(
            {
                "event_id": event_id,
                "event_name": event_name,
                "domain": domain,
                "payload_fields": payload_fields,
            }
        )
        return raw_dictionary

    def _assert_preview_readiness(self, payload: dict[str, object], source: str) -> None:
        readiness = payload["readiness"]
        self.assertEqual(payload["stage"], "preview_ready")
        self.assertEqual(readiness["stage"], "preview_ready")
        self.assertEqual(readiness["source"], source)
        self.assertTrue(readiness["preview"])
        self.assertEqual(readiness["lod_ready"], {"lod0": True, "lod1": False, "lod2": False})
        self.assertEqual(
            readiness["view_ready"],
            {
                "timeline": True,
                "task_states": False,
                "event_table": False,
                "metric_series": False,
                "resource_graph": False,
                "alerts": False,
            },
        )
        self.assertEqual(payload["view_ready"], readiness["view_ready"])

    def _evidence_progress_substages(self, progress_updates: list[dict[str, object]]) -> list[str]:
        return [
            str(item.get("substage"))
            for item in progress_updates
            if item.get("category") == "evidence_export"
        ]

    def _assert_no_legacy_evidence_progress(self, progress_updates: list[dict[str, object]]) -> None:
        substages = self._evidence_progress_substages(progress_updates)
        self.assertNotIn("analysis", substages)
        self.assertNotIn("closure", substages)

    def _assert_round_progress_fields(self, progress_updates: list[dict[str, object]]) -> None:
        round_rows = [
            item
            for item in progress_updates
            if item.get("category") == "evidence_export"
            and str(item.get("substage", "")).startswith("round/")
        ]
        self.assertTrue(round_rows)
        for row in round_rows:
            self.assertIsInstance(row.get("round_id"), int)
            self.assertIn("frontier_count", row)
            self.assertIn("emitted_events", row)
            self.assertIn("emitted_bytes", row)

    def _patent_state_history(self, payload: dict[str, object]) -> list[str]:
        return [str(item.get("state")) for item in list(payload.get("patent_state_history") or [])]

    def _table_row_count(self, widget) -> int:
        if hasattr(widget, "rowCount"):
            return int(widget.rowCount())
        return int(getattr(widget, "_row_count", len(getattr(widget, "_rows", []))))

    def _table_cell_text(self, widget, row: int, column: int) -> str:
        if hasattr(widget, "item"):
            item = widget.item(row, column)
        else:
            item = getattr(widget, "_rows", [])[row][column]
        if item is None:
            return ""
        if hasattr(item, "text"):
            return str(item.text())
        return str(item)

    def _make_runtime_window(self):
        from desktop.app.gui import RuntimeProbeWindow
        from desktop.qt_compat import ensure_qapplication

        self._qt_app = ensure_qapplication([])
        return RuntimeProbeWindow()

    def _wait_for_window_load_completion(self, window, timeout_s: float = 5.0) -> str:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            window._poll_load_job()
            if window._active_load_job_id is None and window.state.active_dataset_id is not None:
                return window.state.active_dataset_id
            time.sleep(0.01)
        self.fail("window load did not finish within timeout")

    def _wait_for_job_result(self, controller: WorkspaceController, job_id: str, timeout_s: float = 5.0):
        deadline = time.time() + timeout_s
        last_status = "created"
        while time.time() < deadline:
            snapshot = controller.jobs.status(job_id)
            self.assertTrue(snapshot.ok, snapshot.message)
            last_status = snapshot.data["status"]
            if last_status in {"succeeded", "failed"}:
                return controller.jobs.result(job_id)
            time.sleep(0.01)
        self.fail(f"background job did not finish: {job_id} status={last_status}")

    def _refresh_manifest_checksums(self, package_dir: Path, *relative_paths: str) -> None:
        manifest_path = package_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        updated_paths = set(relative_paths)
        for entry in manifest["entries"]:
            if entry["path"] in updated_paths:
                entry["checksum"] = checksum_file(package_dir / entry["path"])
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def _refresh_sidecar_manifest_checksums(self, package_dir: Path, *relative_paths: str) -> None:
        sidecar_manifest_path = package_dir / "control" / "sidecar_manifest.json"
        sidecar_manifest = json.loads(sidecar_manifest_path.read_text(encoding="utf-8"))
        updated_paths = set(relative_paths)
        for rel_path in updated_paths:
            if rel_path in sidecar_manifest.get("entry_checksums", {}):
                sidecar_manifest["entry_checksums"][rel_path] = checksum_file(package_dir / rel_path)
        sidecar_manifest_path.write_text(json.dumps(sidecar_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_external_sidecar_artifacts(
        self,
        root: Path,
        trace_path: Path,
        bundle,
        *,
        snapshot_id: str,
        rule_family: tuple[str, ...],
        segmented: bool = False,
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
        anchor_event = bundle.event_stream[0]
        evidence_ref_payload = {
            "ref_type": "event",
            "ref_key": anchor_event.ref_key,
            "t_begin": float(anchor_event.timestamp_aligned),
            "t_end": float(anchor_event.timestamp_aligned),
        }
        alert_objects = [
            Alert(
                alert_id="alert:frozen:1",
                type="frozen_alert",
                severity="warning",
                time_window=(float(anchor_event.timestamp_aligned), float(anchor_event.timestamp_aligned)),
                object_scope={"task_id": anchor_event.task_id},
                threshold=1.0,
                actual=2.0,
                evidence_refs=[EvidenceRef(**evidence_ref_payload)],
                trusted=True,
                support_level="exact",
            )
        ]
        diagnosis_objects = [
            Diagnosis(
                diag_id="diag:frozen:1",
                title="frozen diagnosis",
                diagnosis_type="root_cause",
                time_window=(float(anchor_event.timestamp_aligned), float(anchor_event.timestamp_aligned)),
                object_scope={"task_id": anchor_event.task_id},
                conclusion="reuse frozen result",
                evidence_refs=[EvidenceRef(**evidence_ref_payload)],
                related_alerts=["alert:frozen:1"],
                confidence="high",
                support_level="exact",
            )
        ]
        anchor_rows = [
            {
                "anchor_id": "context:current",
                "anchor_type": "context",
                "evidence_anchor": {"ref_key": anchor_event.ref_key},
                "time_window": [float(anchor_event.timestamp_aligned), float(anchor_event.timestamp_aligned)],
            },
            {
                "anchor_id": "anchor:frozen:1",
                "anchor_type": "manual",
                "evidence_anchor": {"ref_key": anchor_event.ref_key},
                "time_window": [float(anchor_event.timestamp_aligned), float(anchor_event.timestamp_aligned)],
            },
        ]
        analysis_context = {
            "dataset_role": "single",
            "evidence_anchor": {"ref_key": anchor_event.ref_key},
            "selection": {"seed_ref": anchor_event.ref_key},
            "time_window": [float(bundle.event_stream[0].timestamp_aligned), float(bundle.event_stream[-1].timestamp_aligned)],
        }
        sidecar_rows = materialize_dependency_sidecar(
            build_dependency_sidecar(
                bundle,
                snapshot_id=snapshot_id,
                rule_families=rule_family,
                alerts=alert_objects,
                diagnoses=diagnosis_objects,
                context=analysis_context,
                anchors=anchor_rows,
                ref_index_rows=ref_index_rows,
            ),
            trace_checksum=source_trace_checksum,
        )
        if segmented:
            matching_indexes = [index for index, row in enumerate(sidecar_rows) if row.src_ref == anchor_event.ref_key]
            if len(matching_indexes) >= 2:
                sidecar_rows[matching_indexes[0]] = replace(sidecar_rows[matching_indexes[0]], segment_hint="segment:alpha")
                sidecar_rows[matching_indexes[1]] = replace(sidecar_rows[matching_indexes[1]], segment_hint="segment:beta")
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
        if segmented:
            schema_names["sidecar_segment_manifest_schema"] = "sidecar_segment_manifest.schema.json"
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
            "generator_version": "test",
            "trace_checksum": source_trace_checksum,
            "dictionary_checksum": checksum_file(root / "reference" / "dictionary.json"),
            "schema_checksums": schema_checksums,
            "relation_families": list(rule_family),
            "entry_paths": ["control/dependency_sidecar.jsonl"],
            "entry_checksums": {
                "control/dependency_sidecar.jsonl": checksum_file(sidecar_path),
            },
            "created_at": "2026-04-12T00:00:00+00:00",
            "snapshot_id": snapshot_id,
        }
        if segmented:
            segments = partition_dependency_sidecar_segments(sidecar_rows)
            segment_sidecar_rel_by_id: dict[str, str] = {}
            segment_index_rel_by_id: dict[str, str] = {}
            segment_ticket_rel_by_id: dict[str, str] = {}
            segment_checksum_by_id: dict[str, str] = {}
            for segment in segments:
                token = segment.segment_id.replace(":", "_")
                segment_rel = f"control/segments/{token}/dependency_sidecar.jsonl"
                segment_path = root / segment_rel
                jsonl_dump(segment_path, list(segment.rows))
                segment_sidecar_rel_by_id[segment.segment_id] = segment_rel
                segment_index_rel_by_id[segment.segment_id] = f"{segment_rel}.sqlite3"
                segment_ticket_rel_by_id[segment.segment_id] = f"{segment_rel}.sqlite3.ticket.json"
                segment_checksum_by_id[segment.segment_id] = checksum_file(segment_path)
                manifest_payload["entry_paths"].append(segment_rel)
                manifest_payload["entry_checksums"][segment_rel] = segment_checksum_by_id[segment.segment_id]
            segment_manifest = build_sidecar_segment_manifest(
                sidecar_rows,
                segment_dir="control/segments",
                snapshot_id=snapshot_id,
                trace_checksum=source_trace_checksum,
                dictionary_checksum=checksum_file(root / "reference" / "dictionary.json"),
                created_at="2026-04-12T00:00:00+00:00",
                sidecar_path_by_segment=segment_sidecar_rel_by_id,
                index_path_by_segment=segment_index_rel_by_id,
                checksum_by_segment=segment_checksum_by_id,
                ticket_path_by_segment=segment_ticket_rel_by_id,
            )
            json_dump(control_dir / "sidecar_segment_manifest.json", segment_manifest)
            manifest_payload["layout_mode"] = "segmented"
            manifest_payload["segment_manifest_path"] = "control/sidecar_segment_manifest.json"
            manifest_payload["segment_count"] = len(segment_manifest["segments"])
            manifest_payload["entry_paths"].append("control/sidecar_segment_manifest.json")
            manifest_payload["entry_checksums"]["control/sidecar_segment_manifest.json"] = checksum_file(
                control_dir / "sidecar_segment_manifest.json"
            )
        json_dump(manifest_path, manifest_payload)

        alerts_payload = [
            {
                "alert_id": "alert:frozen:1",
                "type": "frozen_alert",
                "severity": "warning",
                "time_window": [float(anchor_event.timestamp_aligned), float(anchor_event.timestamp_aligned)],
                "object_scope": {"task_id": anchor_event.task_id},
                "threshold": 1.0,
                "actual": 2.0,
                "evidence_refs": [evidence_ref_payload],
                "trusted": True,
                "support_level": "exact",
            }
        ]
        diagnoses_payload = [
            {
                "diag_id": "diag:frozen:1",
                "title": "frozen diagnosis",
                "diagnosis_type": "root_cause",
                "time_window": [float(anchor_event.timestamp_aligned), float(anchor_event.timestamp_aligned)],
                "object_scope": {"task_id": anchor_event.task_id},
                "conclusion": "reuse frozen result",
                "evidence_refs": [evidence_ref_payload],
                "related_alerts": ["alert:frozen:1"],
                "confidence": "high",
                "support_level": "exact",
            }
        ]
        json_dump(result_dir / "alerts.json", alerts_payload)
        json_dump(result_dir / "diagnoses.json", diagnoses_payload)
        return sidecar_path, manifest_path

    def test_write_evidence_control_plane_writes_segment_manifest_when_sidecar_spans_multiple_segments(self) -> None:
        trace_path = write_scenario(self.root / "control-plane-segmented.trace", name="basic")
        loaded = load_dataset(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        bundle = loaded.data.bundle
        ref_index_rows = [
            {
                "ref_key": event.ref_key,
                "timestamp_aligned": float(event.timestamp_aligned),
                "core_id": int(event.core_id),
                "seq": int(event.seq),
            }
            for event in bundle.event_stream
        ]
        base_rows = materialize_dependency_sidecar(
            build_dependency_sidecar(
                bundle,
                snapshot_id="snapshot:test:control-plane",
                rule_families=("ref_ref",),
                ref_index_rows=ref_index_rows,
            ),
            trace_checksum=checksum_file(trace_path),
        )
        self.assertGreaterEqual(len(base_rows), 2)
        sidecar_rows = [
            replace(base_rows[0], segment_hint="segment:alpha"),
            replace(base_rows[1], segment_hint="segment:beta"),
        ]
        package_path = self.root / "control-plane-segmented-package"
        dict_ref = {"path": "reference/dictionary.json", "algo": "sha256", "checksum": "dict", "dict_ver": 1}
        schema_ref = {
            "dependency_sidecar_schema": {"path": "reference/schema/dependency_sidecar.schema.json", "algo": "sha256", "checksum": "a"},
            "frontier_snapshot_schema": {"path": "reference/schema/frontier_snapshot.schema.json", "algo": "sha256", "checksum": "b"},
            "frontier_refs_schema": {"path": "reference/schema/frontier_refs.schema.json", "algo": "sha256", "checksum": "c"},
            "proof_digest_schema": {"path": "reference/schema/proof_digest.schema.json", "algo": "sha256", "checksum": "d"},
            "sidecar_manifest_schema": {"path": "reference/schema/sidecar_manifest.schema.json", "algo": "sha256", "checksum": "e"},
            "sidecar_segment_manifest_schema": {
                "path": "reference/schema/sidecar_segment_manifest.schema.json",
                "algo": "sha256",
                "checksum": "f",
            },
            "blocker_artifact_schema": {"path": "reference/schema/blocker_artifact.schema.json", "algo": "sha256", "checksum": "g"},
        }
        control_plane = write_evidence_control_plane(
            package_path,
            sidecar_rows=sidecar_rows,
            frontier_rows=[],
            frontier_snapshot=FrontierSnapshot(
                round_id=0,
                closure_mode="exact",
                frontier_count=0,
                consumed_depth=0,
                consumed_events=0,
                consumed_bytes=0,
                projected_next_events=0,
                projected_next_bytes=0,
                expansion_ratio=1.0,
                halt_reason="FRONTIER_EMPTY",
                frontier_refs_path=None,
                truncated_frontier_count=0,
            ),
            proof_digest=ProofDigest(
                snapshot_id="snapshot:test:control-plane",
                closure_mode="exact",
                complete_wrt_rule_family=True,
                rule_family=["ref_ref"],
                budget_vector={"D_max": 1, "C_events": 1, "S_bytes": 1, "rho_max": 1.0},
                closure_depth_reached=0,
                seed_ref_count=0,
                closed_ref_count=0,
                missing_required_refs=0,
                truncated_frontier_count=0,
                frontier_halt_reason="FRONTIER_EMPTY",
                events_emitted=0,
                bytes_emitted=0,
                scan_count=0,
                seek_count=0,
                window_span_total=0,
                sidecar_lookup_count=0,
                proof_hash="sha256:test",
            ),
            schema_ref=schema_ref,
            request_rule_family=("ref_ref",),
            snapshot_id="snapshot:test:control-plane",
            trace_checksum=checksum_file(trace_path),
            dictionary_checksum="dict:test",
            created_at="2026-06-30T00:00:00+00:00",
            generator_version="test",
        )
        sidecar_manifest = control_plane["sidecar_manifest"]
        self.assertEqual(sidecar_manifest["layout_mode"], "segmented")
        self.assertEqual(sidecar_manifest["segment_manifest_path"], "control/sidecar_segment_manifest.json")
        self.assertEqual(sidecar_manifest["segment_count"], 2)
        self.assertTrue((package_path / "control" / "sidecar_segment_manifest.json").exists())
        self.assertTrue((package_path / "control" / "segments" / "segment_alpha" / "dependency_sidecar.jsonl").exists())
        self.assertTrue((package_path / "control" / "segments" / "segment_beta" / "dependency_sidecar.jsonl").exists())

    def test_evidence_export_mode_b_uses_segmented_external_sidecar_and_repro_reads_segment_metadata(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-b-segmented.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )

        sidecar_root = self.root / "mode-b-segmented-sidecar"
        sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:test:mode-b:segmented",
            rule_family=("ref_ref", "ref_alert", "ref_diagnosis", "ref_anchor", "ref_object"),
            segmented=True,
        )

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "embodiment_mode": "mode_b",
                "sidecar_source": str(sidecar_path),
                "sidecar_manifest_source": str(manifest_path),
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref", "ref_alert", "ref_diagnosis", "ref_anchor", "ref_object"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-mode-b-segmented-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        sidecar_manifest = json.loads((package_dir / "control" / "sidecar_manifest.json").read_text(encoding="utf-8"))
        sidecar_segment_manifest = json.loads((package_dir / "control" / "sidecar_segment_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(proof_digest["sidecar_selector_mode"], "segmented_sqlite")
        self.assertEqual(sidecar_manifest["layout_mode"], "segmented")
        self.assertEqual(sidecar_manifest["segment_manifest_path"], "control/sidecar_segment_manifest.json")
        self.assertGreater(int(sidecar_manifest["segment_count"]), 1)
        self.assertEqual(int(sidecar_segment_manifest["segment_count"]), int(sidecar_manifest["segment_count"]))
        self.assertTrue(
            any((package_dir / str(segment["sidecar_path"])).exists() for segment in sidecar_segment_manifest["segments"])
        )

        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertTrue(opened.ok, opened.message)
        self.assertIn("sidecar_segment_manifest", opened.data)
        self.assertGreater(len(opened.data["sidecar_segments"]), 1)

    def test_context_commit_and_query(self) -> None:
        trace_path = write_scenario(self.root / "basic.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        context = controller.viz_SetContext(
            {
                "focused_view": "timeline",
                "zoom_level": 2.0,
                "evidence_anchor": {"ref_key": bundle.event_stream[0].ref_key},
            }
        )
        self.assertEqual(context.data.focused_view, "timeline")
        task_states = controller.viz_QueryTaskStates(
            TaskStateQuery(
                time_window=(bundle.event_stream[0].timestamp_aligned, bundle.event_stream[-1].timestamp_aligned),
            )
        )
        self.assertTrue(task_states.ok, task_states.message)
        self.assertGreater(len(task_states.data.rows), 0)
        self.assertEqual(task_states.data.time_window, (bundle.event_stream[0].timestamp_aligned, bundle.event_stream[-1].timestamp_aligned))
        self.assertGreater(len(task_states.data.lane_order), 0)
        self.assertIsNotNone(task_states.data.cursor_hint)
        self.assertTrue(task_states.data.cursor_hint.startswith("ref:"))
        first_row = task_states.data.rows[0]
        self.assertGreater(len(first_row.segments), 0)
        self.assertIn(first_row.lane_label, task_states.data.lane_order)
        self.assertGreater(sum(first_row.state_counts.values()), 0)
        self.assertEqual(task_states.data.summary["source"], "task_state_window_index")
        self.assertFalse(task_states.data.summary["cache_hit"])
        self.assertTrue(task_states.data.summary["readiness"]["view_ready"]["task_states"])
        self.assertIn("trusted_summary", task_states.data.summary)

        event_page = controller.viz_QueryEventTable(EventTableQuery(filter={}, limit=4))
        self.assertTrue(event_page.ok, event_page.message)
        self.assertEqual(len(event_page.data.items), 4)
        self.assertEqual(event_page.data.order_by, "sort_key asc")
        self.assertIsNone(event_page.data.cursor_in)
        self.assertEqual(event_page.data.total_hint, len(bundle.event_stream))
        self.assertEqual(event_page.data.summary["source"], "materialized_bundle")
        self.assertEqual(event_page.data.summary["fallback_reason"], "bundle_scan")
        self.assertTrue(event_page.data.summary["readiness"]["view_ready"]["event_table"])

    def test_hover_target_updates_without_mutating_selection(self) -> None:
        trace_path = write_scenario(self.root / "hover-target.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        hovered_event = bundle.event_stream[1]

        selected = controller.viz_ApplySelection({"task_id": bundle.event_stream[0].task_id})
        self.assertTrue(selected.ok, selected.message)

        preview = controller.viz_SetHoverTarget(
            {
                "view": "event_table",
                "kind": "event",
                "event_uid": hovered_event.event_uid,
                "ref_key": hovered_event.ref_key,
            },
            transient_selection={
                "task_id": hovered_event.task_id,
                "core_id": hovered_event.core_id,
                "event_uid": hovered_event.event_uid,
            },
        )
        self.assertTrue(preview.ok, preview.message)

        context = controller.context_store.get()
        self.assertEqual(context.selection, {"task_id": bundle.event_stream[0].task_id})
        self.assertEqual(context.hover_target["ref_key"], hovered_event.ref_key)
        self.assertEqual(context.transient_selection["event_uid"], hovered_event.event_uid)
        self.assertNotIn("hover_target", context.persisted_dict())
        self.assertNotIn("transient_selection", context.persisted_dict())

    def test_transient_selection_clears_after_commit_or_cancel(self) -> None:
        trace_path = write_scenario(self.root / "transient-selection.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        first_event = bundle.event_stream[1]
        second_event = bundle.event_stream[2]

        staged = controller.viz_SetTransientSelection(
            {
                "task_id": first_event.task_id,
                "core_id": first_event.core_id,
                "event_uid": first_event.event_uid,
            },
            hover_target={
                "view": "event_table",
                "kind": "event",
                "ref_key": first_event.ref_key,
            },
        )
        self.assertTrue(staged.ok, staged.message)
        self.assertEqual(controller.context_store.get().selection, {})

        committed = controller.viz_CommitTransientSelection()
        self.assertTrue(committed.ok, committed.message)
        committed_context = controller.context_store.get()
        self.assertEqual(committed_context.selection["event_uid"], first_event.event_uid)
        self.assertIsNone(committed_context.transient_selection)
        self.assertIsNone(committed_context.hover_target)

        restaged = controller.viz_SetTransientSelection(
            {
                "task_id": second_event.task_id,
                "core_id": second_event.core_id,
                "event_uid": second_event.event_uid,
            },
            hover_target={
                "view": "event_table",
                "kind": "event",
                "ref_key": second_event.ref_key,
            },
        )
        self.assertTrue(restaged.ok, restaged.message)
        cancelled = controller.viz_CancelTransientSelection(clear_hover_target=True)
        self.assertTrue(cancelled.ok, cancelled.message)
        cancelled_context = controller.context_store.get()
        self.assertEqual(cancelled_context.selection["event_uid"], first_event.event_uid)
        self.assertIsNone(cancelled_context.transient_selection)
        self.assertIsNone(cancelled_context.hover_target)

    def test_task_state_query_returns_rows_segments_state_counts_and_cursor_hint(self) -> None:
        trace_path = write_scenario(self.root / "task-state-view.trace", name="multi_core")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        ref_key = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": ref_key}})

        task_states = controller.viz_QueryTaskStates(
            TaskStateQuery(
                time_window=(bundle.event_stream[0].timestamp_aligned, bundle.event_stream[-1].timestamp_aligned),
            )
        )
        self.assertTrue(task_states.ok, task_states.message)
        self.assertEqual(task_states.data.cursor_hint, f"ref:{ref_key}")
        self.assertGreater(len(task_states.data.rows), 0)
        self.assertGreater(len(task_states.data.lane_order), 0)
        self.assertGreater(len(task_states.data.state_legend), 0)
        row = task_states.data.rows[0]
        self.assertIsInstance(row.task_id, int)
        self.assertTrue(row.lane_label)
        self.assertGreater(len(row.segments), 0)
        self.assertGreater(sum(row.state_counts.values()), 0)
        segment = row.segments[0]
        self.assertEqual(segment.evidence_ref.ref_key, segment.cause_event)
        self.assertIn(row.lane_label, task_states.data.lane_order)

    def test_task_state_query_uses_active_dataset_without_dataset_filter(self) -> None:
        trace_path = write_scenario(self.root / "task-state-active-dataset.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        full_window = (
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
        )

        formal_query = TaskStateQuery(time_window=full_window)
        formal_result = controller.viz_QueryTaskStates(formal_query)
        self.assertTrue(formal_result.ok, formal_result.message)

        legacy_query = TaskStateQuery(time_window=full_window)
        setattr(legacy_query, "filter", {"dataset_id": loaded.data})
        legacy_result = controller.viz_QueryTaskStates(legacy_query)
        self.assertTrue(legacy_result.ok, legacy_result.message)

        self.assertEqual(len(formal_result.data.rows), len(legacy_result.data.rows))
        self.assertEqual(formal_result.data.lane_order, legacy_result.data.lane_order)
        self.assertEqual(formal_result.data.summary["segment_count"], legacy_result.data.summary["segment_count"])

    def test_async_load_preview_contains_task_state_summary(self) -> None:
        trace_path = write_scenario(self.root / "task-state-preview.trace", name="basic")
        controller = WorkspaceController()

        job = controller.viz_LoadDatasetAsync(str(trace_path))
        self.assertTrue(job.ok, job.message)
        self.assertIsNone(controller.active_dataset_id)
        preview = job.data["preview"]
        task_preview = preview.get("task_state_preview") or {}
        self.assertEqual(job.data["stage"], "preview_ready")
        self.assertTrue(task_preview)
        self.assertGreater(task_preview["lane_count"], 0)
        self.assertGreater(len(task_preview["task_ids"]), 0)
        self.assertGreater(sum(task_preview["state_totals"].values()), 0)
        self.assertGreater(len(task_preview["bucket_summary"]), 0)
        self.assertEqual(task_preview["readiness"]["stage"], "preview_ready")
        self.assertTrue(task_preview["trusted"])

        snapshot = controller.jobs.status(job.data["job_id"])
        self.assertTrue(snapshot.ok, snapshot.message)
        self.assertTrue(snapshot.data["payload"]["preview"].get("task_state_preview"))
        self.assertTrue(snapshot.data["payload"]["parser_artifact_dir"])
        self.assertTrue(snapshot.data["payload"]["parser_cancel_path"])

        loaded = self._wait_for_job_result(controller, job.data["job_id"])
        self.assertTrue(loaded.ok, loaded.message)
        finished = controller.jobs.status(job.data["job_id"])
        self.assertTrue(finished.ok, finished.message)
        payload = finished.data["payload"]
        self.assertTrue(payload["parser_process_artifact"])
        self.assertTrue(payload["child_agent_contract"])
        self.assertEqual(
            loaded.data["parser_process_artifact"]["artifact_version"],
            "parser-process-artifact-v1",
        )
        self.assertTrue(loaded.data["child_agent_contract"])

    def test_viz_load_dataset_applies_runtime_load_plan(self) -> None:
        trace_path = write_scenario(self.root / "runtime-load-plan.trace", name="basic", repeat=4)
        controller = WorkspaceController()

        with patch("desktop.services.RuntimeOptimizationAdvisor.plan_load", return_value=self._cold_preview_runtime_load_plan()):
            loaded = controller.viz_LoadDataset(str(trace_path))

        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        self.assertEqual(record.artifact.bundle.event_stream, [])
        self.assertEqual(record.artifact.bundle.index_bundle.summary["index_build_mode"], "minimal")

    def test_viz_load_dataset_gate_rejection_falls_back_to_full(self) -> None:
        trace_path = write_scenario(self.root / "runtime-load-gate-reject.trace", name="basic", repeat=4)
        controller = WorkspaceController()
        rejected_gate = ValidationGateResult(
            gate_version="runtime-optimization-gate-v1",
            accepted=False,
            rejected_reason="ERR-RUNTIME_LOAD_PLAN_INVALID",
            checked_ticket=False,
            checked_schema=False,
            checked_checksum=False,
            checked_fingerprint=False,
            checked_policy=True,
            execution_plan=[],
        )

        with patch("desktop.services.RuntimeOptimizationAdvisor.plan_load", return_value=self._cold_preview_runtime_load_plan()):
            with patch(
                "desktop.services.gate_ValidateRuntimeLoadPlan",
                return_value=Result(
                    code="ERR-RUNTIME_LOAD_PLAN_INVALID",
                    message="runtime load plan rejected",
                    data=rejected_gate,
                ),
            ):
                loaded = controller._load_artifact_from_source(str(trace_path))

        self.assertTrue(loaded.ok, loaded.message)
        self.assertFalse(loaded.data["runtime_load_gate_result"]["accepted"])
        self.assertEqual(loaded.data["runtime_load_effective_plan"]["load_mode"], "full")
        self.assertEqual(loaded.data["runtime_load_effective_plan"]["index_build_mode"], "full")
        self.assertTrue(loaded.data["runtime_load_effective_plan"]["materialize_event_stream"])
        self.assertGreater(len(loaded.data["artifact"].bundle.event_stream), 0)

    def test_async_load_job_records_effective_runtime_load_plan(self) -> None:
        trace_path = write_scenario(self.root / "runtime-load-async.trace", name="basic", repeat=4)
        controller = WorkspaceController()

        with patch("desktop.services.RuntimeOptimizationAdvisor.plan_load", return_value=self._cold_preview_runtime_load_plan()):
            job = controller.viz_LoadDatasetAsync(str(trace_path))
            self.assertTrue(job.ok, job.message)
            loaded = self._wait_for_job_result(controller, job.data["job_id"])

        self.assertTrue(loaded.ok, loaded.message)
        snapshot = controller.jobs.status(job.data["job_id"])
        self.assertTrue(snapshot.ok, snapshot.message)
        payload = snapshot.data["payload"]
        self.assertEqual(payload["runtime_load_plan"]["load_mode"], "cold_preview")
        self.assertTrue(payload["runtime_load_gate_result"]["accepted"])
        self.assertEqual(payload["runtime_load_effective_plan"]["index_build_mode"], "minimal")
        self.assertFalse(payload["runtime_load_effective_plan"]["materialize_event_stream"])
        self.assertFalse(payload["parser_process_artifact"]["artifact_policy"]["materialize_event_stream"])
        self.assertEqual(payload["parser_process_artifact"]["artifact_policy"]["index_build_mode"], "minimal")
        self.assertEqual(loaded.data["runtime_load_effective_plan"], payload["runtime_load_effective_plan"])

    def test_viz_load_dataset_submits_background_sidecar_prebuild_job(self) -> None:
        trace_path = write_scenario(self.root / "runtime-load-background-prebuild.trace", name="basic", repeat=4)
        controller = WorkspaceController()

        with patch(
            "desktop.services.RuntimeOptimizationAdvisor.plan_load",
            return_value=RuntimeLoadPlan(
                plan_version="runtime-load-plan-v1",
                load_mode="full",
                index_build_mode="full",
                materialize_event_stream=True,
                try_parser_artifact_reuse=False,
                try_sidecar_index_reuse=False,
                background_sidecar_prebuild=True,
                reasons=["unit test"],
            ),
        ):
            loaded = controller.viz_LoadDataset(str(trace_path))

        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        prebuild = dict(record.background_sidecar_prebuild)
        self.assertIn(prebuild["state"], {"created", "queued", "running", "completed"})
        self.assertEqual(prebuild["activation_id"], record.activation_id)
        written = self._wait_for_job_result(controller, prebuild["job_id"])
        self.assertTrue(written.ok, written.message)
        breakdown = dict(written.data["prebuild_stage_breakdown"])
        self.assertTrue(
            {
                "prepare_context_seconds",
                "sidecar_build_seconds",
                "sidecar_write_seconds",
                "sidecar_validate_seconds",
                "sqlite_index_build_seconds",
                "ticket_write_seconds",
                "ticket_write_seconds_estimated",
                "ticket_write_seconds_basis",
                "manifest_write_seconds",
                "ref_index_rows_seconds",
                "ref_index_rows_source",
                "job_wall_seconds",
                "unaccounted_seconds",
            }.issubset(set(breakdown)),
        )
        self.assertGreaterEqual(breakdown["job_wall_seconds"], 0.0)
        self.assertEqual(breakdown["ref_index_rows_source"], "bundle_event_stream")
        self.assertFalse(breakdown["ticket_write_seconds_estimated"])
        path_features = dict(written.data["prebuild_path_features"])
        self.assertTrue(path_features["bundle_event_stream_available"])
        self.assertFalse(path_features["full_trace_reparse"])
        self.assertFalse(path_features["source_ref_index_scan"])
        self.assertTrue(path_features["load_materialize_event_stream"])
        self.assertTrue(path_features["full_sidecar_materialize"])
        self.assertTrue(path_features["full_sidecar_jsonl_write"])
        self.assertTrue(path_features["sqlite_index_full_scan"])
        diagnostics = dict(written.data["prebuild_diagnostics"])
        self.assertEqual(diagnostics["prebuild_build_dependency_sidecar_calls"], 1)
        self.assertEqual(diagnostics["prebuild_materialize_dependency_sidecar_calls"], 1)
        self.assertEqual(diagnostics["prebuild_full_sidecar_jsonl_write_count"], 1)
        self.assertFalse(diagnostics["duplicate_parse_detected"])
        refreshed = controller.repository.get(loaded.data)
        self.assertEqual(refreshed.background_sidecar_prebuild["state"], "completed")
        self.assertEqual(
            Path(refreshed.background_sidecar_prebuild["output_path"]),
            Path(tempfile.gettempdir()) / "rttrace-sidecar-prebuild" / refreshed.activation_id,
        )
        self.assertTrue(Path(refreshed.background_sidecar_prebuild["sidecar_manifest_path"]).exists())
        self.assertEqual(refreshed.background_sidecar_prebuild["prebuild_stage_breakdown"], breakdown)
        self.assertEqual(refreshed.background_sidecar_prebuild["prebuild_path_features"], path_features)
        self.assertEqual(refreshed.background_sidecar_prebuild["prebuild_diagnostics"], diagnostics)

    def test_viz_resolve_load_dataset_job_submits_background_sidecar_prebuild_job(self) -> None:
        trace_path = write_scenario(self.root / "runtime-load-background-prebuild-async.trace", name="basic", repeat=4)
        controller = WorkspaceController()

        with patch(
            "desktop.services.RuntimeOptimizationAdvisor.plan_load",
            return_value=RuntimeLoadPlan(
                plan_version="runtime-load-plan-v1",
                load_mode="full",
                index_build_mode="full",
                materialize_event_stream=True,
                try_parser_artifact_reuse=False,
                try_sidecar_index_reuse=False,
                background_sidecar_prebuild=True,
                reasons=["unit test"],
            ),
        ):
            job = controller.viz_LoadDatasetAsync(str(trace_path))
            self.assertTrue(job.ok, job.message)
            loaded = self._wait_for_job_result(controller, job.data["job_id"])
            self.assertTrue(loaded.ok, loaded.message)
            resolved = controller.viz_ResolveLoadDatasetJob(job.data["job_id"])

        self.assertTrue(resolved.ok, resolved.message)
        record = controller.repository.get(resolved.data)
        prebuild = dict(record.background_sidecar_prebuild)
        self.assertIn(prebuild["state"], {"created", "queued", "running", "completed"})
        written = self._wait_for_job_result(controller, prebuild["job_id"])
        self.assertTrue(written.ok, written.message)
        self.assertEqual(controller.repository.get(resolved.data).background_sidecar_prebuild["state"], "completed")

    def test_background_sidecar_prebuild_supports_cold_preview_without_event_stream(self) -> None:
        trace_path = write_scenario(self.root / "runtime-load-background-prebuild-thin.trace", name="basic", repeat=4)
        controller = WorkspaceController()

        with patch(
            "desktop.services.RuntimeOptimizationAdvisor.plan_load",
            return_value=self._cold_preview_runtime_load_plan(background_sidecar_prebuild=True),
        ):
            loaded = controller.viz_LoadDataset(str(trace_path))

        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        self.assertEqual(record.artifact.bundle.event_stream, [])
        written = self._wait_for_job_result(controller, record.background_sidecar_prebuild["job_id"])
        self.assertTrue(written.ok, written.message)
        self.assertGreater(written.data["edge_count"], 0)
        self.assertTrue(Path(written.data["sidecar_manifest_path"]).exists())
        path_features = dict(written.data["prebuild_path_features"])
        self.assertFalse(path_features["bundle_event_stream_available"])
        self.assertFalse(path_features["full_trace_reparse"])
        self.assertTrue(path_features["source_ref_index_scan"])
        self.assertTrue(path_features["full_sidecar_materialize"])
        self.assertTrue(path_features["full_sidecar_jsonl_write"])

    def test_viz_load_dataset_reuses_parser_artifact_cache(self) -> None:
        trace_path = write_scenario(self.root / "runtime-load-parser-cache.trace", name="basic", repeat=4)
        controller = WorkspaceController()
        dictionary_path = self.root / "runtime-load-parser-cache-dictionary.json"
        dictionary_path.write_text(json.dumps(self._dictionary_copy(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        bindings = build_parser_cache_bindings(
            trace_path,
            dictionary_path=dictionary_path,
            parser_version="parser-mvp-1",
            schema_version="parser-process-artifact-v1",
            index_build_mode="minimal",
            materialize_event_stream=False,
        )
        shutil.rmtree(Path(tempfile.gettempdir()) / "rttrace-parser-agent" / bindings["cache_key"], ignore_errors=True)

        with patch("desktop.services.RuntimeOptimizationAdvisor.plan_load", return_value=self._cold_preview_runtime_load_plan()):
            with patch("desktop.services._resolve_evidence_dictionary_source_path", return_value=dictionary_path):
                first = controller._load_artifact_from_source(str(trace_path), job_id="cache-first")
                second = controller._load_artifact_from_source(str(trace_path), job_id="cache-second")

        self.assertTrue(first.ok, first.message)
        self.assertTrue(second.ok, second.message)
        self.assertFalse(first.data["parser_process_artifact"]["cache_hit"])
        self.assertTrue(second.data["parser_process_artifact"]["cache_hit"])
        self.assertEqual(second.data["runtime_load_effective_plan"]["load_mode"], "hot_reuse")
        self.assertTrue(second.data["runtime_load_effective_plan"]["try_parser_artifact_reuse"])
        self.assertEqual(second.data["runtime_load_effective_plan"]["index_build_mode"], "minimal")
        self.assertFalse(second.data["runtime_load_effective_plan"]["materialize_event_stream"])

    def test_load_artifact_from_source_metadata_only_hot_cache_skips_bundle_materialization(self) -> None:
        trace_path = write_scenario(self.root / "runtime-load-parser-cache-metadata-only.trace", name="basic", repeat=4)
        controller = WorkspaceController()

        with patch("desktop.services.RuntimeOptimizationAdvisor.plan_load", return_value=self._cold_preview_runtime_load_plan()):
            first = controller._load_artifact_from_source(str(trace_path), job_id="metadata-first")
            hot = controller._load_artifact_from_source(
                str(trace_path),
                job_id="metadata-hot",
                load_artifact=False,
            )

        self.assertTrue(first.ok, first.message)
        self.assertTrue(hot.ok, hot.message)
        self.assertIsNotNone(first.data["artifact"])
        self.assertIsNone(hot.data["artifact"])
        self.assertTrue(hot.data["parser_process_artifact"]["cache_hit"])
        self.assertFalse(hot.data["parser_process_artifact"]["load_artifact"])
        self.assertEqual(hot.data["runtime_load_effective_plan"]["load_mode"], "hot_reuse")
        self.assertEqual(hot.data["runtime_load_effective_plan"]["index_build_mode"], "minimal")
        self.assertFalse(hot.data["runtime_load_effective_plan"]["materialize_event_stream"])

    def test_viz_load_dataset_trace_or_dictionary_change_invalidates_parser_cache(self) -> None:
        trace_path = write_scenario(self.root / "runtime-load-parser-cache-invalidate.trace", name="basic", repeat=4)
        controller = WorkspaceController()
        dictionary_path = self.root / "runtime-load-parser-cache-invalidate-dictionary.json"
        dictionary_path.write_text(json.dumps(self._dictionary_copy(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        bindings = build_parser_cache_bindings(
            trace_path,
            dictionary_path=dictionary_path,
            parser_version="parser-mvp-1",
            schema_version="parser-process-artifact-v1",
            index_build_mode="minimal",
            materialize_event_stream=False,
        )
        shutil.rmtree(Path(tempfile.gettempdir()) / "rttrace-parser-agent" / bindings["cache_key"], ignore_errors=True)

        with patch("desktop.services.RuntimeOptimizationAdvisor.plan_load", return_value=self._cold_preview_runtime_load_plan()):
            with patch("desktop.services._resolve_evidence_dictionary_source_path", return_value=dictionary_path):
                first = controller._load_artifact_from_source(str(trace_path), job_id="invalidate-first")
            self.assertTrue(first.ok, first.message)
            self.assertFalse(first.data["parser_process_artifact"]["cache_hit"])

            with patch("desktop.services._resolve_evidence_dictionary_source_path", return_value=dictionary_path):
                second = controller._load_artifact_from_source(str(trace_path), job_id="invalidate-second")
            self.assertTrue(second.ok, second.message)
            self.assertTrue(second.data["parser_process_artifact"]["cache_hit"])

            with patch("desktop.services._resolve_evidence_dictionary_source_path") as mocked_dictionary_path:
                custom_dictionary = self._rename_event(self._dictionary_copy(), 0x1001, "TASK_READY_DESKTOP_CACHE_VARIANT")
                dictionary_path = self.root / "runtime-load-parser-cache-invalidate-dictionary-variant.json"
                dictionary_path.write_text(json.dumps(custom_dictionary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
                variant_bindings = build_parser_cache_bindings(
                    trace_path,
                    dictionary_path=dictionary_path,
                    parser_version="parser-mvp-1",
                    schema_version="parser-process-artifact-v1",
                    index_build_mode="minimal",
                    materialize_event_stream=False,
                )
                shutil.rmtree(Path(tempfile.gettempdir()) / "rttrace-parser-agent" / variant_bindings["cache_key"], ignore_errors=True)
                mocked_dictionary_path.return_value = dictionary_path
                third = controller._load_artifact_from_source(str(trace_path), job_id="invalidate-third")
            self.assertTrue(third.ok, third.message)
            self.assertFalse(third.data["parser_process_artifact"]["cache_hit"])
            self.assertNotEqual(
                third.data["parser_process_artifact"]["dictionary_checksum"],
                second.data["parser_process_artifact"]["dictionary_checksum"],
            )

            trace_path.write_bytes(trace_path.read_bytes() + b"\0")
            modified_bindings = build_parser_cache_bindings(
                trace_path,
                dictionary_path=dictionary_path,
                parser_version="parser-mvp-1",
                schema_version="parser-process-artifact-v1",
                index_build_mode="minimal",
                materialize_event_stream=False,
            )
            shutil.rmtree(Path(tempfile.gettempdir()) / "rttrace-parser-agent" / modified_bindings["cache_key"], ignore_errors=True)
            with patch("desktop.services._resolve_evidence_dictionary_source_path", return_value=dictionary_path):
                fourth = controller._load_artifact_from_source(str(trace_path), job_id="invalidate-fourth")

        self.assertTrue(fourth.ok, fourth.message)
        self.assertFalse(fourth.data["parser_process_artifact"]["cache_hit"])
        self.assertNotEqual(
            fourth.data["parser_process_artifact"]["trace_checksum"],
            third.data["parser_process_artifact"]["trace_checksum"],
        )

    def test_background_load_job_transitions_and_registers_dataset(self) -> None:
        trace_path = write_scenario(self.root / "async-load.trace", name="basic")
        controller = WorkspaceController()

        job = controller.viz_LoadDatasetAsync(str(trace_path))
        self.assertTrue(job.ok, job.message)
        job_id = job.data["job_id"]
        self.assertIsNotNone(job.data["preview"])
        self.assertGreater(len(job.data["preview"]["lod0_buckets"]), 0)
        self.assertTrue(job.data["preview"].get("task_state_preview"))
        self.assertEqual(job.data["job_kind"], "input_prescan")
        self._assert_preview_readiness(job.data, "prescan")

        deadline = time.time() + 5.0
        last_status = "created"
        while time.time() < deadline:
            snapshot = controller.jobs.status(job_id)
            self.assertTrue(snapshot.ok, snapshot.message)
            last_status = snapshot.data["status"]
            self.assertIn("preview", snapshot.data["payload"])
            if last_status in {"succeeded", "failed"}:
                break
            time.sleep(0.01)

        self.assertEqual(last_status, "succeeded")
        resolved = controller.viz_ResolveLoadDatasetJob(job_id)
        self.assertTrue(resolved.ok, resolved.message)
        self.assertTrue(controller.repository.has(resolved.data))
        self.assertEqual(controller.active_dataset_id, resolved.data)

        resolved_again = controller.viz_ResolveLoadDatasetJob(job_id)
        self.assertTrue(resolved_again.ok, resolved_again.message)
        self.assertEqual(resolved_again.data, resolved.data)

    def test_async_load_stage_transitions_are_monotonic(self) -> None:
        trace_path = write_scenario(self.root / "async-stage.trace", name="multi_core", repeat=4)
        controller = WorkspaceController()
        original_load = controller._load_artifact_from_source

        def slow_load(source: str, *args, **kwargs):
            time.sleep(0.05)
            return original_load(source, *args, **kwargs)

        with patch.object(controller, "_load_artifact_from_source", side_effect=slow_load):
            job = controller.viz_LoadDatasetAsync(str(trace_path))
            self.assertTrue(job.ok, job.message)
            stages = [job.data["stage"]]
            job_kinds = [job.data["job_kind"]]
            deadline = time.time() + 5.0
            last_status = "created"
            while time.time() < deadline:
                snapshot = controller.jobs.status(job.data["job_id"])
                self.assertTrue(snapshot.ok, snapshot.message)
                last_status = snapshot.data["status"]
                stages.append(snapshot.data["payload"]["stage"])
                job_kinds.append(snapshot.data["payload"]["job_kind"])
                if last_status in {"succeeded", "failed"}:
                    break
                time.sleep(0.01)

        self.assertEqual(last_status, "succeeded")
        order = {
            "queued": 0,
            "preview_ready": 1,
            "parse_rebuild": 2,
            "query_ready": 3,
        }
        self.assertTrue(all(order[left] <= order[right] for left, right in zip(stages, stages[1:])))
        self.assertEqual(stages[-1], "query_ready")
        self.assertIn("parse_rebuild", stages)
        self.assertEqual(job_kinds[0], "input_prescan")
        self.assertIn("parse_rebuild", job_kinds)

    def test_context_change_does_not_clear_task_state_preview_during_active_load(self) -> None:
        trace_path = write_scenario(self.root / "preview-retain.trace", name="basic")
        window = self._make_runtime_window()
        original_load = window.controller._load_artifact_from_source

        def slow_load(source: str, *args, **kwargs):
            time.sleep(0.1)
            return original_load(source, *args, **kwargs)

        with patch.object(window.controller, "_load_artifact_from_source", side_effect=slow_load):
            window.load_single_dataset(str(trace_path))
            self.assertIsNotNone(window._active_load_job_id)
            self.assertIsNotNone(window._active_load_preview)
            self.assertGreater(self._table_row_count(window.task_state_table), 0)

            window.controller.context_store.commit({"focused_view": "timeline"})

            self.assertIsNotNone(window._active_load_preview)
            self.assertGreater(self._table_row_count(window.task_state_table), 0)
            self.assertIn("lanes", self._table_cell_text(window.task_state_table, 0, 1))

        self._wait_for_window_load_completion(window)
        window.close()

    def test_task_state_preview_is_replaced_by_formal_rows_after_dataset_resolve(self) -> None:
        trace_path = write_scenario(self.root / "preview-to-formal.trace", name="basic")
        window = self._make_runtime_window()
        original_load = window.controller._load_artifact_from_source

        def slow_load(source: str, *args, **kwargs):
            time.sleep(0.05)
            return original_load(source, *args, **kwargs)

        with patch.object(window.controller, "_load_artifact_from_source", side_effect=slow_load):
            window.load_single_dataset(str(trace_path))
            self.assertGreater(self._table_row_count(window.task_state_table), 0)
            preview_lane_text = self._table_cell_text(window.task_state_table, 0, 1)
            self.assertIn("lanes", preview_lane_text)

        dataset_id = self._wait_for_window_load_completion(window)

        self.assertTrue(dataset_id)
        self.assertEqual(window.state.active_dataset_id, dataset_id)
        self.assertGreater(len(window._task_state_rows), 0)
        self.assertGreater(self._table_row_count(window.task_state_table), 0)
        formal_lane_text = self._table_cell_text(window.task_state_table, 0, 1)
        self.assertNotIn("lanes", formal_lane_text)
        self.assertNotEqual(preview_lane_text, formal_lane_text)
        window.close()

    def test_query_lane_does_not_mutate_load_job_stage(self) -> None:
        active_trace = write_scenario(self.root / "query-lane-active.trace", name="multi_core", repeat=2)
        staged_trace = write_scenario(self.root / "query-lane-load.trace", name="multi_core", repeat=6)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(active_trace))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        original_load = controller._load_artifact_from_source

        def slow_load(source: str, *args, **kwargs):
            time.sleep(0.2)
            return original_load(source, *args, **kwargs)

        with patch.object(controller, "_load_artifact_from_source", side_effect=slow_load):
            load_job = controller.viz_LoadDatasetAsync(str(staged_trace))
            self.assertTrue(load_job.ok, load_job.message)
            deadline = time.time() + 5.0
            while time.time() < deadline:
                snapshot = controller.jobs.status(load_job.data["job_id"])
                self.assertTrue(snapshot.ok, snapshot.message)
                if snapshot.data["payload"]["stage"] == "parse_rebuild":
                    break
                time.sleep(0.01)
            self.assertEqual(snapshot.data["payload"]["stage"], "parse_rebuild")
            self.assertEqual(snapshot.data["payload"]["job_kind"], "parse_rebuild")

            query_job = controller.viz_QueryTimelineLODAsync(
                {
                    "dataset_id": loaded.data,
                    "time_window": (
                        bundle.event_stream[0].timestamp_aligned,
                        bundle.event_stream[-1].timestamp_aligned,
                    ),
                    "filter": {},
                    "lod": 0,
                }
            )
            self.assertTrue(query_job.ok, query_job.message)
            query_deadline = time.time() + 5.0
            query_status = "created"
            while time.time() < query_deadline:
                query_snapshot = controller.jobs.status(query_job.data["job_id"])
                self.assertTrue(query_snapshot.ok, query_snapshot.message)
                query_status = query_snapshot.data["status"]
                if query_status in {"succeeded", "failed"}:
                    break
                time.sleep(0.01)
            self.assertEqual(query_status, "succeeded")

            load_snapshot = controller.jobs.status(load_job.data["job_id"])
            self.assertTrue(load_snapshot.ok, load_snapshot.message)
            self.assertEqual(load_snapshot.data["payload"]["stage"], "parse_rebuild")
            self.assertEqual(load_snapshot.data["payload"]["job_kind"], "parse_rebuild")

        resolve_deadline = time.time() + 5.0
        while time.time() < resolve_deadline:
            load_snapshot = controller.jobs.status(load_job.data["job_id"])
            self.assertTrue(load_snapshot.ok, load_snapshot.message)
            if load_snapshot.data["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)

        resolved = controller.viz_ResolveLoadDatasetJob(load_job.data["job_id"])
        self.assertTrue(resolved.ok, resolved.message)

    def test_context_pending_jobs_tracks_background_job_lifecycle(self) -> None:
        controller = WorkspaceController()
        job_id = controller.jobs.create("test_job", {"label": "slow"})

        def slow_task() -> dict[str, bool]:
            time.sleep(0.05)
            return {"done": True}

        submitted = controller.jobs.submit(job_id, slow_task)
        self.assertTrue(submitted.ok, submitted.message)

        saw_pending = False
        deadline = time.time() + 5.0
        while time.time() < deadline:
            snapshot = controller.jobs.status(job_id)
            self.assertTrue(snapshot.ok, snapshot.message)
            context = controller.context_store.get()
            if snapshot.data["status"] in {"queued", "running"}:
                saw_pending = True
                self.assertIn(job_id, context.pending_jobs)
            if snapshot.data["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)

        self.assertTrue(saw_pending)
        self.assertEqual(controller.jobs.result(job_id).data, {"done": True})
        final_context = controller.context_store.get()
        self.assertNotIn(job_id, final_context.pending_jobs)
        self.assertNotIn("pending_jobs", final_context.persisted_dict())

    def test_metric_bucket_series_and_core_lane_grouping(self) -> None:
        trace_path = write_scenario(self.root / "multi-core.trace", name="multi_core")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle

        metrics = controller.viz_QueryMetricSeries(
            {
                "dataset_id": loaded.data,
                "time_window": (bundle.event_stream[0].timestamp_aligned, bundle.event_stream[-1].timestamp_aligned),
                "filter": {},
                "lod": 2,
            }
        )
        self.assertTrue(metrics.ok, metrics.message)
        cpu_metric = next(item for item in metrics.data if item["metric_id"] == "cpu_utilization")
        self.assertGreater(len(cpu_metric["bucket_series"]), 1)
        self.assertTrue(all("midpoint" in point and "value" in point for point in cpu_metric["bucket_series"]))

        task_states = controller.viz_QueryTaskStates(
            TaskStateQuery(
                time_window=(bundle.event_stream[0].timestamp_aligned, bundle.event_stream[-1].timestamp_aligned),
                lane_group="core",
            )
        )
        self.assertTrue(task_states.ok, task_states.message)
        self.assertTrue(all(label.startswith("Core ") for label in task_states.data.lane_order))
        self.assertTrue(all(row.lane_label.startswith("Core ") for row in task_states.data.rows))

    def test_event_table_returns_not_ready_before_query_stage(self) -> None:
        trace_path = write_scenario(self.root / "event-not-ready.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        record.artifact.bundle.event_stream = []
        record.artifact.source = str(self.root / "missing.trace")

        page = controller.viz_QueryEventTable(
            EventTableQuery(
                filter={"dataset_id": loaded.data},
                limit=4,
            )
        )
        self.assertFalse(page.ok)
        self.assertEqual(page.code, "NOT_READY")

    def test_task_state_view_trusted_flag_tracks_untrusted_windows(self) -> None:
        trace_path = write_scenario(self.root / "task-state-untrusted.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        target = record.artifact.bundle.task_states[0]
        existing_window_count = sum(
            1
            for window in record.artifact.bundle.untrusted_windows
            if window.t_begin < target.t_end and window.t_end > target.t_begin
        )
        record.artifact.bundle.untrusted_windows.append(
            UntrustedWindow(
                window_id="task-state-gap",
                source="test",
                scope="task_state",
                t_begin=target.t_begin,
                t_end=target.t_end,
                reason_code="TEST_UNTRUSTED",
                severity="warning",
            )
        )

        task_states = controller.viz_QueryTaskStates(
            TaskStateQuery(
                time_window=(target.t_begin, target.t_end),
                task_filter=[target.task_id],
            )
        )
        self.assertTrue(task_states.ok, task_states.message)
        self.assertFalse(task_states.data.trusted)
        self.assertEqual(
            task_states.data.summary["trusted_summary"]["untrusted_window_count"],
            existing_window_count + 1,
        )
        self.assertGreater(len(task_states.data.rows), 0)
        row = task_states.data.rows[0]
        self.assertFalse(row.trusted)
        self.assertTrue(any(not item.trusted for item in row.segments))

    def test_task_state_query_uses_windowed_source_not_full_bundle_scan(self) -> None:
        trace_path = write_scenario(self.root / "task-state-window-index.trace", name="multi_core", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        full_window = (
            record.artifact.bundle.event_stream[0].timestamp_aligned,
            record.artifact.bundle.event_stream[-1].timestamp_aligned,
        )
        seeded = controller.viz_QueryTaskStates(
            TaskStateQuery(
                time_window=full_window,
            )
        )
        self.assertTrue(seeded.ok, seeded.message)
        self.assertIsNotNone(record.task_state_window_index)
        target_row = seeded.data.rows[0]
        target_segment = target_row.segments[0]

        record.artifact.bundle.task_states = []
        queried = controller.viz_QueryTaskStates(
            TaskStateQuery(
                time_window=target_row.time_window,
                state_mask=[target_segment.state],
                task_filter=[target_row.task_id],
            )
        )
        self.assertTrue(queried.ok, queried.message)
        self.assertGreater(len(queried.data.rows), 0)
        self.assertEqual(queried.data.summary["source"], "task_state_window_index")
        self.assertFalse(queried.data.summary["cache_hit"])

    def test_task_state_cache_key_respects_lane_group_state_mask_filter(self) -> None:
        trace_path = write_scenario(self.root / "task-state-cache.trace", name="multi_core", repeat=2)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        bundle = record.artifact.bundle
        full_window = (
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
        )
        target_segment = bundle.task_states[0]

        queries = [
            TaskStateQuery(time_window=full_window),
            TaskStateQuery(time_window=full_window, lane_group="core"),
            TaskStateQuery(
                time_window=full_window,
                state_mask=[target_segment.state],
            ),
            TaskStateQuery(
                time_window=full_window,
                task_filter=[target_segment.task_id],
            ),
        ]
        for item in queries:
            result = controller.viz_QueryTaskStates(item)
            self.assertTrue(result.ok, result.message)

        self.assertEqual(len(controller.repository.query_cache.keys(loaded.data, "task_states")), 4)
        repeated = controller.viz_QueryTaskStates(queries[0])
        self.assertTrue(repeated.ok, repeated.message)
        self.assertTrue(repeated.data.summary["cache_hit"])
        self.assertEqual(repeated.data.summary["cache_source"], "query_cache:task_states")
        self.assertEqual(len(controller.repository.query_cache.keys(loaded.data, "task_states")), 4)

    def test_task_state_private_resource_filter_keeps_distinct_cache_keys(self) -> None:
        trace_path = write_scenario(self.root / "task-state-resource-cache.trace", name="multi_core", repeat=2)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        full_window = (
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
        )
        target_segment = next((item for item in bundle.task_states if item.related_obj is not None), None)
        self.assertIsNotNone(target_segment)
        assert target_segment is not None

        query_a = TaskStateQuery(time_window=full_window, task_filter=[target_segment.task_id])
        setattr(query_a, "_runtime_filter", {"resource_id": target_segment.related_obj})
        result_a = controller.viz_QueryTaskStates(query_a)
        self.assertTrue(result_a.ok, result_a.message)

        query_b = TaskStateQuery(time_window=full_window, task_filter=[target_segment.task_id])
        setattr(query_b, "_runtime_filter", {"resource_id": 0xDEAD})
        result_b = controller.viz_QueryTaskStates(query_b)
        self.assertTrue(result_b.ok, result_b.message)

        keys = controller.repository.query_cache.keys(loaded.data, "task_states")
        self.assertEqual(len(keys), 2)

    def test_query_cache_eviction_respects_lru_and_budget(self) -> None:
        trace_path = write_scenario(self.root / "query-cache-eviction.trace", name="multi_core", repeat=6)
        controller = WorkspaceController(cache_budget_mb=1.0)
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        bundle = record.artifact.bundle
        full_window = (
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
        )
        task_query = TaskStateQuery(time_window=full_window)
        event_query = EventTableQuery(filter={"dataset_id": loaded.data}, limit=8)
        timeline_scope = {
            "dataset_id": loaded.data,
            "time_window": full_window,
            "filter": {},
            "lod": 2,
            "event_limit": 8,
        }

        task_states = controller.viz_QueryTaskStates(task_query)
        event_page = controller.viz_QueryEventTable(event_query)
        timeline = controller.viz_QueryTimelineLOD(timeline_scope)
        self.assertTrue(task_states.ok, task_states.message)
        self.assertTrue(event_page.ok, event_page.message)
        self.assertTrue(timeline.ok, timeline.message)

        refreshed = controller.viz_QueryTaskStates(task_query)
        self.assertTrue(refreshed.ok, refreshed.message)
        self.assertTrue(refreshed.data.summary["cache_hit"])

        stats_before = controller.repository.query_cache.stats(loaded.data)
        namespaces = stats_before["namespaces"]
        task_bytes = namespaces["task_states"]["byte_size_estimate"]
        event_bytes = namespaces["event_table"]["byte_size_estimate"]
        timeline_bytes = namespaces["timeline"]["byte_size_estimate"]
        budget_bytes = task_bytes + timeline_bytes + min(256, max(event_bytes - 1, 0))

        evicted = controller.repository.query_cache.configure(budget_bytes / (1024 * 1024))
        self.assertEqual([entry.namespace for entry in evicted], ["event_table"])

        stats_after = controller.repository.query_cache.stats(loaded.data)
        self.assertGreater(stats_after["eviction_count"], 0)
        self.assertEqual(len(controller.repository.query_cache.keys(loaded.data, "task_states")), 1)
        self.assertEqual(len(controller.repository.query_cache.keys(loaded.data, "event_table")), 0)
        self.assertEqual(len(controller.repository.query_cache.keys(loaded.data, "timeline")), 1)

        retained = controller.viz_QueryTaskStates(task_query)
        self.assertTrue(retained.ok, retained.message)
        self.assertTrue(retained.data.summary["cache_hit"])
        self.assertEqual(retained.data.summary["cache_source"], "query_cache:task_states")

    def test_query_cache_namespaces_are_separated(self) -> None:
        trace_path = write_scenario(self.root / "query-cache-namespaces.trace", name="multi_core", repeat=4)
        controller = WorkspaceController(cache_budget_mb=1.0)
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        full_window = (
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
        )
        task_query = TaskStateQuery(time_window=full_window)
        event_query = EventTableQuery(filter={"dataset_id": loaded.data}, limit=8)
        timeline_scope = {
            "dataset_id": loaded.data,
            "time_window": full_window,
            "filter": {},
            "lod": 2,
            "event_limit": 8,
        }

        first_task = controller.viz_QueryTaskStates(task_query)
        first_event = controller.viz_QueryEventTable(event_query)
        first_timeline = controller.viz_QueryTimelineLOD(timeline_scope)
        self.assertTrue(first_task.ok, first_task.message)
        self.assertTrue(first_event.ok, first_event.message)
        self.assertTrue(first_timeline.ok, first_timeline.message)
        self.assertFalse(first_task.data.summary["cache_hit"])
        self.assertFalse(first_event.data.summary["cache_hit"])
        self.assertFalse(first_timeline.data.summary["cache_hit"])

        repeated_task = controller.viz_QueryTaskStates(task_query)
        repeated_event = controller.viz_QueryEventTable(event_query)
        repeated_timeline = controller.viz_QueryTimelineLOD(timeline_scope)
        self.assertTrue(repeated_task.ok, repeated_task.message)
        self.assertTrue(repeated_event.ok, repeated_event.message)
        self.assertTrue(repeated_timeline.ok, repeated_timeline.message)
        self.assertTrue(repeated_task.data.summary["cache_hit"])
        self.assertTrue(repeated_event.data.summary["cache_hit"])
        self.assertTrue(repeated_timeline.data.summary["cache_hit"])
        self.assertEqual(repeated_task.data.summary["cache_source"], "query_cache:task_states")
        self.assertEqual(repeated_event.data.summary["cache_source"], "query_cache:event_table")
        self.assertEqual(repeated_timeline.data.summary["cache_source"], "query_cache:timeline")
        self.assertEqual(len(controller.repository.query_cache.keys(loaded.data, "task_states")), 1)
        self.assertEqual(len(controller.repository.query_cache.keys(loaded.data, "event_table")), 1)
        self.assertEqual(len(controller.repository.query_cache.keys(loaded.data, "timeline")), 1)

    def test_task_state_query_returns_not_ready_before_formal_query_stage(self) -> None:
        trace_path = write_scenario(self.root / "task-state-not-ready.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        record.artifact.bundle.task_states = []
        record.task_state_window_index = None
        controller.repository.query_cache.clear_dataset(loaded.data)

        task_states = controller.viz_QueryTaskStates(
            TaskStateQuery(
                time_window=(0.0, 100.0),
            )
        )
        self.assertFalse(task_states.ok)
        self.assertEqual(task_states.code, "NOT_READY")

    def test_thin_artifact_event_table_and_lod2_queries_work_without_manual_stream_mutation(self) -> None:
        trace_path = write_scenario(self.root / "thin-artifact-query.trace", name="basic")
        loaded = load_dataset_with_timings(
            trace_path,
            materialize_event_stream=False,
            index_build_mode="minimal",
        )
        self.assertTrue(loaded.ok, loaded.message)
        self.assertEqual(loaded.data["artifact"].bundle.event_stream, [])
        self.assertEqual(loaded.data["artifact"].bundle.index_bundle.summary["index_build_mode"], "minimal")
        prescan = prs_Prescan(trace_path, include_task_state_preview=False)
        self.assertTrue(prescan.ok, prescan.message)
        prescan_window = prescan.data.get("time_window")
        self.assertIsInstance(prescan_window, (list, tuple))
        self.assertEqual(len(prescan_window), 2)
        time_window = (float(prescan_window[0]), float(prescan_window[1]))

        controller = WorkspaceController()
        registered = controller._register_artifact(loaded.data["artifact"])
        self.assertTrue(registered.ok, registered.message)
        dataset_id = registered.data
        context = controller.viz_SetContext(
            {
                "time_window": time_window,
                "filter": {},
                "selection": {"task_id": 1},
                "zoom_level": 1.5,
                "focused_view": "timeline",
                "dataset_role": "single",
            }
        )
        self.assertTrue(context.ok, context.message)
        event_page = controller.viz_QueryEventTable(EventTableQuery(filter={"dataset_id": dataset_id}, limit=4))
        self.assertTrue(event_page.ok, event_page.message)
        self.assertGreater(len(event_page.data.items), 0)
        self.assertIn(event_page.data.summary["source"], {"trace_window_scan", "package_index"})
        self.assertIsNone(event_page.data.summary["fallback_reason"])
        self.assertTrue(event_page.data.summary["readiness"]["view_ready"]["event_table"])
        self.assertTrue(event_page.data.summary["readiness"]["view_ready"]["timeline"])
        self.assertTrue(event_page.data.summary["readiness"]["lod_ready"]["lod2"])

        lod2 = controller.viz_QueryTimelineLOD(
            {
                "dataset_id": dataset_id,
                "time_window": time_window,
                "filter": {},
                "lod": 2,
                "event_limit": 64,
            }
        )
        self.assertTrue(lod2.ok, lod2.message)
        self.assertGreater(len(lod2.data.events), 0)
        self.assertIn(lod2.data.summary["source"], {"trace_window_scan", "package_index"})
        self.assertIsNone(lod2.data.summary["fallback_reason"])
        self.assertTrue(lod2.data.summary["readiness"]["view_ready"]["event_table"])
        self.assertTrue(lod2.data.summary["readiness"]["view_ready"]["timeline"])
        self.assertTrue(lod2.data.summary["readiness"]["lod_ready"]["lod2"])

    def test_official_thin_load_registers_context_time_window(self) -> None:
        trace_path = write_scenario(self.root / "official-thin-load.trace", name="basic", repeat=4)
        preview = prs_Prescan(trace_path, include_task_state_preview=False)
        self.assertTrue(preview.ok, preview.message)
        expected_window = tuple(float(item) for item in preview.data["time_window"])
        controller = WorkspaceController()

        with patch("desktop.services.RuntimeOptimizationAdvisor.plan_load", return_value=self._cold_preview_runtime_load_plan()):
            loaded = controller.viz_LoadDataset(str(trace_path))

        self.assertTrue(loaded.ok, loaded.message)
        context = controller.context_store.get()
        self.assertEqual(context.time_window, expected_window)
        self.assertEqual(context.dataset_role, "single")
        self.assertEqual(controller.repository.get(loaded.data).artifact.bundle.event_stream, [])

    def test_thin_artifact_deferred_index_build_materializes_minimal_lod0_on_demand(self) -> None:
        trace_path = write_scenario(self.root / "thin-artifact-deferred.trace", name="basic")
        loaded = load_dataset_with_timings(
            trace_path,
            materialize_event_stream=False,
            index_build_mode="deferred",
        )
        self.assertTrue(loaded.ok, loaded.message)
        controller = WorkspaceController()
        registered = controller._register_artifact(loaded.data["artifact"])
        self.assertTrue(registered.ok, registered.message)
        dataset_id = registered.data
        bundle = controller.repository.get(dataset_id).artifact.bundle
        self.assertEqual(bundle.index_bundle.summary["index_build_mode"], "deferred")
        prescan = prs_Prescan(trace_path, include_task_state_preview=False)
        self.assertTrue(prescan.ok, prescan.message)
        time_window = tuple(float(item) for item in prescan.data["time_window"])

        lod0 = controller.viz_QueryTimelineLOD(
            {
                "dataset_id": dataset_id,
                "time_window": time_window,
                "filter": {},
                "lod": 0,
            }
        )
        self.assertTrue(lod0.ok, lod0.message)
        self.assertGreater(len(lod0.data.buckets), 0)
        self.assertEqual(lod0.data.summary["source"], "index_bundle")
        self.assertEqual(bundle.index_bundle.summary["index_build_mode"], "minimal")

    def test_event_table_first_page_does_not_require_full_event_stream_materialization(self) -> None:
        trace_path = write_scenario(self.root / "event-table-source.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        bundle_window = (
            record.artifact.bundle.event_stream[0].timestamp_aligned,
            record.artifact.bundle.event_stream[-1].timestamp_aligned,
        )
        controller.viz_SetContext({"time_window": bundle_window})
        record.artifact.bundle.event_stream = []

        page = controller.viz_QueryEventTable(EventTableQuery(filter={"dataset_id": loaded.data}, limit=4))
        self.assertTrue(page.ok, page.message)
        self.assertGreater(len(page.data.items), 0)
        self.assertEqual(page.data.summary["source"], "trace_window_scan")
        self.assertIsNone(page.data.summary["fallback_reason"])
        self.assertTrue(page.data.summary["readiness"]["view_ready"]["event_table"])

    def test_event_table_source_backed_forward_backward_paging(self) -> None:
        trace_path = write_scenario(self.root / "event-table-source-paging.trace", name="multi_core", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        bundle = record.artifact.bundle
        bundle_window = (
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
        )
        controller.viz_SetContext({"time_window": bundle_window})
        record.artifact.bundle.event_stream = []
        controller.repository.query_cache.clear_dataset(loaded.data)

        page1 = controller.viz_QueryEventTable(
            EventTableQuery(filter={"dataset_id": loaded.data}, limit=4, direction="forward")
        )
        self.assertTrue(page1.ok, page1.message)
        self.assertIn(page1.data.summary["source"], {"trace_window_scan", "package_index"})
        self.assertIsNone(page1.data.summary["fallback_reason"])
        self.assertGreater(len(page1.data.items), 0)
        self.assertIsNotNone(page1.data.next_cursor)

        page2 = controller.viz_QueryEventTable(
            EventTableQuery(
                filter={"dataset_id": loaded.data},
                limit=4,
                cursor=page1.data.next_cursor,
                direction="forward",
            )
        )
        self.assertTrue(page2.ok, page2.message)
        self.assertIn(page2.data.summary["source"], {"trace_window_scan", "package_index"})
        self.assertEqual(page2.data.cursor_in, page1.data.next_cursor)
        self.assertTrue(all(item.sort_key > page1.data.next_cursor.sort_key for item in page2.data.items))
        self.assertTrue(
            all(
                earlier.sort_key < later.sort_key
                for earlier, later in zip(page2.data.items, page2.data.items[1:])
            )
        )
        self.assertIsNotNone(page2.data.prev_cursor)

        backward = controller.viz_QueryEventTable(
            EventTableQuery(
                filter={"dataset_id": loaded.data},
                limit=4,
                cursor=page2.data.prev_cursor,
                direction="backward",
            )
        )
        self.assertTrue(backward.ok, backward.message)
        self.assertIn(backward.data.summary["source"], {"trace_window_scan", "package_index"})
        self.assertEqual(backward.data.cursor_in, page2.data.prev_cursor)
        self.assertEqual(
            [item.event_uid for item in backward.data.items],
            [item.event_uid for item in page1.data.items],
        )

    def test_event_table_cache_eviction_preserves_cursor_round_trip(self) -> None:
        trace_path = write_scenario(self.root / "event-table-cache-roundtrip.trace", name="multi_core", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        bundle = record.artifact.bundle
        bundle_window = (
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
        )
        controller.viz_SetContext({"time_window": bundle_window})
        record.artifact.bundle.event_stream = []
        controller.repository.query_cache.clear_dataset(loaded.data)

        page1 = controller.viz_QueryEventTable(
            EventTableQuery(filter={"dataset_id": loaded.data}, limit=5, direction="forward")
        )
        self.assertTrue(page1.ok, page1.message)
        self.assertIsNotNone(page1.data.next_cursor)

        page2 = controller.viz_QueryEventTable(
            EventTableQuery(
                filter={"dataset_id": loaded.data},
                limit=5,
                cursor=page1.data.next_cursor,
                direction="forward",
            )
        )
        self.assertTrue(page2.ok, page2.message)
        self.assertIn(page2.data.summary["source"], {"trace_window_scan", "package_index"})
        self.assertFalse(page2.data.summary["cache_hit"])

        controller.repository.query_cache.configure(0.0)
        self.assertEqual(len(controller.repository.query_cache.keys(loaded.data, "event_table")), 0)
        controller.repository.query_cache.configure(8.0)

        repeat_page = controller.viz_QueryEventTable(
            EventTableQuery(
                filter={"dataset_id": loaded.data},
                limit=5,
                cursor=page1.data.next_cursor,
                direction="forward",
            )
        )
        self.assertTrue(repeat_page.ok, repeat_page.message)
        self.assertIn(repeat_page.data.summary["source"], {"trace_window_scan", "package_index"})
        self.assertFalse(repeat_page.data.summary["cache_hit"])
        self.assertEqual(
            [item.event_uid for item in repeat_page.data.items],
            [item.event_uid for item in page2.data.items],
        )

        cached_page = controller.viz_QueryEventTable(
            EventTableQuery(
                filter={"dataset_id": loaded.data},
                limit=5,
                cursor=page1.data.next_cursor,
                direction="forward",
            )
        )
        self.assertTrue(cached_page.ok, cached_page.message)
        self.assertTrue(cached_page.data.summary["cache_hit"])
        self.assertEqual(cached_page.data.summary["cache_source"], "query_cache:event_table")
        self.assertEqual(
            [item.event_uid for item in cached_page.data.items],
            [item.event_uid for item in page2.data.items],
        )

    def test_event_table_fallback_reason_marks_bundle_scan_path(self) -> None:
        trace_path = write_scenario(self.root / "event-table-bundle.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)

        page = controller.viz_QueryEventTable(EventTableQuery(filter={"dataset_id": loaded.data}, limit=4))
        self.assertTrue(page.ok, page.message)
        self.assertEqual(page.data.summary["source"], "materialized_bundle")
        self.assertEqual(page.data.summary["fallback_reason"], "bundle_scan")

    def test_timeline_lod_returns_distinct_payload_shapes(self) -> None:
        trace_path = write_scenario(self.root / "timeline-lod.trace", name="multi_core")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        self.assertIsNotNone(bundle.index_bundle)
        self.assertIsNotNone(bundle.index_bundle.summary)
        self.assertTrue(
            {
                "event_count",
                "task_count",
                "core_count",
                "time_origin",
                "time_end",
                "bucket_size",
                "bucket_count",
            }.issubset(bundle.index_bundle.summary)
        )
        scope = {
            "dataset_id": loaded.data,
            "time_window": (bundle.event_stream[0].timestamp_aligned, bundle.event_stream[-1].timestamp_aligned),
            "filter": {},
        }

        lod0 = controller.viz_QueryTimelineLOD({**scope, "lod": 0})
        lod1 = controller.viz_QueryTimelineLOD({**scope, "lod": 1})
        lod2 = controller.viz_QueryTimelineLOD({**scope, "lod": 2})

        self.assertTrue(lod0.ok, lod0.message)
        self.assertTrue(lod1.ok, lod1.message)
        self.assertTrue(lod2.ok, lod2.message)
        self.assertGreater(len(lod0.data.buckets), 0)
        self.assertEqual(len(lod0.data.slices), 0)
        self.assertEqual(lod0.data.summary["render_mode"], "bucket")
        self.assertEqual(lod0.data.summary["source"], "index_bundle")
        self.assertFalse(lod0.data.summary["fallback"])
        self.assertTrue(lod0.data.summary["readiness"]["lod_ready"]["lod0"])
        self.assertTrue(lod0.data.summary["readiness"]["view_ready"]["timeline"])
        self.assertGreater(len(lod1.data.slices), 0)
        self.assertGreater(len(lod1.data.switch_points), 0)
        self.assertEqual(lod1.data.summary["render_mode"], "slice")
        self.assertEqual(lod1.data.summary["source"], "materialized_bundle")
        self.assertEqual(lod1.data.summary["switch_count"], len(lod1.data.switch_points))
        self.assertGreater(len(lod2.data.events), 0)
        self.assertEqual(len(lod2.data.slices), 0)
        self.assertEqual(lod2.data.summary["render_mode"], "event")
        self.assertEqual(lod2.data.summary["source"], "materialized_bundle")
        self.assertEqual(lod2.data.summary["fallback_reason"], "bundle_scan")

    def test_lod2_query_works_with_partial_source(self) -> None:
        trace_path = write_scenario(self.root / "lod2-source.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        window = (
            record.artifact.bundle.event_stream[1].timestamp_aligned,
            record.artifact.bundle.event_stream[-2].timestamp_aligned,
        )
        record.artifact.bundle.event_stream = []

        lod2 = controller.viz_QueryTimelineLOD(
            {
                "dataset_id": loaded.data,
                "time_window": window,
                "filter": {},
                "lod": 2,
            }
        )
        self.assertTrue(lod2.ok, lod2.message)
        self.assertGreater(len(lod2.data.events), 0)
        self.assertEqual(lod2.data.summary["source"], "trace_window_scan")
        self.assertIsNone(lod2.data.summary["fallback_reason"])
        self.assertTrue(lod2.data.summary["readiness"]["lod_ready"]["lod2"])

    def test_timeline_lod0_explicitly_marks_scan_fallback_for_task_filter(self) -> None:
        trace_path = write_scenario(self.root / "timeline-fallback.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        scope = {
            "dataset_id": loaded.data,
            "time_window": (bundle.event_stream[0].timestamp_aligned, bundle.event_stream[-1].timestamp_aligned),
            "filter": {"task_id": 1},
            "lod": 0,
        }

        lod0 = controller.viz_QueryTimelineLOD(scope)

        self.assertTrue(lod0.ok, lod0.message)
        self.assertGreater(len(lod0.data.buckets), 0)
        self.assertEqual(lod0.data.summary["source"], "scan_fallback")
        self.assertTrue(lod0.data.summary["fallback"])

    def test_compare_export_repro_and_replay(self) -> None:
        baseline = write_scenario(self.root / "baseline.trace", name="basic", candidate_variant=False)
        candidate = write_scenario(self.root / "candidate.trace", name="basic", candidate_variant=True)
        controller = WorkspaceController()
        baseline_loaded = controller.viz_LoadDataset(str(baseline))
        candidate_loaded = controller.viz_LoadDataset(str(candidate))
        self.assertTrue(baseline_loaded.ok)
        self.assertTrue(candidate_loaded.ok)

        compare = CompareService(controller.repository, controller.context_store)
        self.assertTrue(compare.cmp_LoadPair(baseline_loaded.data, candidate_loaded.data).ok)
        scope = compare.cmp_SetScope({"baseline_id": baseline_loaded.data, "candidate_id": candidate_loaded.data, "filter": {}})
        self.assertTrue(scope.ok, scope.message)
        summary = compare.cmp_QueryDiffSummary()
        self.assertTrue(summary.ok, summary.message)
        self.assertEqual(summary.data["scope"]["baseline_id"], scope.data.baseline_id)
        self.assertEqual(summary.data["scope"]["candidate_id"], scope.data.candidate_id)
        self.assertGreater(len(_summary_metric_rows(summary.data)), 0)

        replay = ReplayService(controller.context_store)
        baseline_bundle = controller.repository.get(baseline_loaded.data).artifact.bundle
        self.assertTrue(replay.replay_Init(baseline_bundle.event_stream, baseline_bundle.exec_slices, controller.context_store.get()).ok)
        self.assertTrue(replay.replay_StepForward(2).ok)
        replay_state = replay.replay_GetState().data
        self.assertEqual(replay_state.cursor["step_index"], 2)
        self.assertEqual(replay_state.cursor["timestamp"], baseline_bundle.event_stream[2].timestamp_aligned)
        self.assertEqual(replay_state.status, "paused")
        replay_payload = dataclass_to_dict(replay_state)
        self.assertNotIn("current_index", replay_payload)
        self.assertNotIn("cursor_ts", replay_payload)
        self.assertNotIn("mode", replay_payload)
        replay_event = baseline_bundle.event_stream[2]
        replay_context = controller.context_store.get()
        self.assertEqual(
            replay_context.time_window,
            (replay_event.timestamp_aligned - 200.0, replay_event.timestamp_aligned + 200.0),
        )
        self.assertEqual(replay_context.playback_cursor["ref_key"], replay_event.ref_key)
        self.assertEqual(replay_context.selection["task_id"], replay_event.task_id)
        self.assertIsNotNone(replay_context.playback_runtime)
        self.assertEqual(replay_context.playback_runtime["current_index"], 2)
        self.assertEqual(replay_context.playback_runtime["status"], "paused")
        self.assertNotIn("playback_runtime", replay_context.persisted_dict())
        seek = replay.replay_Seek({"ref_key": baseline_bundle.event_stream[-1].ref_key})
        self.assertTrue(seek.ok, seek.message)
        self.assertEqual(seek.data.anchor_ref["ref_key"], baseline_bundle.event_stream[-1].ref_key)
        playing = replay.replay_Play(2.0)
        self.assertTrue(playing.ok, playing.message)
        self.assertEqual(playing.data.status, "ended")
        paused = replay.replay_Pause()
        self.assertTrue(paused.ok, paused.message)
        self.assertEqual(paused.data.status, "paused")
        runtime_context = controller.context_store.get()
        self.assertEqual(runtime_context.playback_runtime["mode"], "paused")
        self.assertEqual(runtime_context.playback_runtime["rate"], 2.0)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full(
            {
                "dataset_id": scope.data.baseline_id,
                "run_batch_id": "batch-alpha",
                "version_id": "build-42",
                "experiment_params": {"profile": "lab", "mode": "full"},
            }
        )
        package_dir = self.root / "package"
        written = export.export_WritePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        normalized = export.export_NormalizePackage(str(package_dir))
        self.assertTrue(normalized.ok, normalized.message)
        meta = json.loads((package_dir / "meta.json").read_text(encoding="utf-8"))
        manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
        ref_index = json.loads((package_dir / "event" / "ref_index.json").read_text(encoding="utf-8"))
        manifest_paths = {entry["path"] for entry in manifest["entries"]}
        self.assertEqual(meta["export_mode"], "full")
        self.assertEqual(meta["run_batch_id"], "batch-alpha")
        self.assertEqual(meta["version_id"], "build-42")
        self.assertEqual(meta["experiment_params"], {"profile": "lab", "mode": "full"})
        self.assertEqual(meta["compare_role"], "baseline")
        self.assertEqual(meta["analysis_context"]["dataset_role"], "baseline")
        self.assertEqual(meta["dictionary_status"]["requested_source"], "default")
        self.assertEqual(meta["dictionary_status"]["resolved_source"], "default")
        self.assertFalse(meta["dictionary_status"]["fallback_used"])
        self.assertNotIn("export_time", normalized.data["meta"])
        self.assertNotIn("snapshot_id", normalized.data["meta"])
        self.assertIn("event/events.trace", manifest_paths)
        self.assertIn("result/metrics.csv", manifest_paths)
        self.assertIn("context/anchors.json", manifest_paths)
        self.assertIn("reference/schema/package.schema.json", manifest_paths)

        exported_trace = load_dataset(package_dir / "event" / "events.trace")
        self.assertTrue(exported_trace.ok, exported_trace.message)
        self.assertEqual(
            [item.ref_key for item in exported_trace.data.bundle.event_stream],
            [item["ref_key"] for item in ref_index],
        )

        compare_from_paths = CompareService(controller.repository, controller.context_store)
        self.assertTrue(compare_from_paths.cmp_LoadPair(str(package_dir), str(candidate)).ok)
        scoped = compare_from_paths.cmp_SetScope({"baseline_id": str(package_dir), "candidate_id": str(candidate), "filter": {}})
        self.assertTrue(scoped.ok, scoped.message)
        detail = compare_from_paths.cmp_ListDiffDetails()
        self.assertTrue(detail.ok, detail.message)
        self.assertTrue(all("related_events" in item for item in detail.data))
        reloaded_package = controller.viz_LoadDataset(str(package_dir))
        self.assertTrue(reloaded_package.ok, reloaded_package.message)
        self.assertEqual(reloaded_package.data, scope.data.baseline_id)

        repro = ReproService(controller.repository, controller.context_store)
        self.assertTrue(repro.repro_OpenPackage(str(package_dir)).ok)
        restored = repro.repro_RestoreContext(None)
        self.assertTrue(restored.ok, restored.message)
        loaded_repro = repro.repro_LoadAsDataset("single")
        self.assertTrue(loaded_repro.ok, loaded_repro.message)

    def test_replay_seek_supports_evidence_ref_targets_and_fallbacks(self) -> None:
        trace_path = write_scenario(self.root / "replay-seek-evidence.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle

        replay = ReplayService(controller.context_store)
        init = replay.replay_Init(bundle.event_stream, bundle.exec_slices, controller.context_store.get())
        self.assertTrue(init.ok, init.message)

        dataclass_target = EvidenceRef(
            ref_type="event",
            ref_key=bundle.event_stream[-1].ref_key,
            t_begin=bundle.event_stream[-1].timestamp_aligned,
            t_end=bundle.event_stream[-1].timestamp_aligned,
        )
        seek_dataclass = replay.replay_Seek(dataclass_target)
        self.assertTrue(seek_dataclass.ok, seek_dataclass.message)
        self.assertEqual(seek_dataclass.data.anchor_ref["ref_key"], bundle.event_stream[-1].ref_key)

        dict_target = {
            "ref_type": "event",
            "ref_key": bundle.event_stream[1].ref_key,
            "t_begin": bundle.event_stream[1].timestamp_aligned,
            "t_end": bundle.event_stream[1].timestamp_aligned,
        }
        seek_dict = replay.replay_Seek(dict_target)
        self.assertTrue(seek_dict.ok, seek_dict.message)
        self.assertEqual(seek_dict.data.anchor_ref["ref_key"], bundle.event_stream[1].ref_key)

        fallback_ts = bundle.event_stream[3].timestamp_aligned
        fallback_target = EvidenceRef(
            ref_type="event",
            ref_key="evt:missing:replay-target",
            t_begin=fallback_ts,
            t_end=fallback_ts,
        )
        seek_fallback = replay.replay_Seek(fallback_target)
        self.assertTrue(seek_fallback.ok, seek_fallback.message)
        self.assertEqual(seek_fallback.data.anchor_ref["ref_key"], bundle.event_stream[3].ref_key)

        invalid_target = EvidenceRef(
            ref_type="event",
            ref_key="",
            t_begin=float("nan"),
            t_end=float("nan"),
        )
        seek_invalid = replay.replay_Seek(invalid_target)
        self.assertFalse(seek_invalid.ok)
        self.assertEqual(seek_invalid.code, "INVALID_ARG")

        legacy_target = {"ref_key": bundle.event_stream[0].ref_key}
        seek_legacy = replay.replay_Seek(legacy_target)
        self.assertTrue(seek_legacy.ok, seek_legacy.message)
        self.assertEqual(seek_legacy.data.anchor_ref["ref_key"], bundle.event_stream[0].ref_key)

    def test_compare_scope_round_trip_persists_identically_across_export_and_repro(self) -> None:
        baseline = write_scenario(self.root / "compare-scope-roundtrip-baseline.trace", name="basic", candidate_variant=False, repeat=3)
        candidate = write_scenario(self.root / "compare-scope-roundtrip-candidate.trace", name="basic", candidate_variant=True, repeat=3)
        controller = WorkspaceController()
        baseline_loaded = controller.viz_LoadDataset(str(baseline))
        candidate_loaded = controller.viz_LoadDataset(str(candidate))
        self.assertTrue(baseline_loaded.ok, baseline_loaded.message)
        self.assertTrue(candidate_loaded.ok, candidate_loaded.message)

        compare = CompareService(controller.repository, controller.context_store)
        self.assertTrue(compare.cmp_LoadPair(baseline_loaded.data, candidate_loaded.data).ok)
        scoped = compare.cmp_SetScope(
            {
                "baseline_id": baseline_loaded.data,
                "candidate_id": candidate_loaded.data,
                "filter": {"task_id": 1},
                "dimensions": ["metric", "alert", "resource", "irq"],
                "metric_ids": ["cpu_utilization", "blocked_time"],
                "bucket_size": 128.0,
                "evidence_policy": "retain",
                "scope_id": "scope:test:compare-scope-roundtrip",
            }
        )
        self.assertTrue(scoped.ok, scoped.message)

        expected_scope = dataclass_to_dict(scoped.data)
        self.assertEqual(set(expected_scope), FORMAL_COMPARE_SCOPE_FIELDS)
        self.assertEqual(controller.context_store.get().compare_scope, expected_scope)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": scoped.data.baseline_id})
        package_dir = self.root / "compare-scope-roundtrip-package"
        written = export.export_WritePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        normalized = export.export_NormalizePackage(str(package_dir))
        self.assertTrue(normalized.ok, normalized.message)

        meta = json.loads((package_dir / "meta.json").read_text(encoding="utf-8"))
        analysis_context = json.loads((package_dir / "context" / "analysis_context.json").read_text(encoding="utf-8"))
        compare_scope = json.loads((package_dir / "context" / "compare_scope.json").read_text(encoding="utf-8"))

        self.assertEqual(meta["compare_scope"], expected_scope)
        self.assertEqual(meta["analysis_context"]["compare_scope"], expected_scope)
        self.assertEqual(analysis_context["compare_scope"], expected_scope)
        self.assertEqual(compare_scope, expected_scope)
        self.assertEqual(normalized.data["compare_scope"], expected_scope)

        repro = ReproService(controller.repository, controller.context_store)
        self.assertTrue(repro.repro_OpenPackage(str(package_dir)).ok)
        restored = repro.repro_RestoreContext(None)
        self.assertTrue(restored.ok, restored.message)
        self.assertEqual(restored.data.compare_scope, expected_scope)

    def test_compare_summary_formal_contract_uses_scope_metric_changes_and_trust_summary(self) -> None:
        baseline = write_scenario(self.root / "compare-summary-baseline.trace", name="basic", candidate_variant=False, repeat=3)
        candidate = write_scenario(self.root / "compare-summary-candidate.trace", name="basic", candidate_variant=True, repeat=3)
        controller = WorkspaceController()
        baseline_loaded = controller.viz_LoadDataset(str(baseline))
        candidate_loaded = controller.viz_LoadDataset(str(candidate))
        self.assertTrue(baseline_loaded.ok, baseline_loaded.message)
        self.assertTrue(candidate_loaded.ok, candidate_loaded.message)

        baseline_bundle = controller.repository.get(baseline_loaded.data).artifact.bundle
        candidate_bundle = controller.repository.get(candidate_loaded.data).artifact.bundle
        baseline_bundle.untrusted_windows.append(
            UntrustedWindow(
                window_id="test:compare-summary:baseline",
                source="test",
                scope="compare",
                t_begin=baseline_bundle.event_stream[0].timestamp_aligned,
                t_end=baseline_bundle.event_stream[-1].timestamp_aligned,
                reason_code="TEST_COMPARE_SUMMARY_BASELINE",
                severity="warning",
            )
        )
        candidate_bundle.untrusted_windows.append(
            UntrustedWindow(
                window_id="test:compare-summary:candidate",
                source="test",
                scope="compare",
                t_begin=candidate_bundle.event_stream[0].timestamp_aligned,
                t_end=candidate_bundle.event_stream[-1].timestamp_aligned,
                reason_code="TEST_COMPARE_SUMMARY_CANDIDATE",
                severity="warning",
            )
        )

        compare = CompareService(controller.repository, controller.context_store)
        self.assertTrue(compare.cmp_LoadPair(baseline_loaded.data, candidate_loaded.data).ok)
        scoped = compare.cmp_SetScope(
            {
                "baseline_id": baseline_loaded.data,
                "candidate_id": candidate_loaded.data,
                "filter": {},
                "dimensions": ["metric", "alert", "hotspot", "interval", "task", "core", "resource", "irq"],
                "metric_ids": ["cpu_utilization"],
            }
        )
        self.assertTrue(scoped.ok, scoped.message)

        summary = compare.cmp_QueryDiffSummary()
        self.assertTrue(summary.ok, summary.message)

        self.assertTrue(FORMAL_DIFF_SUMMARY_FIELDS.issubset(summary.data))
        self.assertEqual(set(summary.data["scope"]), FORMAL_COMPARE_SCOPE_FIELDS)
        self.assertEqual(summary.data["scope"], dataclass_to_dict(scoped.data))
        self.assertEqual(set(summary.data["trust_summary"]), FORMAL_TRUST_SUMMARY_FIELDS)
        self.assertTrue(all(isinstance(summary.data[key], list) for key in FORMAL_DIFF_SUMMARY_FIELDS if key not in {"scope", "trust_summary"}))

        trust_summary = summary.data["trust_summary"]
        self.assertFalse(trust_summary["trusted"])
        self.assertGreaterEqual(trust_summary["baseline_untrusted_window_count"], 1)
        self.assertGreaterEqual(trust_summary["candidate_untrusted_window_count"], 1)
        self.assertGreaterEqual(trust_summary["unified_untrusted_window_count"], 2)
        self.assertIn("TEST_COMPARE_SUMMARY_BASELINE", trust_summary["baseline_reason_codes"])
        self.assertIn("TEST_COMPARE_SUMMARY_CANDIDATE", trust_summary["candidate_reason_codes"])

        metric_row = summary.data["metric_changes"][0]
        self.assertTrue(
            {
                "metric_id",
                "baseline",
                "candidate",
                "delta",
                "ratio",
                "trend",
                "degraded_reason",
            }.issubset(metric_row)
        )
        self.assertEqual(
            sorted(metric_row["degraded_reason"]["baseline_reason_codes"]),
            sorted(trust_summary["baseline_reason_codes"]),
        )
        self.assertEqual(
            sorted(metric_row["degraded_reason"]["candidate_reason_codes"]),
            sorted(trust_summary["candidate_reason_codes"]),
        )

    def test_export_rejects_nonempty_incomplete_compare_scope(self) -> None:
        trace_path = write_scenario(self.root / "compare-scope-invalid-export.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        controller.viz_SetContext({"compare_scope": {"baseline_id": loaded.data}})

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": loaded.data})
        package_dir = self.root / "compare-scope-invalid-export-package"
        written = export.export_WritePackage(job.data["job_id"], str(package_dir))
        self.assertFalse(written.ok)
        self.assertEqual(written.code, "INVALID_ARG")
        self.assertIn("compare_scope schema missing fields", written.message)

    def test_repro_restore_rejects_nonempty_incomplete_compare_scope(self) -> None:
        baseline = write_scenario(self.root / "compare-scope-invalid-repro-baseline.trace", name="basic", candidate_variant=False, repeat=3)
        candidate = write_scenario(self.root / "compare-scope-invalid-repro-candidate.trace", name="basic", candidate_variant=True, repeat=3)
        controller = WorkspaceController()
        baseline_loaded = controller.viz_LoadDataset(str(baseline))
        candidate_loaded = controller.viz_LoadDataset(str(candidate))
        self.assertTrue(baseline_loaded.ok, baseline_loaded.message)
        self.assertTrue(candidate_loaded.ok, candidate_loaded.message)

        compare = CompareService(controller.repository, controller.context_store)
        self.assertTrue(compare.cmp_LoadPair(baseline_loaded.data, candidate_loaded.data).ok)
        scoped = compare.cmp_SetScope(
            {
                "baseline_id": baseline_loaded.data,
                "candidate_id": candidate_loaded.data,
                "filter": {},
                "dimensions": ["metric", "resource"],
                "metric_ids": ["cpu_utilization"],
            }
        )
        self.assertTrue(scoped.ok, scoped.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": scoped.data.baseline_id})
        package_dir = self.root / "compare-scope-invalid-repro-package"
        written = export.export_WritePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        bad_compare_scope = {"baseline_id": scoped.data.baseline_id}
        meta_path = package_dir / "meta.json"
        analysis_context_path = package_dir / "context" / "analysis_context.json"
        compare_scope_path = package_dir / "context" / "compare_scope.json"

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["compare_scope"] = bad_compare_scope
        meta["analysis_context"]["compare_scope"] = bad_compare_scope
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        analysis_context = json.loads(analysis_context_path.read_text(encoding="utf-8"))
        analysis_context["compare_scope"] = bad_compare_scope
        analysis_context_path.write_text(json.dumps(analysis_context, ensure_ascii=False, indent=2), encoding="utf-8")
        compare_scope_path.write_text(json.dumps(bad_compare_scope, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_manifest_checksums(
            package_dir,
            "meta.json",
            "context/analysis_context.json",
            "context/compare_scope.json",
        )

        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertTrue(opened.ok, opened.message)
        restored = repro.repro_RestoreContext(None)
        self.assertFalse(restored.ok)
        self.assertEqual(restored.code, "INVALID_ARG")
        self.assertIn("compare_scope schema missing fields", restored.message)

    def test_compare_scope_dimensions_drive_summary_and_detail_outputs(self) -> None:
        baseline = write_scenario(self.root / "compare-dim-baseline.trace", name="basic", candidate_variant=False, repeat=3)
        candidate = write_scenario(self.root / "compare-dim-candidate.trace", name="basic", candidate_variant=True, repeat=3)
        controller = WorkspaceController()
        baseline_loaded = controller.viz_LoadDataset(str(baseline))
        candidate_loaded = controller.viz_LoadDataset(str(candidate))
        self.assertTrue(baseline_loaded.ok, baseline_loaded.message)
        self.assertTrue(candidate_loaded.ok, candidate_loaded.message)

        compare = CompareService(controller.repository, controller.context_store)
        self.assertTrue(compare.cmp_LoadPair(baseline_loaded.data, candidate_loaded.data).ok)
        scoped = compare.cmp_SetScope(
            {
                "baseline_id": baseline_loaded.data,
                "candidate_id": candidate_loaded.data,
                "filter": {},
                "dimensions": ["alert", "hotspot", "interval", "task", "core", "resource", "irq"],
                "metric_ids": ["cpu_utilization"],
            }
        )
        self.assertTrue(scoped.ok, scoped.message)

        summary = compare.cmp_QueryDiffSummary()
        details = compare.cmp_ListDiffDetails()
        self.assertTrue(summary.ok, summary.message)
        self.assertTrue(details.ok, details.message)
        self.assertEqual(
            _summary_dimensions(summary.data),
            ["alert", "hotspot", "interval", "task", "core", "resource", "irq"],
        )
        self.assertEqual(_summary_metric_rows(summary.data), [])
        self.assertGreater(len(summary.data["alert_changes"]), 0)
        self.assertGreater(len(summary.data["hotspot_changes"]), 0)
        self.assertGreater(len(summary.data["interval_changes"]), 0)
        self.assertGreater(len(summary.data["task_changes"]), 0)
        self.assertGreater(len(summary.data["core_changes"]), 0)
        self.assertGreater(len(summary.data["resource_changes"]), 0)
        self.assertGreater(len(summary.data["irq_changes"]), 0)
        for summary_key in ("alert_changes", "hotspot_changes", "interval_changes"):
            self.assertTrue(
                all(
                    {"added", "removed", "severity_shift"}.issubset(item)
                    and isinstance(item.get("evidence_refs"), list)
                    for item in summary.data[summary_key]
                )
            )

        detail_dimensions = {item["target"]["dimension"] for item in details.data}
        self.assertTrue({"alert", "hotspot", "interval", "task", "core", "resource", "irq"}.issubset(detail_dimensions))
        self.assertNotIn("metric", detail_dimensions)

        core_detail = compare.cmp_ListDiffDetails("core")
        self.assertTrue(core_detail.ok, core_detail.message)
        self.assertTrue(core_detail.data)
        self.assertTrue(all(item["target"]["dimension"] == "core" for item in core_detail.data))
        queried = compare.cmp_QueryDiffDetail(core_detail.data[0]["diff_id"])
        self.assertTrue(queried.ok, queried.message)
        self.assertEqual(queried.data["diff_id"], core_detail.data[0]["diff_id"])
        self.assertEqual(queried.data["target"]["dimension"], "core")
        self.assertNotIn("dimension", queried.data)

    def test_compare_dimension_detail_contains_jump_target(self) -> None:
        baseline = write_scenario(self.root / "compare-jump-baseline.trace", name="basic", candidate_variant=False, repeat=3)
        candidate = write_scenario(self.root / "compare-jump-candidate.trace", name="basic", candidate_variant=True, repeat=3)
        controller = WorkspaceController()
        baseline_loaded = controller.viz_LoadDataset(str(baseline))
        candidate_loaded = controller.viz_LoadDataset(str(candidate))
        self.assertTrue(baseline_loaded.ok, baseline_loaded.message)
        self.assertTrue(candidate_loaded.ok, candidate_loaded.message)

        compare = CompareService(controller.repository, controller.context_store)
        self.assertTrue(compare.cmp_LoadPair(baseline_loaded.data, candidate_loaded.data).ok)
        scoped = compare.cmp_SetScope(
            {
                "baseline_id": baseline_loaded.data,
                "candidate_id": candidate_loaded.data,
                "filter": {},
                "dimensions": ["alert", "hotspot", "interval", "task", "core", "resource", "irq"],
            }
        )
        self.assertTrue(scoped.ok, scoped.message)

        details = compare.cmp_ListDiffDetails()
        self.assertTrue(details.ok, details.message)
        grouped = {
            dimension: next(item for item in details.data if item["target"]["dimension"] == dimension)
            for dimension in {"alert", "hotspot", "interval", "task", "core", "resource", "irq"}
        }
        for dimension, detail in grouped.items():
            self.assertGreater(len(detail["evidence_refs"]), 0, dimension)
            self.assertIsNotNone(detail["jump_target"], dimension)
            self.assertIn("time_window", detail["jump_target"], dimension)
            self.assertIn("focused_view", detail["jump_target"], dimension)
            self.assertIsNotNone(detail["jump_target"].get("peer_target"), dimension)
            self.assertIn("time_window", detail["jump_target"]["peer_target"], dimension)
            self.assertIn("focused_view", detail["jump_target"]["peer_target"], dimension)
            self.assertNotEqual(
                detail["jump_target"].get("dataset_role"),
                detail["jump_target"]["peer_target"].get("dataset_role"),
                dimension,
            )
            self.assertIsNotNone(detail["baseline_view"].get("evidence_anchor") or detail["candidate_view"].get("evidence_anchor"), dimension)
            self.assertEqual(detail["target"]["dimension"], dimension)
            self.assertNotIn("dimension", detail)

        self.assertEqual(set(grouped["alert"]["target"]), {"dimension", "alert_type"})
        self.assertEqual(set(grouped["hotspot"]["target"]), {"dimension", "node_id"})
        self.assertEqual(set(grouped["interval"]["target"]), {"dimension", "interval_type"})
        for dimension in {"alert", "hotspot", "interval"}:
            self.assertTrue(
                {"added", "removed", "severity_shift", "evidence_refs"}.issubset(grouped[dimension]["delta_payload"])
            )

        resource_detail = grouped["resource"]
        self.assertEqual(resource_detail["jump_target"]["selection"]["resource_id"], resource_detail["target"]["resource_id"])

    def test_compare_gui_switches_dimensions_without_losing_scope(self) -> None:
        baseline = write_scenario(self.root / "compare-gui-baseline.trace", name="basic", candidate_variant=False, repeat=3)
        candidate = write_scenario(self.root / "compare-gui-candidate.trace", name="basic", candidate_variant=True, repeat=3)
        window = self._make_runtime_window()

        window.load_compare_pair(str(baseline), str(candidate))
        self.assertGreater(self._table_row_count(window.compare_table), 0)
        compare_scope = window.controller.context_store.get().compare_scope
        self.assertIsNotNone(compare_scope)
        self.assertEqual(
            compare_scope["dimensions"],
            ["metric", "alert", "hotspot", "interval", "task", "core", "resource", "irq"],
        )

        window._on_compare_dimension_changed("资源")
        self.assertEqual(window.state.compare_dimension, "resource")
        self.assertGreater(self._table_row_count(window.compare_table), 0)
        self.assertTrue(self._table_cell_text(window.compare_table, 0, 0).startswith("obj:"))

        window._on_compare_dimension_changed("IRQ")
        self.assertEqual(window.state.compare_dimension, "irq")
        self.assertGreater(self._table_row_count(window.compare_table), 0)
        self.assertTrue(self._table_cell_text(window.compare_table, 0, 0).startswith("IRQ"))
        self.assertEqual(window.controller.context_store.get().compare_scope, compare_scope)
        window.close()

    def test_compare_gui_detail_panel_matrix_covers_all_eight_dimensions(self) -> None:
        baseline = write_scenario(self.root / "compare-gui-detail-matrix-baseline.trace", name="basic", candidate_variant=False, repeat=3)
        candidate = write_scenario(self.root / "compare-gui-detail-matrix-candidate.trace", name="basic", candidate_variant=True, repeat=3)
        window = self._make_runtime_window()

        window.load_compare_pair(str(baseline), str(candidate))
        compare_scope = window.controller.context_store.get().compare_scope
        self.assertIsNotNone(compare_scope)
        self.assertEqual(set(compare_scope), FORMAL_COMPARE_SCOPE_FIELDS)

        labels = {
            "metric": "指标",
            "alert": "告警",
            "hotspot": "热点",
            "interval": "区间",
            "task": "任务",
            "core": "核",
            "resource": "资源",
            "irq": "IRQ",
        }
        for dimension, label in labels.items():
            window._on_compare_dimension_changed(label)
            self.assertEqual(window.state.compare_dimension, dimension)
            self.assertGreater(self._table_row_count(window.compare_table), 0, dimension)

            window._on_compare_row_selected(0, 0)
            detail = window._compare_selected_detail
            self.assertIsNotNone(detail, dimension)
            self.assertEqual(set(detail["scope"]), FORMAL_COMPARE_SCOPE_FIELDS)
            self.assertEqual(detail["scope"], compare_scope)
            self.assertEqual(detail["target"]["dimension"], dimension)
            self.assertNotIn("dimension", detail)
            self.assertTrue(window.compare_jump_button.isEnabled(), dimension)
            self.assertTrue(window.compare_peer_jump_button.isEnabled(), dimension)

            panel_text = window.compare_details.toPlainText()
            self.assertIn("对比明细", panel_text)
            self.assertIn(f"维度: {dimension}", panel_text)
            self.assertIn("diff_id:", panel_text)
            self.assertIn("主侧:", panel_text)
            self.assertIn("对侧:", panel_text)
            self.assertIn("Baseline 视图", panel_text)
            self.assertIn("Candidate 视图", panel_text)
            self.assertIn("Delta 摘要", panel_text)
            self.assertIn("相关事件样本", panel_text)

        self.assertEqual(window.controller.context_store.get().compare_scope, compare_scope)
        window.close()

    def test_compare_diff_drilldown_round_trip(self) -> None:
        def _role_label(role: str) -> str:
            return {"baseline": "Baseline", "candidate": "Candidate", "single": "Single"}.get(role, role.title())

        baseline = write_scenario(self.root / "compare-drill-baseline.trace", name="basic", candidate_variant=False, repeat=3)
        candidate = write_scenario(self.root / "compare-drill-candidate.trace", name="basic", candidate_variant=True, repeat=3)
        window = self._make_runtime_window()

        window.load_compare_pair(str(baseline), str(candidate))
        window._on_compare_dimension_changed("资源")
        self.assertGreater(self._table_row_count(window.compare_table), 0)

        window._on_compare_row_selected(0, 0)
        self.assertIsNotNone(window._compare_selected_detail)
        self.assertTrue(window.compare_jump_button.isEnabled())
        self.assertTrue(window.compare_peer_jump_button.isEnabled())
        self.assertIn("跳转摘要", window.compare_details.toPlainText())
        self.assertIn("主侧:", window.compare_details.toPlainText())
        self.assertIn("对侧:", window.compare_details.toPlainText())
        self.assertEqual(window._compare_selected_detail["target"]["dimension"], "resource")
        self.assertNotIn("dimension", window._compare_selected_detail)

        jump_target = window._compare_selected_detail["jump_target"]
        peer_target = jump_target["peer_target"]
        selected_diff_id = window._compare_selected_detail["diff_id"]
        self.assertEqual(
            window.compare_jump_button.text(),
            f"跳转到 {_role_label(jump_target['dataset_role'])} 证据",
        )
        self.assertEqual(
            window.compare_peer_jump_button.text(),
            f"切换到 {_role_label(peer_target['dataset_role'])} 证据",
        )
        window.jump_selected_compare_detail()

        context = window.controller.context_store.get()
        self.assertEqual(window.state.active_tab, "analysis")
        self.assertEqual(window.state.active_dataset_id, jump_target["dataset_id"])
        self.assertEqual(context.dataset_role, jump_target["dataset_role"])
        self.assertEqual(context.focused_view, jump_target["focused_view"])
        self.assertEqual(context.selection["resource_id"], jump_target["selection"]["resource_id"])
        self.assertEqual(context.evidence_anchor["ref_key"], jump_target["evidence_anchor"]["ref_key"])
        self.assertEqual(list(context.time_window), jump_target["time_window"])
        self.assertIsNotNone(window._compare_selected_detail)
        self.assertEqual(window._compare_selected_detail["diff_id"], selected_diff_id)
        self.assertTrue(window.compare_peer_jump_button.isEnabled())

        window.jump_selected_compare_peer_detail()

        context = window.controller.context_store.get()
        self.assertEqual(window.state.active_tab, "analysis")
        self.assertEqual(window.state.active_dataset_id, peer_target["dataset_id"])
        self.assertEqual(context.dataset_role, peer_target["dataset_role"])
        self.assertEqual(context.focused_view, peer_target["focused_view"])
        self.assertEqual(context.selection["resource_id"], peer_target["selection"]["resource_id"])
        self.assertEqual(context.evidence_anchor["ref_key"], peer_target["evidence_anchor"]["ref_key"])
        self.assertEqual(list(context.time_window), peer_target["time_window"])
        self.assertIsNotNone(window._compare_selected_detail)
        self.assertEqual(window._compare_selected_detail["diff_id"], selected_diff_id)
        window.close()

    def test_export_package_uses_actual_loaded_dictionary(self) -> None:
        trace_path = write_scenario(self.root / "external-dict.trace", name="basic")
        custom_dictionary = self._rename_event(self._dictionary_copy(), 0x1001, "TASK_READY_CUSTOM_EXPORT")
        dataset = load_dataset(trace_path, dictionary=custom_dictionary)
        self.assertTrue(dataset.ok, dataset.message)

        controller = WorkspaceController()
        metric_session = metric_Init().data
        ingested = metric_Ingest(metric_session, dataset.data.bundle)
        self.assertTrue(ingested.ok, ingested.message)
        dataset_id = controller.repository.add(DatasetRecord(artifact=dataset.data, metric_session=metric_session))
        controller.active_dataset_id = dataset_id

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": dataset_id})
        package_dir = self.root / "external-dict-package"
        written = export.export_WritePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        meta = json.loads((package_dir / "meta.json").read_text(encoding="utf-8"))
        packaged_dictionary = json.loads((package_dir / "reference" / "dictionary.json").read_text(encoding="utf-8"))

        self.assertEqual(meta["dictionary_status"]["requested_source"], "external_dict")
        self.assertEqual(meta["dictionary_status"]["resolved_source"], "external")
        self.assertEqual(packaged_dictionary["dict_ver"], custom_dictionary["dict_ver"])
        ready_def = next(item for item in packaged_dictionary["event_defs"] if item["event_id"] == 0x1001)
        self.assertEqual(ready_def["event_name"], "TASK_READY_CUSTOM_EXPORT")

    def test_source_backed_thin_export_matches_materialized_normalized_package(self) -> None:
        trace_path = write_scenario(self.root / "thin-source-backed-export.trace", name="multi_core", repeat=4)
        prescan = prs_Prescan(trace_path, include_task_state_preview=False)
        self.assertTrue(prescan.ok, prescan.message)
        time_window = tuple(float(item) for item in prescan.data["time_window"])

        full_controller = WorkspaceController()
        full_loaded = full_controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(full_loaded.ok, full_loaded.message)
        full_context = full_controller.viz_SetContext(
            {
                "time_window": time_window,
                "filter": {},
                "selection": {"task_id": 1},
                "zoom_level": 1.5,
                "focused_view": "timeline",
                "dataset_role": "single",
            }
        )
        self.assertTrue(full_context.ok, full_context.message)

        thin_loaded = load_dataset_with_timings(
            trace_path,
            materialize_event_stream=False,
            index_build_mode="minimal",
        )
        self.assertTrue(thin_loaded.ok, thin_loaded.message)
        self.assertEqual(thin_loaded.data["artifact"].bundle.event_stream, [])
        thin_controller = WorkspaceController()
        thin_registered = thin_controller._register_artifact(thin_loaded.data["artifact"])
        self.assertTrue(thin_registered.ok, thin_registered.message)
        thin_context = thin_controller.viz_SetContext(
            {
                "time_window": time_window,
                "filter": {},
                "selection": {"task_id": 1},
                "zoom_level": 1.5,
                "focused_view": "timeline",
                "dataset_role": "single",
            }
        )
        self.assertTrue(thin_context.ok, thin_context.message)

        full_export = ExportService(full_controller.repository, full_controller.context_store, full_controller.jobs)
        full_job = full_export.export_Full({"dataset_id": full_loaded.data})
        self.assertTrue(full_job.ok, full_job.message)
        full_package = self.root / "materialized-export-package"
        full_written = full_export.export_WritePackage(full_job.data["job_id"], str(full_package))
        self.assertTrue(full_written.ok, full_written.message)
        full_normalized = full_export.export_NormalizePackage(str(full_package))
        self.assertTrue(full_normalized.ok, full_normalized.message)

        thin_export = ExportService(thin_controller.repository, thin_controller.context_store, thin_controller.jobs)
        thin_job = thin_export.export_Full({"dataset_id": thin_registered.data})
        self.assertTrue(thin_job.ok, thin_job.message)
        thin_package = self.root / "source-backed-export-package"
        thin_written = thin_export.export_WritePackage(thin_job.data["job_id"], str(thin_package))
        self.assertTrue(thin_written.ok, thin_written.message)
        self.assertEqual(thin_written.data["write_mode"], "source_backed_streaming")
        thin_rebuild = json.loads((thin_package / "rebuild" / "rebuild_bundle.json").read_text(encoding="utf-8"))
        self.assertEqual(thin_rebuild["event_stream"], [])
        self.assertIn("alignment", thin_rebuild)
        self.assertIsNotNone(thin_rebuild["alignment"])
        self.assertTrue(thin_rebuild["alignment"]["calibrated"])
        import desktop.services as services_module

        rebuild_bundle_path = thin_package / "rebuild" / "rebuild_bundle.json"
        rebuild_bundle_load_count = 0
        original_json_load = services_module.json_load

        def _counting_json_load(path: str | Path) -> object:
            nonlocal rebuild_bundle_load_count
            if Path(path) == rebuild_bundle_path:
                rebuild_bundle_load_count += 1
            return original_json_load(path)

        with patch("desktop.services.json_load", side_effect=_counting_json_load):
            thin_normalized = thin_export.export_NormalizePackage(str(thin_package))
        self.assertTrue(thin_normalized.ok, thin_normalized.message)
        self.assertEqual(rebuild_bundle_load_count, 1)
        self.assertEqual(full_normalized.data, thin_normalized.data)
        self.assertEqual(full_normalized.data["anchors"], thin_normalized.data["anchors"])

        repro_controller = WorkspaceController()
        repro = ReproService(repro_controller.repository, repro_controller.context_store)
        opened = repro.repro_OpenPackage(str(thin_package))
        self.assertTrue(opened.ok, opened.message)
        restored = repro.repro_RestoreContext(None)
        self.assertTrue(restored.ok, restored.message)
        repro_loaded = repro.repro_LoadAsDataset("single")
        self.assertTrue(repro_loaded.ok, repro_loaded.message)
        repro_bundle = repro_controller.repository.get(repro_loaded.data).artifact.bundle
        self.assertGreater(len(repro_bundle.event_stream), 0)
        self.assertIsNotNone(repro_bundle.alignment)
        self.assertTrue(repro_bundle.alignment.calibrated)

    def test_anchor_rows_preserve_diagnosis_anchor_contract_without_materialized_diagnoses(self) -> None:
        controller = WorkspaceController()
        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        alerts = [
            Alert(
                alert_id="alert-1",
                type="deadline_miss",
                severity="high",
                time_window=(10.0, 20.0),
                object_scope={"task_id": 7},
                threshold=8.0,
                actual=12.0,
                evidence_refs=[
                    EvidenceRef(ref_type="index", ref_key="ref:10", t_begin=10.0, t_end=12.0),
                    EvidenceRef(ref_type="index", ref_key="ref:11", t_begin=12.0, t_end=20.0),
                ],
                trusted=True,
            )
        ]
        anchors = export._anchor_rows(
            {
                "time_window": [10.0, 20.0],
                "evidence_anchor": {"ref_key": "ref:10"},
            },
            alerts,
        )
        self.assertEqual(
            anchors,
            [
                {
                    "anchor_id": "context:current",
                    "anchor_type": "context",
                    "evidence_anchor": {"ref_key": "ref:10"},
                    "time_window": [10.0, 20.0],
                },
                {
                    "anchor_id": "alert-1:0",
                    "anchor_type": "alert",
                    "owner_id": "alert-1",
                    "evidence_anchor": {"ref_type": "index", "ref_key": "ref:10", "t_begin": 10.0, "t_end": 12.0},
                    "time_window": [10.0, 20.0],
                },
                {
                    "anchor_id": "alert-1:1",
                    "anchor_type": "alert",
                    "owner_id": "alert-1",
                    "evidence_anchor": {"ref_type": "index", "ref_key": "ref:11", "t_begin": 12.0, "t_end": 20.0},
                    "time_window": [10.0, 20.0],
                },
                {
                    "anchor_id": "diag:alert-1:0",
                    "anchor_type": "diagnosis",
                    "owner_id": "diag:alert-1",
                    "evidence_anchor": {"ref_type": "index", "ref_key": "ref:10", "t_begin": 10.0, "t_end": 12.0},
                    "time_window": [10.0, 20.0],
                },
                {
                    "anchor_id": "diag:alert-1:1",
                    "anchor_type": "diagnosis",
                    "owner_id": "diag:alert-1",
                    "evidence_anchor": {"ref_type": "index", "ref_key": "ref:11", "t_begin": 12.0, "t_end": 20.0},
                    "time_window": [10.0, 20.0],
                },
            ],
        )

    def test_export_package_streams_diagnoses_without_calling_diag_generate(self) -> None:
        trace_path = write_scenario(self.root / "diag-stream.trace", name="basic", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        context = controller.viz_SetContext({"evidence_anchor": {"ref_key": "ref:stream"}})
        self.assertTrue(context.ok, context.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": loaded.data})
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "diag-stream-package"

        with patch(
            "desktop.services.diag_Generate",
            side_effect=AssertionError("export_WritePackage should stream diagnoses without diag_Generate"),
        ):
            written = export.export_WritePackage(job.data["job_id"], str(package_dir))

        self.assertTrue(written.ok, written.message)
        alerts = json.loads((package_dir / "result" / "alerts.json").read_text(encoding="utf-8"))
        diagnoses = json.loads((package_dir / "result" / "diagnoses.json").read_text(encoding="utf-8"))
        anchors = json.loads((package_dir / "context" / "anchors.json").read_text(encoding="utf-8"))
        manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(len(diagnoses), len(alerts))
        self.assertGreaterEqual(len(anchors), 1)
        manifest_by_path = {entry["path"]: entry for entry in manifest["entries"]}
        self.assertEqual(manifest_by_path["result/alerts.json"]["count"], len(alerts))
        self.assertEqual(manifest_by_path["result/diagnoses.json"]["count"], len(diagnoses))
        self.assertEqual(manifest_by_path["context/anchors.json"]["count"], len(anchors))

    def test_export_package_writes_rebuild_bundle_without_calling_rebuild_to_dict(self) -> None:
        trace_path = write_scenario(self.root / "rebuild-stream.trace", name="multi_core", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": loaded.data})
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "rebuild-stream-package"

        with patch(
            "desktop.services.RebuildBundle.to_dict",
            side_effect=AssertionError("export_WritePackage should write rebuild bundle without RebuildBundle.to_dict"),
        ):
            written = export.export_WritePackage(job.data["job_id"], str(package_dir))

        self.assertTrue(written.ok, written.message)
        rebuild = json.loads((package_dir / "rebuild" / "rebuild_bundle.json").read_text(encoding="utf-8"))
        manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertIn("event_stream", rebuild)
        self.assertIn("task_states", rebuild)
        self.assertIn("exec_slices", rebuild)
        manifest_by_path = {entry["path"]: entry for entry in manifest["entries"]}
        self.assertEqual(
            manifest_by_path["rebuild/rebuild_bundle.json"]["count"],
            written.data["event_count"],
        )

    def test_export_package_reports_write_progress_substages(self) -> None:
        trace_path = write_scenario(self.root / "export-progress.trace", name="basic", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)

        progress_updates: list[dict[str, object]] = []
        export = ExportService(
            controller.repository,
            controller.context_store,
            controller.jobs,
            on_progress=progress_updates.append,
        )
        job = export.export_Full({"dataset_id": loaded.data})
        self.assertTrue(job.ok, job.message)
        written = export.export_WritePackage(job.data["job_id"], str(self.root / "export-progress-package"))
        self.assertTrue(written.ok, written.message)

        self.assertTrue(progress_updates)
        substages = {(item.get("substage"), item.get("status")) for item in progress_updates}
        self.assertIn(("prepare/build_export_bundle", "started"), substages)
        self.assertIn(("result/diagnoses.json", "completed"), substages)
        self.assertIn(("manifest.json", "completed"), substages)

    def test_source_backed_thin_export_reuses_persisted_alignment_for_full_and_clipped(self) -> None:
        trace_path = write_scenario(self.root / "thin-source-backed-reuse.trace", name="multi_core", repeat=4)
        prescan = prs_Prescan(trace_path, include_task_state_preview=False)
        self.assertTrue(prescan.ok, prescan.message)
        time_window = tuple(float(item) for item in prescan.data["time_window"])

        thin_loaded = load_dataset_with_timings(
            trace_path,
            materialize_event_stream=False,
            index_build_mode="minimal",
        )
        self.assertTrue(thin_loaded.ok, thin_loaded.message)
        thin_bundle = thin_loaded.data["artifact"].bundle
        self.assertEqual(thin_bundle.event_stream, [])
        self.assertIsNotNone(thin_bundle.alignment)

        controller = WorkspaceController()
        registered = controller._register_artifact(thin_loaded.data["artifact"])
        self.assertTrue(registered.ok, registered.message)
        context = controller.viz_SetContext(
            {
                "time_window": time_window,
                "filter": {},
                "selection": {"task_id": 1},
                "zoom_level": 1.5,
                "focused_view": "timeline",
                "dataset_role": "single",
            }
        )
        self.assertTrue(context.ok, context.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        full_job = export.export_Full({"dataset_id": registered.data})
        self.assertTrue(full_job.ok, full_job.message)
        clipped_job = export.export_Clipped({"dataset_id": registered.data})
        self.assertTrue(clipped_job.ok, clipped_job.message)

        with patch.object(ExportService, "_source_alignment_offsets", side_effect=AssertionError("unexpected rescan")):
            full_written = export.export_WritePackage(full_job.data["job_id"], str(self.root / "thin-reuse-full"))
            self.assertTrue(full_written.ok, full_written.message)
            self.assertEqual(full_written.data["write_mode"], "source_backed_streaming")

            clipped_written = export.export_WritePackage(clipped_job.data["job_id"], str(self.root / "thin-reuse-clipped"))
            self.assertTrue(clipped_written.ok, clipped_written.message)
            self.assertEqual(clipped_written.data["write_mode"], "source_backed_streaming")

    def test_source_backed_export_write_does_not_reload_trace_for_analysis(self) -> None:
        trace_path = write_scenario(self.root / "thin-source-backed-no-reload.trace", name="multi_core", repeat=4)
        prescan = prs_Prescan(trace_path, include_task_state_preview=False)
        self.assertTrue(prescan.ok, prescan.message)
        time_window = tuple(float(item) for item in prescan.data["time_window"])

        thin_loaded = load_dataset_with_timings(
            trace_path,
            materialize_event_stream=False,
            index_build_mode="minimal",
        )
        self.assertTrue(thin_loaded.ok, thin_loaded.message)
        self.assertEqual(thin_loaded.data["artifact"].bundle.event_stream, [])

        controller = WorkspaceController()
        registered = controller._register_artifact(thin_loaded.data["artifact"])
        self.assertTrue(registered.ok, registered.message)
        context = controller.viz_SetContext(
            {
                "time_window": time_window,
                "filter": {"task_id": 1},
                "selection": {"task_id": 1},
                "zoom_level": 1.5,
                "focused_view": "timeline",
                "dataset_role": "single",
            }
        )
        self.assertTrue(context.ok, context.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        full_job = export.export_Full({"dataset_id": registered.data})
        self.assertTrue(full_job.ok, full_job.message)
        clipped_job = export.export_Clipped({"dataset_id": registered.data})
        self.assertTrue(clipped_job.ok, clipped_job.message)

        with patch.object(ExportService, "_source_alignment_offsets", side_effect=AssertionError("unexpected rescan")):
            with patch("desktop.services.load_dataset", side_effect=AssertionError("unexpected trace reload")):
                full_written = export.export_WritePackage(full_job.data["job_id"], str(self.root / "thin-no-reload-full"))
                self.assertTrue(full_written.ok, full_written.message)
                self.assertEqual(full_written.data["write_mode"], "source_backed_streaming")

                clipped_written = export.export_WritePackage(
                    clipped_job.data["job_id"],
                    str(self.root / "thin-no-reload-clipped"),
                )
                self.assertTrue(clipped_written.ok, clipped_written.message)
                self.assertEqual(clipped_written.data["write_mode"], "source_backed_streaming")

    def test_source_backed_export_write_scans_source_trace_once_per_package(self) -> None:
        import desktop.services as desktop_services

        trace_path = write_scenario(self.root / "thin-source-backed-single-scan.trace", name="multi_core", repeat=4)
        prescan = prs_Prescan(trace_path, include_task_state_preview=False)
        self.assertTrue(prescan.ok, prescan.message)
        time_window = tuple(float(item) for item in prescan.data["time_window"])

        thin_loaded = load_dataset_with_timings(
            trace_path,
            materialize_event_stream=False,
            index_build_mode="minimal",
        )
        self.assertTrue(thin_loaded.ok, thin_loaded.message)
        self.assertEqual(thin_loaded.data["artifact"].bundle.event_stream, [])

        controller = WorkspaceController()
        registered = controller._register_artifact(thin_loaded.data["artifact"])
        self.assertTrue(registered.ok, registered.message)
        context = controller.viz_SetContext(
            {
                "time_window": time_window,
                "filter": {"task_id": 1},
                "selection": {"task_id": 1},
                "zoom_level": 1.5,
                "focused_view": "timeline",
                "dataset_role": "single",
            }
        )
        self.assertTrue(context.ok, context.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        full_job = export.export_Full({"dataset_id": registered.data})
        self.assertTrue(full_job.ok, full_job.message)
        clipped_job = export.export_Clipped({"dataset_id": registered.data})
        self.assertTrue(clipped_job.ok, clipped_job.message)

        with patch("desktop.services._scan_trace_source", wraps=desktop_services._scan_trace_source) as scan_trace_source:
            full_written = export.export_WritePackage(full_job.data["job_id"], str(self.root / "thin-single-scan-full"))
            self.assertTrue(full_written.ok, full_written.message)
            self.assertEqual(full_written.data["write_mode"], "source_backed_streaming")
            self.assertEqual(scan_trace_source.call_count, 1)

            clipped_written = export.export_WritePackage(
                clipped_job.data["job_id"],
                str(self.root / "thin-single-scan-clipped"),
            )
            self.assertTrue(clipped_written.ok, clipped_written.message)
            self.assertEqual(clipped_written.data["write_mode"], "source_backed_streaming")
            self.assertEqual(scan_trace_source.call_count, 2)

    def test_source_backed_export_write_clamps_aligned_timestamp_to_int64(self) -> None:
        trace_path = encode_trace(
            self.root / "thin-source-backed-int64-clamp.trace",
            [
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_READY"),
                    "seq": 1,
                    "timestamp": (1 << 63) - 1,
                    "payload": {"task_id": 1, "prio": 1, "core_hint": 0, "reason": 1},
                }
            ],
            producer_ver="google-clusterdata-v3-instance-events",
        )
        prescan = prs_Prescan(trace_path, include_task_state_preview=False)
        self.assertTrue(prescan.ok, prescan.message)
        time_window = tuple(float(item) for item in prescan.data["time_window"])

        thin_loaded = load_dataset_with_timings(
            trace_path,
            materialize_event_stream=False,
            index_build_mode="minimal",
        )
        self.assertTrue(thin_loaded.ok, thin_loaded.message)

        controller = WorkspaceController()
        registered = controller._register_artifact(thin_loaded.data["artifact"])
        self.assertTrue(registered.ok, registered.message)
        context = controller.viz_SetContext(
            {
                "time_window": time_window,
                "filter": {},
                "selection": {"task_id": 1},
                "zoom_level": 1.0,
                "focused_view": "timeline",
                "dataset_role": "single",
            }
        )
        self.assertTrue(context.ok, context.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": registered.data})
        self.assertTrue(job.ok, job.message)
        written = export.export_WritePackage(job.data["job_id"], str(self.root / "thin-int64-clamp"))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["write_mode"], "source_backed_streaming")

    def test_source_backed_streaming_analysis_matches_materialized_exports_for_dependency_cases(self) -> None:
        trace_path = self.root / "thin-source-backed-analysis-coverage.trace"
        custom_dictionary = self._dictionary_copy()
        self._append_event_definition(
            custom_dictionary,
            0x9001,
            "TASK_READY",
            ["task_id", "prio", "core_hint", "reason", "job_id", "instance_id", "release_ts", "deadline_ts"],
        )
        self._append_event_definition(
            custom_dictionary,
            0x9003,
            "TASK_EXIT",
            ["task_id", "exit_code", "job_id", "instance_id", "finish_ts"],
        )
        encode_trace(
            trace_path,
            [
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_READY"),
                    "seq": 1,
                    "timestamp": 20,
                    "payload": {"task_id": 9, "prio": 1, "core_hint": 0, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 2,
                    "timestamp": 25,
                    "payload": {"core_id": 0, "prev_task_id": 0, "next_task_id": 9, "reason": 1},
                },
                {
                    "core_id": 1,
                    "event_id": 0x9001,
                    "seq": 1,
                    "timestamp": 100,
                    "payload": {
                        "task_id": 1,
                        "prio": 9,
                        "core_hint": 1,
                        "reason": 1,
                        "job_id": 7,
                        "instance_id": 1,
                        "release_ts": 100,
                        "deadline_ts": 140,
                    },
                },
                {
                    "core_id": 1,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 2,
                    "timestamp": 110,
                    "payload": {"core_id": 1, "prev_task_id": 0, "next_task_id": 1, "reason": 1},
                },
                {
                    "core_id": 1,
                    "event_id": event_id_for("SYNC_LOCK"),
                    "seq": 3,
                    "timestamp": 120,
                    "payload": {"task_id": 1, "obj_id": 0x2A, "obj_type": 1, "timeout_ns": 0},
                },
                {
                    "core_id": 1,
                    "event_id": event_id_for("TASK_READY"),
                    "seq": 4,
                    "timestamp": 130,
                    "payload": {"task_id": 2, "prio": 3, "core_hint": 1, "reason": 1},
                },
                {
                    "core_id": 1,
                    "event_id": event_id_for("TASK_BLOCK"),
                    "seq": 5,
                    "timestamp": 140,
                    "payload": {"task_id": 2, "wait_obj_id": 0x2A, "reason": 3, "owner_task_id": 1},
                },
                {
                    "core_id": 1,
                    "event_id": event_id_for("TASK_READY"),
                    "seq": 6,
                    "timestamp": 150,
                    "payload": {"task_id": 3, "prio": 5, "core_hint": 1, "reason": 1},
                },
                {
                    "core_id": 1,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 7,
                    "timestamp": 160,
                    "payload": {"core_id": 1, "prev_task_id": 1, "next_task_id": 3, "reason": 2},
                },
                {
                    "core_id": 1,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 8,
                    "timestamp": 280,
                    "payload": {"core_id": 1, "prev_task_id": 3, "next_task_id": 1, "reason": 2},
                },
                {
                    "core_id": 1,
                    "event_id": event_id_for("SYNC_UNLOCK"),
                    "seq": 9,
                    "timestamp": 290,
                    "payload": {"task_id": 1, "obj_id": 0x2A, "obj_type": 1, "timeout_ns": 0},
                },
                {
                    "core_id": 1,
                    "event_id": event_id_for("TASK_WAKEUP"),
                    "seq": 10,
                    "timestamp": 295,
                    "payload": {"task_id": 2, "wake_src": 1, "obj_id": 0x2A},
                },
                {
                    "core_id": 1,
                    "event_id": 0x9003,
                    "seq": 11,
                    "timestamp": 300,
                    "payload": {"task_id": 1, "exit_code": 0, "job_id": 7, "instance_id": 1, "finish_ts": 300},
                },
                {
                    "core_id": 1,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 12,
                    "timestamp": 305,
                    "payload": {"core_id": 1, "prev_task_id": 1, "next_task_id": 2, "reason": 3},
                },
            ],
        )

        full_loaded = load_dataset(trace_path, dictionary=custom_dictionary)
        self.assertTrue(full_loaded.ok, full_loaded.message)
        thin_loaded = load_dataset_with_timings(
            trace_path,
            dictionary=custom_dictionary,
            materialize_event_stream=False,
            index_build_mode="minimal",
        )
        self.assertTrue(thin_loaded.ok, thin_loaded.message)
        self.assertEqual(thin_loaded.data["artifact"].bundle.event_stream, [])
        boundary_ref_key = full_loaded.data.bundle.event_stream[0].ref_key

        def assert_resource_edge_time_bounds(graph_payload: dict[str, object]) -> None:
            edges = list(graph_payload.get("hold_edges", [])) + list(graph_payload.get("wait_edges", []))
            self.assertTrue(edges)
            for edge in edges:
                self.assertIn("t_begin", edge)
                self.assertIn("t_end", edge)
                self.assertGreaterEqual(float(edge["t_end"]), float(edge["t_begin"]))

        def assert_clipped_event_ref_closure(package_dir: Path) -> None:
            ref_index = json.loads((package_dir / "event" / "ref_index.json").read_text(encoding="utf-8"))
            exported_event_refs = {
                str(item["ref_key"])
                for item in ref_index
                if item.get("ref_key")
            }
            self.assertIn(boundary_ref_key, exported_event_refs)

            rebuild_bundle = json.loads((package_dir / "rebuild" / "rebuild_bundle.json").read_text(encoding="utf-8"))
            alerts_rows = json.loads((package_dir / "result" / "alerts.json").read_text(encoding="utf-8"))
            diagnosis_rows = json.loads((package_dir / "result" / "diagnoses.json").read_text(encoding="utf-8"))
            anchor_rows = json.loads((package_dir / "context" / "anchors.json").read_text(encoding="utf-8"))
            analysis_context = json.loads((package_dir / "context" / "analysis_context.json").read_text(encoding="utf-8"))

            required_event_refs: set[str] = set()
            for row in rebuild_bundle.get("task_states", []):
                if row.get("cause_event"):
                    required_event_refs.add(str(row["cause_event"]))
            for row in rebuild_bundle.get("exec_slices", []):
                if row.get("start_event"):
                    required_event_refs.add(str(row["start_event"]))
                if row.get("end_event"):
                    required_event_refs.add(str(row["end_event"]))
            graph_payload = rebuild_bundle.get("resource_graph") or {}
            assert_resource_edge_time_bounds(graph_payload)
            resource_graph_payload = json.loads((package_dir / "rebuild" / "resource_graph.json").read_text(encoding="utf-8"))
            assert_resource_edge_time_bounds(resource_graph_payload)
            for edge in list(graph_payload.get("hold_edges", [])) + list(graph_payload.get("wait_edges", [])):
                if edge.get("evidence_ref"):
                    required_event_refs.add(str(edge["evidence_ref"]))
            for row in alerts_rows:
                for evidence_ref in row.get("evidence_refs") or []:
                    ref_type = str(evidence_ref.get("ref_type", "")).strip().lower()
                    ref_key = evidence_ref.get("ref_key")
                    if ref_key and ref_type == "event":
                        required_event_refs.add(str(ref_key))
            for row in diagnosis_rows:
                for evidence_ref in row.get("evidence_refs") or []:
                    ref_type = str(evidence_ref.get("ref_type", "")).strip().lower()
                    ref_key = evidence_ref.get("ref_key")
                    if ref_key and ref_type == "event":
                        required_event_refs.add(str(ref_key))
            for row in anchor_rows:
                evidence_anchor = row.get("evidence_anchor") or {}
                ref_type = str(evidence_anchor.get("ref_type", "")).strip().lower()
                ref_key = evidence_anchor.get("ref_key")
                if ref_key and (ref_type == "event" or (ref_type in {"", "index"} and str(ref_key).startswith("evt:"))):
                    required_event_refs.add(str(ref_key))
            context_anchor = analysis_context.get("evidence_anchor") or {}
            context_ref_key = context_anchor.get("ref_key")
            if context_ref_key and str(context_ref_key).startswith("evt:"):
                required_event_refs.add(str(context_ref_key))
            meta = json.loads((package_dir / "meta.json").read_text(encoding="utf-8"))
            padding_rule = meta.get("context_padding_rule") or {}
            self.assertEqual(padding_rule.get("event_policy"), "window_intersection")
            self.assertEqual(padding_rule.get("event_closure_policy"), "window_intersection_plus_required_refs")
            self.assertGreaterEqual(
                int(padding_rule.get("required_event_ref_count", 0)),
                len(required_event_refs),
            )

            missing_event_refs = sorted(required_event_refs - exported_event_refs)
            self.assertEqual(
                missing_event_refs,
                [],
                f"missing clipped event refs: {missing_event_refs[:8]}",
            )

        def export_clipped_package(
            *,
            materialized: bool,
            time_window: tuple[float, float],
            filter_spec: dict[str, object],
            package_name: str,
        ) -> dict[str, object]:
            controller = WorkspaceController()
            artifact = full_loaded.data if materialized else thin_loaded.data["artifact"]
            registered = controller._register_artifact(artifact)
            self.assertTrue(registered.ok, registered.message)
            context = controller.viz_SetContext(
                {
                    "time_window": time_window,
                    "filter": filter_spec,
                    "selection": {"task_id": 1},
                    "zoom_level": 1.25,
                    "focused_view": "timeline",
                    "dataset_role": "single",
                    "evidence_anchor": {"ref_key": boundary_ref_key},
                }
            )
            self.assertTrue(context.ok, context.message)
            export = ExportService(controller.repository, controller.context_store, controller.jobs)
            job = export.export_Clipped({"dataset_id": registered.data})
            self.assertTrue(job.ok, job.message)
            package_dir = self.root / package_name
            written = export.export_WritePackage(job.data["job_id"], str(package_dir))
            self.assertTrue(written.ok, written.message)
            assert_clipped_event_ref_closure(package_dir)
            normalized = export.export_NormalizePackage(str(package_dir))
            self.assertTrue(normalized.ok, normalized.message)
            assert_resource_edge_time_bounds(normalized.data["rebuild_bundle"]["resource_graph"])
            return normalized.data

        coverage_window = (95.0, 305.0)
        full_normalized = export_clipped_package(
            materialized=True,
            time_window=coverage_window,
            filter_spec={},
            package_name="analysis-coverage-materialized",
        )
        thin_normalized = export_clipped_package(
            materialized=False,
            time_window=coverage_window,
            filter_spec={},
            package_name="analysis-coverage-source-backed",
        )
        self.assertEqual(full_normalized, thin_normalized)
        metric_map = {item["metric_id"]: item for item in thin_normalized["metrics"]}
        self.assertGreater(metric_map["context_switch_count"]["summary"]["count"], 0)
        self.assertEqual(metric_map["deadline_miss"]["summary"]["miss_count"], 1)
        alert_types = {item["type"] for item in thin_normalized["alerts"]}
        self.assertIn("deadline_miss", alert_types)
        self.assertIn("priority_inversion", alert_types)
        diagnosis_types = {item["diagnosis_type"] for item in thin_normalized["diagnoses"]}
        self.assertIn("deadline_miss", diagnosis_types)
        self.assertIn("priority_inversion", diagnosis_types)

        filtered_window = (100.0, 305.0)
        filtered_full = export_clipped_package(
            materialized=True,
            time_window=filtered_window,
            filter_spec={"task_id": 1},
            package_name="analysis-filter-materialized",
        )
        filtered_thin = export_clipped_package(
            materialized=False,
            time_window=filtered_window,
            filter_spec={"task_id": 1},
            package_name="analysis-filter-source-backed",
        )
        self.assertEqual(filtered_full["metrics"], filtered_thin["metrics"])
        self.assertEqual(filtered_full["alerts"], filtered_thin["alerts"])
        self.assertEqual(filtered_full["diagnoses"], filtered_thin["diagnoses"])
        self.assertEqual(filtered_full["anchors"], filtered_thin["anchors"])
        diagnosis_anchor_ids = {
            item["owner_id"]
            for item in filtered_thin["anchors"]
            if item["anchor_type"] == "diagnosis"
        }
        expected_diagnosis_anchor_ids = {
            item["diag_id"]
            for item in filtered_thin["diagnoses"]
            if item["evidence_refs"]
        }
        self.assertEqual(diagnosis_anchor_ids, expected_diagnosis_anchor_ids)

    def test_scan_trace_source_feeds_chunk_header_and_payload_separately(self) -> None:
        import desktop.services as desktop_services

        trace_path = encode_trace(
            self.root / "scan-split.trace",
            build_scenario(name="basic"),
            chunk_size=64,
        )
        trace_bytes = trace_path.read_bytes()
        feed_lengths: list[int] = []
        observed_events: list[object] = []
        original_feed = desktop_services.TraceDecodeSession.feed

        def recording_feed(self, payload, source_core_id=None):
            feed_lengths.append(len(payload))
            return original_feed(self, payload, source_core_id=source_core_id)

        with patch("desktop.services.TraceDecodeSession.feed", new=recording_feed):
            scanned = desktop_services._scan_trace_source(
                [trace_path],
                dataset_id="scan-split",
                dictionary=None,
                on_events=lambda events: observed_events.extend(events),
            )

        self.assertTrue(scanned.ok, scanned.message)
        self.assertGreater(len(observed_events), 0)
        expected_feed_lengths = [desktop_services.GLOBAL_HEADER_STRUCT.size]
        offset = desktop_services.GLOBAL_HEADER_STRUCT.size
        while offset < len(trace_bytes):
            chunk_tuple = desktop_services.CHUNK_HEADER_STRUCT.unpack_from(trace_bytes, offset)
            self.assertEqual(int(chunk_tuple[0]), desktop_services.TRACE_CHUNK_MAGIC)
            payload_size = int(chunk_tuple[4])
            expected_feed_lengths.extend(
                [
                    desktop_services.CHUNK_HEADER_STRUCT.size,
                    payload_size,
                ]
            )
            offset += desktop_services.CHUNK_HEADER_STRUCT.size + payload_size
        self.assertEqual(
            feed_lengths,
            expected_feed_lengths,
        )

    def test_export_package_preserves_segment_chain_metadata(self) -> None:
        events = build_scenario(name="multi_core")
        segment_dir = self.root / "segment-chain-source"
        segment_dir.mkdir()
        midpoint = len(events) // 2
        encode_trace(
            segment_dir / "segment_000.trace",
            events[:midpoint],
            run_id="segment-chain-source",
            format_ver=2,
            segment_meta={
                "segment_seq": 1,
                "prev_segment_seq": 0,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0x11111111,
            },
        )
        encode_trace(
            segment_dir / "segment_001.trace",
            events[midpoint:],
            run_id="segment-chain-source",
            format_ver=2,
            segment_meta={
                "segment_seq": 2,
                "prev_segment_seq": 1,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0x11111111,
            },
        )

        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(segment_dir))
        self.assertTrue(loaded.ok, loaded.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": loaded.data})
        package_dir = self.root / "segment-chain-package"
        written = export.export_WritePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        meta = json.loads((package_dir / "meta.json").read_text(encoding="utf-8"))
        manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
        rebuild = json.loads((package_dir / "rebuild" / "rebuild_bundle.json").read_text(encoding="utf-8"))

        self.assertEqual(meta["segment_chain"]["count"], 2)
        self.assertEqual(manifest["segment_chain"]["count"], 2)
        self.assertEqual(meta["segment_chain"], manifest["segment_chain"])
        self.assertEqual(meta["run_id"], "segment-chain-source")
        self.assertIn("header", rebuild)
        self.assertIsNotNone(rebuild["header"])
        self.assertEqual(rebuild["header"]["run_id"], "segment-chain-source")
        self.assertEqual(rebuild["header"]["run_id"], meta["run_id"])
        self.assertEqual([item["segment_seq"] for item in meta["segment_chain"]["segments"]], [1, 2])
        self.assertEqual([item["prev_segment_seq"] for item in meta["segment_chain"]["segments"]], [0, 1])
        self.assertEqual([item["segment_seq"] for item in rebuild["segment_metas"]], [1, 2])

    def test_repro_open_validates_segment_chain_metadata(self) -> None:
        events = build_scenario(name="multi_core")
        segment_dir = self.root / "segment-chain-validate-source"
        segment_dir.mkdir()
        midpoint = len(events) // 2
        encode_trace(
            segment_dir / "segment_000.trace",
            events[:midpoint],
            run_id="segment-chain-validate",
            format_ver=2,
            segment_meta={
                "segment_seq": 1,
                "prev_segment_seq": 0,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0xAAAA1111,
            },
        )
        encode_trace(
            segment_dir / "segment_001.trace",
            events[midpoint:],
            run_id="segment-chain-validate",
            format_ver=2,
            segment_meta={
                "segment_seq": 2,
                "prev_segment_seq": 1,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0xAAAA1111,
            },
        )

        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(segment_dir))
        self.assertTrue(loaded.ok, loaded.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": loaded.data})
        package_dir = self.root / "segment-chain-validate-package"
        written = export.export_WritePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        manifest_path = package_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["segment_chain"]["segments"][1]["prev_segment_seq"] = 99
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertFalse(opened.ok)
        self.assertIn("segment_chain", opened.message)

    def test_package_validation_blocks_tampered_loads(self) -> None:
        trace_path = write_scenario(self.root / "tamper.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": loaded.data})
        package_dir = self.root / "tampered-package"
        written = export.export_WritePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        metrics_path = package_dir / "result" / "metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        metrics.append({"tampered": True})
        metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertFalse(opened.ok)
        self.assertIn("checksum mismatch", opened.message)
        reloaded = controller.viz_LoadDataset(str(package_dir))
        self.assertFalse(reloaded.ok)
        self.assertIn("checksum mismatch", reloaded.message)

        candidate = write_scenario(self.root / "candidate.trace", name="basic", candidate_variant=True)
        compare = CompareService(controller.repository, controller.context_store)
        pair = compare.cmp_LoadPair(str(package_dir), str(candidate))
        self.assertFalse(pair.ok)
        self.assertIn("checksum mismatch", pair.message)

    def test_evidence_export_reports_spec_progress_stages_for_exact_mode_a(self) -> None:
        trace_path = write_scenario(self.root / "evidence-progress-exact.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        progress_updates: list[dict[str, object]] = []
        export = ExportService(controller.repository, controller.context_store, controller.jobs, on_progress=progress_updates.append)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-progress-exact-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "exact")

        substages = self._evidence_progress_substages(progress_updates)
        for stage in [
            "prepare/context_freeze",
            "sidecar/build_or_load",
            "sidecar/validate",
            "seed/resolve",
            "seed/materialize",
            "round/project",
            "round/budget",
            "round/window_plan",
            "round/read",
            "round/merge",
            "finalize/proof",
            "write/control",
            "write/event",
            "write/rebuild",
            "write/result",
            "write/meta",
            "write/manifest",
        ]:
            self.assertIn(stage, substages)
        self._assert_no_legacy_evidence_progress(progress_updates)
        self._assert_round_progress_fields(progress_updates)

    def test_evidence_export_thin_bundle_writes_source_backed_event_artifacts_without_materialized_reload(self) -> None:
        trace_path = write_scenario(self.root / "evidence-thin-source-backed.trace", name="multi_core", repeat=4)
        full_controller = WorkspaceController()
        full_loaded = full_controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(full_loaded.ok, full_loaded.message)
        anchor_ref = full_controller.repository.get(full_loaded.data).artifact.bundle.event_stream[0].ref_key

        thin_loaded = load_dataset_with_timings(
            trace_path,
            materialize_event_stream=False,
            index_build_mode="minimal",
        )
        self.assertTrue(thin_loaded.ok, thin_loaded.message)
        self.assertEqual(thin_loaded.data["artifact"].bundle.event_stream, [])

        controller = WorkspaceController()
        registered = controller._register_artifact(thin_loaded.data["artifact"])
        self.assertTrue(registered.ok, registered.message)
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": registered.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 64, "S_bytes": 32768, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 64},
            }
        )
        self.assertTrue(job.ok, job.message)

        package_dir = self.root / "evidence-thin-source-backed-package"
        with patch("desktop.evidence_export._bundle_ref_index_rows", side_effect=AssertionError("materialized ref-index fallback")):
            written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        rebuild = json.loads((package_dir / "rebuild" / "rebuild_bundle.json").read_text(encoding="utf-8"))
        ref_index = json.loads((package_dir / "event" / "ref_index.json").read_text(encoding="utf-8"))

        self.assertEqual(rebuild["event_stream"], [])
        self.assertGreater((package_dir / "event" / "events.trace").stat().st_size, 0)
        self.assertGreater(len(ref_index), 0)
        self.assertGreater(written.data["event_count"], 0)

    def test_evidence_export_mode_a_safe_path_ingests_selected_bundle_instead_of_full_bundle(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-a-selected-analysis.trace", name="multi_core", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        full_event_count = len(bundle.event_stream)
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 1, "C_events": 8, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            }
        )
        self.assertTrue(job.ok, job.message)

        observed_ingest_sizes: list[int] = []
        original_ingest = metric_Ingest

        def _capture_ingest(session, ingest_bundle):
            observed_ingest_sizes.append(len(list(ingest_bundle.event_stream)))
            return original_ingest(session, ingest_bundle)

        package_dir = self.root / "evidence-mode-a-selected-analysis-package"
        with patch("desktop.evidence_export.metric_Ingest", side_effect=_capture_ingest):
            written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        self.assertEqual(len(observed_ingest_sizes), 1)
        self.assertEqual(observed_ingest_sizes[0], written.data["event_count"])
        self.assertGreater(observed_ingest_sizes[0], 0)
        self.assertLess(observed_ingest_sizes[0], full_event_count)

    def test_evidence_export_rebuild_bundle_resource_graph_uses_selected_scope(self) -> None:
        trace_path = write_scenario(self.root / "evidence-resource-graph-subset.trace", name="multi_core", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        source_edge_count = len(bundle.resource_graph.hold_edges) + len(bundle.resource_graph.wait_edges)
        self.assertGreater(source_edge_count, 1)
        anchor_ref = next(event.ref_key for event in bundle.event_stream if event.event_name == "TASK_BLOCK")
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 1, "C_events": 8, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            }
        )
        self.assertTrue(job.ok, job.message)

        package_dir = self.root / "evidence-resource-graph-subset-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        rebuild = json.loads((package_dir / "rebuild" / "rebuild_bundle.json").read_text(encoding="utf-8"))
        ref_index = json.loads((package_dir / "event" / "ref_index.json").read_text(encoding="utf-8"))
        exported_refs = {str(item["ref_key"]) for item in ref_index if item.get("ref_key")}

        graph_payload = rebuild.get("resource_graph") or {}
        exported_edges = list(graph_payload.get("hold_edges", [])) + list(graph_payload.get("wait_edges", []))
        self.assertLess(len(exported_edges), source_edge_count)
        self.assertTrue(exported_edges)
        for edge in exported_edges:
            self.assertIn("t_begin", edge)
            self.assertIn("t_end", edge)
            self.assertGreaterEqual(float(edge["t_end"]), float(edge["t_begin"]))
            evidence_ref = edge.get("evidence_ref")
            if evidence_ref:
                self.assertIn(str(evidence_ref), exported_refs)
        self.assertEqual(
            graph_payload.get("nodes", []),
            sorted(graph_payload.get("nodes", []), key=lambda item: item["node_id"]),
        )
        self.assertEqual(
            graph_payload.get("hotspot_stats", []),
            sorted(graph_payload.get("hotspot_stats", []), key=lambda item: (-item["count"], item["node_id"])),
        )
        normalized = export.export_NormalizePackage(str(package_dir))
        self.assertTrue(normalized.ok, normalized.message)
        normalized_graph = normalized.data["rebuild_bundle"]["resource_graph"]
        normalized_edges = list(normalized_graph.get("hold_edges", [])) + list(normalized_graph.get("wait_edges", []))
        self.assertEqual(normalized_edges, exported_edges)

    def test_resource_graph_selected_scope_empty_selection_returns_empty_graph(self) -> None:
        from desktop.evidence_export import _subset_resource_graph_for_selected_scope

        graph = ResourceGraph(
            nodes=[{"node_id": "task:1", "count": 3}],
            hold_edges=[{"task_id": 1, "obj_id": 42, "evidence_ref": "evt:outside:0:1"}],
            wait_edges=[{"task_id": 2, "obj_id": 42, "owner_task_id": 1, "evidence_ref": "evt:outside:0:2"}],
            hotspot_stats=[{"node_id": "task:1", "count": 3}],
        )
        subset = _subset_resource_graph_for_selected_scope(graph, [])

        self.assertEqual(subset, ResourceGraph())

    def test_evidence_export_mode_a_risky_rule_family_keeps_full_bundle_analysis(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-a-risky-analysis.trace", name="multi_core", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        full_event_count = len(bundle.event_stream)
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref", "ref_alert"],
                "budget_vector": {"D_max": 1, "C_events": 8, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            }
        )
        self.assertTrue(job.ok, job.message)

        observed_ingest_sizes: list[int] = []
        original_ingest = metric_Ingest

        def _capture_ingest(session, ingest_bundle):
            observed_ingest_sizes.append(len(list(ingest_bundle.event_stream)))
            return original_ingest(session, ingest_bundle)

        package_dir = self.root / "evidence-mode-a-risky-analysis-package"
        with patch("desktop.evidence_export.metric_Ingest", side_effect=_capture_ingest):
            written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        self.assertEqual(len(observed_ingest_sizes), 1)
        self.assertEqual(observed_ingest_sizes[0], full_event_count)
        self.assertGreater(full_event_count, written.data["event_count"])

    def test_evidence_export_reports_budget_freeze_without_read_progress(self) -> None:
        trace_path = write_scenario(self.root / "evidence-progress-bounded.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        progress_updates: list[dict[str, object]] = []
        export = ExportService(controller.repository, controller.context_store, controller.jobs, on_progress=progress_updates.append)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 0, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-progress-bounded-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "bounded")

        substages = self._evidence_progress_substages(progress_updates)
        self.assertIn("round/project", substages)
        self.assertIn("round/budget", substages)
        self.assertNotIn("round/window_plan", substages)
        self.assertNotIn("round/read", substages)
        self.assertNotIn("round/merge", substages)
        self.assertIn("finalize/proof", substages)
        self._assert_no_legacy_evidence_progress(progress_updates)
        self._assert_round_progress_fields(progress_updates)

    def test_evidence_export_writes_peak_rss_mb_into_proof_digest_without_changing_hash_boundary(self) -> None:
        trace_path = write_scenario(self.root / "evidence-peak-rss.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        with patch("desktop.evidence_export._current_process_peak_rss_mb", return_value=123.456):
            job = export.export_Evidence(
                {
                    "dataset_id": loaded.data,
                    "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                    "rule_family": ["ref_ref"],
                    "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                    "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
                }
            )
            self.assertTrue(job.ok, job.message)
            package_dir = self.root / "evidence-peak-rss-package"
            written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        self.assertEqual(float(proof_digest["peak_rss_mb"]), 123.456)
        self.assertEqual(proof_digest["proof_hash"], evd_RecomputeProofHash(proof_digest))

    def test_evidence_export_reports_finalize_proof_for_degraded_mode_b_validation_failure(self) -> None:
        trace_path = write_scenario(self.root / "evidence-progress-degraded.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        sidecar_root = self.root / "mode-b-progress-sidecar-mismatch"
        sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:test:mode-b:progress-mismatch",
            rule_family=("ref_ref",),
        )
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_payload["trace_checksum"] = "deadbeef"
        manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        progress_updates: list[dict[str, object]] = []
        export = ExportService(controller.repository, controller.context_store, controller.jobs, on_progress=progress_updates.append)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "embodiment_mode": "mode_b",
                "sidecar_source": str(sidecar_path),
                "sidecar_manifest_source": str(manifest_path),
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-progress-degraded-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "degraded")

        stage_pairs = {(str(item.get("substage")), str(item.get("status"))) for item in progress_updates if item.get("category") == "evidence_export"}
        self.assertIn(("sidecar/build_or_load", "completed"), stage_pairs)
        self.assertIn(("sidecar/validate", "failed"), stage_pairs)
        self.assertIn(("finalize/proof", "started"), stage_pairs)
        self.assertIn(("finalize/proof", "completed"), stage_pairs)
        self._assert_no_legacy_evidence_progress(progress_updates)

    def test_evidence_export_writes_exact_package_and_repro_reads_control_plane(self) -> None:
        trace_path = write_scenario(self.root / "evidence-exact.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        context = controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )
        self.assertTrue(context.ok, context.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-exact-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "exact")

        manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
        meta = json.loads((package_dir / "meta.json").read_text(encoding="utf-8"))
        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        result_validity = json.loads((package_dir / "result" / "result_validity.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["package_version"], "rttrace-package-2")
        self.assertEqual(meta["export_family"], "evidence")
        self.assertEqual(meta["export_mode"], "evidence")
        self.assertEqual(proof_digest["closure_mode"], "exact")
        self.assertTrue((package_dir / "result" / "alerts.json").exists())
        result_paths = {row["path"] for row in result_validity["results"]}
        self.assertEqual(result_paths, {"result/alerts.json", "result/diagnoses.json"})
        result_kinds = {row["object_kind"] for row in result_validity["results"]}
        self.assertEqual(result_kinds, {"alert", "diagnosis"})
        for row in result_validity["results"]:
            self.assertIn("object_id", row)
            self.assertEqual(row["validity_scope"], "source_snapshot")
            self.assertEqual(row["derivation_mode"], "reused_context")
        alert_row = next(row for row in result_validity["results"] if row["object_kind"] == "alert")
        diag_row = next(row for row in result_validity["results"] if row["object_kind"] == "diagnosis")

        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertTrue(opened.ok, opened.message)
        self.assertEqual(opened.data["proof_digest"]["closure_mode"], "exact")
        self.assertIn("dependency_sidecar", opened.data)
        self.assertIn("consumer_mode_explain", opened.data)
        self.assertEqual(opened.data["consumer_mode_explain"]["final_mode"], opened.data["consumer_mode"])

        context_before_queries = controller.context_store.get().persisted_dict()
        alert_query = repro.repro_QueryValidityByAlertId(str(alert_row["object_id"]))
        self.assertTrue(alert_query.ok, alert_query.message)
        self.assertEqual(alert_query.data["status"], "HIT")
        diag_query = repro.repro_QueryValidityByDiagId(str(diag_row["object_id"]))
        self.assertTrue(diag_query.ok, diag_query.message)
        self.assertEqual(diag_query.data["status"], "HIT")
        object_query = repro.repro_QueryValidityByObject("diagnosis", str(diag_row["object_id"]))
        self.assertTrue(object_query.ok, object_query.message)
        self.assertEqual(object_query.data["status"], "HIT")
        context_after_queries = controller.context_store.get().persisted_dict()
        self.assertEqual(context_before_queries, context_after_queries)

        reloaded = controller.viz_LoadDataset(str(package_dir))
        self.assertTrue(reloaded.ok, reloaded.message)

    def test_evidence_export_mode_a_reuses_stable_snapshot_and_proof_hash(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-a-stable.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        payload = {
            "dataset_id": loaded.data,
            "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
            "rule_family": ["ref_ref"],
            "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
            "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
        }

        job_a = export.export_Evidence(payload)
        self.assertTrue(job_a.ok, job_a.message)
        package_a = self.root / "evidence-mode-a-stable-a"
        written_a = export.export_WriteEvidencePackage(job_a.data["job_id"], str(package_a))
        self.assertTrue(written_a.ok, written_a.message)

        job_b = export.export_Evidence(payload)
        self.assertTrue(job_b.ok, job_b.message)
        package_b = self.root / "evidence-mode-a-stable-b"
        written_b = export.export_WriteEvidencePackage(job_b.data["job_id"], str(package_b))
        self.assertTrue(written_b.ok, written_b.message)

        proof_a = json.loads((package_a / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        proof_b = json.loads((package_b / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        self.assertEqual(written_a.data["snapshot_id"], written_b.data["snapshot_id"])
        self.assertEqual(proof_a["proof_hash"], proof_b["proof_hash"])

    def test_evidence_export_mode_a_proof_hash_changes_with_rule_family_and_budget(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-a-proof-hash-sensitive.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )
        export = ExportService(controller.repository, controller.context_store, controller.jobs)

        base_job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(base_job.ok, base_job.message)
        base_package = self.root / "evidence-mode-a-proof-hash-base"
        written_base = export.export_WriteEvidencePackage(base_job.data["job_id"], str(base_package))
        self.assertTrue(written_base.ok, written_base.message)
        base_proof = json.loads((base_package / "control" / "proof_digest.json").read_text(encoding="utf-8"))

        rule_job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref", "ref_anchor"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(rule_job.ok, rule_job.message)
        rule_package = self.root / "evidence-mode-a-proof-hash-rule"
        written_rule = export.export_WriteEvidencePackage(rule_job.data["job_id"], str(rule_package))
        self.assertTrue(written_rule.ok, written_rule.message)
        rule_proof = json.loads((rule_package / "control" / "proof_digest.json").read_text(encoding="utf-8"))

        budget_job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 12, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(budget_job.ok, budget_job.message)
        budget_package = self.root / "evidence-mode-a-proof-hash-budget"
        written_budget = export.export_WriteEvidencePackage(budget_job.data["job_id"], str(budget_package))
        self.assertTrue(written_budget.ok, written_budget.message)
        budget_proof = json.loads((budget_package / "control" / "proof_digest.json").read_text(encoding="utf-8"))

        self.assertNotEqual(base_proof["proof_hash"], rule_proof["proof_hash"])
        self.assertNotEqual(base_proof["proof_hash"], budget_proof["proof_hash"])

    def test_evidence_export_mode_a_writes_workset_sidecar_subset(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-a-workset.trace", name="basic", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 64, "S_bytes": 16384, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-mode-a-workset-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        package_rows = [
            json.loads(line)
            for line in (package_dir / "control" / "dependency_sidecar.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        ref_index_rows = [
            {
                "ref_key": event.ref_key,
                "timestamp_aligned": float(event.timestamp_aligned),
                "core_id": int(event.core_id),
                "seq": int(event.seq),
            }
            for event in sorted(bundle.event_stream, key=lambda item: (item.timestamp_aligned, item.core_id, item.seq))
        ]
        full_rows = build_dependency_sidecar(
            bundle,
            snapshot_id=written.data["snapshot_id"],
            rule_families=("ref_ref",),
            ref_index_rows=ref_index_rows,
        )
        self.assertLessEqual(len(package_rows), len(full_rows))
        self.assertTrue({row["edge_hash"] for row in package_rows}.issubset({row.edge_hash for row in full_rows}))

    def test_evidence_export_mode_a_degrades_when_built_sidecar_fails_validation(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-a-invalid-sidecar.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )
        ref_index_rows = [
            {
                "ref_key": event.ref_key,
                "timestamp_aligned": float(event.timestamp_aligned),
                "core_id": int(event.core_id),
                "seq": int(event.seq),
            }
            for event in sorted(bundle.event_stream, key=lambda item: (item.timestamp_aligned, item.core_id, item.seq))
        ]
        bad_row = replace(
            build_dependency_sidecar(
                bundle,
                snapshot_id="snapshot:test:invalid",
                rule_families=("ref_ref",),
                ref_index_rows=ref_index_rows,
            )[0],
            priority=101,
        )

        progress_updates: list[dict[str, object]] = []
        export = ExportService(controller.repository, controller.context_store, controller.jobs, on_progress=progress_updates.append)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-mode-a-invalid-sidecar-package"
        with patch("desktop.evidence_export.build_dependency_sidecar", return_value=[bad_row]):
            written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "degraded")

        stage_pairs = {(str(item.get("substage")), str(item.get("status"))) for item in progress_updates if item.get("category") == "evidence_export"}
        self.assertIn(("sidecar/validate", "failed"), stage_pairs)
        blocker_artifact = json.loads((package_dir / "control" / "blocker_artifact.json").read_text(encoding="utf-8"))
        self.assertEqual(blocker_artifact["exception_code"], "SIDECAR_MISMATCH")

    def test_evidence_export_mode_a_degrades_when_built_sidecar_is_empty(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-a-empty-sidecar.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )

        progress_updates: list[dict[str, object]] = []
        export = ExportService(controller.repository, controller.context_store, controller.jobs, on_progress=progress_updates.append)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-mode-a-empty-sidecar-package"
        with patch("desktop.evidence_export.build_dependency_sidecar", return_value=[]):
            written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "degraded")

        stage_pairs = {(str(item.get("substage")), str(item.get("status"))) for item in progress_updates if item.get("category") == "evidence_export"}
        self.assertIn(("sidecar/validate", "failed"), stage_pairs)
        blocker_artifact = json.loads((package_dir / "control" / "blocker_artifact.json").read_text(encoding="utf-8"))
        self.assertEqual(blocker_artifact["exception_code"], "SIDECAR_MISMATCH")

    def test_evidence_export_mode_b_uses_external_sidecar_and_writes_exact_package(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-b.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        context = controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )
        self.assertTrue(context.ok, context.message)

        sidecar_root = self.root / "mode-b-sidecar"
        sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:test:mode-b:exact",
            rule_family=("ref_ref",),
        )

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "embodiment_mode": "mode_b",
                "sidecar_source": str(sidecar_path),
                "sidecar_manifest_source": str(manifest_path),
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-mode-b-package"
        with patch("desktop.evidence_export.metric_Init", side_effect=AssertionError("mode_b should not call metric_Init")), patch(
            "desktop.evidence_export.metric_Ingest",
            side_effect=AssertionError("mode_b should not call metric_Ingest"),
        ), patch("desktop.evidence_export.alert_Evaluate", side_effect=AssertionError("mode_b should not call alert_Evaluate")), patch(
            "desktop.evidence_export.diag_Generate",
            side_effect=AssertionError("mode_b should not call diag_Generate"),
        ), patch(
            "desktop.evidence_export.build_dependency_sidecar",
            side_effect=AssertionError("mode_b should not call build_dependency_sidecar"),
        ), patch(
            "desktop.evidence_export.load_dependency_sidecar",
            side_effect=AssertionError("mode_b should not call load_dependency_sidecar"),
        ), patch(
            "desktop.evidence_export.select_candidate_edges_from_sidecar",
            side_effect=AssertionError("mode_b should not stream-scan external sidecar"),
        ):
            written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "exact")
        self.assertEqual(written.data["snapshot_id"], "snapshot:test:mode-b:exact")

        meta = json.loads((package_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["embodiment_mode"], "mode_b")
        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        self.assertEqual(proof_digest["snapshot_id"], "snapshot:test:mode-b:exact")
        self.assertEqual(proof_digest["sidecar_selector_mode"], "indexed_sqlite")
        self.assertGreater(proof_digest["sidecar_selector_calls"], 0)
        self.assertGreaterEqual(proof_digest["sidecar_bytes_scanned"], proof_digest["sidecar_bytes"])
        self.assertEqual(
            json.loads((package_dir / "result" / "alerts.json").read_text(encoding="utf-8")),
            json.loads((sidecar_root / "result" / "alerts.json").read_text(encoding="utf-8")),
        )
        self.assertEqual(
            json.loads((package_dir / "result" / "diagnoses.json").read_text(encoding="utf-8")),
            json.loads((sidecar_root / "result" / "diagnoses.json").read_text(encoding="utf-8")),
        )
        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertTrue(opened.ok, opened.message)
        self.assertEqual(opened.data["proof_digest"]["closure_mode"], "exact")

    def test_evidence_export_mode_b_reuses_ticket_fast_path_without_stream_validation_after_ticket_exists(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-b-ticket-fast-path.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )

        sidecar_root = self.root / "mode-b-ticket-fast-path-sidecar"
        sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:test:mode-b:ticket-fast-path",
            rule_family=("ref_ref",),
        )

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        payload = {
            "dataset_id": loaded.data,
            "embodiment_mode": "mode_b",
            "sidecar_source": str(sidecar_path),
            "sidecar_manifest_source": str(manifest_path),
            "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
            "rule_family": ["ref_ref"],
            "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
            "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
        }

        first_job = export.export_Evidence(payload)
        self.assertTrue(first_job.ok, first_job.message)
        first_package = self.root / "evidence-mode-b-ticket-fast-path-first"
        first_written = export.export_WriteEvidencePackage(first_job.data["job_id"], str(first_package))
        self.assertTrue(first_written.ok, first_written.message)
        self.assertTrue((sidecar_root / "control" / "dependency_sidecar.jsonl.sqlite3.ticket.json").exists())

        second_job = export.export_Evidence(payload)
        self.assertTrue(second_job.ok, second_job.message)
        second_package = self.root / "evidence-mode-b-ticket-fast-path-second"
        with patch("desktop.evidence_export.validate_dependency_sidecar_stream", side_effect=AssertionError("ticket fast path must not stream validate")), patch(
            "desktop.evidence_export.select_candidate_edges_from_sidecar",
            side_effect=AssertionError("ticket fast path must not stream scan"),
        ):
            second_written = export.export_WriteEvidencePackage(second_job.data["job_id"], str(second_package))
        self.assertTrue(second_written.ok, second_written.message)
        proof_digest = json.loads((second_package / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        self.assertEqual(proof_digest["sidecar_selector_mode"], "indexed_sqlite")
        self.assertEqual(proof_digest["sidecar_bytes_scanned"], 0)
        self.assertNotIn("recommend_index_prebuild", proof_digest)
        self.assertNotIn("predicted_runtime_seconds", proof_digest)

    def test_evidence_export_reuses_completed_background_prebuild_index(self) -> None:
        trace_path = write_scenario(self.root / "evidence-background-prebuild-reuse.trace", name="basic", repeat=4)
        controller = WorkspaceController()
        with patch(
            "desktop.services.RuntimeOptimizationAdvisor.plan_load",
            return_value=RuntimeLoadPlan(
                plan_version="runtime-load-plan-v1",
                load_mode="full",
                index_build_mode="full",
                materialize_event_stream=True,
                try_parser_artifact_reuse=False,
                try_sidecar_index_reuse=False,
                background_sidecar_prebuild=True,
                reasons=["unit test"],
            ),
        ):
            loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        finished = self._wait_for_job_result(controller, record.background_sidecar_prebuild["job_id"])
        self.assertTrue(finished.ok, finished.message)
        self.assertIn("prebuild_stage_breakdown", finished.data)

        progress_updates: list[dict[str, object]] = []
        export = ExportService(controller.repository, controller.context_store, controller.jobs, on_progress=progress_updates.append)
        job = export.export_Evidence({"dataset_id": loaded.data})
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-background-prebuild-reuse-package"
        with patch(
            "desktop.evidence_export.build_dependency_sidecar",
            side_effect=AssertionError("background prebuild should bypass local sidecar build"),
        ), patch(
            "desktop.evidence_export.validate_dependency_sidecar_stream",
            side_effect=AssertionError("ticket fast path must not stream validate"),
        ), patch(
            "desktop.evidence_export.select_candidate_edges_from_sidecar",
            side_effect=AssertionError("ticket fast path must not stream scan"),
        ):
            written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertTrue(
            any(bool(item.get("background_prebuild_reused")) for item in progress_updates if item.get("substage") == "sidecar/preflight")
        )

    def test_evidence_export_background_prebuild_mismatch_falls_back_to_local_build(self) -> None:
        trace_path = write_scenario(self.root / "evidence-background-prebuild-mismatch.trace", name="basic", repeat=4)
        controller = WorkspaceController()
        with patch(
            "desktop.services.RuntimeOptimizationAdvisor.plan_load",
            return_value=RuntimeLoadPlan(
                plan_version="runtime-load-plan-v1",
                load_mode="full",
                index_build_mode="full",
                materialize_event_stream=True,
                try_parser_artifact_reuse=False,
                try_sidecar_index_reuse=False,
                background_sidecar_prebuild=True,
                reasons=["unit test"],
            ),
        ):
            loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        self.assertTrue(self._wait_for_job_result(controller, record.background_sidecar_prebuild["job_id"]).ok)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence({"dataset_id": loaded.data, "rule_family": ["ref_ref"]})
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-background-prebuild-mismatch-package"
        with patch("desktop.evidence_export.build_dependency_sidecar", wraps=build_dependency_sidecar) as mocked_build:
            written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertTrue(mocked_build.called)

    def test_evidence_export_writes_advisor_trace_and_report_without_touching_proof_digest(self) -> None:
        trace_path = write_scenario(self.root / "evidence-advisor.trace", name="basic", repeat=3)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )

        sidecar_root = self.root / "evidence-advisor-sidecar"
        sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:test:advisor",
            rule_family=("ref_ref",),
        )
        telemetry_history_path = self.root / "evidence-advisor-history.jsonl"
        TelemetryHistoryStore(telemetry_history_path).append_record(
            TelemetryReportAgent().build_record(
                run_id="seed-advisor-history",
                dataset_id=loaded.data,
                embodiment_mode="mode_b",
                input_bytes=int(trace_path.stat().st_size),
                sidecar_bytes=int(sidecar_path.stat().st_size),
                sidecar_row_count=1,
                sidecar_bytes_scanned=0,
                sidecar_validate_seconds=0.1,
                index_build_open_seconds=0.2,
                package_write_seconds=0.3,
                runtime_seconds=12.5,
                peak_rss_mb=256.0,
            ),
            job_id="seed-advisor-history",
        )

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "embodiment_mode": "mode_b",
                "sidecar_source": str(sidecar_path),
                "sidecar_manifest_source": str(manifest_path),
                "telemetry_history_path": str(telemetry_history_path),
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
                "advisor_enabled": True,
                "advisor_mode": "heuristic",
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-advisor-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
        meta = json.loads((package_dir / "meta.json").read_text(encoding="utf-8"))
        sidecar_manifest = json.loads((package_dir / "control" / "sidecar_manifest.json").read_text(encoding="utf-8"))
        manifest_paths = {entry["path"] for entry in manifest["entries"]}
        manifest_entries = {entry["path"]: entry for entry in manifest["entries"]}
        self.assertIn("control/pre_execution_advisor_trace.json", manifest_paths)
        self.assertIn("control/pre_execution_runtime_advisor_agent_contract.json", manifest_paths)
        self.assertIn("control/advisor_trace.json", manifest_paths)
        self.assertIn("control/advisor_report.json", manifest_paths)
        self.assertIn("control/runtime_advisor_agent_contract.json", manifest_paths)
        self.assertEqual(
            manifest_entries["control/pre_execution_advisor_trace.json"]["schema_ref"],
            "advisor_trace_schema",
        )
        self.assertEqual(
            manifest_entries["control/pre_execution_runtime_advisor_agent_contract.json"]["schema_ref"],
            "agent_job_contract_schema",
        )
        self.assertEqual(manifest_entries["control/advisor_trace.json"]["schema_ref"], "advisor_trace_schema")
        self.assertEqual(
            manifest_entries["control/runtime_advisor_agent_contract.json"]["schema_ref"],
            "agent_job_contract_schema",
        )
        self.assertEqual(manifest_entries["control/advisor_report.json"]["schema_ref"], "advisor_report_schema")
        expected_runtime_schema_refs = {
            "sidecar_index_ticket.schema.json": "sidecar_index_ticket_schema",
            "telemetry_record.schema.json": "telemetry_record_schema",
            "telemetry_record_update.schema.json": "telemetry_record_update_schema",
            "advisor_decision.schema.json": "advisor_decision_schema",
            "advisor_trace.schema.json": "advisor_trace_schema",
            "advisor_report.schema.json": "advisor_report_schema",
            "validation_gate_result.schema.json": "validation_gate_result_schema",
            "agent_job_contract.schema.json": "agent_job_contract_schema",
            "export_write_metric.schema.json": "export_write_metric_schema",
            "write_failure_blocker.schema.json": "write_failure_blocker_schema",
            "parser_process_artifact.schema.json": "parser_process_artifact_schema",
            "formal_schedule_plan.schema.json": "formal_schedule_plan_schema",
            "benchmark_scenario.schema.json": "benchmark_scenario_schema",
            "benchmark_report.schema.json": "benchmark_report_schema",
        }
        for schema_name, schema_key in expected_runtime_schema_refs.items():
            rel_path = f"reference/schema/{schema_name}"
            with self.subTest(schema=schema_name):
                self.assertIn(rel_path, manifest_paths)
                self.assertIn(schema_key, meta["schema_ref"])
                self.assertIn(schema_key, manifest["schema_ref"])
                self.assertEqual(meta["schema_ref"][schema_key]["path"], rel_path)
                self.assertEqual(manifest["schema_ref"][schema_key]["path"], rel_path)
                self.assertEqual(meta["schema_ref"][schema_key]["checksum"], checksum_file(package_dir / rel_path))
                self.assertEqual(manifest["schema_ref"][schema_key]["checksum"], meta["schema_ref"][schema_key]["checksum"])
                self.assertEqual(manifest_entries[rel_path]["category"], "reference")
        self.assertTrue(
            set(expected_runtime_schema_refs.values()).isdisjoint(set(sidecar_manifest["schema_checksums"])),
            "runtime schemas must not be added to sidecar manifest schema_checksums",
        )
        advisor_trace = json.loads((package_dir / "control" / "advisor_trace.json").read_text(encoding="utf-8"))
        pre_execution_advisor_trace = json.loads(
            (package_dir / "control" / "pre_execution_advisor_trace.json").read_text(encoding="utf-8")
        )
        advisor_report = json.loads((package_dir / "control" / "advisor_report.json").read_text(encoding="utf-8"))
        pre_execution_advisor_contract = json.loads(
            (package_dir / "control" / "pre_execution_runtime_advisor_agent_contract.json").read_text(encoding="utf-8")
        )
        advisor_contract = json.loads(
            (package_dir / "control" / "runtime_advisor_agent_contract.json").read_text(encoding="utf-8")
        )
        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["control_refs"]["pre_execution_advisor_trace"], "control/pre_execution_advisor_trace.json")
        self.assertEqual(
            meta["control_refs"]["pre_execution_runtime_advisor_agent_contract"],
            "control/pre_execution_runtime_advisor_agent_contract.json",
        )
        self.assertEqual(meta["control_refs"]["runtime_advisor_agent_contract"], "control/runtime_advisor_agent_contract.json")
        self.assertEqual(pre_execution_advisor_contract["agent_name"], "RuntimeOptimizationAdvisor")
        self.assertEqual(pre_execution_advisor_contract["agent_state"], "AGENT-completed")
        self.assertEqual(pre_execution_advisor_contract["status"], "AGENT-completed")
        self.assertEqual(pre_execution_advisor_contract["job_id"], pre_execution_advisor_trace["request_id"])
        self.assertEqual(
            pre_execution_advisor_trace["agent_contract_ref"]["path"],
            "control/pre_execution_runtime_advisor_agent_contract.json",
        )
        self.assertEqual(
            pre_execution_advisor_trace["agent_contract_ref"]["job_id"],
            pre_execution_advisor_contract["job_id"],
        )
        self.assertEqual(pre_execution_advisor_trace["feature_snapshot"]["advisor_phase"], "pre_execution")
        self.assertIn("pre_execution_features", pre_execution_advisor_trace["feature_snapshot"])
        self.assertIn("evidence_context", pre_execution_advisor_trace["feature_snapshot"])
        self.assertTrue(pre_execution_advisor_trace["feature_snapshot"]["evidence_context"]["retrieved_case_refs"])
        self.assertTrue(pre_execution_advisor_trace["gate_result"]["accepted"])
        self.assertEqual(advisor_contract["agent_name"], "RuntimeOptimizationAdvisor")
        self.assertEqual(advisor_contract["agent_state"], "AGENT-completed")
        self.assertEqual(advisor_contract["status"], "AGENT-completed")
        self.assertEqual(advisor_contract["job_id"], advisor_trace["request_id"])
        self.assertEqual(advisor_report["request_id"], advisor_contract["job_id"])
        self.assertEqual(advisor_trace["agent_contract_ref"]["path"], "control/runtime_advisor_agent_contract.json")
        self.assertEqual(advisor_report["agent_contract_ref"], advisor_trace["agent_contract_ref"])
        self.assertEqual(advisor_report["pre_execution_agent_contract_ref"], pre_execution_advisor_trace["agent_contract_ref"])
        self.assertEqual(
            advisor_report["pre_execution_advisor_trace_ref"]["path"],
            "control/pre_execution_advisor_trace.json",
        )
        self.assertEqual(
            advisor_report["pre_execution_advisor_trace_ref"]["job_id"],
            pre_execution_advisor_trace["request_id"],
        )
        self.assertEqual(advisor_trace["agent_contract_ref"]["job_id"], advisor_contract["job_id"])
        self.assertEqual(advisor_trace["decision"]["advisor_mode"], "heuristic")
        self.assertIn("evidence_context", advisor_trace["feature_snapshot"])
        self.assertTrue(advisor_trace["feature_snapshot"]["evidence_context"]["retrieved_case_refs"])
        self.assertTrue(advisor_report["decision"]["recommend_index_reuse_attempt"])
        self.assertIn("retrieval", advisor_report["advisor_metadata"])
        self.assertTrue(advisor_report["advisor_metadata"]["retrieval"]["retrieved_case_refs"])
        self.assertEqual(advisor_report["pre_execution_decision"]["advisor_mode"], "heuristic")
        self.assertEqual(advisor_report["pre_execution_decision"]["predicted_runtime_seconds"], 12.5)
        self.assertTrue(advisor_report["pre_execution_gate_result"]["accepted"])
        self.assertTrue(advisor_report["effective_execution_plan"])
        self.assertTrue(advisor_report["write_metrics"])
        report_metric_paths = {metric["path"] for metric in advisor_report["write_metrics"]}
        self.assertIn(str(package_dir / "event" / "events.trace"), report_metric_paths)
        self.assertIn(str(package_dir / "event" / "ref_index.json"), report_metric_paths)
        self.assertIn(str(package_dir / "reference" / "dictionary.json"), report_metric_paths)
        self.assertIn(str(package_dir / "control" / "pre_execution_advisor_trace.json"), report_metric_paths)
        self.assertIn(
            str(package_dir / "control" / "pre_execution_runtime_advisor_agent_contract.json"),
            report_metric_paths,
        )
        self.assertIn(str(package_dir / "control" / "advisor_trace.json"), report_metric_paths)
        self.assertIn(str(package_dir / "control" / "runtime_advisor_agent_contract.json"), report_metric_paths)
        self.assertNotIn("recommend_index_reuse_attempt", proof_digest)
        self.assertNotIn("predicted_peak_rss_mb", proof_digest)
        self.assertNotIn("advisor_decision", proof_digest)
        self.assertNotIn("agent_contract", proof_digest)
        self.assertNotIn("agent_contract_ref", proof_digest)
        self.assertNotIn("advisor_trace", proof_digest)
        self.assertNotIn("advisor_report", proof_digest)
        self.assertNotIn("runtime_advisor_agent_contract", proof_digest)
        self.assertNotIn("telemetry_history", proof_digest)
        self.assertNotIn("telemetry_history_path", proof_digest)
        self.assertNotIn("telemetry_record", proof_digest)
        self.assertNotIn("pre_execution_decision", proof_digest)
        self.assertNotIn("pre_execution_agent_contract_ref", proof_digest)
        self.assertNotIn("pre_execution_advisor_trace_ref", proof_digest)
        self.assertNotIn("pre_execution_advisor_trace", proof_digest)
        self.assertNotIn("pre_execution_runtime_advisor_agent_contract", proof_digest)
        self.assertNotIn("predicted_runtime_seconds", proof_digest)
        self.assertNotIn("retrieved_case_refs", proof_digest)
        self.assertNotIn("case_similarity_features", proof_digest)
        self.assertNotIn("evidence_context", proof_digest)
        self.assertEqual(proof_digest["proof_hash"], evd_RecomputeProofHash(proof_digest))
        self.assertEqual(len(TelemetryHistoryStore(telemetry_history_path).read_advisor_history(export_family="evidence")), 2)

    def test_evidence_export_openai_structured_mock_writes_trace_and_gate_reject_without_touching_proof_digest(self) -> None:
        class MockOpenAIAdvisorClient:
            def request_advisor_decision(
                self,
                *,
                feature_payload: dict[str, object],
                decision_schema: dict[str, object],
                feature_snapshot_hash: str,
                decision_version: str,
            ) -> OpenAIAdvisorClientResult:
                phase = str(feature_payload.get("advisor_phase") or "")
                payload = {
                    "action_set_version": decision_version,
                    "generated_at": "2026-07-06T00:00:00+00:00",
                    "proposed_actions": (
                        [
                            {
                                "action_id": "action:mock:baseline_full_load",
                                "action_kind": "baseline_full_load",
                                "required_artifacts": [],
                                "expected_benefit": {
                                    "runtime_seconds_delta": None,
                                    "peak_rss_mb_delta": None,
                                    "notes": [f"mock openai advisor phase: {phase or 'unknown'}"],
                                },
                                "risk_level": "medium",
                                "proof_scope_impact": "none",
                                "fallback_action": None,
                            }
                        ]
                        if phase == "pre_execution"
                        else []
                    ),
                    "abstained": False,
                    "abstain_reason": None,
                }
                return OpenAIAdvisorClientResult(
                    decision_payload=payload,
                    response_id=f"resp-{phase or 'unknown'}",
                    model="gpt-test",
                    usage={"input_tokens": 3, "output_tokens": 4},
                    latency_seconds=0.123,
                    request_payload={},
                )

        trace_path = write_scenario(self.root / "evidence-openai-advisor.trace", name="basic", repeat=3)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )

        sidecar_root = self.root / "evidence-openai-advisor-sidecar"
        sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:test:openai-advisor",
            rule_family=("ref_ref",),
        )
        telemetry_history_path = self.root / "evidence-openai-advisor-history.jsonl"

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "embodiment_mode": "mode_b",
                "sidecar_source": str(sidecar_path),
                "sidecar_manifest_source": str(manifest_path),
                "telemetry_history_path": str(telemetry_history_path),
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
                "advisor_enabled": True,
                "advisor_mode": "openai_structured",
                "advisor_config": {
                    "advisor_mode": "openai_structured",
                    "openai_client": MockOpenAIAdvisorClient(),
                },
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-openai-advisor-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest_paths = {entry["path"] for entry in manifest["entries"]}
        self.assertIn("control/pre_execution_advisor_trace.json", manifest_paths)
        self.assertIn("control/advisor_trace.json", manifest_paths)
        self.assertIn("control/advisor_report.json", manifest_paths)

        pre_execution_advisor_trace = json.loads(
            (package_dir / "control" / "pre_execution_advisor_trace.json").read_text(encoding="utf-8")
        )
        advisor_trace = json.loads((package_dir / "control" / "advisor_trace.json").read_text(encoding="utf-8"))
        advisor_report = json.loads((package_dir / "control" / "advisor_report.json").read_text(encoding="utf-8"))
        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))

        self.assertEqual(pre_execution_advisor_trace["decision"]["advisor_mode"], "openai_structured")
        self.assertIn("evidence_context", pre_execution_advisor_trace["feature_snapshot"])
        self.assertEqual(
            [item["action_kind"] for item in pre_execution_advisor_trace["decision"]["proposed_actions"]],
            ["baseline_full_load"],
        )
        self.assertTrue(pre_execution_advisor_trace["gate_result"]["accepted"])
        self.assertIsNone(pre_execution_advisor_trace["gate_result"]["rejected_reason"])
        self.assertEqual(advisor_report["pre_execution_gate_result"], pre_execution_advisor_trace["gate_result"])
        self.assertEqual(advisor_trace["decision"]["advisor_mode"], "openai_structured")
        self.assertEqual(advisor_report["decision"]["advisor_mode"], "openai_structured")
        self.assertEqual(advisor_report["advisor_metadata"]["openai_latency_seconds"], 0.123)
        self.assertEqual(advisor_report["advisor_metadata"]["openai_tokens"], 7)
        self.assertIsNone(advisor_report["advisor_metadata"]["fallback_reason"])
        self.assertIn("retrieval", advisor_report["advisor_metadata"])
        self.assertEqual(advisor_report["pre_execution_advisor_metadata"]["openai_tokens"], 7)

        self.assertNotIn("advisor_report", proof_digest)
        self.assertNotIn("advisor_trace", proof_digest)
        self.assertNotIn("openai_latency_seconds", proof_digest)
        self.assertNotIn("openai_tokens", proof_digest)
        self.assertNotIn("pre_execution_advisor_trace", proof_digest)
        self.assertNotIn("retrieved_case_refs", proof_digest)
        self.assertNotIn("case_similarity_features", proof_digest)
        self.assertEqual(proof_digest["proof_hash"], evd_RecomputeProofHash(proof_digest))

    def test_evidence_export_returns_package_write_failure_with_blocker_artifact(self) -> None:
        trace_path = write_scenario(self.root / "evidence-write-failure.trace", name="basic", repeat=3)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-write-failure-package"
        with patch("desktop.evidence_export.encode_trace", side_effect=RuntimeError("trace boom")):
            written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))

        self.assertFalse(written.ok)
        self.assertEqual(written.code, "ERR-PACKAGE_WRITE_FAILED")
        self.assertIn("package_result", written.data)
        self.assertFalse(written.data["package_result"]["ok"])
        blocker_path = package_dir / "control" / "write_failure_blocker.json"
        self.assertTrue(blocker_path.exists())
        blocker = json.loads(blocker_path.read_text(encoding="utf-8"))
        self.assertTrue(blocker["failed_path"].endswith("event/events.trace"))

    def test_evidence_export_returns_manifest_checksum_failure_with_blocker_artifact(self) -> None:
        trace_path = write_scenario(self.root / "evidence-manifest-checksum-failure.trace", name="basic", repeat=3)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-manifest-checksum-failure-package"

        def checksum_side_effect(path: str | Path) -> str:
            target = Path(path)
            if target == package_dir / "event" / "ref_index.json":
                raise OSError(5, "manifest checksum boom", str(target))
            return checksum_file(target)

        with patch("desktop.package_writer.checksum_file", side_effect=checksum_side_effect):
            written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))

        self.assertFalse(written.ok)
        self.assertEqual(written.code, "ERR-PACKAGE_WRITE_FAILED")
        self.assertFalse(written.data["package_result"]["ok"])
        blockers = written.data["package_result"]["write_failure_blockers"]
        self.assertEqual(len(blockers), 1)
        self.assertTrue(blockers[0]["failed_path"].endswith("event/ref_index.json"))
        blocker_path = package_dir / "control" / "write_failure_blocker.json"
        self.assertTrue(blocker_path.exists())
        blocker = json.loads(blocker_path.read_text(encoding="utf-8"))
        self.assertIn("manifest checksum boom", blocker["error_message"])

    def test_evidence_export_mode_b_writes_workset_sidecar_subset_of_external_sidecar(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-b-workset.trace", name="basic", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )

        sidecar_root = self.root / "mode-b-workset-sidecar"
        sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:test:mode-b:workset",
            rule_family=("ref_ref",),
        )

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "embodiment_mode": "mode_b",
                "sidecar_source": str(sidecar_path),
                "sidecar_manifest_source": str(manifest_path),
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 64, "S_bytes": 16384, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-mode-b-workset-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        self.assertEqual(proof_digest["sidecar_selector_mode"], "indexed_sqlite")

        package_rows = [
            json.loads(line)
            for line in (package_dir / "control" / "dependency_sidecar.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        source_rows = [
            json.loads(line)
            for line in sidecar_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertLessEqual(len(package_rows), len(source_rows))
        self.assertTrue({row["edge_hash"] for row in package_rows}.issubset({row["edge_hash"] for row in source_rows}))

    def test_evidence_export_mode_b_large_sidecar_gate_never_falls_back_to_stream_scan(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-b-large-gate.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )

        sidecar_root = self.root / "mode-b-large-gate-sidecar"
        sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:test:mode-b:large-gate",
            rule_family=("ref_ref",),
        )

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "embodiment_mode": "mode_b",
                "sidecar_source": str(sidecar_path),
                "sidecar_manifest_source": str(manifest_path),
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": False, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-mode-b-large-gate-package"
        with patch("desktop.evidence_export.sidecar_stream_scan_fallback_allowed", return_value=False), patch(
            "desktop.evidence_export.build_or_open_sidecar_index",
            return_value=err_result("INVALID_ARG", "synthetic index failure"),
        ), patch(
            "desktop.evidence_export.select_candidate_edges_from_sidecar",
            side_effect=AssertionError("large sidecar gate must not fall back to stream scan"),
        ):
            written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertFalse(written.ok)
        self.assertEqual(written.code, "INVALID_ARG")
        self.assertIn("requires sqlite index", written.message)

    def test_evidence_export_mode_b_degrades_when_external_sidecar_mismatches_source(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-b-mismatch.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext(
            {
                "evidence_anchor": {"ref_key": anchor_ref},
                "selection": {"seed_ref": anchor_ref},
            }
        )

        sidecar_root = self.root / "mode-b-sidecar-mismatch"
        sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:test:mode-b:mismatch",
            rule_family=("ref_ref",),
        )
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_payload["trace_checksum"] = "deadbeef"
        manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "embodiment_mode": "mode_b",
                "sidecar_source": str(sidecar_path),
                "sidecar_manifest_source": str(manifest_path),
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-mode-b-mismatch-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "degraded")

        blocker_artifact = json.loads((package_dir / "control" / "blocker_artifact.json").read_text(encoding="utf-8"))
        self.assertEqual(blocker_artifact["exception_code"], "SIDECAR_MISMATCH")
        self.assertEqual((package_dir / "event" / "events.trace").read_bytes(), b"")
        self.assertEqual((package_dir / "control" / "dependency_sidecar.jsonl").read_text(encoding="utf-8").strip(), "")

    def test_evidence_export_mode_b_non_result_seeds_do_not_require_frozen_results(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-b-missing-results-non-required.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        anchor_time = float(bundle.event_stream[0].timestamp_aligned)
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        sidecar_root = self.root / "mode-b-sidecar-missing-results-non-required"
        sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:test:mode-b:missing-results-non-required",
            rule_family=("ref_ref",),
        )
        (sidecar_root / "result" / "alerts.json").unlink()
        (sidecar_root / "result" / "diagnoses.json").unlink()

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        seeds = [
            ("analysis_context", {}),
            ("manual_refs", {"refs": [anchor_ref]}),
            ("time_window", {"time_window": [anchor_time, anchor_time]}),
            ("anchor", {"anchor_ids": ["context:current"]}),
        ]
        for source_kind, source_payload in seeds:
            with self.subTest(source_kind=source_kind):
                job = export.export_Evidence(
                    {
                        "dataset_id": loaded.data,
                        "embodiment_mode": "mode_b",
                        "sidecar_source": str(sidecar_path),
                        "sidecar_manifest_source": str(manifest_path),
                        "seed_spec": {"source_kind": source_kind, "source_payload": source_payload},
                        "rule_family": ["ref_ref"],
                        "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                        "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
                    }
                )
                self.assertTrue(job.ok, job.message)
                package_dir = self.root / f"evidence-mode-b-missing-results-{source_kind}"
                written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
                self.assertTrue(written.ok, written.message)
                if written.data["closure_mode"] == "degraded":
                    blocker_artifact = json.loads((package_dir / "control" / "blocker_artifact.json").read_text(encoding="utf-8"))
                    self.assertNotEqual(blocker_artifact.get("exception_code"), "SIDECAR_MISMATCH")
                    self.assertNotIn("frozen result whitelist", str(blocker_artifact.get("exception_message") or ""))

    def test_evidence_export_mode_b_result_seeds_fail_closed_when_frozen_results_missing(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-b-missing-results-required.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        sidecar_root = self.root / "mode-b-sidecar-missing-results-required"
        sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:test:mode-b:missing-results-required",
            rule_family=("ref_ref",),
        )
        (sidecar_root / "result" / "alerts.json").unlink()
        (sidecar_root / "result" / "diagnoses.json").unlink()

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        for source_kind in ("alert", "diagnosis"):
            with self.subTest(source_kind=source_kind):
                job = export.export_Evidence(
                    {
                        "dataset_id": loaded.data,
                        "embodiment_mode": "mode_b",
                        "sidecar_source": str(sidecar_path),
                        "sidecar_manifest_source": str(manifest_path),
                        "seed_spec": {"source_kind": source_kind, "source_payload": {}},
                        "rule_family": ["ref_ref"],
                        "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                        "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32},
                    }
                )
                self.assertTrue(job.ok, job.message)
                package_dir = self.root / f"evidence-mode-b-missing-results-{source_kind}-degraded"
                written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
                self.assertTrue(written.ok, written.message)
                self.assertEqual(written.data["closure_mode"], "degraded")
                blocker_artifact = json.loads((package_dir / "control" / "blocker_artifact.json").read_text(encoding="utf-8"))
                self.assertEqual(blocker_artifact["exception_code"], "SIDECAR_MISMATCH")
                self.assertIn("frozen result whitelist", blocker_artifact["exception_message"])

    def test_evidence_export_mode_b_result_seed_missing_frozen_results_fails_when_degrade_disabled(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-b-missing-results-fail.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        sidecar_root = self.root / "mode-b-sidecar-missing-results-fail"
        sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:test:mode-b:missing-results-fail",
            rule_family=("ref_ref",),
        )
        (sidecar_root / "result" / "alerts.json").unlink()
        (sidecar_root / "result" / "diagnoses.json").unlink()

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "embodiment_mode": "mode_b",
                "sidecar_source": str(sidecar_path),
                "sidecar_manifest_source": str(manifest_path),
                "seed_spec": {"source_kind": "diagnosis", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": False, "frontier_ref_limit": 32},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-mode-b-missing-results-fail-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertFalse(written.ok)
        self.assertEqual(written.code, "SIDECAR_MISMATCH")
        self.assertIn("frozen result whitelist", written.message)

    def test_evidence_export_mode_b_success_matrix_covers_all_seed_kinds(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-b-success-matrix.trace", name="basic", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        anchor_time = float(bundle.event_stream[0].timestamp_aligned)
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        cases = [
            ("analysis_context", {}, ("ref_ref",)),
            ("manual_refs", {"refs": [anchor_ref]}, ("ref_ref",)),
            ("time_window", {"time_window": [anchor_time, anchor_time]}, ("ref_ref",)),
            ("anchor", {"anchor_ids": ["context:current"]}, ("ref_anchor", "ref_ref")),
            ("alert", {"alert_ids": ["alert:frozen:1"]}, ("ref_alert", "ref_ref")),
            ("diagnosis", {"diag_ids": ["diag:frozen:1"]}, ("ref_diagnosis", "ref_ref")),
        ]
        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        repro = ReproService(controller.repository, controller.context_store)

        for source_kind, source_payload, rule_family in cases:
            with self.subTest(source_kind=source_kind, rule_family=rule_family):
                sidecar_root = self.root / f"mode-b-success-matrix-sidecar-{source_kind}"
                sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
                    sidecar_root,
                    trace_path,
                    bundle,
                    snapshot_id=f"snapshot:test:mode-b:success:{source_kind}",
                    rule_family=rule_family,
                )
                job = export.export_Evidence(
                    {
                        "dataset_id": loaded.data,
                        "embodiment_mode": "mode_b",
                        "sidecar_source": str(sidecar_path),
                        "sidecar_manifest_source": str(manifest_path),
                        "seed_spec": {"source_kind": source_kind, "source_payload": source_payload},
                        "rule_family": list(rule_family),
                        "budget_vector": {"D_max": 16, "C_events": 128, "S_bytes": 65536, "rho_max": 8.0},
                        "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 64},
                    }
                )
                self.assertTrue(job.ok, job.message)
                package_dir = self.root / f"evidence-mode-b-success-matrix-{source_kind}"
                written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
                self.assertTrue(written.ok, written.message)
                self.assertIn(written.data["closure_mode"], {"exact", "bounded"})

                proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
                self.assertEqual(set(proof_digest["rule_family"]), set(rule_family))
                self.assertEqual(proof_digest["sidecar_selector_mode"], "indexed_sqlite")
                self.assertGreater(proof_digest["sidecar_selector_calls"], 0)
                package_rows = [
                    json.loads(line)
                    for line in (package_dir / "control" / "dependency_sidecar.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                self.assertTrue(package_rows)
                opened = repro.repro_OpenPackage(str(package_dir))
                self.assertTrue(opened.ok, opened.message)
                self.assertTrue(opened.data["proof_verification"]["ok"])

                if source_kind in {"alert", "diagnosis"}:
                    self.assertEqual(
                        json.loads((package_dir / "result" / "alerts.json").read_text(encoding="utf-8")),
                        json.loads((sidecar_root / "result" / "alerts.json").read_text(encoding="utf-8")),
                    )
                    self.assertEqual(
                        json.loads((package_dir / "result" / "diagnoses.json").read_text(encoding="utf-8")),
                        json.loads((sidecar_root / "result" / "diagnoses.json").read_text(encoding="utf-8")),
                    )

    def test_evidence_export_mode_b_object_edge_rule_family_expands_successfully(self) -> None:
        trace_path = write_scenario(self.root / "evidence-mode-b-object-edge.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        sidecar_root = self.root / "mode-b-object-edge-sidecar"
        sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id="snapshot:test:mode-b:object-edge",
            rule_family=("ref_object", "ref_ref"),
        )

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "embodiment_mode": "mode_b",
                "sidecar_source": str(sidecar_path),
                "sidecar_manifest_source": str(manifest_path),
                "seed_spec": {"source_kind": "manual_refs", "source_payload": {"refs": [anchor_ref]}},
                "rule_family": ["ref_object", "ref_ref"],
                "budget_vector": {"D_max": 64, "C_events": 1024, "S_bytes": 1048576, "rho_max": 100.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 256},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-mode-b-object-edge-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertIn(written.data["closure_mode"], {"exact", "bounded"})

        package_rows = [
            json.loads(line)
            for line in (package_dir / "control" / "dependency_sidecar.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(any(row.get("src_kind") == "object" or row.get("dst_kind") == "object" for row in package_rows))
        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(proof_digest["rule_family"]), {"ref_object", "ref_ref"})
        opened = ReproService(controller.repository, controller.context_store).repro_OpenPackage(str(package_dir))
        self.assertTrue(opened.ok, opened.message)
        self.assertTrue(opened.data["proof_verification"]["ok"])

    def test_evidence_export_halts_as_bounded_when_budget_projects_over_depth(self) -> None:
        trace_path = write_scenario(self.root / "evidence-bounded.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 0, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-bounded-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "bounded")

        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        frontier_snapshot = json.loads((package_dir / "control" / "frontier_snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(proof_digest["closure_mode"], "bounded")
        self.assertEqual(proof_digest["frontier_halt_reason"], "DEPTH_LIMIT")
        self.assertEqual(frontier_snapshot["frontier_refs_path"], "control/frontier_refs.jsonl")
        self.assertTrue((package_dir / "control" / "frontier_refs.jsonl").exists())
        sidecar_rows = [
            json.loads(line)
            for line in (package_dir / "control" / "dependency_sidecar.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        frontier_rows = [
            json.loads(line)
            for line in (package_dir / "control" / "frontier_refs.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(frontier_rows)
        self.assertTrue({row["ref_key"] for row in frontier_rows}.issubset({row["dst_ref"] for row in sidecar_rows}))

    def test_evidence_export_progress_contract_for_exact_path(self) -> None:
        trace_path = write_scenario(self.root / "evidence-progress-exact.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        progress_updates: list[dict[str, object]] = []
        export = ExportService(
            controller.repository,
            controller.context_store,
            controller.jobs,
            on_progress=progress_updates.append,
        )
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 64, "S_bytes": 16384, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-progress-exact-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "exact")

        evidence_updates = [item for item in progress_updates if item.get("category") == "evidence_export"]
        self.assertTrue(evidence_updates)
        observed_substages = {str(item.get("substage")) for item in evidence_updates}
        self.assertNotIn("analysis", observed_substages)
        self.assertNotIn("closure", observed_substages)
        required_substages = {
            "prepare/context_freeze",
            "sidecar/build_or_load",
            "sidecar/validate",
            "seed/resolve",
            "seed/materialize",
            "round/project",
            "round/budget",
            "round/window_plan",
            "round/read",
            "round/merge",
            "finalize/proof",
            "write/control",
            "write/event",
            "write/rebuild",
            "write/result",
            "write/meta",
            "write/manifest",
        }
        self.assertTrue(required_substages.issubset(observed_substages))

        started_order: dict[str, int] = {}
        for index, item in enumerate(evidence_updates):
            if item.get("status") != "started":
                continue
            substage = str(item.get("substage"))
            if substage not in started_order:
                started_order[substage] = index
        expected_order = [
            "prepare/context_freeze",
            "sidecar/build_or_load",
            "sidecar/validate",
            "seed/resolve",
            "seed/materialize",
            "round/project",
            "round/budget",
            "round/window_plan",
            "round/read",
            "round/merge",
            "finalize/proof",
        ]
        for substage in expected_order:
            self.assertIn(substage, started_order)
        self.assertEqual([started_order[item] for item in expected_order], sorted(started_order[item] for item in expected_order))

        round_updates = [item for item in evidence_updates if str(item.get("substage", "")).startswith("round/")]
        self.assertTrue(round_updates)
        for row in round_updates:
            self.assertIn("round_id", row)
            self.assertIn("frontier_count", row)
            self.assertIn("emitted_events", row)
            self.assertIn("emitted_bytes", row)

    def test_evidence_export_progress_bounded_stops_at_round_budget(self) -> None:
        trace_path = write_scenario(self.root / "evidence-progress-bounded.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        progress_updates: list[dict[str, object]] = []
        export = ExportService(
            controller.repository,
            controller.context_store,
            controller.jobs,
            on_progress=progress_updates.append,
        )
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 0, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-progress-bounded-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "bounded")

        evidence_updates = [item for item in progress_updates if item.get("category") == "evidence_export"]
        round_substages = [str(item.get("substage")) for item in evidence_updates if str(item.get("substage", "")).startswith("round/")]
        self.assertIn("round/project", round_substages)
        self.assertIn("round/budget", round_substages)
        self.assertNotIn("round/window_plan", round_substages)
        self.assertNotIn("round/read", round_substages)
        self.assertNotIn("round/merge", round_substages)
        self.assertTrue(
            any(item.get("substage") == "round/budget" and item.get("status") == "rejected" for item in evidence_updates)
        )
        substage_statuses = {(item.get("substage"), item.get("status")) for item in evidence_updates}
        self.assertIn(("finalize/proof", "started"), substage_statuses)
        self.assertIn(("finalize/proof", "completed"), substage_statuses)

    def test_evidence_export_progress_degraded_path_still_emits_finalize_proof(self) -> None:
        trace_path = write_scenario(self.root / "evidence-progress-degraded.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)

        progress_updates: list[dict[str, object]] = []
        export = ExportService(
            controller.repository,
            controller.context_store,
            controller.jobs,
            on_progress=progress_updates.append,
        )
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "manual_refs", "source_payload": {"refs": []}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 8, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-progress-degraded-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "degraded")

        evidence_updates = [item for item in progress_updates if item.get("category") == "evidence_export"]
        substage_statuses = {(item.get("substage"), item.get("status")) for item in evidence_updates}
        self.assertIn(("finalize/proof", "started"), substage_statuses)
        self.assertIn(("finalize/proof", "completed"), substage_statuses)

    def test_evidence_export_can_degrade_when_seed_resolution_fails(self) -> None:
        trace_path = write_scenario(self.root / "evidence-degraded.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "manual_refs", "source_payload": {"refs": []}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 8, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-degraded-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "degraded")

        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        blocker_artifact = json.loads((package_dir / "control" / "blocker_artifact.json").read_text(encoding="utf-8"))
        self.assertEqual(proof_digest["closure_mode"], "degraded")
        self.assertEqual(blocker_artifact["exception_code"], "SEED_EMPTY")
        self.assertEqual((package_dir / "event" / "events.trace").read_bytes(), b"")

    def test_evidence_export_degraded_read_failure_backtraces_read_telemetry(self) -> None:
        trace_path = write_scenario(self.root / "evidence-degraded-read.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 8, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-degraded-read-package"
        with patch("parser.evidence_closure.read_window_plan", return_value=err_result("TRACE_IO_GUARD", "simulated read failure")):
            written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "degraded")

        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        blocker_artifact = json.loads((package_dir / "control" / "blocker_artifact.json").read_text(encoding="utf-8"))
        window_summary = dict(blocker_artifact["window_plan_summary"])
        emitted_metrics = dict(blocker_artifact["emitted_metrics"])

        self.assertEqual(proof_digest["scan_count"], 0)
        self.assertEqual(proof_digest["seek_count"], 0)
        self.assertEqual(proof_digest["window_span_total"], 0)
        self.assertEqual(blocker_artifact["exception_code"], "TRACE_IO_GUARD")
        self.assertGreater(window_summary["target_ref_count"], 0)
        self.assertEqual(window_summary["matched_ref_count"], 0)
        self.assertEqual(window_summary["missed_ref_count"], window_summary["target_ref_count"])
        self.assertEqual(window_summary["window_hit_rate"], 0.0)
        self.assertGreaterEqual(window_summary["failed_read_count"], 1)
        self.assertEqual(emitted_metrics["selected_ref_count_from_window_read"], 0)
        self.assertEqual(
            emitted_metrics["selected_ref_count_from_seed_materialization"],
            proof_digest["events_emitted"],
        )

    def test_cli_export_evidence_mode_a_emits_summary_payload(self) -> None:
        trace_path = write_scenario(self.root / "cli-evidence-mode-a.trace", name="basic")
        loaded = load_dataset(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        seed_ref = loaded.data.bundle.event_stream[0].ref_key
        package_dir = self.root / "cli-evidence-mode-a-package"
        captured = io.StringIO()
        with redirect_stdout(captured):
            exit_code = cli_main(
                [
                    "export-evidence",
                    "--input",
                    str(trace_path),
                    "--output-dir",
                    str(package_dir),
                    "--seed-spec-json",
                    json.dumps({"source_kind": "manual_refs", "source_payload": {"refs": [seed_ref]}}),
                    "--rule-family",
                    "ref_ref",
                    "--budget-vector-json",
                    json.dumps({"D_max": 16, "C_events": 64, "S_bytes": 16384, "rho_max": 4.0}),
                    "--closure-policy-json",
                    json.dumps({"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32}),
                ]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(captured.getvalue())
        self.assertIn("job_id", payload)
        self.assertEqual(payload["package_path"], str(package_dir))
        self.assertIn(payload["closure_mode"], {"exact", "bounded", "degraded"})
        self.assertIn("proof_digest", payload)
        self.assertTrue(payload["proof_digest"].get("proof_hash"))

    def test_cli_export_evidence_deepseek_provider_without_advisor_enabled_writes_no_advisor_artifacts(self) -> None:
        trace_path = write_scenario(self.root / "cli-evidence-provider-disabled.trace", name="basic")
        loaded = load_dataset(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        seed_ref = loaded.data.bundle.event_stream[0].ref_key
        package_dir = self.root / "cli-evidence-provider-disabled-package"
        captured = io.StringIO()
        with redirect_stdout(captured):
            exit_code = cli_main(
                [
                    "export-evidence",
                    "--input",
                    str(trace_path),
                    "--output-dir",
                    str(package_dir),
                    "--seed-spec-json",
                    json.dumps({"source_kind": "manual_refs", "source_payload": {"refs": [seed_ref]}}),
                    "--rule-family",
                    "ref_ref",
                    "--llm-advisor-provider",
                    "deepseek",
                ]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(captured.getvalue())
        self.assertEqual(payload["package_path"], str(package_dir))

        manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest_paths = {entry["path"] for entry in manifest["entries"]}
        for rel_path in (
            "control/advisor_report.json",
            "control/advisor_trace.json",
            "control/runtime_advisor_agent_contract.json",
        ):
            self.assertNotIn(rel_path, manifest_paths)
            self.assertFalse((package_dir / rel_path).exists())

    def test_cli_export_evidence_time_window_json_freezes_export_scope(self) -> None:
        trace_path = write_scenario(self.root / "cli-evidence-window.trace", name="basic", repeat=3)
        loaded = load_dataset(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        seed_event = loaded.data.bundle.event_stream[0]
        seed_ref = seed_event.ref_key
        time_window = [float(seed_event.timestamp_aligned), float(seed_event.timestamp_aligned)]
        package_dir = self.root / "cli-evidence-window-package"
        captured = io.StringIO()
        with redirect_stdout(captured):
            exit_code = cli_main(
                [
                    "export-evidence",
                    "--input",
                    str(trace_path),
                    "--output-dir",
                    str(package_dir),
                    "--time-window-json",
                    json.dumps(time_window),
                    "--seed-spec-json",
                    json.dumps({"source_kind": "manual_refs", "source_payload": {"refs": [seed_ref]}}),
                    "--rule-family",
                    "ref_ref",
                ]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(captured.getvalue())
        self.assertEqual(payload["package_path"], str(package_dir))

        meta = json.loads((package_dir / "meta.json").read_text(encoding="utf-8"))
        analysis_context = json.loads((package_dir / "context" / "analysis_context.json").read_text(encoding="utf-8"))
        compare_scope = json.loads((package_dir / "context" / "compare_scope.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["time_window"], time_window)
        self.assertEqual(meta["analysis_context"]["time_window"], time_window)
        self.assertEqual(analysis_context["time_window"], time_window)
        self.assertEqual(compare_scope["aligned_time_window"], time_window)

    def test_cli_export_evidence_mode_b_accepts_external_sidecar_inputs(self) -> None:
        trace_path = write_scenario(self.root / "cli-evidence-mode-b.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        seed_ref = bundle.event_stream[0].ref_key
        snapshot_id = "snapshot:test:cli:mode-b"
        sidecar_root = self.root / "cli-mode-b-sidecar"
        sidecar_path, manifest_path = self._write_external_sidecar_artifacts(
            sidecar_root,
            trace_path,
            bundle,
            snapshot_id=snapshot_id,
            rule_family=("ref_ref",),
        )
        package_dir = self.root / "cli-evidence-mode-b-package"
        captured = io.StringIO()
        with redirect_stdout(captured):
            exit_code = cli_main(
                [
                    "export-evidence",
                    "--input",
                    str(trace_path),
                    "--output-dir",
                    str(package_dir),
                    "--embodiment-mode",
                    "mode_b",
                    "--sidecar-source",
                    str(sidecar_path),
                    "--sidecar-manifest-source",
                    str(manifest_path),
                    "--seed-spec-json",
                    json.dumps({"source_kind": "manual_refs", "source_payload": {"refs": [seed_ref]}}),
                    "--rule-family",
                    "ref_ref",
                    "--budget-vector-json",
                    json.dumps({"D_max": 16, "C_events": 64, "S_bytes": 16384, "rho_max": 4.0}),
                    "--closure-policy-json",
                    json.dumps({"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 32}),
                ]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(captured.getvalue())
        self.assertEqual(payload["package_path"], str(package_dir))
        self.assertIn(payload["closure_mode"], {"exact", "bounded", "degraded"})
        self.assertEqual(payload["proof_digest"]["snapshot_id"], snapshot_id)

    def test_sidecar_build_lane_writes_sidecar_manifest(self) -> None:
        trace_path = write_scenario(self.root / "sidecar-build-lane.trace", name="basic", repeat=3)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        controller.viz_SetContext({"evidence_anchor": {"ref_key": bundle.event_stream[0].ref_key}})

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.sidecar_Build({"dataset_id": loaded.data, "rule_family": ["ref_ref"]})
        self.assertTrue(job.ok, job.message)
        sidecar_dir = self.root / "sidecar-build-lane-package"
        submitted = controller.jobs.submit(
            job.data["job_id"],
            export.sidecar_WriteArtifacts,
            job.data["job_id"],
            str(sidecar_dir),
        )
        self.assertTrue(submitted.ok, submitted.message)
        written = self._wait_for_job_result(controller, job.data["job_id"])
        self.assertTrue(written.ok, written.message)

        snapshot = controller.jobs.status(job.data["job_id"])
        self.assertTrue(snapshot.ok, snapshot.message)
        self.assertEqual(snapshot.data["kind"], "sidecar_build")
        self.assertEqual(snapshot.data["payload"]["lane"], "sidecar_build")
        self.assertGreater(written.data["edge_count"], 0)
        sidecar_manifest = json.loads((sidecar_dir / "control" / "sidecar_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar_manifest["snapshot_id"], written.data["snapshot_id"])
        self.assertIn("control/dependency_sidecar.jsonl", sidecar_manifest["entry_paths"])
        self.assertIn("control/dependency_sidecar.jsonl.sqlite3", sidecar_manifest["entry_paths"])
        self.assertIn("control/dependency_sidecar.jsonl.sqlite3.ticket.json", sidecar_manifest["entry_paths"])
        self.assertTrue((sidecar_dir / "control" / "dependency_sidecar.jsonl").exists())
        self.assertTrue((sidecar_dir / "control" / "dependency_sidecar.jsonl.sqlite3").exists())
        self.assertTrue((sidecar_dir / "control" / "dependency_sidecar.jsonl.sqlite3.ticket.json").exists())
        self.assertEqual(written.data["sidecar_index_agent"]["status"], "AGENT-completed")

    def test_cli_sidecar_build_emits_summary_payload(self) -> None:
        trace_path = write_scenario(self.root / "cli-sidecar-build.trace", name="basic", repeat=3)
        sidecar_dir = self.root / "cli-sidecar-build-package"
        captured = io.StringIO()
        with redirect_stdout(captured):
            exit_code = cli_main(
                [
                    "sidecar-build",
                    "--input",
                    str(trace_path),
                    "--output-dir",
                    str(sidecar_dir),
                    "--rule-family",
                    "ref_ref",
                ]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(captured.getvalue())
        self.assertIn("job_id", payload)
        self.assertEqual(payload["output_path"], str(sidecar_dir))
        self.assertGreater(payload["edge_count"], 0)
        self.assertTrue((sidecar_dir / "control" / "sidecar_manifest.json").exists())

    def test_cli_evidence_query_proof_emits_async_result(self) -> None:
        trace_path = write_scenario(self.root / "cli-evidence-query-proof.trace", name="basic", repeat=3)
        loaded = load_dataset(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        seed_ref = loaded.data.bundle.event_stream[0].ref_key
        package_dir = self.root / "cli-evidence-query-proof-package"
        export_output = io.StringIO()
        with redirect_stdout(export_output):
            exit_code = cli_main(
                [
                    "export-evidence",
                    "--input",
                    str(trace_path),
                    "--output-dir",
                    str(package_dir),
                    "--seed-spec-json",
                    json.dumps({"source_kind": "manual_refs", "source_payload": {"refs": [seed_ref]}}),
                    "--rule-family",
                    "ref_ref",
                ]
            )
        self.assertEqual(exit_code, 0)

        query_output = io.StringIO()
        with redirect_stdout(query_output):
            query_exit_code = cli_main(["evidence-query", "--package", str(package_dir), "--kind", "proof"])
        self.assertEqual(query_exit_code, 0)
        payload = json.loads(query_output.getvalue())
        self.assertTrue(payload["job_id"].startswith("evidence_query_proof-"))
        self.assertIn("proof_digest", payload["result"])
        self.assertIn("consumer_mode", payload["result"])

    def test_evidence_export_job_status_exposes_patent_contract(self) -> None:
        trace_path = write_scenario(self.root / "evidence-job-contract.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})
        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 64, "S_bytes": 16384, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-job-contract-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        snapshot = controller.jobs.status(job.data["job_id"])
        self.assertTrue(snapshot.ok, snapshot.message)
        payload = snapshot.data["payload"]
        self.assertEqual(payload["state_contract_version"], "patent-job-v1")
        self.assertEqual(payload["lane"], "evidence_export")
        self.assertEqual(payload["job_kind"], "export_evidence")
        self.assertEqual(payload["patent_job_state"], "JOB-completed")
        self.assertEqual(payload["finalized_mode"], written.data["closure_mode"])
        self.assertEqual(payload["last_substage"], "write/manifest")
        self.assertEqual(payload["last_substage_status"], "completed")
        history = self._patent_state_history(payload)
        for state in (
            "JOB-queued",
            "JOB-context_frozen",
            "JOB-sidecar_ready",
            "JOB-sidecar_validated",
            "JOB-seeds_ready",
            "JOB-closing",
            "JOB-finalized",
            "JOB-package_written",
            "JOB-completed",
        ):
            self.assertIn(state, history)

    def test_evidence_export_job_status_records_budget_rejection_contract(self) -> None:
        trace_path = write_scenario(self.root / "evidence-budget-contract.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        anchor_ref = bundle.event_stream[0].ref_key
        controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})
        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(
            {
                "dataset_id": loaded.data,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 0, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            }
        )
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "evidence-budget-contract-package"
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertEqual(written.data["closure_mode"], "bounded")

        snapshot = controller.jobs.status(job.data["job_id"])
        self.assertTrue(snapshot.ok, snapshot.message)
        payload = snapshot.data["payload"]
        self.assertEqual(payload["patent_job_state"], "JOB-completed")
        self.assertTrue(payload["budget_rejected"])
        self.assertEqual(payload["finalized_mode"], "bounded")
        self.assertTrue(str(payload["halt_reason"]))
        self.assertIn("JOB-finalized", self._patent_state_history(payload))

    def test_background_job_timeout_sets_patent_contract_fields(self) -> None:
        jobs = BackgroundJobManager()
        job_id = jobs.create("sidecar_build", {"timeout_s": 0.01})
        submitted = jobs.submit(
            job_id,
            lambda: (time.sleep(0.05), ok_result({"ok": True}))[-1],
        )
        self.assertTrue(submitted.ok, submitted.message)
        deadline = time.time() + 1.0
        result = None
        while time.time() < deadline:
            snapshot = jobs.status(job_id)
            self.assertTrue(snapshot.ok, snapshot.message)
            if snapshot.data["status"] in {"succeeded", "failed"}:
                result = jobs.result(job_id)
                break
            time.sleep(0.01)
        self.assertIsNotNone(result)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "TIMEOUT")
        snapshot = jobs.status(job_id)
        self.assertTrue(snapshot.ok, snapshot.message)
        payload = snapshot.data["payload"]
        self.assertTrue(payload["timed_out"])
        self.assertEqual(payload["error_code"], "TIMEOUT")
        self.assertTrue(str(payload["failed_at_state"]))

    def test_background_job_cancel_sets_patent_contract_fields(self) -> None:
        jobs = BackgroundJobManager()
        job_id = jobs.create("sidecar_build", {})
        cancelled = jobs.request_cancel(job_id)
        self.assertTrue(cancelled.ok, cancelled.message)
        snapshot = jobs.status(job_id)
        self.assertTrue(snapshot.ok, snapshot.message)
        self.assertEqual(snapshot.data["status"], "failed")
        payload = snapshot.data["payload"]
        self.assertTrue(payload["cancel_requested"])
        self.assertTrue(payload["cancelled"])
        self.assertEqual(payload["error_code"], "CANCELLED")
        result = jobs.result(job_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "CANCELLED")

    def test_background_job_cancel_writes_parser_cancel_marker(self) -> None:
        jobs = BackgroundJobManager()
        cancel_path = self.root / "parser-cancel" / "cancel.flag"
        job_id = jobs.create("load_dataset", {"parser_cancel_path": str(cancel_path)})

        cancelled = jobs.request_cancel(job_id)

        self.assertTrue(cancelled.ok, cancelled.message)
        self.assertTrue(cancel_path.exists())
        marker = json.loads(cancel_path.read_text(encoding="utf-8"))
        self.assertEqual(marker["job_id"], job_id)
        snapshot = jobs.status(job_id)
        self.assertTrue(snapshot.ok, snapshot.message)
        payload = snapshot.data["payload"]
        self.assertTrue(payload["parser_cancel_marker_written"])
        self.assertEqual(payload["parser_cancel_marker_path"], str(cancel_path.resolve()))

    def test_background_sidecar_prebuild_stale_activation_does_not_write_back(self) -> None:
        trace_path = write_scenario(self.root / "runtime-load-background-prebuild-stale.trace", name="basic", repeat=4)
        controller = WorkspaceController()
        started = threading.Event()
        release = threading.Event()

        def delayed_sidecar_write(export_service, job_id: str, output_path: str):
            started.set()
            self.assertTrue(release.wait(5.0))
            return ok_result(
                {
                    "job_id": job_id,
                    "output_path": output_path,
                    "snapshot_id": "snapshot:test:background-prebuild-stale",
                    "dependency_sidecar_path": str(Path(output_path) / "control" / "dependency_sidecar.jsonl"),
                    "sidecar_manifest_path": str(Path(output_path) / "control" / "sidecar_manifest.json"),
                    "sidecar_index_path": str(Path(output_path) / "control" / "dependency_sidecar.jsonl.sqlite3"),
                    "sidecar_index_ticket_path": str(
                        Path(output_path) / "control" / "dependency_sidecar.jsonl.sqlite3.ticket.json"
                    ),
                }
            )

        with patch(
            "desktop.services.RuntimeOptimizationAdvisor.plan_load",
            return_value=RuntimeLoadPlan(
                plan_version="runtime-load-plan-v1",
                load_mode="full",
                index_build_mode="full",
                materialize_event_stream=True,
                try_parser_artifact_reuse=False,
                try_sidecar_index_reuse=False,
                background_sidecar_prebuild=True,
                reasons=["unit test"],
            ),
        ), patch("desktop.services.ExportService.sidecar_WriteArtifacts", new=delayed_sidecar_write):
            loaded = controller.viz_LoadDataset(str(trace_path))
            self.assertTrue(loaded.ok, loaded.message)
            record = controller.repository.get(loaded.data)
            job_id = str(record.background_sidecar_prebuild["job_id"])
            self.assertTrue(started.wait(5.0))
            record.activation_id = "activation:replaced"
            record.background_sidecar_prebuild = {}
            release.set()
            finished = self._wait_for_job_result(controller, job_id)

        self.assertTrue(finished.ok, finished.message)
        record = controller.repository.get(loaded.data)
        self.assertEqual(record.activation_id, "activation:replaced")
        self.assertEqual(record.background_sidecar_prebuild, {})

    def test_export_job_keeps_snapshot_context_stable(self) -> None:
        baseline = write_scenario(self.root / "snapshot.trace", name="multi_core", repeat=6)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(baseline))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        frozen_window = (
            bundle.event_stream[2].timestamp_aligned,
            bundle.event_stream[-3].timestamp_aligned,
        )
        frozen_compare_scope = {
            "baseline_id": loaded.data,
            "candidate_id": loaded.data,
            "aligned_time_window": list(frozen_window),
            "filter": {"task_id": 1},
            "dimensions": ["metric", "task"],
            "metric_ids": ["cpu_utilization"],
        }
        frozen_context = controller.viz_SetContext(
            {
                "time_window": frozen_window,
                "filter": {"task_id": 1},
                "selection": {"task_id": 1},
                "zoom_level": 2.5,
                "focused_view": "timeline",
                "compare_scope": frozen_compare_scope,
                "dataset_role": "single",
            }
        )
        self.assertEqual(frozen_context.data.selection["task_id"], 1)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Clipped({"dataset_id": loaded.data})
        self.assertTrue(job.ok, job.message)

        mutated = controller.viz_SetContext(
            {
                "time_window": (bundle.event_stream[0].timestamp_aligned, bundle.event_stream[1].timestamp_aligned),
                "filter": {"task_id": 2},
                "selection": {"task_id": 2},
                "zoom_level": 0.5,
                "focused_view": "alerts",
                "compare_scope": {
                    "baseline_id": "mutated",
                    "candidate_id": "mutated",
                    "aligned_time_window": [0.0, 1.0],
                    "filter": {"task_id": 2},
                    "dimensions": ["metric", "task"],
                    "metric_ids": ["cpu_utilization"],
                },
                "dataset_role": "candidate",
            }
        )
        self.assertEqual(mutated.data.selection["task_id"], 2)

        package_dir = self.root / "snapshot-package"
        written = export.export_WritePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        meta = json.loads((package_dir / "meta.json").read_text(encoding="utf-8"))
        analysis_context = json.loads((package_dir / "context" / "analysis_context.json").read_text(encoding="utf-8"))
        compare_scope = json.loads((package_dir / "context" / "compare_scope.json").read_text(encoding="utf-8"))
        rebuild = json.loads((package_dir / "rebuild" / "rebuild_bundle.json").read_text(encoding="utf-8"))

        self.assertEqual(meta["time_window"], list(frozen_window))
        self.assertEqual(meta["selection"], {"task_id": 1})
        self.assertEqual(meta["analysis_context"]["selection"], {"task_id": 1})
        self.assertEqual(meta["analysis_context"]["focused_view"], "timeline")
        self.assertEqual(meta["analysis_context"]["dataset_role"], "single")
        self.assertEqual(analysis_context["selection"], {"task_id": 1})
        self.assertEqual(analysis_context["filter"], {"task_id": 1})
        self.assertEqual(analysis_context["zoom_level"], 2.5)
        self.assertEqual(compare_scope, frozen_compare_scope)
        event_timestamps = [item["timestamp_aligned"] for item in rebuild["event_stream"]]
        self.assertTrue(all(frozen_window[0] <= item <= frozen_window[1] for item in event_timestamps))

    def test_background_export_job_transitions_and_writes_package(self) -> None:
        trace_path = write_scenario(self.root / "async-export.trace", name="multi_core", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": loaded.data})
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "async-package"

        submitted = controller.jobs.submit(
            job.data["job_id"],
            export.export_WritePackage,
            job.data["job_id"],
            str(package_dir),
        )
        self.assertTrue(submitted.ok, submitted.message)

        deadline = time.time() + 5.0
        last_status = "created"
        while time.time() < deadline:
            snapshot = controller.jobs.status(job.data["job_id"])
            self.assertTrue(snapshot.ok, snapshot.message)
            last_status = snapshot.data["status"]
            if last_status in {"succeeded", "failed"}:
                break
            time.sleep(0.01)

        self.assertEqual(last_status, "succeeded")
        written = controller.jobs.result(job.data["job_id"])
        self.assertTrue(written.ok, written.message)
        self.assertTrue((package_dir / "manifest.json").exists())
        self.assertTrue((package_dir / "meta.json").exists())

    def test_async_load_package_returns_preview_before_full_register(self) -> None:
        trace_path = write_scenario(self.root / "package-preview.trace", name="multi_core", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": loaded.data})
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "package-preview"
        written = export.export_WritePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        package_controller = WorkspaceController()
        async_load = package_controller.viz_LoadDatasetAsync(str(package_dir))
        self.assertTrue(async_load.ok, async_load.message)
        trace_preview = prs_Prescan(package_dir / "event" / "events.trace")
        self.assertTrue(trace_preview.ok, trace_preview.message)
        self.assertIsNotNone(async_load.data["preview"])
        package_task_preview = async_load.data["preview"].get("task_state_preview") or {}
        trace_task_preview = trace_preview.data.get("task_state_preview") or {}
        self.assertEqual(async_load.data["preview"]["source"], "package_preview")
        self.assertGreater(len(async_load.data["preview"]["lod0_buckets"]), 0)
        self.assertEqual(async_load.data["preview"]["chunk_count"], trace_preview.data["chunk_count"])
        self.assertEqual(async_load.data["preview"]["record_count"], trace_preview.data["record_count"])
        self.assertEqual(async_load.data["preview"]["core_ids"], trace_preview.data["core_ids"])
        self.assertTrue(package_task_preview)
        self.assertEqual(package_task_preview, trace_task_preview)
        self.assertEqual(package_task_preview["readiness"]["stage"], "preview_ready")
        self.assertFalse(package_task_preview["readiness"]["view_ready"]["task_states"])
        self.assertEqual(async_load.data["job_kind"], "input_prescan")
        self._assert_preview_readiness(async_load.data, "package_preview")

        deadline = time.time() + 5.0
        last_status = "created"
        while time.time() < deadline:
            snapshot = package_controller.jobs.status(async_load.data["job_id"])
            self.assertTrue(snapshot.ok, snapshot.message)
            last_status = snapshot.data["status"]
            if last_status in {"succeeded", "failed"}:
                break
            time.sleep(0.01)

        self.assertEqual(last_status, "succeeded")
        resolved = package_controller.viz_ResolveLoadDatasetJob(async_load.data["job_id"])
        self.assertTrue(resolved.ok, resolved.message)
        self.assertTrue(package_controller.repository.has(resolved.data))

    def test_package_reconstruction_uses_isolated_parse_when_direct_load_is_blocked(self) -> None:
        trace_path = write_scenario(self.root / "package-isolated-reconstruct.trace", name="basic", repeat=4)
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": loaded.data})
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "package-isolated-reconstruct"
        written = export.export_WritePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)

        rebuild_path = package_dir / "rebuild" / "rebuild_bundle.json"
        rebuild = json.loads(rebuild_path.read_text(encoding="utf-8"))
        rebuild.pop("event_stream", None)
        json_dump(rebuild_path, rebuild)
        manifest_path = package_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["entries"]:
            if entry["path"] == "rebuild/rebuild_bundle.json":
                entry["checksum"] = checksum_file(rebuild_path)
                break
        json_dump(manifest_path, manifest)

        with patch("desktop.services.load_dataset", side_effect=AssertionError("direct package load_dataset")):
            package_controller = WorkspaceController()
            loaded_package = package_controller.viz_LoadDataset(str(package_dir))
            self.assertTrue(loaded_package.ok, loaded_package.message)
            package_bundle = package_controller.repository.get(loaded_package.data).artifact.bundle
            self.assertGreater(len(package_bundle.event_stream), 0)

            repro_controller = WorkspaceController()
            repro = ReproService(repro_controller.repository, repro_controller.context_store)
            opened = repro.repro_OpenPackage(str(package_dir))
            self.assertTrue(opened.ok, opened.message)
            loaded_repro = repro.repro_LoadAsDataset("single")
            self.assertTrue(loaded_repro.ok, loaded_repro.message)

    def test_compare_rejects_non_overlapping_window(self) -> None:
        baseline = write_scenario(self.root / "baseline.trace", name="basic", candidate_variant=False)
        candidate = self._write_shifted_trace(self.root / "candidate-shifted.trace", offset=10000)
        controller = WorkspaceController()
        baseline_loaded = controller.viz_LoadDataset(str(baseline))
        candidate_loaded = controller.viz_LoadDataset(str(candidate))
        self.assertTrue(baseline_loaded.ok, baseline_loaded.message)
        self.assertTrue(candidate_loaded.ok, candidate_loaded.message)

        compare = CompareService(controller.repository, controller.context_store)
        self.assertTrue(compare.cmp_LoadPair(baseline_loaded.data, candidate_loaded.data).ok)
        scoped = compare.cmp_SetScope({"baseline_id": baseline_loaded.data, "candidate_id": candidate_loaded.data, "filter": {}})
        self.assertFalse(scoped.ok)
        self.assertIn("no overlap", scoped.message)

    def test_resource_graph_filter_keeps_hold_edges_and_closes_open_relations(self) -> None:
        trace_path = self.root / "locks.trace"
        encode_trace(
            trace_path,
            [
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_READY"),
                    "seq": 1,
                    "timestamp": 100,
                    "payload": {"task_id": 1, "prio": 8, "core_hint": 0, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 2,
                    "timestamp": 110,
                    "payload": {"core_id": 0, "prev_task_id": 0, "next_task_id": 1, "reason": 2},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("SYNC_LOCK"),
                    "seq": 3,
                    "timestamp": 120,
                    "payload": {"task_id": 1, "obj_id": 0x2A, "obj_type": 1, "timeout_ns": 0},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_BLOCK"),
                    "seq": 4,
                    "timestamp": 130,
                    "payload": {"task_id": 2, "wait_obj_id": 0x2A, "reason": 3, "owner_task_id": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_WAKEUP"),
                    "seq": 5,
                    "timestamp": 140,
                    "payload": {"task_id": 2, "wake_src": 1, "obj_id": 0x2A},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("SYNC_UNLOCK"),
                    "seq": 6,
                    "timestamp": 150,
                    "payload": {"task_id": 1, "obj_id": 0x2A, "obj_type": 1, "timeout_ns": 0},
                },
            ],
        )

        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        bundle = controller.repository.get(loaded.data).artifact.bundle
        self.assertTrue(bundle.capability_flags.get("resource_closed", True))
        self.assertFalse(
            any(window.reason_code in {"LOCK_WITHOUT_UNLOCK", "WAIT_WITHOUT_WAKEUP"} for window in bundle.untrusted_windows)
        )

        graph = controller.viz_QueryResourceGraph({"dataset_id": loaded.data, "filter": {"resource_id": 0x2A}})
        self.assertTrue(graph.ok, graph.message)
        self.assertEqual(len(graph.data["hold_edges"]), 1)
        self.assertEqual(len(graph.data["wait_edges"]), 1)
        self.assertEqual(len(graph.data["wait_chains"]), 1)
        self.assertEqual(graph.data["hold_edges"][0]["task_id"], 1)
        self.assertEqual(graph.data["hold_edges"][0]["obj_id"], 0x2A)
        self.assertEqual(graph.data["wait_edges"][0]["task_id"], 2)
        self.assertEqual(graph.data["wait_edges"][0]["obj_id"], 0x2A)
        self.assertEqual(graph.data["wait_chains"][0]["owner_task_id"], 1)

        drilldown = controller.viz_QueryResourceDrilldown({"dataset_id": loaded.data, "filter": {"resource_id": "0x2A"}})
        self.assertTrue(drilldown.ok, drilldown.message)
        self.assertEqual(drilldown.data["resource_id"], 0x2A)
        self.assertEqual(len(drilldown.data["wait_chains"]), 1)
        self.assertEqual(drilldown.data["wait_chains"][0]["task_id"], 2)
        self.assertEqual(drilldown.data["wait_chains"][0]["owner_task_id"], 1)
        self.assertEqual(drilldown.data["wait_chains"][0]["wait_event"]["event_name"], "TASK_BLOCK")
        self.assertEqual(drilldown.data["wait_chains"][0]["hold_event"]["event_name"], "SYNC_LOCK")
        self.assertEqual(drilldown.data["jump_target"]["selection"]["resource_id"], 0x2A)


if __name__ == "__main__":
    unittest.main()
