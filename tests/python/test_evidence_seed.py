from __future__ import annotations

from copy import copy
import tempfile
import unittest
from pathlib import Path

from desktop.sample_data import write_scenario
from parser import load_dataset
from parser.evidence_models import EvidenceExportRequest
from parser.evidence_seed import resolve_seed_refs
from parser.models import Alert, Diagnosis, EvidenceRef


class _ExplodingEventStream:
    def __iter__(self):
        raise AssertionError("bundle.event_stream must not be scanned in this path")


class EvidenceSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        trace_path = write_scenario(self.root / "seed.trace", name="basic")
        loaded = load_dataset(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        self.bundle = loaded.data.bundle
        self.ref_index_rows = [
            {
                "ref_key": event.ref_key,
                "timestamp_aligned": float(event.timestamp_aligned),
                "core_id": int(event.core_id),
                "seq": int(event.seq),
            }
            for event in self.bundle.event_stream
        ]
        self.context = {
            "time_window": [self.bundle.event_stream[0].timestamp_aligned, self.bundle.event_stream[2].timestamp_aligned],
            "filter": {},
            "selection": {},
            "zoom_level": 1.0,
            "focused_view": "timeline",
            "evidence_anchor": None,
            "playback_cursor": None,
            "compare_scope": {},
            "dataset_role": "single",
        }
        refs = [
            EvidenceRef(
                ref_type="event",
                ref_key=event.ref_key,
                t_begin=float(event.timestamp_aligned),
                t_end=float(event.timestamp_aligned),
            )
            for event in self.bundle.event_stream[:3]
        ]
        self.alerts = [
            Alert(
                alert_id="alert:a",
                type="latency",
                severity="warning",
                time_window=(refs[0].t_begin, refs[1].t_end),
                object_scope={"task_id": 1},
                threshold=1.0,
                actual=2.0,
                evidence_refs=[refs[1], refs[0]],
                trusted=True,
            )
        ]
        self.diagnoses = [
            Diagnosis(
                diag_id="diag:a",
                title="diag",
                diagnosis_type="root_cause",
                time_window=(refs[0].t_begin, refs[1].t_end),
                object_scope={"task_id": 1},
                conclusion="rooted",
                evidence_refs=[refs[2], refs[0]],
                related_alerts=["alert:a"],
                confidence="high",
            )
        ]
        self.anchors = [
            {
                "anchor_id": "anchor:a",
                "anchor_type": "manual",
                "evidence_anchor": {"ref_key": refs[1].ref_key},
                "time_window": [refs[1].t_begin, refs[1].t_end],
            }
        ]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _request(
        self,
        seed_spec: dict[str, object],
        *,
        seed_materialization: str = "required",
        time_window: tuple[float, float] | None = None,
    ) -> EvidenceExportRequest:
        return EvidenceExportRequest.from_payload(
            {
                "dataset_id": self.bundle.dataset_id,
                "seed_spec": seed_spec,
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 4, "C_events": 16, "S_bytes": 4096, "rho_max": 4.0},
                "closure_policy": {
                    "allow_bounded": True,
                    "allow_degraded": True,
                    "frontier_ref_limit": 16,
                    "seed_materialization": seed_materialization,
                },
                "time_window": list(time_window) if time_window is not None else None,
            },
            default_dataset_id=self.bundle.dataset_id,
            default_time_window=time_window,
            default_filter={},
        )

    def test_manual_refs_are_sorted_and_deduped(self) -> None:
        request = self._request(
            {"source_kind": "manual_refs", "source_payload": {"refs": [self.bundle.event_stream[2].ref_key, self.bundle.event_stream[0].ref_key, self.bundle.event_stream[2].ref_key]}}
        )

        resolved = resolve_seed_refs(request, self.context, self.bundle, self.alerts, self.diagnoses, self.anchors, self.ref_index_rows)

        self.assertTrue(resolved.ok, resolved.message)
        self.assertEqual(
            resolved.data.seed_refs,
            sorted({self.bundle.event_stream[0].ref_key, self.bundle.event_stream[2].ref_key}),
        )

    def test_alert_seed_resolves_alert_ids(self) -> None:
        request = self._request({"source_kind": "alert", "source_payload": {"alert_ids": ["alert:a"]}})

        resolved = resolve_seed_refs(request, self.context, self.bundle, self.alerts, self.diagnoses, self.anchors, self.ref_index_rows)

        self.assertTrue(resolved.ok, resolved.message)
        self.assertEqual(
            resolved.data.seed_refs,
            sorted({self.bundle.event_stream[0].ref_key, self.bundle.event_stream[1].ref_key}),
        )

    def test_diagnosis_seed_resolves_diag_ids(self) -> None:
        request = self._request({"source_kind": "diagnosis", "source_payload": {"diag_ids": ["diag:a"]}})

        resolved = resolve_seed_refs(request, self.context, self.bundle, self.alerts, self.diagnoses, self.anchors, self.ref_index_rows)

        self.assertTrue(resolved.ok, resolved.message)
        self.assertEqual(
            resolved.data.seed_refs,
            sorted({self.bundle.event_stream[0].ref_key, self.bundle.event_stream[2].ref_key}),
        )

    def test_anchor_seed_resolves_anchor_ids(self) -> None:
        request = self._request({"source_kind": "anchor", "source_payload": {"anchor_ids": ["anchor:a"]}})

        resolved = resolve_seed_refs(request, self.context, self.bundle, self.alerts, self.diagnoses, self.anchors, self.ref_index_rows)

        self.assertTrue(resolved.ok, resolved.message)
        self.assertEqual(resolved.data.seed_refs, [self.bundle.event_stream[1].ref_key])

    def test_analysis_context_falls_back_to_time_window_without_scanning_event_stream(self) -> None:
        request = self._request(
            {"source_kind": "analysis_context", "source_payload": {}},
            time_window=(self.bundle.event_stream[0].timestamp_aligned, self.bundle.event_stream[2].timestamp_aligned),
        )
        bundle = copy(self.bundle)
        bundle.event_stream = _ExplodingEventStream()

        resolved = resolve_seed_refs(request, self.context, bundle, self.alerts, self.diagnoses, self.anchors, self.ref_index_rows)

        self.assertTrue(resolved.ok, resolved.message)
        self.assertEqual(
            resolved.data.scope_events,
            sorted({self.bundle.event_stream[0].ref_key, self.bundle.event_stream[1].ref_key, self.bundle.event_stream[2].ref_key}),
        )

    def test_time_window_without_ref_index_fail_closes_without_scanning_event_stream(self) -> None:
        request = self._request(
            {"source_kind": "time_window", "source_payload": {"time_window": [self.bundle.event_stream[0].timestamp_aligned, self.bundle.event_stream[2].timestamp_aligned]}},
            time_window=(self.bundle.event_stream[0].timestamp_aligned, self.bundle.event_stream[2].timestamp_aligned),
        )
        bundle = copy(self.bundle)
        bundle.event_stream = _ExplodingEventStream()

        resolved = resolve_seed_refs(request, self.context, bundle, self.alerts, self.diagnoses, self.anchors, None)

        self.assertFalse(resolved.ok)
        self.assertEqual(resolved.code, "SEED_EMPTY")
        self.assertIn("unable to legally materialize time_window seeds", resolved.message)

    def test_manual_refs_split_required_and_best_effort_materialization(self) -> None:
        existing_ref = self.bundle.event_stream[0].ref_key
        missing_ref = "evt:missing"
        request_required = self._request(
            {"source_kind": "manual_refs", "source_payload": {"refs": [existing_ref, missing_ref]}},
            seed_materialization="required",
        )
        request_best_effort = self._request(
            {"source_kind": "manual_refs", "source_payload": {"refs": [existing_ref, missing_ref]}},
            seed_materialization="best_effort",
        )
        ref_index_rows = [self.ref_index_rows[0]]

        required_result = resolve_seed_refs(
            request_required,
            self.context,
            self.bundle,
            self.alerts,
            self.diagnoses,
            self.anchors,
            ref_index_rows,
        )
        best_effort_result = resolve_seed_refs(
            request_best_effort,
            self.context,
            self.bundle,
            self.alerts,
            self.diagnoses,
            self.anchors,
            ref_index_rows,
        )

        self.assertFalse(required_result.ok)
        self.assertIn("unable to legally materialize seed refs", required_result.message)
        self.assertTrue(best_effort_result.ok, best_effort_result.message)
        self.assertEqual(best_effort_result.data.seed_refs, sorted({existing_ref, missing_ref}))
        self.assertEqual(best_effort_result.data.scope_events, [existing_ref])
        self.assertEqual(best_effort_result.data.missing_required_refs, [missing_ref])


if __name__ == "__main__":
    unittest.main()
