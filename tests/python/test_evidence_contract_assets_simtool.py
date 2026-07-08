from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from typing import Any

from parser.evidence_models import evd_RecomputeProofHash
from spec.schema_loader import load_specs


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _fixture_root() -> Path:
    root = _workspace_root()
    candidates = [
        root / "docs" / "evidence-fixture",
        root / "sim tool" / "docs" / "evidence-fixture",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise AssertionError("evidence fixture root not found")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))
    return rows


def _python_types(type_name: str) -> tuple[type[Any], ...]:
    mapping: dict[str, tuple[type[Any], ...]] = {
        "object": (dict,),
        "array": (list,),
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "null": (type(None),),
    }
    return mapping[type_name]


def _assert_schema(schema: dict[str, Any], value: Any, *, label: str) -> None:
    if "oneOf" in schema:
        failures: list[str] = []
        for candidate in schema["oneOf"]:
            try:
                _assert_schema(candidate, value, label=label)
                return
            except AssertionError as exc:
                failures.append(str(exc))
        raise AssertionError(f"{label}: oneOf mismatch: {failures}")

    schema_type = schema.get("type")
    if schema_type is not None:
        type_options = schema_type if isinstance(schema_type, list) else [schema_type]
        if not any(isinstance(value, _python_types(option)) for option in type_options):
            raise AssertionError(f"{label}: expected {type_options}, got {type(value).__name__}")

    if "enum" in schema:
        if value not in schema["enum"]:
            raise AssertionError(f"{label}: expected one of {schema['enum']}, got {value!r}")

    if isinstance(value, bool):
        numeric_value: float | None = None
    elif isinstance(value, (int, float)):
        numeric_value = float(value)
    else:
        numeric_value = None
    if numeric_value is not None and "minimum" in schema and numeric_value < float(schema["minimum"]):
        raise AssertionError(f"{label}: value {value!r} below minimum {schema['minimum']!r}")
    if numeric_value is not None and "maximum" in schema and numeric_value > float(schema["maximum"]):
        raise AssertionError(f"{label}: value {value!r} above maximum {schema['maximum']!r}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise AssertionError(f"{label}: missing keys {missing}")
        properties = schema.get("properties", {})
        for key, child in properties.items():
            if key in value:
                _assert_schema(child, value[key], label=f"{label}.{key}")
        additional = schema.get("additionalProperties", True)
        if additional is False:
            extras = sorted(set(value).difference(properties))
            if extras:
                raise AssertionError(f"{label}: unexpected keys {extras}")
        elif isinstance(additional, dict):
            for key in set(value).difference(properties):
                _assert_schema(additional, value[key], label=f"{label}.{key}")

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _assert_schema(schema["items"], item, label=f"{label}[{index}]")


def _file_count(path: Path, rel_path: str) -> int:
    if rel_path.endswith(".jsonl"):
        return len(_load_jsonl(path))
    if rel_path == "event/events.trace":
        return len(_load_json(path.parent / "ref_index.json"))
    if rel_path.endswith(".json"):
        payload = _load_json(path)
        if rel_path == "result/result_validity.json":
            return len(payload.get("results", []))
        if isinstance(payload, list):
            return len(payload)
        return 1
    return 1


class EvidenceContractAssetTests(unittest.TestCase):
    def test_schema_loader_includes_frozen_evidence_schemas(self) -> None:
        specs = load_specs()
        self.assertTrue(
            {
                "dependency_sidecar",
                "frontier_snapshot",
                "frontier_refs",
                "proof_digest",
                "sidecar_manifest",
                "sidecar_segment_manifest",
                "blocker_artifact",
                "result_validity",
                "sidecar_index_ticket",
                "telemetry_record",
                "telemetry_record_update",
                "advisor_decision",
                "advisor_trace",
                "advisor_report",
                "runtime_action",
                "runtime_action_set",
                "advisor_grounding_report",
                "validation_gate_result",
                "agent_job_contract",
                "export_write_metric",
                "write_failure_blocker",
                "parser_process_artifact",
                "formal_schedule_plan",
                "benchmark_scenario",
                "benchmark_report",
            }.issubset(specs.keys())
        )
        runtime_schema_ref_keys = {
            "sidecar_index_ticket_schema",
            "sidecar_segment_manifest_schema",
            "telemetry_record_schema",
            "telemetry_record_update_schema",
            "advisor_decision_schema",
            "advisor_trace_schema",
            "advisor_report_schema",
            "runtime_action_schema",
            "runtime_action_set_schema",
            "advisor_grounding_report_schema",
            "validation_gate_result_schema",
            "agent_job_contract_schema",
            "export_write_metric_schema",
            "write_failure_blocker_schema",
            "parser_process_artifact_schema",
            "formal_schedule_plan_schema",
            "benchmark_scenario_schema",
            "benchmark_report_schema",
        }
        for spec_name in ("meta", "manifest"):
            with self.subTest(schema=spec_name):
                schema_ref_props = specs[spec_name]["properties"]["schema_ref"]["properties"]
                self.assertTrue(runtime_schema_ref_keys.issubset(set(schema_ref_props)))
                self.assertTrue(runtime_schema_ref_keys.isdisjoint(set(specs[spec_name]["properties"]["schema_ref"].get("required", []))))
        action_props = specs["runtime_action"]["properties"]
        self.assertEqual(action_props["action_kind"]["type"], "string")
        self.assertEqual(action_props["proof_scope_impact"]["type"], "string")
        self.assertEqual(set(action_props["fallback_action"]["type"]), {"string", "null"})
        self.assertIn("abstain", action_props["action_kind"]["enum"])
        self.assertEqual(action_props["proof_scope_impact"]["enum"], ["none"])
        action_item_props = specs["runtime_action_set"]["properties"]["proposed_actions"]["items"]["properties"]
        self.assertEqual(action_item_props["action_kind"]["type"], "string")
        self.assertEqual(action_item_props["proof_scope_impact"]["type"], "string")
        self.assertEqual(set(action_item_props["fallback_action"]["type"]), {"string", "null"})
        self.assertIn("baseline_full_load", action_item_props["action_kind"]["enum"])
        self.assertEqual(action_item_props["proof_scope_impact"]["enum"], ["none"])

        known_action = {
            "action_id": "action:test:known",
            "action_kind": "cold_preview",
            "required_artifacts": [],
            "expected_benefit": {"runtime_seconds_delta": -1.0, "peak_rss_mb_delta": None, "notes": ["known action"]},
            "risk_level": "medium",
            "proof_scope_impact": "none",
            "fallback_action": None,
        }
        permissive_action = {
            "action_id": "action:test:unknown",
            "action_kind": "hallucinated_action_kind",
            "required_artifacts": ["control/unknown.json"],
            "expected_benefit": {"runtime_seconds_delta": None, "peak_rss_mb_delta": None, "notes": ["gate should reject later"]},
            "risk_level": "high",
            "proof_scope_impact": "mutates_proof_facts",
            "fallback_action": "other_unknown_action",
        }
        _assert_schema(specs["runtime_action"], known_action, label="runtime_action.known")
        with self.assertRaises(AssertionError):
            _assert_schema(specs["runtime_action"], permissive_action, label="runtime_action.permissive")
        with self.assertRaises(AssertionError):
            _assert_schema(
                specs["runtime_action_set"],
                {
                    "action_set_version": "runtime-action-set-v1",
                    "generated_at": "2026-07-06T00:00:00+00:00",
                    "proposed_actions": [permissive_action],
                    "abstained": False,
                    "abstain_reason": None,
                },
                label="runtime_action_set.permissive",
            )

    def test_dependency_sidecar_schema_freezes_kind_and_relation_enums(self) -> None:
        schema = load_specs()["dependency_sidecar"]
        properties = schema.get("properties", {})
        self.assertEqual(properties.get("src_kind", {}).get("enum"), ["ref", "object"])
        self.assertEqual(properties.get("dst_kind", {}).get("enum"), ["ref", "object"])
        self.assertEqual(
            properties.get("rule_family", {}).get("enum"),
            ["ref_ref", "ref_alert", "ref_diagnosis", "ref_anchor", "ref_object"],
        )
        self.assertEqual(
            properties.get("relation_kind", {}).get("enum"),
            [
                "ref_index_next",
                "ref_index_prev",
                "slice_start",
                "slice_end",
                "slice_start_event",
                "slice_end_event",
                "state_cause",
                "state_cause_event",
                "resource_wait",
                "resource_wait_event",
                "resource_hold",
                "resource_hold_event",
                "alert_evidence",
                "alert_event",
                "diagnosis_evidence",
                "diagnosis_event",
                "anchor_evidence",
                "anchor_event",
                "context_anchor",
                "context_anchor_event",
            ],
        )

    def test_p13_schema_contract_extensions_are_present_and_legacy_compatible(self) -> None:
        specs = load_specs()

        proof_props = specs["proof_digest"]["properties"]
        self.assertIn("round_count", proof_props)
        self.assertEqual(proof_props["round_count"]["type"], "integer")
        self.assertIn("window_hit_rate", proof_props)
        self.assertEqual(proof_props["window_hit_rate"]["type"], "number")
        self.assertIn("peak_rss_mb", proof_props)
        self.assertEqual(set(proof_props["peak_rss_mb"]["type"]), {"number", "null"})
        self.assertNotIn("round_count", specs["proof_digest"].get("required", []))
        self.assertNotIn("window_hit_rate", specs["proof_digest"].get("required", []))
        self.assertNotIn("peak_rss_mb", specs["proof_digest"].get("required", []))

        sidecar_manifest_props = specs["sidecar_manifest"]["properties"]
        self.assertIn("layout_mode", sidecar_manifest_props)
        self.assertIn("segment_manifest_path", sidecar_manifest_props)
        self.assertIn("segment_count", sidecar_manifest_props)
        self.assertNotIn("layout_mode", specs["sidecar_manifest"].get("required", []))
        self.assertNotIn("segment_manifest_path", specs["sidecar_manifest"].get("required", []))
        self.assertNotIn("segment_count", specs["sidecar_manifest"].get("required", []))

        segment_manifest_props = specs["sidecar_segment_manifest"]["properties"]
        self.assertIn("segments", segment_manifest_props)
        segment_item_props = segment_manifest_props["segments"]["items"]["properties"]
        self.assertTrue(
            {
                "segment_id",
                "time_begin",
                "time_end",
                "core_ids",
                "event_count",
                "row_count",
                "sidecar_path",
                "index_path",
                "checksum",
                "schema_version",
            }.issubset(segment_item_props.keys())
        )

        frontier_props = specs["frontier_snapshot"]["properties"]
        self.assertIn("freeze_round_id", frontier_props)
        self.assertIn("pre_read_reject", frontier_props)
        self.assertIn("pre_read_reject_round_id", frontier_props)
        self.assertNotIn("freeze_round_id", specs["frontier_snapshot"].get("required", []))
        self.assertNotIn("pre_read_reject", specs["frontier_snapshot"].get("required", []))
        self.assertNotIn("pre_read_reject_round_id", specs["frontier_snapshot"].get("required", []))

        blocker_props = specs["blocker_artifact"]["properties"]
        self.assertIn("oneOf", blocker_props["window_plan_summary"])
        self.assertIn("oneOf", blocker_props["emitted_metrics"])
        window_shapes = blocker_props["window_plan_summary"]["oneOf"]
        emitted_shapes = blocker_props["emitted_metrics"]["oneOf"]
        self.assertTrue(any({"planned_windows", "total_span_ns"}.issubset(set(shape.get("required", []))) for shape in window_shapes))
        self.assertTrue(any({"window_count", "window_hit_rate"}.issubset(set(shape.get("required", []))) for shape in window_shapes))
        self.assertTrue(any({"events_emitted", "bytes_emitted", "sidecar_lookup_count"}.issubset(set(shape.get("required", []))) for shape in emitted_shapes))
        self.assertTrue(any({"scan_count", "read_window_hit_rate"}.issubset(set(shape.get("required", []))) for shape in emitted_shapes))

    def test_minimal_packages_validate_against_frozen_contracts(self) -> None:
        specs = load_specs()
        fixture_root = _fixture_root()
        fixture_manifest = _load_json(fixture_root / "validation_fixture_manifest.json")
        packages = fixture_manifest["packages"]

        for package_name, descriptor in packages.items():
            with self.subTest(package=package_name):
                package_root = fixture_root / package_name
                meta = _load_json(package_root / "meta.json")
                manifest = _load_json(package_root / "manifest.json")
                analysis_context = _load_json(package_root / "context" / "analysis_context.json")
                compare_scope = _load_json(package_root / "context" / "compare_scope.json")
                proof_digest = _load_json(package_root / "control" / "proof_digest.json")
                frontier_snapshot = _load_json(package_root / "control" / "frontier_snapshot.json")
                sidecar_manifest = _load_json(package_root / "control" / "sidecar_manifest.json")
                alerts = _load_json(package_root / "result" / "alerts.json")
                diagnoses = _load_json(package_root / "result" / "diagnoses.json")
                result_validity = _load_json(package_root / "result" / "result_validity.json")
                sidecar_rows = _load_jsonl(package_root / "control" / "dependency_sidecar.jsonl")

                _assert_schema(specs["meta"], meta, label=f"{package_name}.meta")
                _assert_schema(specs["manifest"], manifest, label=f"{package_name}.manifest")
                _assert_schema(specs["analysis_context"], analysis_context, label=f"{package_name}.analysis_context")
                _assert_schema(specs["compare_scope"], compare_scope, label=f"{package_name}.compare_scope")
                _assert_schema(specs["frontier_snapshot"], frontier_snapshot, label=f"{package_name}.frontier_snapshot")
                _assert_schema(specs["proof_digest"], proof_digest, label=f"{package_name}.proof_digest")
                _assert_schema(specs["sidecar_manifest"], sidecar_manifest, label=f"{package_name}.sidecar_manifest")
                _assert_schema(specs["result_validity"], result_validity, label=f"{package_name}.result_validity")
                for index, row in enumerate(sidecar_rows):
                    _assert_schema(specs["dependency_sidecar"], row, label=f"{package_name}.dependency_sidecar[{index}]")

                # Freeze P1-3 extension fields into the contract assets (still legacy-compatible at schema level).
                self.assertIn("round_count", proof_digest)
                self.assertIn("window_hit_rate", proof_digest)
                self.assertIn("freeze_round_id", frontier_snapshot)
                self.assertIn("pre_read_reject", frontier_snapshot)
                self.assertIn("pre_read_reject_round_id", frontier_snapshot)

                # proof_hash must be the recomputed digest over the frozen proof hash boundary fields.
                self.assertEqual(proof_digest.get("proof_hash"), evd_RecomputeProofHash(proof_digest))

                frontier_refs_path = package_root / "control" / "frontier_refs.jsonl"
                if frontier_refs_path.exists():
                    for index, row in enumerate(_load_jsonl(frontier_refs_path)):
                        _assert_schema(specs["frontier_refs"], row, label=f"{package_name}.frontier_refs[{index}]")

                blocker_path = package_root / "control" / "blocker_artifact.json"
                if blocker_path.exists():
                    _assert_schema(
                        specs["blocker_artifact"],
                        _load_json(blocker_path),
                        label=f"{package_name}.blocker_artifact",
                    )

                self.assertEqual(meta["export_family"], "evidence")
                self.assertEqual(meta["export_mode"], "evidence")
                self.assertEqual(manifest["package_version"], "rttrace-package-2")
                self.assertEqual(descriptor["snapshot_id"], meta["snapshot_id"])
                self.assertEqual(descriptor["closure_mode"], proof_digest["closure_mode"])
                self.assertEqual(descriptor["manifest_sha256"], _sha256(package_root / "manifest.json"))
                self.assertEqual(descriptor["meta_sha256"], _sha256(package_root / "meta.json"))

                dict_path = package_root / meta["dict_ref"]["path"]
                self.assertTrue(dict_path.exists())
                self.assertEqual(meta["dict_ref"]["checksum"], _sha256(dict_path))

                for schema_key, ref_payload in meta["schema_ref"].items():
                    ref_path = package_root / ref_payload["path"]
                    self.assertTrue(ref_path.exists(), schema_key)
                    self.assertEqual(ref_payload["checksum"], _sha256(ref_path), schema_key)
                    self.assertEqual(manifest["schema_ref"][schema_key]["checksum"], ref_payload["checksum"])

                trace_checksum = _sha256(package_root / "event" / "events.trace")
                self.assertEqual(sidecar_manifest["trace_checksum"], trace_checksum)
                for row in sidecar_rows:
                    self.assertEqual(row["snapshot_id"], meta["snapshot_id"])
                    self.assertEqual(row["trace_checksum"], trace_checksum)

                dictionary_checksum = _sha256(package_root / "reference" / "dictionary.json")
                self.assertEqual(sidecar_manifest["dictionary_checksum"], dictionary_checksum)

                for schema_key, ref_payload in sidecar_manifest["schema_checksums"].items():
                    schema_path = package_root / ref_payload["path"]
                    self.assertEqual(ref_payload["checksum"], _sha256(schema_path), schema_key)

                for rel_path, checksum in sidecar_manifest["entry_checksums"].items():
                    self.assertEqual(checksum, _sha256(package_root / rel_path), rel_path)

                manifest_entries = {entry["path"]: entry for entry in manifest["entries"]}
                for rel_path, entry in manifest_entries.items():
                    path = package_root / rel_path
                    self.assertTrue(path.exists(), rel_path)
                    self.assertEqual(entry["checksum"], _sha256(path), rel_path)
                    self.assertEqual(entry["count"], _file_count(path, rel_path), rel_path)
                    if entry["category"] == "control":
                        self.assertIsNotNone(entry["schema_ref"], rel_path)

                self.assertIn("result/alerts.json", manifest_entries)
                self.assertIn("result/diagnoses.json", manifest_entries)
                self.assertIn("result/result_validity.json", manifest_entries)
                self.assertEqual(manifest_entries["result/result_validity.json"]["schema_ref"], "result_validity_schema")

                alert_ids = {str(row.get("alert_id")) for row in alerts}
                diagnosis_ids = {str(row.get("diag_id")) for row in diagnoses}
                for row in result_validity.get("results", []):
                    self.assertIn(row["object_kind"], {"alert", "diagnosis"})
                    self.assertTrue(row["object_id"])
                    if row["object_kind"] == "alert":
                        self.assertEqual(row["path"], "result/alerts.json")
                        self.assertIn(row["object_id"], alert_ids)
                    else:
                        self.assertEqual(row["path"], "result/diagnoses.json")
                        self.assertIn(row["object_id"], diagnosis_ids)

    def test_mode_specific_package_invariants(self) -> None:
        fixture_root = _fixture_root()

        exact_root = fixture_root / "exact-package"
        self.assertTrue(exact_root.exists())
        exact_proof = _load_json(exact_root / "control" / "proof_digest.json")
        exact_meta = _load_json(exact_root / "meta.json")
        self.assertEqual(exact_proof["closure_mode"], "exact")
        self.assertEqual(exact_meta.get("experiment_params", {}).get("fixture"), "exact")
        self.assertFalse((exact_root / "control" / "frontier_refs.jsonl").exists())
        self.assertFalse((exact_root / "control" / "blocker_artifact.json").exists())

        exact_mode_b_root = fixture_root / "exact-mode-b-package"
        self.assertTrue(exact_mode_b_root.exists())
        exact_mode_b_proof = _load_json(exact_mode_b_root / "control" / "proof_digest.json")
        exact_mode_b_meta = _load_json(exact_mode_b_root / "meta.json")
        self.assertEqual(exact_mode_b_proof["closure_mode"], "exact")
        self.assertEqual(exact_mode_b_meta.get("experiment_params", {}).get("fixture"), "exact-mode-b")
        self.assertFalse((exact_mode_b_root / "control" / "frontier_refs.jsonl").exists())
        self.assertFalse((exact_mode_b_root / "control" / "blocker_artifact.json").exists())

        bounded_root = fixture_root / "bounded-package"
        self.assertTrue(bounded_root.exists())
        bounded_proof = _load_json(bounded_root / "control" / "proof_digest.json")
        bounded_snapshot = _load_json(bounded_root / "control" / "frontier_snapshot.json")
        self.assertEqual(bounded_proof["closure_mode"], "bounded")
        self.assertTrue((bounded_root / "control" / "frontier_refs.jsonl").exists())
        self.assertGreater(bounded_snapshot["truncated_frontier_count"], 0)

        degraded_root = fixture_root / "degraded-package"
        self.assertTrue(degraded_root.exists())
        degraded_proof = _load_json(degraded_root / "control" / "proof_digest.json")
        self.assertEqual(degraded_proof["closure_mode"], "degraded")
        self.assertEqual(degraded_proof["events_emitted"], 0)
        self.assertTrue((degraded_root / "control" / "blocker_artifact.json").exists())
        self.assertEqual((degraded_root / "event" / "events.trace").read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
