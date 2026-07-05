from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.context import ContextStore
from desktop.repository import DatasetRepository
from desktop.sample_data import write_scenario
from desktop.services import ExportService, ReproService, WorkspaceController
from spec.io import checksum_file


class ReproEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

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

    def _write_json(self, path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_jsonl(self, path: Path, rows: list[dict[str, object]]) -> None:
        path.write_text(
            "".join(f"{json.dumps(row, ensure_ascii=False)}\n" for row in rows),
            encoding="utf-8",
        )

    def _prepare_controller_with_anchor(self, trace_name: str) -> tuple[WorkspaceController, str]:
        trace_path = write_scenario(self.root / trace_name, name="basic")
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
        return controller, loaded.data

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

    def _export_evidence_package(
        self,
        controller: WorkspaceController,
        dataset_id: str,
        package_name: str,
        payload: dict[str, object],
    ) -> Path:
        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job_payload = dict(payload)
        job_payload["dataset_id"] = dataset_id
        job = export.export_Evidence(job_payload)
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / package_name
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        return package_dir

    def test_repro_rejects_tampered_proof_hash_after_checksums_are_refreshed(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-proof-hash.trace")
        package_dir = self._export_evidence_package(
            controller,
            dataset_id,
            "evidence-proof-hash-package",
            {
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
            },
        )

        proof_digest_path = package_dir / "control" / "proof_digest.json"
        proof_digest = json.loads(proof_digest_path.read_text(encoding="utf-8"))
        proof_digest["proof_hash"] = "sha256:tampered"
        proof_digest_path.write_text(json.dumps(proof_digest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_sidecar_manifest_checksums(package_dir, "control/proof_digest.json")
        self._refresh_manifest_checksums(package_dir, "control/proof_digest.json", "control/sidecar_manifest.json")

        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertFalse(opened.ok)
        self.assertIn("proof_hash mismatch", opened.message)

    def test_exported_proof_digest_includes_p13_round_count_and_window_hit_rate(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-proof-p13-fields.trace")
        package_dir = self._export_evidence_package(
            controller,
            dataset_id,
            "evidence-proof-p13-fields-package",
            {
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
            },
        )
        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        self.assertIn("round_count", proof_digest)
        self.assertIn("window_hit_rate", proof_digest)
        self.assertEqual(int(proof_digest["round_count"]), int(proof_digest["closure_depth_reached"]))
        self.assertGreaterEqual(float(proof_digest["window_hit_rate"]), 0.0)
        self.assertLessEqual(float(proof_digest["window_hit_rate"]), 1.0)

    def test_exported_proof_digest_includes_peak_rss_mb_when_available(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-proof-peak-rss.trace")
        with patch("desktop.evidence_export._current_process_peak_rss_mb", return_value=123.456):
            package_dir = self._export_evidence_package(
                controller,
                dataset_id,
                "evidence-proof-peak-rss-package",
                {
                    "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                    "rule_family": ["ref_ref"],
                },
            )
        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        self.assertIn("peak_rss_mb", proof_digest)
        self.assertEqual(float(proof_digest["peak_rss_mb"]), 123.456)

    def test_frontier_snapshot_contains_freeze_vs_pre_read_reject_relation_for_bounded_mode(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-proof-frontier-p13.trace")
        package_dir = self._export_evidence_package(
            controller,
            dataset_id,
            "evidence-proof-frontier-p13-package",
            {
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 0, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            },
        )
        proof_digest = json.loads((package_dir / "control" / "proof_digest.json").read_text(encoding="utf-8"))
        frontier_snapshot = json.loads((package_dir / "control" / "frontier_snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(proof_digest["closure_mode"], "bounded")
        self.assertIn("freeze_round_id", frontier_snapshot)
        self.assertIn("pre_read_reject", frontier_snapshot)
        self.assertIn("pre_read_reject_round_id", frontier_snapshot)
        self.assertTrue(frontier_snapshot["pre_read_reject"])
        self.assertEqual(frontier_snapshot["freeze_round_id"], frontier_snapshot["pre_read_reject_round_id"])
        self.assertEqual(int(frontier_snapshot["freeze_round_id"]), int(frontier_snapshot["round_id"]) + 1)

    def test_repro_accepts_window_hit_rate_change_without_proof_hash_boundary_change(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-proof-hash-window-rate.trace")
        package_dir = self._export_evidence_package(
            controller,
            dataset_id,
            "evidence-proof-hash-window-rate-package",
            {
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
            },
        )
        proof_digest_path = package_dir / "control" / "proof_digest.json"
        proof_digest = json.loads(proof_digest_path.read_text(encoding="utf-8"))
        proof_digest["window_hit_rate"] = 0.5
        proof_digest_path.write_text(json.dumps(proof_digest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_sidecar_manifest_checksums(package_dir, "control/proof_digest.json")
        self._refresh_manifest_checksums(package_dir, "control/proof_digest.json", "control/sidecar_manifest.json")

        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertTrue(opened.ok, opened.message)
        self.assertTrue(opened.data["proof_verification"]["proof_hash_matches"])

    def test_repro_accepts_peak_rss_mb_change_without_proof_hash_boundary_change(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-proof-hash-peak-rss.trace")
        with patch("desktop.evidence_export._current_process_peak_rss_mb", return_value=10.0):
            package_dir = self._export_evidence_package(
                controller,
                dataset_id,
                "evidence-proof-hash-peak-rss-package",
                {
                    "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                    "rule_family": ["ref_ref"],
                },
            )
        proof_digest_path = package_dir / "control" / "proof_digest.json"
        proof_digest = json.loads(proof_digest_path.read_text(encoding="utf-8"))
        proof_digest["peak_rss_mb"] = 42.0
        proof_digest_path.write_text(json.dumps(proof_digest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_sidecar_manifest_checksums(package_dir, "control/proof_digest.json")
        self._refresh_manifest_checksums(package_dir, "control/proof_digest.json", "control/sidecar_manifest.json")

        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertTrue(opened.ok, opened.message)
        self.assertTrue(opened.data["proof_verification"]["proof_hash_matches"])

    def test_repro_rejects_invalid_peak_rss_mb_after_checksums_are_refreshed(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-proof-invalid-peak-rss.trace")
        with patch("desktop.evidence_export._current_process_peak_rss_mb", return_value=10.0):
            package_dir = self._export_evidence_package(
                controller,
                dataset_id,
                "evidence-proof-invalid-peak-rss-package",
                {
                    "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                    "rule_family": ["ref_ref"],
                },
            )
        proof_digest_path = package_dir / "control" / "proof_digest.json"
        proof_digest = json.loads(proof_digest_path.read_text(encoding="utf-8"))
        proof_digest["peak_rss_mb"] = -1
        proof_digest_path.write_text(json.dumps(proof_digest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_sidecar_manifest_checksums(package_dir, "control/proof_digest.json")
        self._refresh_manifest_checksums(package_dir, "control/proof_digest.json", "control/sidecar_manifest.json")

        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertFalse(opened.ok)
        self.assertIn("peak_rss_mb", opened.message)

    def test_repro_rejects_inconsistent_blocker_p13_metrics_after_checksums_are_refreshed(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-proof-blocker-p13.trace")
        package_dir = self._export_evidence_package(
            controller,
            dataset_id,
            "evidence-proof-blocker-p13-package",
            {
                "seed_spec": {"source_kind": "manual_refs", "source_payload": {"refs": []}},
                "rule_family": ["ref_ref"],
            },
        )
        blocker_path = package_dir / "control" / "blocker_artifact.json"
        blocker_artifact = json.loads(blocker_path.read_text(encoding="utf-8"))
        blocker_artifact["emitted_metrics"]["read_window_hit_rate"] = 0.25
        blocker_path.write_text(json.dumps(blocker_artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_sidecar_manifest_checksums(package_dir, "control/blocker_artifact.json")
        self._refresh_manifest_checksums(package_dir, "control/blocker_artifact.json", "control/sidecar_manifest.json")

        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertFalse(opened.ok)
        self.assertIn("window_hit_rate", opened.message)

    def test_repro_rejects_frontier_boundary_mismatch_after_checksums_are_refreshed(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-proof-frontier.trace")
        package_dir = self._export_evidence_package(
            controller,
            dataset_id,
            "evidence-proof-frontier-package",
            {
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 0, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            },
        )

        frontier_snapshot_path = package_dir / "control" / "frontier_snapshot.json"
        frontier_snapshot = json.loads(frontier_snapshot_path.read_text(encoding="utf-8"))
        frontier_snapshot["frontier_refs_path"] = None
        frontier_snapshot_path.write_text(json.dumps(frontier_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_sidecar_manifest_checksums(package_dir, "control/frontier_snapshot.json")
        self._refresh_manifest_checksums(package_dir, "control/frontier_snapshot.json", "control/sidecar_manifest.json")

        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertFalse(opened.ok)
        self.assertIn("frontier_refs", opened.message)

    def test_repro_rejects_degraded_package_missing_blocker_after_metadata_update(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-proof-degraded.trace")
        package_dir = self._export_evidence_package(
            controller,
            dataset_id,
            "evidence-proof-degraded-package",
            {
                "seed_spec": {"source_kind": "manual_refs", "source_payload": {"refs": []}},
                "rule_family": ["ref_ref"],
            },
        )

        blocker_path = package_dir / "control" / "blocker_artifact.json"
        blocker_path.unlink()

        sidecar_manifest_path = package_dir / "control" / "sidecar_manifest.json"
        sidecar_manifest = json.loads(sidecar_manifest_path.read_text(encoding="utf-8"))
        sidecar_manifest["entry_paths"] = [
            path for path in sidecar_manifest["entry_paths"] if path != "control/blocker_artifact.json"
        ]
        sidecar_manifest["entry_checksums"].pop("control/blocker_artifact.json", None)
        sidecar_manifest_path.write_text(json.dumps(sidecar_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        meta_path = package_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["control_refs"].pop("blocker_artifact", None)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        manifest_path = package_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["entries"] = [entry for entry in manifest["entries"] if entry["path"] != "control/blocker_artifact.json"]
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_manifest_checksums(package_dir, "meta.json", "control/sidecar_manifest.json")

        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertFalse(opened.ok)
        self.assertIn("missing blocker_artifact", opened.message)

    def test_repro_returns_consumer_modes_for_exact_bounded_and_degraded_packages(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-consumer-mode.trace")
        export = ExportService(controller.repository, controller.context_store, controller.jobs)

        exact_job = export.export_Evidence(
            {
                "dataset_id": dataset_id,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 64, "S_bytes": 16384, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            }
        )
        self.assertTrue(exact_job.ok, exact_job.message)
        exact_dir = self.root / "evidence-consumer-exact"
        self.assertTrue(export.export_WriteEvidencePackage(exact_job.data["job_id"], str(exact_dir)).ok)

        bounded_job = export.export_Evidence(
            {
                "dataset_id": dataset_id,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 0, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            }
        )
        self.assertTrue(bounded_job.ok, bounded_job.message)
        bounded_dir = self.root / "evidence-consumer-bounded"
        self.assertTrue(export.export_WriteEvidencePackage(bounded_job.data["job_id"], str(bounded_dir)).ok)

        degraded_job = export.export_Evidence(
            {
                "dataset_id": dataset_id,
                "seed_spec": {"source_kind": "manual_refs", "source_payload": {"refs": []}},
                "rule_family": ["ref_ref"],
            }
        )
        self.assertTrue(degraded_job.ok, degraded_job.message)
        degraded_dir = self.root / "evidence-consumer-degraded"
        self.assertTrue(export.export_WriteEvidencePackage(degraded_job.data["job_id"], str(degraded_dir)).ok)

        repro = ReproService(controller.repository, controller.context_store)
        exact_opened = repro.repro_OpenPackage(str(exact_dir))
        self.assertTrue(exact_opened.ok, exact_opened.message)
        self.assertEqual(
            exact_opened.data["consumer_mode"],
            {"compare": "ALLOW_COMPARE", "replay": "ALLOW_COMPARE", "audit": "ALLOW_COMPARE"},
        )
        self.assertTrue(exact_opened.data["proof_verification"]["ok"])
        self.assertIn("consumer_mode_explain", exact_opened.data)
        self.assertEqual(exact_opened.data["consumer_mode_explain"]["final_mode"], exact_opened.data["consumer_mode"])
        self.assertIn("result_validity_summary", exact_opened.data["consumer_mode_explain"])

        bounded_opened = repro.repro_OpenPackage(str(bounded_dir))
        self.assertTrue(bounded_opened.ok, bounded_opened.message)
        self.assertEqual(
            bounded_opened.data["consumer_mode"],
            {"compare": "REFERENCE_ONLY", "replay": "REFERENCE_ONLY", "audit": "REFERENCE_ONLY"},
        )
        self.assertIn("consumer_mode_explain", bounded_opened.data)
        self.assertEqual(bounded_opened.data["consumer_mode_explain"]["final_mode"], bounded_opened.data["consumer_mode"])

        degraded_opened = repro.repro_OpenPackage(str(degraded_dir))
        self.assertTrue(degraded_opened.ok, degraded_opened.message)
        self.assertEqual(
            degraded_opened.data["consumer_mode"],
            {"compare": "REFERENCE_ONLY", "replay": "REJECT_AUTOMATION", "audit": "REFERENCE_ONLY"},
        )
        self.assertIn("consumer_mode_explain", degraded_opened.data)
        self.assertEqual(degraded_opened.data["consumer_mode_explain"]["final_mode"], degraded_opened.data["consumer_mode"])

    def test_repro_downgrades_exact_consumer_mode_when_result_validity_marks_subset(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-consumer-subset.trace")
        package_dir = self._export_evidence_package(
            controller,
            dataset_id,
            "evidence-consumer-subset-package",
            {
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 64, "S_bytes": 16384, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            },
        )
        result_validity_path = package_dir / "result" / "result_validity.json"
        result_validity = json.loads(result_validity_path.read_text(encoding="utf-8"))
        rows = list(result_validity.get("results") or [])
        self.assertTrue(rows)
        rows[0]["validity_scope"] = "evidence_subset"
        rows[0]["derivation_mode"] = "recomputed_subset"
        result_validity_path.write_text(json.dumps(result_validity, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_manifest_checksums(package_dir, "result/result_validity.json")

        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertTrue(opened.ok, opened.message)
        self.assertEqual(
            opened.data["consumer_mode"],
            {"compare": "REFERENCE_ONLY", "replay": "REFERENCE_ONLY", "audit": "REFERENCE_ONLY"},
        )
        explain = opened.data["consumer_mode_explain"]
        self.assertIn("subset_scope_present", explain["decision_reasons"])
        self.assertIn("recomputed_subset_present", explain["decision_reasons"])
        self.assertTrue(explain["blocking_objects"])
        self.assertEqual(explain["final_mode"], opened.data["consumer_mode"])

    def test_repro_result_validity_query_helpers_cover_hit_miss_conflict_and_invalid_arg(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-validity-query.trace")
        package_dir = self._export_evidence_package(
            controller,
            dataset_id,
            "evidence-validity-query-package",
            {
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 16, "C_events": 64, "S_bytes": 16384, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            },
        )
        result_validity_path = package_dir / "result" / "result_validity.json"
        result_validity = json.loads(result_validity_path.read_text(encoding="utf-8"))
        rows = list(result_validity.get("results") or [])
        self.assertTrue(rows)
        alert_row = next((row for row in rows if row.get("object_kind") == "alert"), None)
        diag_row = next((row for row in rows if row.get("object_kind") == "diagnosis"), None)
        self.assertIsNotNone(alert_row)
        self.assertIsNotNone(diag_row)
        conflict_base_row = {
            "path": "result/alerts.json",
            "category": "result",
            "object_kind": "alert",
            "object_id": "alert:synthetic:conflict",
            "validity_scope": "source_snapshot",
            "derivation_mode": "reused_context",
            "notes": "synthetic conflict base",
        }
        conflict_variant_row = dict(conflict_base_row)
        conflict_variant_row["validity_scope"] = "evidence_subset"
        conflict_variant_row["derivation_mode"] = "recomputed_subset"
        conflict_variant_row["notes"] = "synthetic conflict variant"
        rows.append(conflict_base_row)
        rows.append(conflict_variant_row)
        result_validity["results"] = rows
        result_validity_path.write_text(json.dumps(result_validity, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_manifest_checksums(package_dir, "result/result_validity.json")

        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertTrue(opened.ok, opened.message)
        self.assertEqual(
            opened.data["consumer_mode"],
            {"compare": "REJECT_AUTOMATION", "replay": "REJECT_AUTOMATION", "audit": "REJECT_AUTOMATION"},
        )
        self.assertIn("validity_conflict", opened.data["consumer_mode_explain"]["decision_reasons"])

        alert_hit = repro.repro_QueryValidityByAlertId(str(alert_row["object_id"]))
        self.assertTrue(alert_hit.ok, alert_hit.message)
        self.assertEqual(alert_hit.data["status"], "HIT")
        self.assertTrue(alert_hit.data["rows"])

        diag_hit = repro.repro_QueryValidityByDiagId(str(diag_row["object_id"]))
        self.assertTrue(diag_hit.ok, diag_hit.message)
        self.assertEqual(diag_hit.data["status"], "HIT")
        self.assertTrue(diag_hit.data["rows"])

        object_conflict = repro.repro_QueryValidityByObject("alert", "alert:synthetic:conflict")
        self.assertTrue(object_conflict.ok, object_conflict.message)
        self.assertEqual(object_conflict.data["status"], "CONFLICT")
        self.assertTrue(object_conflict.data["conflict"]["present"])
        self.assertTrue(object_conflict.warnings)

        mutated_rows = object_conflict.data["rows"]
        self.assertTrue(mutated_rows)
        mutated_rows[0]["notes"] = "external mutation"
        object_conflict_again = repro.repro_QueryValidityByObject("alert", "alert:synthetic:conflict")
        self.assertTrue(object_conflict_again.ok, object_conflict_again.message)
        self.assertNotEqual(object_conflict_again.data["rows"][0].get("notes"), "external mutation")

        miss = repro.repro_QueryValidityByAlertId("alert:not-exists")
        self.assertTrue(miss.ok, miss.message)
        self.assertEqual(miss.data["status"], "MISS")
        self.assertEqual(miss.data["rows"], [])

        invalid = repro.repro_QueryValidityByObject("unknown_kind", "id-1")
        self.assertFalse(invalid.ok)
        self.assertEqual(invalid.code, "INVALID_ARG")

    def test_repro_query_proof_async_matches_sync_payload(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-proof-query-async.trace")
        package_dir = self._export_evidence_package(
            controller,
            dataset_id,
            "evidence-proof-query-async-package",
            {
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
            },
        )

        sync_repro = ReproService(controller.repository, controller.context_store)
        sync_query = sync_repro.repro_QueryProof(str(package_dir))
        self.assertTrue(sync_query.ok, sync_query.message)

        async_repro = ReproService(controller.repository, controller.context_store, controller.jobs)
        job = async_repro.repro_QueryProofAsync(str(package_dir))
        self.assertTrue(job.ok, job.message)
        result = self._wait_for_job_result(controller, job.data["job_id"])
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.data["proof_digest"], sync_query.data["proof_digest"])
        self.assertEqual(result.data["consumer_mode"], sync_query.data["consumer_mode"])

        snapshot = controller.jobs.status(job.data["job_id"])
        self.assertTrue(snapshot.ok, snapshot.message)
        self.assertEqual(snapshot.data["kind"], "evidence_query_proof")
        self.assertEqual(snapshot.data["payload"]["lane"], "evidence_query")
        self.assertEqual(snapshot.data["payload"]["state_contract_version"], "patent-job-v1")
        self.assertEqual(snapshot.data["payload"]["patent_job_state"], "JOB-completed")
        self.assertIn(
            "JOB-finalized",
            [str(item.get("state")) for item in list(snapshot.data["payload"].get("patent_state_history") or [])],
        )

    def test_repro_query_validity_async_matches_sync_payload(self) -> None:
        controller, dataset_id = self._prepare_controller_with_anchor("evidence-validity-query-async.trace")
        package_dir = self._export_evidence_package(
            controller,
            dataset_id,
            "evidence-validity-query-async-package",
            {
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
            },
        )
        result_validity = json.loads((package_dir / "result" / "result_validity.json").read_text(encoding="utf-8"))
        alert_row = next(row for row in result_validity["results"] if row.get("object_kind") == "alert")
        alert_id = str(alert_row["object_id"])

        sync_repro = ReproService(controller.repository, controller.context_store)
        opened = sync_repro.repro_OpenPackage(str(package_dir))
        self.assertTrue(opened.ok, opened.message)
        sync_query = sync_repro.repro_QueryValidityByAlertId(alert_id)
        self.assertTrue(sync_query.ok, sync_query.message)

        async_repro = ReproService(controller.repository, controller.context_store, controller.jobs)
        job = async_repro.repro_QueryValidityByAlertIdAsync(alert_id, str(package_dir))
        self.assertTrue(job.ok, job.message)
        result = self._wait_for_job_result(controller, job.data["job_id"])
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.data, sync_query.data)

        snapshot = controller.jobs.status(job.data["job_id"])
        self.assertTrue(snapshot.ok, snapshot.message)
        self.assertEqual(snapshot.data["kind"], "evidence_query_validity")
        self.assertEqual(snapshot.data["payload"]["lane"], "evidence_query")
        self.assertEqual(snapshot.data["payload"]["state_contract_version"], "patent-job-v1")
        self.assertEqual(snapshot.data["payload"]["patent_job_state"], "JOB-completed")
        self.assertIn(
            "JOB-finalized",
            [str(item.get("state")) for item in list(snapshot.data["payload"].get("patent_state_history") or [])],
        )

    def test_repro_opens_frozen_fixture_and_consumes_proof_frontier_and_result_validity(self) -> None:
        fixture_root = Path(__file__).resolve().parents[2] / "docs" / "evidence-fixture" / "exact-package"
        repro = ReproService(DatasetRepository(), ContextStore())
        opened = repro.repro_OpenPackage(str(fixture_root))
        self.assertTrue(opened.ok, opened.message)
        self.assertIn("proof_digest", opened.data)
        self.assertIn("frontier_snapshot", opened.data)
        self.assertIn("result_validity", opened.data)
        self.assertEqual(opened.data["proof_digest"]["closure_mode"], "exact")
        self.assertEqual(opened.data["frontier_snapshot"]["closure_mode"], "exact")
        self.assertIn("round_count", opened.data["proof_digest"])
        self.assertIn("window_hit_rate", opened.data["proof_digest"])
        self.assertIn("freeze_round_id", opened.data["frontier_snapshot"])
        self.assertIn("pre_read_reject", opened.data["frontier_snapshot"])
        self.assertIn("pre_read_reject_round_id", opened.data["frontier_snapshot"])
        self.assertEqual(int(opened.data["proof_digest"]["round_count"]), int(opened.data["proof_digest"]["closure_depth_reached"]))
        self.assertEqual(int(opened.data["proof_digest"]["round_count"]), int(opened.data["frontier_snapshot"]["round_id"]))

        result_validity = opened.data["result_validity"]
        self.assertTrue(list(result_validity.get("results") or []))
        first_row = result_validity["results"][0]
        self.assertIn("object_kind", first_row)
        self.assertIn("object_id", first_row)
        self.assertIn("validity_scope", first_row)
        self.assertIn("derivation_mode", first_row)

    def test_repro_opens_all_frozen_contract_fixtures(self) -> None:
        fixture_root = Path(__file__).resolve().parents[2] / "docs" / "evidence-fixture"
        for package_name in ["exact-package", "exact-mode-b-package", "bounded-package", "degraded-package"]:
            with self.subTest(package=package_name):
                repro = ReproService(DatasetRepository(), ContextStore())
                opened = repro.repro_OpenPackage(str(fixture_root / package_name))
                self.assertTrue(opened.ok, opened.message)

    def test_repro_rejects_schema_invalid_frozen_fixture_artifacts_before_checksum_checks(self) -> None:
        fixture_root = Path(__file__).resolve().parents[2] / "docs" / "evidence-fixture"
        scenarios = [
            ("dependency_sidecar[0]", "exact-package", "control/dependency_sidecar.jsonl"),
            ("frontier_snapshot", "exact-package", "control/frontier_snapshot.json"),
            ("frontier_refs[0]", "bounded-package", "control/frontier_refs.jsonl"),
            ("proof_digest", "exact-package", "control/proof_digest.json"),
            ("sidecar_manifest", "exact-package", "control/sidecar_manifest.json"),
            ("blocker_artifact", "degraded-package", "control/blocker_artifact.json"),
            ("result_validity", "exact-package", "result/result_validity.json"),
        ]

        for label, fixture_name, rel_path in scenarios:
            with self.subTest(label=label):
                package_dir = self.root / (
                    f"schema-invalid-{label.replace('[', '-').replace(']', '').replace('/', '-')}"
                )
                shutil.copytree(fixture_root / fixture_name, package_dir)
                target_path = package_dir / rel_path

                if rel_path.endswith(".jsonl"):
                    rows = [
                        json.loads(line)
                        for line in target_path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
                    self.assertTrue(rows)
                    if label == "dependency_sidecar[0]":
                        rows[0]["priority"] = True
                    elif label == "frontier_refs[0]":
                        rows[0]["priority"] = True
                    else:
                        self.fail(f"unexpected jsonl scenario: {label}")
                    self._write_jsonl(target_path, rows)
                    self._refresh_sidecar_manifest_checksums(package_dir, rel_path)
                    self._refresh_manifest_checksums(package_dir, rel_path, "control/sidecar_manifest.json")
                else:
                    payload = json.loads(target_path.read_text(encoding="utf-8"))
                    if label == "frontier_snapshot":
                        payload["round_id"] = True
                        self._write_json(target_path, payload)
                        self._refresh_sidecar_manifest_checksums(package_dir, rel_path)
                        self._refresh_manifest_checksums(package_dir, rel_path, "control/sidecar_manifest.json")
                    elif label == "proof_digest":
                        payload["closure_depth_reached"] = True
                        self._write_json(target_path, payload)
                        self._refresh_sidecar_manifest_checksums(package_dir, rel_path)
                        self._refresh_manifest_checksums(package_dir, rel_path, "control/sidecar_manifest.json")
                    elif label == "sidecar_manifest":
                        payload["entry_checksums"]["control/proof_digest.json"] = 1
                        self._write_json(target_path, payload)
                        self._refresh_manifest_checksums(package_dir, "control/sidecar_manifest.json")
                    elif label == "blocker_artifact":
                        payload["window_plan_summary"] = {"planned_windows": 1}
                        self._write_json(target_path, payload)
                        self._refresh_sidecar_manifest_checksums(package_dir, rel_path)
                        self._refresh_manifest_checksums(package_dir, rel_path, "control/sidecar_manifest.json")
                    elif label == "result_validity":
                        payload["results"][0]["object_kind"] = "unknown"
                        self._write_json(target_path, payload)
                        self._refresh_manifest_checksums(package_dir, rel_path)
                    else:
                        self.fail(f"unexpected json scenario: {label}")

                repro = ReproService(DatasetRepository(), ContextStore())
                opened = repro.repro_OpenPackage(str(package_dir))
                self.assertFalse(opened.ok)
                self.assertEqual(opened.code, "INVALID_ARG")
                self.assertTrue(opened.message.startswith(f"evidence schema invalid: {label}:"))
                self.assertNotIn("checksum mismatch", opened.message)
                self.assertNotIn("invalid proof bundle", opened.message)

    def test_repro_accepts_legacy_fixture_missing_p13_fields_after_checksums_refresh(self) -> None:
        fixture_root = Path(__file__).resolve().parents[2] / "docs" / "evidence-fixture" / "exact-package"
        package_dir = self.root / "legacy-exact-package"
        shutil.copytree(fixture_root, package_dir)

        proof_digest_path = package_dir / "control" / "proof_digest.json"
        proof_digest = json.loads(proof_digest_path.read_text(encoding="utf-8"))
        proof_digest.pop("round_count", None)
        proof_digest.pop("window_hit_rate", None)
        proof_digest_path.write_text(json.dumps(proof_digest, ensure_ascii=False, indent=2), encoding="utf-8")

        frontier_snapshot_path = package_dir / "control" / "frontier_snapshot.json"
        frontier_snapshot = json.loads(frontier_snapshot_path.read_text(encoding="utf-8"))
        frontier_snapshot.pop("freeze_round_id", None)
        frontier_snapshot.pop("pre_read_reject", None)
        frontier_snapshot.pop("pre_read_reject_round_id", None)
        frontier_snapshot_path.write_text(json.dumps(frontier_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

        self._refresh_sidecar_manifest_checksums(package_dir, "control/proof_digest.json", "control/frontier_snapshot.json")
        self._refresh_manifest_checksums(package_dir, "control/proof_digest.json", "control/frontier_snapshot.json", "control/sidecar_manifest.json")

        repro = ReproService(DatasetRepository(), ContextStore())
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertTrue(opened.ok, opened.message)


if __name__ == "__main__":
    unittest.main()
