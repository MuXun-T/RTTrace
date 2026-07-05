from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from desktop.sample_data import write_scenario
from desktop.services import CompareService, WorkspaceController
from parser.models import UntrustedWindow, dataclass_to_dict


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
FORMAL_DIFF_DETAIL_FIELDS = {
    "diff_id",
    "scope",
    "target",
    "baseline_view",
    "candidate_view",
    "delta_payload",
    "evidence_refs",
    "related_events",
    "jump_target",
    "trusted",
}
DIMENSION_TARGET_KEYS = {
    "metric": {"dimension", "metric_id"},
    "alert": {"dimension", "alert_type"},
    "hotspot": {"dimension", "node_id"},
    "interval": {"dimension", "interval_type"},
    "task": {"dimension", "task_id"},
    "core": {"dimension", "core_id"},
    "resource": {"dimension", "resource_id"},
    "irq": {"dimension", "irq_id"},
}
DIMENSION_DELTA_KEYS = {
    "metric": {"baseline", "candidate", "delta", "ratio"},
    "alert": {"baseline", "candidate", "delta", "added", "removed", "severity_shift", "evidence_refs"},
    "hotspot": {"baseline", "candidate", "delta", "added", "removed", "severity_shift", "evidence_refs"},
    "interval": {"baseline", "candidate", "delta", "added", "removed", "severity_shift", "evidence_refs"},
    "task": {"baseline", "candidate", "delta", "baseline_blocked", "candidate_blocked", "blocked_delta"},
    "core": {"baseline", "candidate", "delta", "baseline_switches", "candidate_switches", "switch_delta"},
    "resource": {"baseline", "candidate", "delta", "baseline_blocked", "candidate_blocked", "blocked_delta"},
    "irq": {"baseline", "candidate", "delta", "baseline_count", "candidate_count", "count_delta"},
}


class DiffDetailContractP13Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _load_compare(self) -> tuple[WorkspaceController, CompareService, str, str]:
        baseline = write_scenario(self.root / "p1-3-baseline.trace", name="basic", candidate_variant=False, repeat=3)
        candidate = write_scenario(self.root / "p1-3-candidate.trace", name="basic", candidate_variant=True, repeat=3)
        controller = WorkspaceController()
        baseline_loaded = controller.viz_LoadDataset(str(baseline))
        candidate_loaded = controller.viz_LoadDataset(str(candidate))
        self.assertTrue(baseline_loaded.ok, baseline_loaded.message)
        self.assertTrue(candidate_loaded.ok, candidate_loaded.message)
        compare = CompareService(controller.repository, controller.context_store)
        loaded = compare.cmp_LoadPair(baseline_loaded.data, candidate_loaded.data)
        self.assertTrue(loaded.ok, loaded.message)
        return controller, compare, baseline_loaded.data, candidate_loaded.data

    def _assert_formal_detail_shape(
        self,
        detail: dict[str, object],
        expected_scope: dict[str, object],
        *,
        dimension: str,
    ) -> None:
        self.assertEqual(set(detail), FORMAL_DIFF_DETAIL_FIELDS)
        self.assertEqual(set(detail["scope"]), FORMAL_COMPARE_SCOPE_FIELDS)
        self.assertEqual(detail["scope"], expected_scope)
        self.assertEqual(set(detail["target"]), DIMENSION_TARGET_KEYS[dimension])
        self.assertEqual(detail["target"]["dimension"], dimension)
        self.assertNotIn("dimension", detail)
        self.assertIsInstance(detail["baseline_view"], dict)
        self.assertIsInstance(detail["candidate_view"], dict)
        self.assertEqual(detail["baseline_view"]["dataset_role"], "baseline")
        self.assertEqual(detail["candidate_view"]["dataset_role"], "candidate")
        self.assertTrue(detail["baseline_view"]["dataset_id"])
        self.assertTrue(detail["candidate_view"]["dataset_id"])
        self.assertTrue(detail["evidence_refs"])
        self.assertIsInstance(detail["related_events"], list)
        self.assertTrue(DIMENSION_DELTA_KEYS[dimension].issubset(detail["delta_payload"]))
        self.assertIsInstance(detail["trusted"], bool)

        jump_target = detail["jump_target"]
        self.assertIsNotNone(jump_target)
        self.assertEqual(jump_target["dataset_role"], "baseline")
        self.assertIn("time_window", jump_target)
        self.assertIn("focused_view", jump_target)
        self.assertIn("selection", jump_target)
        self.assertIsNotNone(jump_target.get("peer_target"))
        self.assertEqual(jump_target["peer_target"]["dataset_role"], "candidate")
        self.assertIn("time_window", jump_target["peer_target"])
        self.assertIn("focused_view", jump_target["peer_target"])
        self.assertIn("selection", jump_target["peer_target"])

    def test_diff_detail_formal_contract_uses_target_and_diff_id(self) -> None:
        _, compare, baseline_id, candidate_id = self._load_compare()
        scoped = compare.cmp_SetScope(
            {
                "baseline_id": baseline_id,
                "candidate_id": candidate_id,
                "filter": {},
                "dimensions": ["metric", "resource", "alert"],
                "metric_ids": ["cpu_utilization"],
            }
        )
        self.assertTrue(scoped.ok, scoped.message)

        details = compare.cmp_ListDiffDetails()
        self.assertTrue(details.ok, details.message)
        self.assertGreater(len(details.data), 0)

        expected_scope = dataclass_to_dict(scoped.data)
        for item in details.data:
            self._assert_formal_detail_shape(
                item,
                expected_scope,
                dimension=str(item["target"]["dimension"]),
            )
            self.assertNotIn("metric_id", item)
            self.assertNotIn("baseline_value", item)
            self.assertNotIn("candidate_value", item)
            self.assertNotIn("delta", item)

        metric_detail = next(item for item in details.data if item["target"]["dimension"] == "metric")
        self.assertEqual(metric_detail["target"], {"dimension": "metric", "metric_id": "cpu_utilization"})
        self.assertEqual(metric_detail["scope"], expected_scope)

        resource_details = compare.cmp_ListDiffDetails("resource")
        self.assertTrue(resource_details.ok, resource_details.message)
        self.assertTrue(resource_details.data)
        self.assertTrue(all(item["target"]["dimension"] == "resource" for item in resource_details.data))

        exact = compare.cmp_QueryDiffDetail(metric_detail["diff_id"])
        self.assertTrue(exact.ok, exact.message)
        self.assertEqual(exact.data["diff_id"], metric_detail["diff_id"])
        self.assertEqual(exact.data["target"], metric_detail["target"])

        structured = compare.cmp_QueryDiffDetail(resource_details.data[0]["target"])
        self.assertTrue(structured.ok, structured.message)
        self.assertEqual(structured.data["target"], resource_details.data[0]["target"])

    def test_diff_detail_minimum_invariant_matrix_covers_all_eight_dimensions(self) -> None:
        _, compare, baseline_id, candidate_id = self._load_compare()
        scoped = compare.cmp_SetScope(
            {
                "baseline_id": baseline_id,
                "candidate_id": candidate_id,
                "filter": {},
                "dimensions": ["metric", "alert", "hotspot", "interval", "task", "core", "resource", "irq"],
                "metric_ids": ["cpu_utilization"],
            }
        )
        self.assertTrue(scoped.ok, scoped.message)

        summary = compare.cmp_QueryDiffSummary()
        details = compare.cmp_ListDiffDetails()
        self.assertTrue(summary.ok, summary.message)
        self.assertTrue(details.ok, details.message)

        expected_scope = dataclass_to_dict(scoped.data)
        detail_by_dimension = {
            item["target"]["dimension"]: item
            for item in details.data
        }
        self.assertEqual(
            set(detail_by_dimension),
            {"metric", "alert", "hotspot", "interval", "task", "core", "resource", "irq"},
        )

        for dimension, detail in detail_by_dimension.items():
            self._assert_formal_detail_shape(detail, expected_scope, dimension=dimension)
            queried = compare.cmp_QueryDiffDetail(detail["diff_id"])
            self.assertTrue(queried.ok, queried.message)
            self.assertEqual(queried.data["target"], detail["target"])
            self.assertEqual(queried.data["trusted"], summary.data["trust_summary"]["trusted"])

    def test_diff_detail_trusted_inherits_scope_trust_summary(self) -> None:
        controller, compare, baseline_id, candidate_id = self._load_compare()
        baseline_bundle = controller.repository.get(baseline_id).artifact.bundle
        baseline_bundle.untrusted_windows.append(
            UntrustedWindow(
                window_id="test:compare-untrusted",
                source="test",
                scope="compare",
                t_begin=baseline_bundle.event_stream[0].timestamp_aligned,
                t_end=baseline_bundle.event_stream[-1].timestamp_aligned,
                reason_code="TEST_WINDOW",
                severity="warning",
            )
        )

        scoped = compare.cmp_SetScope(
            {
                "baseline_id": baseline_id,
                "candidate_id": candidate_id,
                "filter": {},
                "dimensions": ["metric", "alert", "resource"],
                "metric_ids": ["cpu_utilization"],
            }
        )
        self.assertTrue(scoped.ok, scoped.message)

        summary = compare.cmp_QueryDiffSummary()
        details = compare.cmp_ListDiffDetails()
        self.assertTrue(summary.ok, summary.message)
        self.assertTrue(details.ok, details.message)
        self.assertFalse(summary.data["trust_summary"]["trusted"])
        self.assertTrue(details.data)
        self.assertTrue(all(item["trusted"] is False for item in details.data))
