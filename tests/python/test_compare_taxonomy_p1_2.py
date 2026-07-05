from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from desktop.sample_data import build_scenario
from desktop.services import CompareService, WorkspaceController
from metric.core import _alert_change_summary, _hotspot_change_summary
from parser import encode_trace
from parser.models import Alert, EvidenceRef, RebuildBundle, ResourceGraph
from spec.events import event_id_for


class CompareTaxonomyP12Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _alert(self, alert_id: str, alert_type: str, severity: str) -> Alert:
        return Alert(
            alert_id=alert_id,
            type=alert_type,
            severity=severity,
            time_window=(100.0, 120.0),
            object_scope={"task_id": 1},
            threshold=100.0,
            actual=120.0,
            evidence_refs=[
                EvidenceRef(
                    ref_type="event",
                    ref_key=f"evt:{alert_id}",
                    t_begin=100.0,
                    t_end=120.0,
                )
            ],
            trusted=True,
        )

    def _bundle_with_hotspots(self, dataset_id: str, rows: list[dict[str, int | str]]) -> RebuildBundle:
        return RebuildBundle(
            bundle_id=f"bundle:{dataset_id}",
            dataset_id=dataset_id,
            event_stream=[],
            task_states=[],
            exec_slices=[],
            resource_graph=ResourceGraph(hotspot_stats=list(rows)),
            irq_spans=[],
            untrusted_windows=[],
            rebuild_rev=1,
            capability_flags={},
        )

    def _write_basic_trace(self, output_path: Path, *, repeat: int, wakeup_shift: int) -> Path:
        events = build_scenario(name="basic", repeat=repeat)
        wakeup_id = event_id_for("TASK_WAKEUP")
        switch_id = event_id_for("CTX_SWITCH")
        for item in events:
            if item["event_id"] == wakeup_id:
                item["timestamp"] = int(item["timestamp"]) + wakeup_shift
            elif (
                item["event_id"] == switch_id
                and int(item["core_id"]) == 1
                and int(item["payload"].get("next_task_id", 0)) == 2
            ):
                item["timestamp"] = int(item["timestamp"]) + wakeup_shift
        return encode_trace(output_path, events, producer_ver="python-sim-0.1")

    def test_alert_change_summary_includes_added_removed_and_structured_severity_shift(self) -> None:
        rows = {
            row["alert_type"]: row
            for row in _alert_change_summary(
                [
                    self._alert("base-1", "long_block", "warning"),
                    self._alert("base-2", "long_block", "critical"),
                    self._alert("base-3", "long_irq", "warning"),
                ],
                [
                    self._alert("cand-1", "long_block", "warning"),
                    self._alert("cand-2", "long_block", "critical"),
                    self._alert("cand-3", "long_block", "critical"),
                ],
            )
        }

        long_block = rows["long_block"]
        self.assertEqual(long_block["baseline"], 2)
        self.assertEqual(long_block["candidate"], 3)
        self.assertEqual(long_block["delta"], 1)
        self.assertEqual(long_block["added"], 1)
        self.assertEqual(long_block["removed"], 0)
        self.assertEqual(
            long_block["severity_shift"],
            {
                "changed": True,
                "baseline": {"critical": 1, "warning": 1},
                "candidate": {"critical": 2, "warning": 1},
                "delta": {"critical": 1, "warning": 0},
            },
        )
        self.assertEqual(long_block["evidence_refs"], [])

        long_irq = rows["long_irq"]
        self.assertEqual(long_irq["baseline"], 1)
        self.assertEqual(long_irq["candidate"], 0)
        self.assertEqual(long_irq["delta"], -1)
        self.assertEqual(long_irq["added"], 0)
        self.assertEqual(long_irq["removed"], 1)
        self.assertEqual(
            long_irq["severity_shift"],
            {
                "changed": True,
                "baseline": {"warning": 1},
                "candidate": {},
                "delta": {"warning": -1},
            },
        )
        self.assertEqual(long_irq["evidence_refs"], [])

    def test_hotspot_change_summary_prioritizes_change_importance_before_truncation(self) -> None:
        baseline = self._bundle_with_hotspots(
            "baseline",
            [{"node_id": f"node:{index:02d}", "count": 5} for index in range(12)],
        )
        candidate = self._bundle_with_hotspots(
            "candidate",
            [{"node_id": f"node:{index:02d}", "count": 5} for index in range(12)]
            + [{"node_id": "node:zz", "count": 50}],
        )

        rows = _hotspot_change_summary(baseline, candidate)

        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["node_id"], "node:zz")
        self.assertEqual(rows[0]["baseline"], 0)
        self.assertEqual(rows[0]["candidate"], 50)
        self.assertEqual(rows[0]["delta"], 50)
        self.assertEqual(rows[0]["added"], 50)
        self.assertEqual(rows[0]["removed"], 0)
        self.assertEqual(
            rows[0]["severity_shift"],
            {"impact_delta": {"changed": True, "direction": "up"}},
        )
        self.assertEqual(rows[0]["evidence_refs"], [])

    def test_compare_taxonomy_rows_fill_summary_evidence_and_keep_detail_target_id_only(self) -> None:
        baseline = self._write_basic_trace(self.root / "taxonomy-baseline.trace", repeat=1, wakeup_shift=-70)
        candidate = self._write_basic_trace(self.root / "taxonomy-candidate.trace", repeat=2, wakeup_shift=0)

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
                "dimensions": ["alert", "hotspot", "interval"],
            }
        )
        self.assertTrue(scoped.ok, scoped.message)

        summary = compare.cmp_QueryDiffSummary()
        details = compare.cmp_ListDiffDetails()
        self.assertTrue(summary.ok, summary.message)
        self.assertTrue(details.ok, details.message)

        alert_row = next(row for row in summary.data["alert_changes"] if row["alert_type"] == "long_block")
        self.assertEqual(alert_row["baseline"], 0)
        self.assertEqual(alert_row["candidate"], 1)
        self.assertEqual(alert_row["delta"], 1)
        self.assertEqual(alert_row["added"], 1)
        self.assertEqual(alert_row["removed"], 0)
        self.assertEqual(
            alert_row["severity_shift"],
            {
                "changed": True,
                "baseline": {},
                "candidate": {"warning": 1},
                "delta": {"warning": 1},
            },
        )
        self.assertGreater(len(alert_row["evidence_refs"]), 0)

        hotspot_row = next(row for row in summary.data["hotspot_changes"] if row["node_id"] == "obj:2748")
        self.assertEqual(hotspot_row["baseline"], 4)
        self.assertEqual(hotspot_row["candidate"], 8)
        self.assertEqual(hotspot_row["delta"], 4)
        self.assertEqual(hotspot_row["added"], 4)
        self.assertEqual(hotspot_row["removed"], 0)
        self.assertEqual(
            hotspot_row["severity_shift"],
            {"impact_delta": {"changed": True, "direction": "up"}},
        )
        self.assertGreater(len(hotspot_row["evidence_refs"]), 0)

        interval_row = summary.data["interval_changes"][0]
        self.assertEqual(interval_row["interval_type"], "max_blocked_segment")
        self.assertEqual(interval_row["baseline"], 90.0)
        self.assertEqual(interval_row["candidate"], 160.0)
        self.assertEqual(interval_row["delta"], 70.0)
        self.assertEqual(interval_row["added"], 70.0)
        self.assertEqual(interval_row["removed"], 0.0)
        self.assertEqual(
            interval_row["severity_shift"],
            {"impact_delta": {"changed": True, "direction": "up"}},
        )
        self.assertGreater(len(interval_row["evidence_refs"]), 0)

        alert_detail = next(
            item
            for item in details.data
            if item["target"].get("dimension") == "alert" and item["target"].get("alert_type") == "long_block"
        )
        hotspot_detail = next(
            item
            for item in details.data
            if item["target"].get("dimension") == "hotspot" and item["target"].get("node_id") == "obj:2748"
        )
        interval_detail = next(
            item
            for item in details.data
            if item["target"].get("dimension") == "interval" and item["target"].get("interval_type") == "max_blocked_segment"
        )

        self.assertEqual(set(alert_detail["target"]), {"dimension", "alert_type"})
        self.assertEqual(set(hotspot_detail["target"]), {"dimension", "node_id"})
        self.assertEqual(set(interval_detail["target"]), {"dimension", "interval_type"})
        self.assertEqual(alert_detail["delta_payload"], alert_row)
        self.assertEqual(hotspot_detail["delta_payload"], hotspot_row)
        self.assertEqual(interval_detail["delta_payload"], interval_row)
