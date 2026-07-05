from __future__ import annotations

from types import SimpleNamespace
import unittest

from parser.evidence_models import (
    BudgetVector,
    ClosurePolicy,
    DependencySidecarEdge,
    EvidenceExportRequest,
    ProofDigest,
    ReadWindowResult,
    SeedResolution,
    SeedSpec,
    WindowPlan,
    evd_DeriveStableSnapshotId,
    evd_NormalizeBudgetVector,
    evd_NormalizeRuleFamily,
    evd_NormalizeSeedSpecForSnapshot,
    evd_ProofHashInput,
    evd_RecomputeProofHash,
    evd_SnapshotDerivationInput,
    evd_StableEdgeSortKey,
    evd_StableEventSortKey,
)


class EvidenceModelTests(unittest.TestCase):
    def test_seed_spec_and_seed_resolution_support_compat_access(self) -> None:
        seed_spec = SeedSpec.from_payload({"source_kind": "manual_refs", "source_payload": {"refs": ["evt:1"]}})
        self.assertEqual(seed_spec.source_kind, "manual_refs")
        self.assertEqual(seed_spec.get("source_payload"), {"refs": ["evt:1"]})
        self.assertEqual(seed_spec["source_kind"], "manual_refs")

        resolution = SeedResolution.from_payload(
            {
                "seed_refs": ["evt:1", "evt:2"],
                "scope_events": ["evt:1"],
                "missing_required_refs": ["evt:2"],
            }
        )
        self.assertEqual(resolution.seed_refs, ["evt:1", "evt:2"])
        self.assertEqual(resolution.get("scope_events"), ["evt:1"])
        self.assertEqual(resolution["missing_required_refs"], ["evt:2"])

    def test_window_plan_and_read_result_roundtrip(self) -> None:
        window_plan = WindowPlan.from_payload(
            {
                "round_id": 2,
                "spans": [
                    {
                        "span_id": "span:1",
                        "t_begin": 100,
                        "t_end": 120,
                        "core_id": 0,
                        "seq_begin": 1,
                        "seq_end": 2,
                        "segment_seq": None,
                        "source_edge_count": 1,
                        "target_refs": ["evt:1", "evt:1"],
                    }
                ],
                "unplanned_refs": ["evt:9"],
            }
        )
        self.assertEqual(window_plan.round_id, 2)
        self.assertEqual(window_plan.spans[0].target_refs, ["evt:1"])
        self.assertEqual(window_plan.get("unplanned_refs"), ["evt:9"])

        read_result = ReadWindowResult.from_payload(
            {
                "round_id": 2,
                "matched_refs": ["evt:1"],
                "missed_refs": ["evt:2"],
                "scan_count": 3,
                "seek_count": 3,
                "span_total": 20,
                "window_span_total": 20,
                "window_count": 1,
            }
        )
        self.assertEqual(read_result["scan_count"], 3)
        self.assertEqual(read_result.to_dict()["window_count"], 1)

    def test_closure_policy_normalizes_seed_materialization_contract(self) -> None:
        self.assertEqual(ClosurePolicy.from_payload({"seed_materialization": "required"}).seed_materialization, "required")
        self.assertEqual(ClosurePolicy.from_payload({"seed_materialization": "best_effort"}).seed_materialization, "best_effort")
        self.assertEqual(ClosurePolicy.from_payload({"seed_materialization": "event"}).seed_materialization, "required")

    def test_normalize_rule_family_deduplicates_and_falls_back_to_default(self) -> None:
        self.assertEqual(
            evd_NormalizeRuleFamily(["ref_ref", "ref_alert", "ref_ref", ""]),
            ("ref_ref", "ref_alert"),
        )
        self.assertEqual(
            evd_NormalizeRuleFamily(None),
            ("ref_ref", "ref_alert", "ref_diagnosis", "ref_anchor", "ref_object"),
        )

    def test_normalize_budget_vector_accepts_dict_and_object(self) -> None:
        self.assertEqual(
            evd_NormalizeBudgetVector({"D_max": "5", "C_events": 9, "S_bytes": 64, "rho_max": "2.5"}).to_dict(),
            {"D_max": 5, "C_events": 9, "S_bytes": 64, "rho_max": 2.5},
        )
        self.assertEqual(
            evd_NormalizeBudgetVector(BudgetVector(D_max=2, C_events=3, S_bytes=4, rho_max=1.5)).to_dict(),
            {"D_max": 2, "C_events": 3, "S_bytes": 4, "rho_max": 1.5},
        )

    def test_snapshot_derivation_input_is_stable_and_normalized(self) -> None:
        request = EvidenceExportRequest.from_payload(
            {
                "dataset_id": "dataset:test",
                "seed_spec": {"source_kind": "manual_refs", "source_payload": {"refs": ["evt:2", "evt:1"]}},
                "rule_family": ["ref_ref", "ref_anchor", "ref_ref"],
                "budget_vector": {"S_bytes": 128, "C_events": 6, "rho_max": 2.0, "D_max": 3},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True},
                "time_window": [10.0, 20.0],
                "filter": {"task_id": 7, "kinds": ["alert", "diag"]},
            },
            default_dataset_id="dataset:test",
        )
        analysis_context = {
            "selection": {"seed_ref": "evt:1"},
            "evidence_anchor": {"ref_key": "evt:1"},
            "compare_scope": {"scope_id": "scope:test"},
        }

        normalized = evd_SnapshotDerivationInput(
            dataset_id="dataset:test",
            embodiment_mode="mode_a",
            trace_checksum="trace:test",
            dictionary_checksum="dict:test",
            request=request,
            analysis_context=analysis_context,
        )
        self.assertEqual(normalized["dataset_id"], "dataset:test")
        self.assertEqual(normalized["embodiment_mode"], "mode_a")
        self.assertEqual(normalized["rule_family"], ["ref_ref", "ref_anchor"])
        self.assertEqual(normalized["budget_vector"], {"D_max": 3, "C_events": 6, "S_bytes": 128, "rho_max": 2.0})
        self.assertEqual(evd_NormalizeSeedSpecForSnapshot(request.seed_spec)["source_kind"], "manual_refs")

    def test_stable_snapshot_id_changes_with_frozen_inputs(self) -> None:
        request = EvidenceExportRequest.from_payload(
            {
                "dataset_id": "dataset:test",
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 3, "C_events": 6, "S_bytes": 128, "rho_max": 2.0},
            },
            default_dataset_id="dataset:test",
            default_time_window=(1.0, 2.0),
            default_filter={"task_id": 1},
        )
        analysis_context = {
            "selection": {"seed_ref": "evt:1"},
            "evidence_anchor": {"ref_key": "evt:1"},
            "compare_scope": {"scope_id": "scope:test"},
        }

        stable_a = evd_DeriveStableSnapshotId(
            dataset_id="dataset:test",
            embodiment_mode="mode_a",
            trace_checksum="trace:test",
            dictionary_checksum="dict:test",
            request=request,
            analysis_context=analysis_context,
        )
        stable_b = evd_DeriveStableSnapshotId(
            dataset_id="dataset:test",
            embodiment_mode="mode_a",
            trace_checksum="trace:test",
            dictionary_checksum="dict:test",
            request=request,
            analysis_context=analysis_context,
        )
        changed_budget = evd_DeriveStableSnapshotId(
            dataset_id="dataset:test",
            embodiment_mode="mode_a",
            trace_checksum="trace:test",
            dictionary_checksum="dict:test",
            request=EvidenceExportRequest.from_payload(
                {
                    "dataset_id": "dataset:test",
                    "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                    "rule_family": ["ref_ref"],
                    "budget_vector": {"D_max": 4, "C_events": 6, "S_bytes": 128, "rho_max": 2.0},
                },
                default_dataset_id="dataset:test",
                default_time_window=(1.0, 2.0),
                default_filter={"task_id": 1},
            ),
            analysis_context=analysis_context,
        )
        self.assertEqual(stable_a, stable_b)
        self.assertNotEqual(stable_a, changed_budget)
        self.assertTrue(stable_a.startswith("snapshot:mode_a:"))

    def test_stable_sort_keys_follow_documented_order(self) -> None:
        edges = [
            DependencySidecarEdge(
                snapshot_id="snapshot:test",
                trace_checksum="pending",
                src_ref="src:1",
                dst_ref="evt:later",
                src_kind="ref",
                dst_kind="ref",
                relation_kind="ref_index_next",
                rule_family="ref_ref",
                provenance="ref_index_row",
                priority=60,
                time_hint_begin_ns=20,
                time_hint_end_ns=20,
                core_hint=0,
                seq_hint_begin=2,
                seq_hint_end=2,
                segment_hint="core:0",
                cycle_guard_token="cg:2",
                estimate_events=1,
                estimate_bytes=64,
                edge_hash="b",
            ),
            DependencySidecarEdge(
                snapshot_id="snapshot:test",
                trace_checksum="pending",
                src_ref="src:1",
                dst_ref="evt:earlier",
                src_kind="ref",
                dst_kind="ref",
                relation_kind="ref_index_next",
                rule_family="ref_ref",
                provenance="ref_index_row",
                priority=80,
                time_hint_begin_ns=10,
                time_hint_end_ns=10,
                core_hint=0,
                seq_hint_begin=1,
                seq_hint_end=1,
                segment_hint="core:0",
                cycle_guard_token="cg:1",
                estimate_events=1,
                estimate_bytes=64,
                edge_hash="a",
            ),
        ]
        ordered_edges = sorted(edges, key=evd_StableEdgeSortKey)
        self.assertEqual([row.dst_ref for row in ordered_edges], ["evt:earlier", "evt:later"])

        events = [
            SimpleNamespace(timestamp_aligned=20.0, core_id=1, seq=5),
            SimpleNamespace(timestamp_aligned=20.0, core_id=0, seq=7),
            SimpleNamespace(timestamp_aligned=10.0, core_id=0, seq=1),
        ]
        ordered_events = sorted(events, key=evd_StableEventSortKey)
        self.assertEqual(
            [(row.timestamp_aligned, row.core_id, row.seq) for row in ordered_events],
            [(10.0, 0, 1), (20.0, 0, 7), (20.0, 1, 5)],
        )

    def test_proof_hash_input_and_recompute_are_stable(self) -> None:
        digest_dict = {
            "snapshot_id": "snapshot:test",
            "closure_mode": "exact",
            "complete_wrt_rule_family": True,
            "rule_family": ["ref_ref", "ref_anchor", "ref_ref"],
            "budget_vector": {"C_events": 6, "S_bytes": 128, "rho_max": 2, "D_max": 3},
            "closure_depth_reached": 2,
            "seed_ref_count": 1,
            "closed_ref_count": 4,
            "missing_required_refs": 0,
            "truncated_frontier_count": 0,
            "frontier_halt_reason": "FRONTIER_EMPTY",
            "events_emitted": 4,
            "bytes_emitted": 256,
        }
        normalized = evd_ProofHashInput(digest_dict)
        self.assertEqual(normalized["rule_family"], ["ref_ref", "ref_anchor"])
        self.assertEqual(normalized["budget_vector"], {"D_max": 3, "C_events": 6, "S_bytes": 128, "rho_max": 2.0})

        digest_object = ProofDigest(
            **normalized,
            scan_count=9,
            seek_count=3,
            window_span_total=17,
            sidecar_lookup_count=4,
            proof_hash="placeholder",
        )
        self.assertEqual(evd_RecomputeProofHash(digest_dict), evd_RecomputeProofHash(digest_object))

    def test_proof_hash_changes_when_rule_family_or_budget_changes(self) -> None:
        base_digest = {
            "snapshot_id": "snapshot:test",
            "closure_mode": "exact",
            "complete_wrt_rule_family": True,
            "rule_family": ["ref_ref"],
            "budget_vector": {"D_max": 3, "C_events": 6, "S_bytes": 128, "rho_max": 2.0},
            "closure_depth_reached": 2,
            "seed_ref_count": 1,
            "closed_ref_count": 4,
            "missing_required_refs": 0,
            "truncated_frontier_count": 0,
            "frontier_halt_reason": "FRONTIER_EMPTY",
            "events_emitted": 4,
            "bytes_emitted": 256,
        }
        changed_rule_family = {
            **base_digest,
            "rule_family": ["ref_ref", "ref_anchor"],
        }
        changed_budget = {
            **base_digest,
            "budget_vector": {"D_max": 4, "C_events": 6, "S_bytes": 128, "rho_max": 2.0},
        }
        base_hash = evd_RecomputeProofHash(base_digest)
        self.assertNotEqual(base_hash, evd_RecomputeProofHash(changed_rule_family))
        self.assertNotEqual(base_hash, evd_RecomputeProofHash(changed_budget))


if __name__ == "__main__":
    unittest.main()
