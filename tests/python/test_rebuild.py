from __future__ import annotations

import unittest
from typing import Any

from parser import rb_Rebuild
from parser.models import DecodedEvent, RebuildBundle, dataclass_to_dict
from spec.events import event_id_for


class RebuildResourceGraphTests(unittest.TestCase):
    def _event(self, seq: int, timestamp: float, event_name: str, payload: dict[str, Any]) -> DecodedEvent:
        task_id = payload.get("task_id")
        obj_id = payload.get("obj_id", payload.get("wait_obj_id"))
        return DecodedEvent(
            core_id=0,
            seq=seq,
            timestamp_raw=timestamp,
            timestamp_aligned=timestamp,
            event_id=event_id_for(event_name),
            event_name=event_name,
            task_id=None if task_id is None else int(task_id),
            obj_id=None if obj_id is None else int(obj_id),
            irq_id=None,
            job_id=None,
            instance_id=None,
            payload=payload,
            trust_tags=[],
            chunk_id=0,
        )

    def _assert_edge_bounds(self, edge: dict[str, Any], begin: float, end: float) -> None:
        self.assertIn("t_begin", edge)
        self.assertIn("t_end", edge)
        self.assertEqual(edge["t_begin"], begin)
        self.assertEqual(edge["t_end"], end)
        self.assertGreaterEqual(float(edge["t_end"]), float(edge["t_begin"]))

    def _bundle_core_dict(self, bundle: RebuildBundle) -> dict[str, Any]:
        payload = dataclass_to_dict(bundle)
        payload["capability_flags"] = {
            key: value
            for key, value in payload["capability_flags"].items()
            if not str(key).startswith("experimental_")
        }
        return payload

    def _experimental_parity_events(self) -> list[DecodedEvent]:
        return [
            self._event(1, 100.0, "TASK_READY", {"task_id": 1, "prio": 1, "core_hint": 0, "reason": 1}),
            self._event(2, 105.0, "CTX_SWITCH", {"core_id": 0, "prev_task_id": 0, "next_task_id": 1, "reason": 1}),
            self._event(3, 110.0, "SYNC_LOCK", {"task_id": 1, "obj_id": 42, "obj_type": 1}),
            self._event(4, 115.0, "TASK_BLOCK", {"task_id": 2, "wait_obj_id": 42, "owner_task_id": 1}),
            self._event(5, 120.0, "IRQ_ENTER", {"irq_id": 7, "nesting_depth": 1}),
            self._event(6, 130.0, "IRQ_EXIT", {"irq_id": 7, "nesting_depth": 1}),
            self._event(7, 140.0, "TASK_WAKEUP", {"task_id": 2, "obj_id": 42, "wake_src": 1}),
            self._event(8, 150.0, "SYNC_UNLOCK", {"task_id": 1, "obj_id": 42, "obj_type": 1}),
            self._event(9, 160.0, "CTX_SWITCH", {"core_id": 0, "prev_task_id": 1, "next_task_id": 2, "reason": 2}),
            self._event(10, 170.0, "SYNC_LOCK", {"task_id": 3, "obj_id": 99, "obj_type": 1}),
            self._event(11, 180.0, "TASK_BLOCK", {"task_id": 4, "wait_obj_id": 99, "owner_task_id": 3}),
            self._event(12, 190.0, "IRQ_ENTER", {"irq_id": 9, "nesting_depth": 1}),
        ]

    def test_resource_edges_emit_time_bounds_for_closed_and_open_relations(self) -> None:
        rebuilt = rb_Rebuild(
            [
                self._event(1, 100.0, "SYNC_LOCK", {"task_id": 1, "obj_id": 42, "obj_type": 1}),
                self._event(2, 110.0, "TASK_BLOCK", {"task_id": 2, "wait_obj_id": 42, "owner_task_id": 1}),
                self._event(3, 150.0, "SYNC_UNLOCK", {"task_id": 1, "obj_id": 42, "obj_type": 1}),
                self._event(4, 160.0, "TASK_WAKEUP", {"task_id": 2, "obj_id": 42, "wake_src": 1}),
                self._event(5, 200.0, "SYNC_LOCK", {"task_id": 3, "obj_id": 50, "obj_type": 1}),
                self._event(6, 210.0, "TASK_BLOCK", {"task_id": 3, "wait_obj_id": 51, "owner_task_id": 8}),
                self._event(7, 250.0, "TASK_EXIT", {"task_id": 3, "exit_code": 0}),
                self._event(8, 300.0, "SYNC_LOCK", {"task_id": 4, "obj_id": 60, "obj_type": 1}),
                self._event(9, 310.0, "TASK_BLOCK", {"task_id": 5, "wait_obj_id": 60, "owner_task_id": 4}),
                self._event(10, 400.0, "TASK_READY", {"task_id": 9, "prio": 1, "core_hint": 0, "reason": 1}),
            ],
            dataset_id="unit",
        )
        self.assertTrue(rebuilt.ok, rebuilt.message)

        graph = rebuilt.data.resource_graph
        self.assertEqual(len(graph.hold_edges), 3)
        self.assertEqual(len(graph.wait_edges), 3)
        self._assert_edge_bounds(graph.hold_edges[0], 100.0, 150.0)
        self._assert_edge_bounds(graph.wait_edges[0], 110.0, 160.0)
        self._assert_edge_bounds(graph.hold_edges[1], 200.0, 250.0)
        self._assert_edge_bounds(graph.wait_edges[1], 210.0, 250.0)
        self._assert_edge_bounds(graph.hold_edges[2], 300.0, 400.0)
        self._assert_edge_bounds(graph.wait_edges[2], 310.0, 400.0)
        self.assertFalse(rebuilt.data.capability_flags["resource_closed"])
        self.assertEqual(
            {window.reason_code for window in rebuilt.data.untrusted_windows},
            {"LOCK_WITHOUT_UNLOCK", "WAIT_WITHOUT_WAKEUP"},
        )

    def test_repeated_resource_starts_close_previous_edge_at_new_start(self) -> None:
        rebuilt = rb_Rebuild(
            [
                self._event(1, 100.0, "SYNC_LOCK", {"task_id": 1, "obj_id": 42, "obj_type": 1}),
                self._event(2, 125.0, "SYNC_LOCK", {"task_id": 1, "obj_id": 42, "obj_type": 1}),
                self._event(3, 180.0, "SYNC_UNLOCK", {"task_id": 1, "obj_id": 42, "obj_type": 1}),
                self._event(4, 200.0, "TASK_BLOCK", {"task_id": 2, "wait_obj_id": 42, "owner_task_id": 1}),
                self._event(5, 220.0, "TASK_BLOCK", {"task_id": 2, "wait_obj_id": 42, "owner_task_id": 1}),
                self._event(6, 260.0, "TASK_WAKEUP", {"task_id": 2, "obj_id": 42, "wake_src": 1}),
            ],
            dataset_id="unit",
        )
        self.assertTrue(rebuilt.ok, rebuilt.message)

        graph = rebuilt.data.resource_graph
        self.assertEqual(len(graph.hold_edges), 2)
        self.assertEqual(len(graph.wait_edges), 2)
        self._assert_edge_bounds(graph.hold_edges[0], 100.0, 125.0)
        self._assert_edge_bounds(graph.hold_edges[1], 125.0, 180.0)
        self._assert_edge_bounds(graph.wait_edges[0], 200.0, 220.0)
        self._assert_edge_bounds(graph.wait_edges[1], 220.0, 260.0)
        self.assertTrue(rebuilt.data.capability_flags.get("resource_closed", True))

    def test_many_open_resource_relations_close_only_target_edges(self) -> None:
        events = []
        seq = 1
        for index in range(20):
            events.append(self._event(seq, 100.0 + seq, "SYNC_LOCK", {"task_id": index + 1, "obj_id": 100 + index, "obj_type": 1}))
            seq += 1
            events.append(self._event(seq, 100.0 + seq, "TASK_BLOCK", {"task_id": 1000 + index, "wait_obj_id": 100 + index, "owner_task_id": index + 1}))
            seq += 1
        events.extend(
            [
                self._event(seq, 200.0, "TASK_WAKEUP", {"task_id": 1005, "obj_id": 105, "wake_src": 6}),
                self._event(seq + 1, 210.0, "TASK_EXIT", {"task_id": 7, "exit_code": 0}),
                self._event(seq + 2, 220.0, "TASK_READY", {"task_id": 99, "prio": 1, "core_hint": 0, "reason": 1}),
            ]
        )

        rebuilt = rb_Rebuild(events, dataset_id="unit-many-open")
        self.assertTrue(rebuilt.ok, rebuilt.message)

        hold_by_obj = {int(edge["obj_id"]): edge for edge in rebuilt.data.resource_graph.hold_edges}
        wait_by_task = {int(edge["task_id"]): edge for edge in rebuilt.data.resource_graph.wait_edges}
        self._assert_edge_bounds(wait_by_task[1005], 112.0, 200.0)
        self._assert_edge_bounds(hold_by_obj[106], 113.0, 210.0)
        self._assert_edge_bounds(wait_by_task[1006], 114.0, 220.0)
        self._assert_edge_bounds(hold_by_obj[105], 111.0, 220.0)
        self.assertFalse(rebuilt.data.capability_flags["resource_closed"])

    def test_experimental_morsel_rebuild_matches_serial_oracle(self) -> None:
        events = self._experimental_parity_events()
        serial = rb_Rebuild(events, dataset_id="unit-parity")
        self.assertTrue(serial.ok, serial.message)

        morsel = rb_Rebuild(
            list(self._experimental_parity_events()),
            dataset_id="unit-parity",
            morsel_size=3,
            experimental_parallel_rebuild=True,
        )
        self.assertTrue(morsel.ok, morsel.message)

        self.assertEqual(self._bundle_core_dict(morsel.data), self._bundle_core_dict(serial.data))
        self.assertFalse(any(key.startswith("experimental_") for key in serial.data.capability_flags))
        self.assertTrue(morsel.data.capability_flags["experimental_parallel_rebuild"])
        self.assertTrue(morsel.data.capability_flags["experimental_morsel_rebuild"])
        self.assertFalse(morsel.data.capability_flags["experimental_parallel_active"])
        self.assertFalse(morsel.data.capability_flags["resource_closed"])
        self.assertFalse(morsel.data.capability_flags["irq_closed"])

    def test_parallel_workers_experiment_falls_back_to_serial_with_parity(self) -> None:
        serial = rb_Rebuild(self._experimental_parity_events(), dataset_id="unit-parallel-fallback")
        self.assertTrue(serial.ok, serial.message)

        fallback = rb_Rebuild(
            list(self._experimental_parity_events()),
            dataset_id="unit-parallel-fallback",
            morsel_size=2,
            parallel_workers=4,
            experimental_parallel_rebuild=True,
        )
        self.assertTrue(fallback.ok, fallback.message)

        self.assertEqual(self._bundle_core_dict(fallback.data), self._bundle_core_dict(serial.data))
        self.assertTrue(fallback.data.capability_flags["experimental_parallel_requested"])
        self.assertTrue(fallback.data.capability_flags["experimental_parallel_fallback_serial"])
        self.assertFalse(fallback.data.capability_flags["experimental_parallel_active"])


if __name__ == "__main__":
    unittest.main()
