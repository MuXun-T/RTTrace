from __future__ import annotations

import unittest

from parser.evidence_models import evd_RecomputeProofHash
from parser.runtime_cost_graph import (
    RUNTIME_COST_GRAPH_NODE_ORDER,
    annotate_evidence_export_progress,
    build_benchmark_runtime_cost_graph,
    build_formal_runtime_cost_graph,
    build_pipeline_runtime_cost_graph,
    build_runtime_cost_cache_key,
    graph_node_ids_for_action,
    merge_runtime_cost_graphs,
    proof_boundary_summary,
)
from parser.runtime_optimization_gate import RUNTIME_ACTION_GATE_ALLOWLIST
from spec.schema_loader import load_specs
from spec.schema_validator import validate_schema


class RuntimeCostGraphTests(unittest.TestCase):
    def test_pipeline_runtime_cost_graph_schema_validates_and_keeps_stable_node_order(self) -> None:
        graph = build_pipeline_runtime_cost_graph(
            dataset_id="dataset:pipeline",
            stage_timings={
                "_decode_chunk_seconds": 0.11,
                "prs_Verify_seconds": 0.21,
                "align_events_seconds": 0.03,
                "rb_Rebuild_seconds": 0.04,
                "idx_Build_seconds": 0.01,
            },
            index_build_mode="minimal",
            materialize_event_stream=False,
        )

        self.assertIsNone(validate_schema(load_specs()["runtime_cost_graph"], graph))
        scenario_graph = graph["graphs"][0]
        self.assertEqual(scenario_graph["node_order"], list(RUNTIME_COST_GRAPH_NODE_ORDER))
        self.assertEqual([node["node_id"] for node in scenario_graph["nodes"]], list(RUNTIME_COST_GRAPH_NODE_ORDER))
        self.assertEqual(scenario_graph["nodes"][0]["node_id"], "decode")
        self.assertEqual(scenario_graph["nodes"][4]["status"], "skipped")
        self.assertEqual(scenario_graph["nodes"][5]["status"], "completed")
        self.assertEqual(scenario_graph["stage_coverage"]["expected_nodes"], len(RUNTIME_COST_GRAPH_NODE_ORDER))

    def test_runtime_cost_cache_key_requires_all_reuse_fields(self) -> None:
        self.assertIsNone(
            build_runtime_cost_cache_key(
                {
                    "snapshot_id": "snapshot:test",
                    "trace_checksum": "trace:test",
                    "dictionary_checksum": "dict:test",
                    "sidecar_checksum": "",
                }
            )
        )
        self.assertEqual(
            build_runtime_cost_cache_key(
                {
                    "snapshot_id": "snapshot:test",
                    "trace_checksum": "trace:test",
                    "dictionary_checksum": "dict:test",
                    "sidecar_checksum": "sidecar:test",
                }
            ),
            "snapshot:test|trace:test|dict:test|sidecar:test",
        )

    def test_runtime_cost_graph_action_bindings_cover_gate_allowlist(self) -> None:
        observed = {action_kind for action_kind in RUNTIME_ACTION_GATE_ALLOWLIST}
        bound = {action_kind for action_kind in observed if graph_node_ids_for_action(action_kind) is not None}
        self.assertEqual(observed, bound)
        self.assertEqual(
            graph_node_ids_for_action("baseline_full_load"),
            ("decode", "verify", "align", "rebuild", "index_full"),
        )
        self.assertEqual(
            graph_node_ids_for_action("deferred_index_build"),
            ("rebuild", "index_deferred"),
        )
        self.assertEqual(graph_node_ids_for_action("abstain"), ())

    def test_runtime_cost_graph_marks_missing_cache_key_as_unavailable_for_reuse_action(self) -> None:
        base_graph = build_pipeline_runtime_cost_graph(
            dataset_id="dataset:reuse",
            stage_timings={},
            index_build_mode="full",
            materialize_event_stream=True,
        )
        graph = build_benchmark_runtime_cost_graph(
            base_graph_payload=base_graph,
            scenario_id="scenario:reuse-missing",
            progress=[],
            row={
                "status": "completed",
                "sidecar_validate_seconds": 0.0,
                "index_build_open_seconds": 0.0,
                "package_write_seconds": 0.0,
                "artifact_refs": {},
                "summary_metrics": {"sidecar_ticket_fast_path": False},
                "telemetry_records": [],
            },
            proof_digest={},
            advisor_report={
                "decision": {
                    "proposed_actions": [{"action_kind": "sidecar_index_reuse"}],
                },
                "gate_result": {
                    "accepted": False,
                    "rejected_reason": "ERR-ACTION_MISSING_ARTIFACT",
                    "execution_plan": ["open_index"],
                },
            },
            action_context={
                "snapshot_id": "snapshot:test",
                "trace_checksum": "trace:test",
                "dictionary_checksum": "",
                "sidecar_checksum": "",
            },
        )

        binding = next(
            item for item in graph["graphs"][0]["action_bindings"] if item["action_kind"] == "sidecar_index_reuse"
        )
        self.assertFalse(binding["available"])
        self.assertIn("missing_cache_key", str(binding["unavailable_reason"]))

    def test_benchmark_runtime_cost_graph_keeps_partial_coverage_when_stage_telemetry_is_missing(self) -> None:
        base_graph = build_pipeline_runtime_cost_graph(
            dataset_id="dataset:partial",
            stage_timings={},
            index_build_mode="full",
            materialize_event_stream=True,
        )

        graph = build_benchmark_runtime_cost_graph(
            base_graph_payload=base_graph,
            scenario_id="scenario:partial",
            progress=[],
            row={
                "status": "failed",
                "sidecar_validate_seconds": None,
                "index_build_open_seconds": None,
                "package_write_seconds": None,
                "artifact_refs": {},
                "summary_metrics": {},
                "telemetry_records": [],
            },
            proof_digest={},
            advisor_report=None,
            action_context={},
        )

        scenario_graph = graph["graphs"][0]
        nodes = {node["node_id"]: node for node in scenario_graph["nodes"]}
        self.assertLess(
            scenario_graph["stage_coverage"]["covered_nodes"],
            scenario_graph["stage_coverage"]["expected_nodes"],
        )
        self.assertEqual(nodes["sidecar_validate"]["status"], "planned")
        self.assertEqual(nodes["package_write"]["status"], "planned")
        self.assertEqual(nodes["proof_validate"]["status"], "failed")

    def test_benchmark_runtime_cost_graph_marks_fallback_progress_as_degraded(self) -> None:
        base_graph = build_pipeline_runtime_cost_graph(
            dataset_id="dataset:fallback",
            stage_timings={},
            index_build_mode="full",
            materialize_event_stream=True,
        )

        graph = build_benchmark_runtime_cost_graph(
            base_graph_payload=base_graph,
            scenario_id="scenario:fallback",
            progress=[
                {
                    "substage": "sidecar/index",
                    "status": "started",
                    "observed_at": 1.0,
                },
                {
                    "substage": "sidecar/index",
                    "status": "fallback",
                    "observed_at": 1.2,
                },
            ],
            row={
                "status": "completed_with_fallback",
                "sidecar_validate_seconds": 0.01,
                "index_build_open_seconds": None,
                "package_write_seconds": 0.02,
                "artifact_refs": {},
                "summary_metrics": {"sidecar_ticket_fast_path": False},
                "telemetry_records": [],
            },
            proof_digest={"sidecar_selector_mode": "stream_scan"},
            advisor_report=None,
            action_context={},
        )

        nodes = {node["node_id"]: node for node in graph["graphs"][0]["nodes"]}
        self.assertEqual(nodes["sidecar_index_open"]["status"], "degraded")
        self.assertEqual(nodes["sidecar_index_open"]["fallback_edge"], "sidecar_validate_to_window_read:stream_scan")

    def test_formal_and_product_graphs_keep_path_types_separate(self) -> None:
        product_graph = build_pipeline_runtime_cost_graph(
            dataset_id="dataset:product",
            stage_timings={},
            index_build_mode="full",
            materialize_event_stream=True,
        )
        formal_graph = build_formal_runtime_cost_graph(
            scenario_id="scenario:formal",
            active_nodes={"decode", "verify", "proof_validate"},
            observed_seconds={"decode": 0.1, "verify": 0.2, "proof_validate": 0.3},
        )
        merged = merge_runtime_cost_graphs([product_graph, formal_graph])

        self.assertIsNone(validate_schema(load_specs()["runtime_cost_graph"], merged))
        self.assertEqual(merged["graphs"][0]["path_type"], "product")
        self.assertEqual(merged["graphs"][1]["path_type"], "formal")

    def test_annotate_evidence_export_progress_assigns_expected_node_ids(self) -> None:
        self.assertEqual(
            annotate_evidence_export_progress({"substage": "round/project", "status": "started"})["graph_node_id"],
            "closure_project",
        )
        self.assertEqual(
            annotate_evidence_export_progress({"substage": "sidecar/index", "status": "completed", "sidecar_index_rebuilt": True})["graph_node_id"],
            "sidecar_index_build",
        )

    def test_runtime_cost_graph_proof_boundary_stays_outside_proof_hash_inputs(self) -> None:
        proof_digest = {
            "snapshot_id": "snapshot:test",
            "closure_mode": "exact",
            "complete_wrt_rule_family": True,
            "rule_family": ["ref_ref"],
            "budget_vector": {"D_max": 1, "C_events": 2, "S_bytes": 3, "rho_max": 4.0},
            "closure_depth_reached": 1,
            "seed_ref_count": 1,
            "closed_ref_count": 1,
            "missing_required_refs": 0,
            "truncated_frontier_count": 0,
            "frontier_halt_reason": "FRONTIER_EMPTY",
            "events_emitted": 1,
            "bytes_emitted": 64,
        }
        baseline = evd_RecomputeProofHash(proof_digest)
        polluted = {
            **proof_digest,
            "runtime_cost_graph": {"should_not": "matter"},
            "advisor_metadata": {"should_not": "matter"},
        }
        self.assertEqual(baseline, evd_RecomputeProofHash(polluted))
        self.assertEqual(
            proof_boundary_summary(polluted),
            {
                "cost_graph_fields_in_proof_digest": 1,
                "advisor_fields_in_proof_digest": 1,
            },
        )
