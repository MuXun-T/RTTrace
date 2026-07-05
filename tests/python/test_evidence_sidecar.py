from __future__ import annotations

from dataclasses import asdict, replace
import json
import tempfile
import unittest
from pathlib import Path

from parser import encode_trace, load_dataset
from parser.evidence_sidecar import (
    ALLOWED_DST_KINDS,
    ALLOWED_RELATION_KINDS,
    ALLOWED_RULE_FAMILIES,
    ALLOWED_SRC_KINDS,
    REQUIRED_SIDECAR_SCHEMA_KEYS,
    build_sidecar_segment_manifest,
    build_dependency_sidecar,
    load_dependency_sidecar,
    materialize_dependency_sidecar,
    partition_dependency_sidecar_segments,
    select_candidate_edges,
    select_candidate_edges_from_sidecar,
    validate_dependency_sidecar_stream,
    validate_sidecar_payload,
    validate_sidecar_segment_manifest_metadata,
    validate_sidecar_manifest,
)
from parser.evidence_sidecar_index import (
    DEFAULT_SIDECAR_STREAM_SCAN_MAX_BYTES,
    build_or_open_sidecar_index,
    select_candidate_edges_from_segment_manifest,
    select_candidate_edges_from_index,
    read_sidecar_index_ticket,
    sidecar_index_ticket_path_for_source,
    sidecar_stream_scan_fallback_allowed,
    validate_sidecar_index_ticket,
)
from parser.models import Alert, Diagnosis, EvidenceRef
from spec.events import event_id_for
from spec.io import checksum_file


class EvidenceSidecarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _sidecar_fixture(self) -> tuple[object, list[Alert], list[Diagnosis], list[dict[str, object]], dict[str, object], list[dict[str, object]]]:
        trace_path = self.root / "sidecar-fixture.trace"
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
        loaded = load_dataset(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        bundle = loaded.data.bundle
        event_refs = [
            EvidenceRef(
                ref_type="event",
                ref_key=event.ref_key,
                t_begin=float(event.timestamp_aligned),
                t_end=float(event.timestamp_aligned),
            )
            for event in bundle.event_stream[:2]
        ]
        alerts = [
            Alert(
                alert_id="alert:test",
                type="latency",
                severity="warning",
                time_window=(event_refs[0].t_begin, event_refs[-1].t_end),
                object_scope={"task_id": 1},
                threshold=1.0,
                actual=2.0,
                evidence_refs=list(event_refs),
                trusted=True,
            )
        ]
        diagnoses = [
            Diagnosis(
                diag_id="diag:test",
                title="diag",
                diagnosis_type="root_cause",
                time_window=(event_refs[0].t_begin, event_refs[-1].t_end),
                object_scope={"task_id": 1},
                conclusion="rooted",
                evidence_refs=list(event_refs),
                related_alerts=["alert:test"],
                confidence="high",
            )
        ]
        anchors = [
            {
                "anchor_id": "anchor:test",
                "anchor_type": "manual",
                "evidence_anchor": {"ref_key": event_refs[0].ref_key},
                "time_window": [event_refs[0].t_begin, event_refs[0].t_end],
            }
        ]
        context = {
            "evidence_anchor": {"ref_key": event_refs[0].ref_key},
        }
        ref_index_rows = [
            {
                "ref_key": event.ref_key,
                "timestamp_aligned": float(event.timestamp_aligned),
                "core_id": int(event.core_id),
                "seq": int(event.seq),
            }
            for event in bundle.event_stream
        ]
        return bundle, alerts, diagnoses, anchors, context, ref_index_rows

    def _build_rows(self) -> list[object]:
        bundle, alerts, diagnoses, anchors, context, ref_index_rows = self._sidecar_fixture()
        rows = build_dependency_sidecar(
            bundle,
            snapshot_id="snapshot:test:sidecar",
            rule_families=("ref_ref", "ref_alert", "ref_diagnosis", "ref_anchor", "ref_object"),
            alerts=alerts,
            diagnoses=diagnoses,
            context=context,
            anchors=anchors,
            ref_index_rows=ref_index_rows,
        )
        self.assertGreater(len(rows), 0)
        return rows

    def _write_sidecar(self, path: Path, rows: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _stream_manifest(self, path: Path, *, snapshot_id: str, trace_checksum: str) -> dict[str, object]:
        return {
            "snapshot_id": snapshot_id,
            "trace_checksum": trace_checksum,
            "dictionary_checksum": "dict:test",
            "schema_checksums": {key: {} for key in REQUIRED_SIDECAR_SCHEMA_KEYS},
            "entry_paths": ["control/dependency_sidecar.jsonl"],
            "entry_checksums": {
                "control/dependency_sidecar.jsonl": checksum_file(path),
            },
        }

    def _write_segmented_sidecar_manifest(
        self,
        *,
        rows: list[object],
        name: str,
    ) -> tuple[dict[str, object], dict[str, Path]]:
        segments = partition_dependency_sidecar_segments(rows)
        segment_paths: dict[str, Path] = {}
        sidecar_path_by_segment: dict[str, str] = {}
        index_path_by_segment: dict[str, str] = {}
        ticket_path_by_segment: dict[str, str] = {}
        checksum_by_segment: dict[str, str] = {}
        for segment in segments:
            token = segment.segment_id.replace(":", "_")
            sidecar_rel = f"{name}/segments/{token}/dependency_sidecar.jsonl"
            sidecar_path = self.root / sidecar_rel
            self._write_sidecar(sidecar_path, [asdict(row) for row in segment.rows])
            segment_paths[segment.segment_id] = sidecar_path
            sidecar_path_by_segment[segment.segment_id] = sidecar_rel
            index_path_by_segment[segment.segment_id] = f"{sidecar_rel}.sqlite3"
            ticket_path_by_segment[segment.segment_id] = f"{sidecar_rel}.sqlite3.ticket.json"
            checksum_by_segment[segment.segment_id] = checksum_file(sidecar_path)
        manifest = build_sidecar_segment_manifest(
            rows,
            segment_dir=f"{name}/segments",
            snapshot_id="snapshot:test:sidecar",
            trace_checksum="trace:test",
            dictionary_checksum="dict:test",
            created_at="2026-06-30T00:00:00+00:00",
            sidecar_path_by_segment=sidecar_path_by_segment,
            index_path_by_segment=index_path_by_segment,
            checksum_by_segment=checksum_by_segment,
            ticket_path_by_segment=ticket_path_by_segment,
        )
        return manifest, segment_paths

    def test_build_dependency_sidecar_covers_documented_source_matrix(self) -> None:
        bundle, alerts, diagnoses, anchors, context, ref_index_rows = self._sidecar_fixture()
        self.assertGreater(len(bundle.exec_slices), 0)
        self.assertGreater(len(bundle.task_states), 0)
        self.assertGreater(len(bundle.resource_graph.wait_edges), 0)
        self.assertGreater(len(bundle.resource_graph.hold_edges), 0)

        rows = build_dependency_sidecar(
            bundle,
            snapshot_id="snapshot:test:sidecar",
            rule_families=("ref_ref", "ref_alert", "ref_diagnosis", "ref_anchor", "ref_object"),
            alerts=alerts,
            diagnoses=diagnoses,
            context=context,
            anchors=anchors,
            ref_index_rows=ref_index_rows,
        )

        self.assertGreater(len(rows), 0)
        provenances = {row.provenance for row in rows}
        self.assertTrue(
            {
                "ref_index_row",
                "exec_slice_boundary",
                "task_state_cause",
                "resource_wait_edge",
                "resource_hold_edge",
                "alert_evidence",
                "diagnosis_evidence",
                "anchor_evidence",
                "analysis_context_anchor",
            }.issubset(provenances)
        )
        self.assertTrue(any(row.src_kind == "object" or row.dst_kind == "object" for row in rows))
        self.assertTrue(all(row.cycle_guard_token for row in rows))
        self.assertTrue(all(row.estimate_events > 0 for row in rows))
        self.assertTrue(all(row.estimate_bytes > 0 for row in rows))

    def test_build_dependency_sidecar_uses_fixed_estimate_rules(self) -> None:
        bundle, _, _, _, _, _ = self._sidecar_fixture()
        rows = self._build_rows()

        alert_rows = [row for row in rows if row.relation_kind in {"alert_evidence", "alert_event"}]
        diagnosis_rows = [row for row in rows if row.relation_kind in {"diagnosis_evidence", "diagnosis_event"}]
        ref_rows = [row for row in rows if row.relation_kind == "ref_index_next"]
        slice_row = next(
            row
            for row in rows
            if row.relation_kind == "slice_start" and row.dst_ref == f"slice:{bundle.exec_slices[0].slice_id}"
        )
        state_row = next(
            row
            for row in rows
            if row.relation_kind == "state_cause" and row.dst_ref == f"state:{bundle.task_states[0].seg_id}"
        )
        self.assertTrue(alert_rows)
        self.assertTrue(diagnosis_rows)
        self.assertTrue(ref_rows)
        self.assertTrue(all(row.estimate_events == 2 for row in alert_rows))
        self.assertTrue(all(row.estimate_events == 2 for row in diagnosis_rows))
        self.assertTrue(all(row.estimate_events == 1 for row in ref_rows))
        unit = ref_rows[0].estimate_bytes
        self.assertGreater(unit, 0)
        self.assertTrue(all(row.estimate_bytes == row.estimate_events * unit for row in rows))
        self.assertEqual(slice_row.time_hint_begin_ns, int(bundle.exec_slices[0].t_begin))
        self.assertEqual(slice_row.time_hint_end_ns, int(bundle.exec_slices[0].t_end))
        self.assertEqual(state_row.time_hint_begin_ns, int(bundle.task_states[0].t_begin))
        self.assertEqual(state_row.time_hint_end_ns, int(bundle.task_states[0].t_end))

    def test_build_dependency_sidecar_explicit_ref_index_rows_match_default_path(self) -> None:
        bundle, alerts, diagnoses, anchors, context, ref_index_rows = self._sidecar_fixture()

        explicit_rows = build_dependency_sidecar(
            bundle,
            snapshot_id="snapshot:test:sidecar",
            rule_families=("ref_ref", "ref_alert", "ref_diagnosis", "ref_anchor", "ref_object"),
            alerts=alerts,
            diagnoses=diagnoses,
            context=context,
            anchors=anchors,
            ref_index_rows=ref_index_rows,
        )
        default_rows = build_dependency_sidecar(
            bundle,
            snapshot_id="snapshot:test:sidecar",
            rule_families=("ref_ref", "ref_alert", "ref_diagnosis", "ref_anchor", "ref_object"),
            alerts=alerts,
            diagnoses=diagnoses,
            context=context,
            anchors=anchors,
        )

        self.assertEqual(
            [asdict(row) for row in explicit_rows],
            [asdict(row) for row in default_rows],
        )

    def test_build_dependency_sidecar_ref_index_rows_keep_output_stable_for_unsorted_event_stream(self) -> None:
        bundle, alerts, diagnoses, anchors, context, ref_index_rows = self._sidecar_fixture()
        stable_rows = build_dependency_sidecar(
            bundle,
            snapshot_id="snapshot:test:sidecar",
            rule_families=("ref_ref", "ref_alert", "ref_diagnosis", "ref_anchor", "ref_object"),
            alerts=alerts,
            diagnoses=diagnoses,
            context=context,
            anchors=anchors,
            ref_index_rows=ref_index_rows,
        )
        shuffled_bundle = replace(bundle, event_stream=list(reversed(bundle.event_stream)))
        shuffled_rows = build_dependency_sidecar(
            shuffled_bundle,
            snapshot_id="snapshot:test:sidecar",
            rule_families=("ref_ref", "ref_alert", "ref_diagnosis", "ref_anchor", "ref_object"),
            alerts=alerts,
            diagnoses=diagnoses,
            context=context,
            anchors=anchors,
            ref_index_rows=ref_index_rows,
        )

        self.assertEqual(
            [asdict(row) for row in shuffled_rows],
            [asdict(row) for row in stable_rows],
        )

    def test_load_dependency_sidecar_fail_closes_on_contract_violations(self) -> None:
        rows = self._build_rows()
        base_row = asdict(rows[0])
        path = self.root / "invalid-sidecar.jsonl"

        bad_provenance = dict(base_row)
        bad_provenance["provenance"] = ""
        self._write_sidecar(path, [bad_provenance])
        loaded = load_dependency_sidecar(path)
        self.assertFalse(loaded.ok)
        self.assertEqual(loaded.code, "SIDECAR_MISMATCH")
        self.assertIn("provenance", loaded.message)

        bad_estimate = dict(base_row)
        bad_estimate["estimate_events"] = 0
        self._write_sidecar(path, [bad_estimate])
        loaded = load_dependency_sidecar(path)
        self.assertFalse(loaded.ok)
        self.assertEqual(loaded.code, "SIDECAR_MISMATCH")
        self.assertIn("estimate_events", loaded.message)

        bad_hints = dict(base_row)
        bad_hints["segment_hint"] = None
        bad_hints["core_hint"] = None
        bad_hints["seq_hint_begin"] = None
        bad_hints["seq_hint_end"] = None
        self._write_sidecar(path, [bad_hints])
        loaded = load_dependency_sidecar(path)
        self.assertFalse(loaded.ok)
        self.assertEqual(loaded.code, "SIDECAR_MISMATCH")
        self.assertIn("hints", loaded.message)

        bad_priority = dict(base_row)
        bad_priority["priority"] = 101
        self._write_sidecar(path, [bad_priority])
        loaded = load_dependency_sidecar(path)
        self.assertFalse(loaded.ok)
        self.assertEqual(loaded.code, "SIDECAR_MISMATCH")
        self.assertIn("priority", loaded.message)

    def test_select_candidate_edges_from_sidecar_matches_in_memory_order(self) -> None:
        rows = materialize_dependency_sidecar(self._build_rows(), trace_checksum="trace:test")
        path = self.root / "stream-select-sidecar.jsonl"
        self._write_sidecar(path, [asdict(row) for row in rows])
        manifest = self._stream_manifest(
            path,
            snapshot_id="snapshot:test:sidecar",
            trace_checksum="trace:test",
        )
        validated = validate_dependency_sidecar_stream(
            path,
            manifest,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
        )
        self.assertTrue(validated.ok, validated.message)

        frontier_refs = sorted({row.src_ref for row in rows if row.rule_family in {"ref_ref", "ref_object"}})[:4]
        expected = select_candidate_edges(rows, frontier_refs, ("ref_ref", "ref_object"))
        selected = select_candidate_edges_from_sidecar(
            path,
            frontier_refs,
            ("ref_ref", "ref_object"),
            file_fingerprint=validated.data["file_fingerprint"],
        )
        self.assertTrue(selected.ok, selected.message)
        self.assertEqual([row.edge_hash for row in expected], [row.edge_hash for row in selected.data])

    def test_select_candidate_edges_from_index_matches_in_memory_order(self) -> None:
        rows = materialize_dependency_sidecar(self._build_rows(), trace_checksum="trace:test")
        path = self.root / "indexed-select-sidecar.jsonl"
        self._write_sidecar(path, [asdict(row) for row in rows])
        manifest = self._stream_manifest(
            path,
            snapshot_id="snapshot:test:sidecar",
            trace_checksum="trace:test",
        )
        validated = validate_dependency_sidecar_stream(
            path,
            manifest,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
        )
        self.assertTrue(validated.ok, validated.message)

        indexed = build_or_open_sidecar_index(
            path,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
            sidecar_checksum=validated.data["checksum"],
            file_fingerprint=validated.data["file_fingerprint"],
            rebuild_on_mismatch=True,
        )
        self.assertTrue(indexed.ok, indexed.message)

        frontier_refs = sorted({row.src_ref for row in rows if row.rule_family in {"ref_ref", "ref_object"}})[:4]
        expected = select_candidate_edges(rows, frontier_refs, ("ref_ref", "ref_object"))
        selected = select_candidate_edges_from_index(
            indexed.data,
            frontier_refs,
            ("ref_ref", "ref_object"),
            expected_sidecar_checksum=validated.data["checksum"],
            expected_file_fingerprint=validated.data["file_fingerprint"],
        )
        self.assertTrue(selected.ok, selected.message)
        self.assertEqual([row.edge_hash for row in expected], [row.edge_hash for row in selected.data])

    def test_sidecar_index_rejects_changed_sidecar_file(self) -> None:
        rows = materialize_dependency_sidecar(self._build_rows(), trace_checksum="trace:test")
        path = self.root / "indexed-invalidated-sidecar.jsonl"
        self._write_sidecar(path, [asdict(row) for row in rows])
        manifest = self._stream_manifest(
            path,
            snapshot_id="snapshot:test:sidecar",
            trace_checksum="trace:test",
        )
        validated = validate_dependency_sidecar_stream(
            path,
            manifest,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
        )
        self.assertTrue(validated.ok, validated.message)

        indexed = build_or_open_sidecar_index(
            path,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
            sidecar_checksum=validated.data["checksum"],
            file_fingerprint=validated.data["file_fingerprint"],
            rebuild_on_mismatch=True,
        )
        self.assertTrue(indexed.ok, indexed.message)

        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n")

        selected = select_candidate_edges_from_index(
            indexed.data,
            [rows[0].src_ref],
            (rows[0].rule_family,),
            expected_sidecar_checksum=validated.data["checksum"],
            expected_file_fingerprint=validated.data["file_fingerprint"],
        )
        self.assertFalse(selected.ok)
        self.assertEqual(selected.code, "SIDECAR_MISMATCH")
        self.assertIn("changed", selected.message)

    def test_sidecar_index_ticket_round_trip_and_validation(self) -> None:
        rows = materialize_dependency_sidecar(self._build_rows(), trace_checksum="trace:test")
        path = self.root / "ticket-sidecar.jsonl"
        self._write_sidecar(path, [asdict(row) for row in rows])
        manifest = self._stream_manifest(
            path,
            snapshot_id="snapshot:test:sidecar",
            trace_checksum="trace:test",
        )
        validated = validate_dependency_sidecar_stream(
            path,
            manifest,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
        )
        self.assertTrue(validated.ok, validated.message)

        indexed = build_or_open_sidecar_index(
            path,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
            sidecar_checksum=validated.data["checksum"],
            file_fingerprint=validated.data["file_fingerprint"],
            dictionary_checksum="dict:test",
            rebuild_on_mismatch=True,
        )
        self.assertTrue(indexed.ok, indexed.message)
        ticket_path = sidecar_index_ticket_path_for_source(path)
        loaded_ticket = read_sidecar_index_ticket(ticket_path)
        self.assertTrue(loaded_ticket.ok, loaded_ticket.message)
        self.assertEqual(loaded_ticket.data.snapshot_id, "snapshot:test:sidecar")
        self.assertEqual(loaded_ticket.data.dictionary_checksum, "dict:test")
        validated_ticket = validate_sidecar_index_ticket(
            loaded_ticket.data,
            sidecar_path=path,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
            expected_dictionary_checksum="dict:test",
            sidecar_checksum=validated.data["checksum"],
        )
        self.assertTrue(validated_ticket.ok, validated_ticket.message)

    def test_sidecar_index_ticket_reopen_keeps_ticket_checksum_stable(self) -> None:
        rows = materialize_dependency_sidecar(self._build_rows(), trace_checksum="trace:test")
        path = self.root / "stable-ticket-sidecar.jsonl"
        self._write_sidecar(path, [asdict(row) for row in rows])
        manifest = self._stream_manifest(
            path,
            snapshot_id="snapshot:test:sidecar",
            trace_checksum="trace:test",
        )
        validated = validate_dependency_sidecar_stream(
            path,
            manifest,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
        )
        self.assertTrue(validated.ok, validated.message)

        build_args = {
            "expected_snapshot_id": "snapshot:test:sidecar",
            "expected_trace_checksum": "trace:test",
            "sidecar_checksum": validated.data["checksum"],
            "file_fingerprint": validated.data["file_fingerprint"],
            "dictionary_checksum": "dict:test",
            "rebuild_on_mismatch": True,
        }
        first = build_or_open_sidecar_index(path, **build_args)
        self.assertTrue(first.ok, first.message)
        ticket_path = sidecar_index_ticket_path_for_source(path)
        ticket_checksum = checksum_file(ticket_path)
        ticket_text = ticket_path.read_text(encoding="utf-8")

        second = build_or_open_sidecar_index(path, **build_args)
        self.assertTrue(second.ok, second.message)
        self.assertFalse(second.data.built)
        self.assertEqual(checksum_file(ticket_path), ticket_checksum)
        self.assertEqual(ticket_path.read_text(encoding="utf-8"), ticket_text)

    def test_large_sidecar_gate_disables_stream_scan_fallback_by_default(self) -> None:
        self.assertTrue(sidecar_stream_scan_fallback_allowed(DEFAULT_SIDECAR_STREAM_SCAN_MAX_BYTES))
        self.assertFalse(sidecar_stream_scan_fallback_allowed(DEFAULT_SIDECAR_STREAM_SCAN_MAX_BYTES + 1))

    def test_validate_dependency_sidecar_stream_fail_closes_on_bad_rows(self) -> None:
        rows = materialize_dependency_sidecar(self._build_rows(), trace_checksum="trace:test")
        bad_row = asdict(rows[0])
        bad_row["estimate_events"] = 0
        path = self.root / "bad-stream-sidecar.jsonl"
        self._write_sidecar(path, [bad_row])
        manifest = self._stream_manifest(
            path,
            snapshot_id="snapshot:test:sidecar",
            trace_checksum="trace:test",
        )
        validated = validate_dependency_sidecar_stream(
            path,
            manifest,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
        )
        self.assertFalse(validated.ok)
        self.assertEqual(validated.code, "SIDECAR_MISMATCH")
        self.assertIn("estimate_events", validated.message)

    def test_build_sidecar_segment_manifest_keeps_checksums_and_legacy_manifest_compatibility(self) -> None:
        rows = materialize_dependency_sidecar(self._build_rows(), trace_checksum="trace:test")
        segmented_rows = [
            replace(row, segment_hint="segment:alpha") if index < len(rows) // 2 else replace(row, segment_hint="segment:beta")
            for index, row in enumerate(rows)
        ]
        manifest, segment_paths = self._write_segmented_sidecar_manifest(
            rows=segmented_rows,
            name="segmented-sidecar",
        )
        self.assertEqual(manifest["segment_count"], 2)
        self.assertEqual({segment["segment_id"] for segment in manifest["segments"]}, {"segment:alpha", "segment:beta"})
        for segment in manifest["segments"]:
            self.assertEqual(segment["event_count"], segment["row_count"])
            self.assertEqual(segment["checksum"], checksum_file(segment_paths[segment["segment_id"]]))
        validated_segmented = validate_sidecar_segment_manifest_metadata(
            manifest,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
            expected_dictionary_checksum="dict:test",
            manifest_root=self.root,
        )
        self.assertTrue(validated_segmented.ok, validated_segmented.message)

        legacy_manifest = {
            "snapshot_id": "snapshot:test:sidecar",
            "trace_checksum": "trace:test",
            "dictionary_checksum": "dict:test",
            "schema_checksums": {key: {} for key in REQUIRED_SIDECAR_SCHEMA_KEYS},
        }
        validated_legacy = validate_sidecar_manifest(
            segmented_rows,
            legacy_manifest,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
            expected_dictionary_checksum="dict:test",
        )
        self.assertTrue(validated_legacy.ok, validated_legacy.message)

    def test_validate_sidecar_segment_manifest_fail_closes_on_segment_contract_mismatch(self) -> None:
        rows = materialize_dependency_sidecar(self._build_rows(), trace_checksum="trace:test")
        segmented_rows = [replace(row, segment_hint="segment:alpha") for row in rows[:2]]
        manifest, segment_paths = self._write_segmented_sidecar_manifest(
            rows=segmented_rows,
            name="segmented-contract",
        )
        bad_version_manifest = json.loads(json.dumps(manifest))
        bad_version_manifest["segments"][0]["schema_version"] = "old"
        bad_version = validate_sidecar_segment_manifest_metadata(
            bad_version_manifest,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
            expected_dictionary_checksum="dict:test",
            manifest_root=self.root,
        )
        self.assertFalse(bad_version.ok)
        self.assertIn("schema_version", bad_version.message)

        first_segment = manifest["segments"][0]
        first_path = segment_paths[first_segment["segment_id"]]
        tampered_rows = [asdict(replace(segmented_rows[0], trace_checksum="trace:other"))]
        self._write_sidecar(first_path, tampered_rows)
        bad_trace_manifest = json.loads(json.dumps(manifest))
        bad_trace_manifest["segments"][0]["checksum"] = checksum_file(first_path)
        bad_trace = validate_sidecar_segment_manifest_metadata(
            bad_trace_manifest,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
            expected_dictionary_checksum="dict:test",
            manifest_root=self.root,
        )
        self.assertFalse(bad_trace.ok)
        self.assertIn("trace_checksum", bad_trace.message)

    def test_select_candidate_edges_from_segment_manifest_dedupes_and_builds_missing_segment_indexes(self) -> None:
        rows = materialize_dependency_sidecar(self._build_rows(), trace_checksum="trace:test")
        duplicated_edge = replace(rows[0], segment_hint="segment:alpha")
        duplicated_edge_copy = replace(rows[0], segment_hint="segment:beta")
        manifest, _ = self._write_segmented_sidecar_manifest(
            rows=[duplicated_edge, duplicated_edge_copy],
            name="segmented-selector",
        )

        selected = select_candidate_edges_from_segment_manifest(
            manifest,
            manifest_root=self.root,
            frontier_refs=[duplicated_edge.src_ref],
            rule_families=(duplicated_edge.rule_family,),
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
            expected_dictionary_checksum="dict:test",
            expected_segment_checksums={segment["segment_id"]: segment["checksum"] for segment in manifest["segments"]},
        )
        self.assertTrue(selected.ok, selected.message)
        self.assertEqual([row.edge_hash for row in selected.data], [duplicated_edge.edge_hash])
        for segment in manifest["segments"]:
            self.assertTrue((self.root / segment["index_path"]).exists())

    def test_build_dependency_sidecar_drops_contract_violating_edges_and_counts_rejections(self) -> None:
        bundle, alerts, diagnoses, anchors, context, ref_index_rows = self._sidecar_fixture()
        anchors = [
            *anchors,
            {
                "anchor_id": "anchor:bad",
                "anchor_type": "manual",
                "evidence_anchor": {"ref_key": "evt:missing"},
            },
        ]
        rows = build_dependency_sidecar(
            bundle,
            snapshot_id="snapshot:test:drop",
            rule_families=("ref_ref", "ref_alert", "ref_diagnosis", "ref_anchor", "ref_object"),
            alerts=alerts,
            diagnoses=diagnoses,
            context=context,
            anchors=anchors,
            ref_index_rows=ref_index_rows,
        )
        self.assertFalse(any(row.src_ref == "evt:missing" or row.dst_ref == "evt:missing" for row in rows))
        self.assertGreater(getattr(build_dependency_sidecar, "last_rejected_contract_count", 0), 0)

    def test_validate_sidecar_manifest_fail_closes_on_contract_violations(self) -> None:
        rows = materialize_dependency_sidecar(self._build_rows(), trace_checksum="trace:test")
        manifest = {
            "snapshot_id": "snapshot:test:sidecar",
            "trace_checksum": "trace:test",
            "dictionary_checksum": "dict:test",
            "schema_checksums": {key: {} for key in REQUIRED_SIDECAR_SCHEMA_KEYS},
        }
        valid = validate_sidecar_manifest(
            rows,
            manifest,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
            expected_dictionary_checksum="dict:test",
        )
        self.assertTrue(valid.ok, valid.message)

        for label, bad_row, fragment in (
            ("provenance", replace(rows[0], provenance=""), "provenance"),
            ("priority", replace(rows[0], priority=101), "priority"),
            ("estimate", replace(rows[0], estimate_events=0), "estimate_events"),
            ("src_kind", replace(rows[0], src_kind="event"), "src_kind"),
            ("dst_kind", replace(rows[0], dst_kind="event"), "dst_kind"),
            ("relation_kind", replace(rows[0], relation_kind="event_to_event"), "relation_kind"),
            ("rule_family", replace(rows[0], rule_family="ref_unknown"), "rule_family"),
            (
                "hints",
                replace(rows[0], segment_hint=None, core_hint=None, seq_hint_begin=None, seq_hint_end=None),
                "hints",
            ),
        ):
            with self.subTest(label=label):
                result = validate_sidecar_manifest(
                    [bad_row],
                    manifest,
                    expected_snapshot_id="snapshot:test:sidecar",
                    expected_trace_checksum="trace:test",
                    expected_dictionary_checksum="dict:test",
                )
                self.assertFalse(result.ok)
                self.assertEqual(result.code, "SIDECAR_MISMATCH")
                self.assertIn(fragment, result.message)

        self.assertEqual(ALLOWED_SRC_KINDS, {"ref", "object"})
        self.assertEqual(ALLOWED_DST_KINDS, {"ref", "object"})
        self.assertIn("ref_ref", ALLOWED_RULE_FAMILIES)
        self.assertIn("ref_index_next", ALLOWED_RELATION_KINDS)

    def test_validate_sidecar_payload_supports_mode_a_manifest_draft(self) -> None:
        rows = materialize_dependency_sidecar(self._build_rows(), trace_checksum="trace:test")
        manifest = {
            "snapshot_id": "snapshot:test:sidecar",
            "trace_checksum": "trace:test",
            "dictionary_checksum": "dict:test",
            "schema_checksums": {key: {} for key in REQUIRED_SIDECAR_SCHEMA_KEYS},
        }

        valid = validate_sidecar_payload(
            rows,
            manifest,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
            expected_dictionary_checksum="dict:test",
        )
        self.assertTrue(valid.ok, valid.message)

        invalid = validate_sidecar_payload(
            [replace(rows[0], priority=101)],
            manifest,
            expected_snapshot_id="snapshot:test:sidecar",
            expected_trace_checksum="trace:test",
            expected_dictionary_checksum="dict:test",
        )
        self.assertFalse(invalid.ok)
        self.assertEqual(invalid.code, "SIDECAR_MISMATCH")
        self.assertIn("priority", invalid.message)


if __name__ == "__main__":
    unittest.main()
