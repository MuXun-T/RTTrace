from __future__ import annotations

from dataclasses import MISSING, fields
from pathlib import Path
import unittest

import parser.models as parser_models
import spec
import spec.models as spec_models

LIVE_LEGACY_MODEL_NAMES = frozenset({"Dataset"})
RETIRED_LEGACY_MODEL_NAMES = frozenset(
    {
        "ExportJob",
        "HoldEdge",
        "MetricSession",
        "PackageResult",
        "ReproSession",
        "WaitEdge",
    }
)
ALL_LEGACY_MODEL_NAMES = LIVE_LEGACY_MODEL_NAMES | RETIRED_LEGACY_MODEL_NAMES
RUNTIME_MODEL_DIRECTORIES = ("parser", "desktop", "metric", "tool")
FULL_SIGNATURE_DRIFT_WAIVERS = frozenset()
EXPECTED_LEGACY_MODEL_OWNER_MAP = {
    "Dataset": (
        "parser.models.DatasetArtifact + desktop.repository.DatasetRecord + "
        "DatasetHandle(runtime currently Result[str], explicit DTO still pending)"
    ),
    "ExportJob": "desktop.services.ExportService.export_Full/export_Clipped -> Result[{job_id}]",
    "HoldEdge": "parser.models.ResourceGraph.hold_edges[*] (dict edge payload)",
    "MetricSession": "metric.core.MetricSession",
    "PackageResult": (
        "desktop.services.ExportService.export_WritePackage -> "
        "Result[{package_path, entry_count, snapshot_id}]"
    ),
    "ReproSession": (
        "desktop.services.ReproService.repro_OpenPackage -> "
        "Result[{package_path, meta, manifest}] ; "
        "repro_RestoreContext/repro_LoadAsDataset own the rest"
    ),
    "WaitEdge": "parser.models.ResourceGraph.wait_edges[*] (dict edge payload)",
}
EXPECTED_LEGACY_MODEL_DEPRECATION_NOTES = {
    "Dataset": (
        "live legacy compatibility model kept only in spec.models; "
        "top-level spec re-export removed; do not use for new runtime code"
    ),
    "ExportJob": (
        "retired from spec.models/spec package in phase E; "
        "replaced by interface-level export job payload"
    ),
    "HoldEdge": (
        "retired from spec.models in phase E; "
        "edge data remains internal to ResourceGraph.hold_edges"
    ),
    "MetricSession": (
        "retired from spec.models/spec package in phase E; "
        "runtime owner is metric.core.MetricSession"
    ),
    "PackageResult": (
        "retired from spec.models/spec package in phase E; "
        "replaced by interface-level write-package payload"
    ),
    "ReproSession": (
        "retired from spec.models/spec package in phase E; "
        "replaced by repro_OpenPackage payload plus dedicated restore/load interfaces"
    ),
    "WaitEdge": (
        "retired from spec.models in phase E; "
        "edge data remains internal to ResourceGraph.wait_edges"
    ),
}


def _normalize_type_string(annotation: object) -> str:
    text = annotation if isinstance(annotation, str) else str(annotation)
    for prefix in ("parser.models.", "spec.models.", "typing."):
        text = text.replace(prefix, "")
    return " ".join(text.replace("NoneType", "None").split())


def _normalized_field_signature(model: type[object]) -> list[tuple[str, str, tuple[str, object | None]]]:
    rows: list[tuple[str, str, tuple[str, object | None]]] = []
    for item in fields(model):
        if item.default_factory is not MISSING:
            default_meta = ("factory", getattr(item.default_factory, "__name__", repr(item.default_factory)))
        elif item.default is not MISSING:
            default_meta = ("value", item.default)
        else:
            default_meta = ("required", None)
        rows.append((item.name, _normalize_type_string(item.type), default_meta))
    return rows


def _canonical_signature_drift() -> dict[str, tuple[list[tuple[str, str, tuple[str, object | None]]], list[tuple[str, str, tuple[str, object | None]]]]]:
    drift: dict[str, tuple[list[tuple[str, str, tuple[str, object | None]]], list[tuple[str, str, tuple[str, object | None]]]]] = {}
    for name in sorted(spec.CANONICAL_PEER_MODEL_NAMES):
        spec_signature = _normalized_field_signature(getattr(spec_models, name))
        parser_signature = _normalized_field_signature(getattr(parser_models, name))
        if spec_signature != parser_signature:
            drift[name] = (spec_signature, parser_signature)
    return drift


def _runtime_spec_model_import_hits() -> list[str]:
    root = Path(__file__).resolve().parents[2]
    hits: list[str] = []
    for directory in RUNTIME_MODEL_DIRECTORIES:
        for path in sorted((root / directory).rglob("*.py")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("import spec.models") or stripped.startswith("from spec.models import"):
                    hits.append(f"{path.relative_to(root)}:{lineno}: {stripped}")
    return hits


class SpecModelsCompatTests(unittest.TestCase):
    def assertCanonicalRuntimeSignatureMatches(self, name: str) -> None:
        self.assertTrue(spec.has_canonical_peer(name))
        self.assertIs(spec.canonical_model(name), getattr(parser_models, name))
        self.assertEqual(
            _normalized_field_signature(getattr(spec_models, name)),
            _normalized_field_signature(getattr(parser_models, name)),
        )

    def test_legacy_only_model_inventory_is_explicit(self) -> None:
        self.assertEqual(spec.LEGACY_ONLY_MODEL_NAMES, LIVE_LEGACY_MODEL_NAMES)
        self.assertEqual(spec.RETIRED_LEGACY_MODEL_NAMES, RETIRED_LEGACY_MODEL_NAMES)
        self.assertTrue(spec.LEGACY_ONLY_MODEL_NAMES.isdisjoint(spec.CANONICAL_PEER_MODEL_NAMES))
        self.assertTrue(spec.RETIRED_LEGACY_MODEL_NAMES.isdisjoint(spec.CANONICAL_PEER_MODEL_NAMES))
        self.assertTrue(spec.LEGACY_ONLY_MODEL_NAMES.isdisjoint(spec.RETIRED_LEGACY_MODEL_NAMES))
        self.assertEqual(spec.LEGACY_ONLY_MODEL_NAMES | spec.RETIRED_LEGACY_MODEL_NAMES, ALL_LEGACY_MODEL_NAMES)
        for name in LIVE_LEGACY_MODEL_NAMES:
            self.assertTrue(spec.is_legacy_only_model(name))
            self.assertIsNone(spec.canonical_model(name))
            self.assertTrue(hasattr(spec_models, name))
        for name in RETIRED_LEGACY_MODEL_NAMES:
            self.assertFalse(spec.is_legacy_only_model(name))
            self.assertIsNone(spec.canonical_model(name))
            self.assertFalse(hasattr(spec_models, name))

    def test_spec_package_declares_canonical_runtime_models(self) -> None:
        self.assertEqual(spec.CANONICAL_RUNTIME_MODEL_MODULE, "parser.models")
        self.assertTrue(spec.has_canonical_peer("UnifiedEvent"))
        self.assertIs(spec.canonical_model("UnifiedEvent"), parser_models.UnifiedEvent)
        self.assertTrue(spec.has_canonical_peer("AnalysisContext"))
        self.assertIs(spec.canonical_model("AnalysisContext"), parser_models.AnalysisContext)

    def test_no_runtime_module_imports_spec_models(self) -> None:
        self.assertEqual(_runtime_spec_model_import_hits(), [])

    def test_legacy_only_exports_have_owner_mapping(self) -> None:
        self.assertEqual(spec.LEGACY_MODEL_OWNER_MAP, EXPECTED_LEGACY_MODEL_OWNER_MAP)
        self.assertEqual(set(spec.LEGACY_MODEL_OWNER_MAP), ALL_LEGACY_MODEL_NAMES)
        dataset_owner = spec.LEGACY_MODEL_OWNER_MAP["Dataset"]
        self.assertIn("DatasetArtifact", dataset_owner)
        self.assertIn("DatasetRecord", dataset_owner)
        self.assertIn("DatasetHandle", dataset_owner)
        self.assertIn("Result[str]", dataset_owner)
        self.assertEqual(spec.LEGACY_MODEL_OWNER_MAP["MetricSession"], "metric.core.MetricSession")
        self.assertIn("Result[{job_id}]", spec.LEGACY_MODEL_OWNER_MAP["ExportJob"])
        self.assertIn("entry_count", spec.LEGACY_MODEL_OWNER_MAP["PackageResult"])
        self.assertIn("repro_RestoreContext/repro_LoadAsDataset", spec.LEGACY_MODEL_OWNER_MAP["ReproSession"])

    def test_legacy_model_deprecation_notes_are_explicit(self) -> None:
        self.assertEqual(spec.LEGACY_MODEL_DEPRECATION_NOTES, EXPECTED_LEGACY_MODEL_DEPRECATION_NOTES)
        self.assertEqual(set(spec.LEGACY_MODEL_DEPRECATION_NOTES), ALL_LEGACY_MODEL_NAMES)
        self.assertIn("live legacy", spec.LEGACY_MODEL_DEPRECATION_NOTES["Dataset"])
        self.assertIn("spec.models", spec.LEGACY_MODEL_DEPRECATION_NOTES["Dataset"])
        for name in RETIRED_LEGACY_MODEL_NAMES:
            self.assertIn("retired", spec.LEGACY_MODEL_DEPRECATION_NOTES[name])

    def test_spec_package_omits_legacy_dataclass_exports(self) -> None:
        for name in ALL_LEGACY_MODEL_NAMES:
            self.assertFalse(hasattr(spec, name), name)
        self.assertTrue(hasattr(spec_models, "Dataset"))
        for name in RETIRED_LEGACY_MODEL_NAMES:
            self.assertFalse(hasattr(spec_models, name), name)

    def test_task_state_view_model_matches_detailed_design_fields(self) -> None:
        parser_field_names = [item.name for item in fields(parser_models.TaskStateViewModel)]
        spec_field_names = [item.name for item in fields(spec_models.TaskStateViewModel)]
        self.assertEqual(
            parser_field_names,
            ["time_window", "lane_order", "rows", "state_legend", "summary", "cursor_hint", "trusted"],
        )
        self.assertEqual(spec_field_names, parser_field_names)
        self.assertTrue(spec.has_canonical_peer("TaskStateViewSegment"))
        self.assertTrue(spec.has_canonical_peer("TaskStateViewRow"))
        self.assertIs(spec.canonical_model("TaskStateViewSegment"), parser_models.TaskStateViewSegment)
        self.assertIs(spec.canonical_model("TaskStateViewRow"), parser_models.TaskStateViewRow)
        self.assertEqual(
            [item.name for item in fields(parser_models.TaskStateViewSegment)],
            [item.name for item in fields(spec_models.TaskStateViewSegment)],
        )
        self.assertEqual(
            [item.name for item in fields(parser_models.TaskStateViewRow)],
            [item.name for item in fields(spec_models.TaskStateViewRow)],
        )

    def test_compare_scope_matches_canonical_runtime_fields(self) -> None:
        self.assertTrue(spec.has_canonical_peer("CompareScope"))
        self.assertIs(spec.canonical_model("CompareScope"), parser_models.CompareScope)
        self.assertEqual(
            [item.name for item in fields(parser_models.CompareScope)],
            [
                "baseline_id",
                "candidate_id",
                "aligned_time_window",
                "filter",
                "dimensions",
                "metric_ids",
                "bucket_size",
                "evidence_policy",
                "scope_id",
            ],
        )
        self.assertEqual(
            [item.name for item in fields(spec_models.CompareScope)],
            [item.name for item in fields(parser_models.CompareScope)],
        )

    def test_diff_summary_matches_canonical_runtime_fields(self) -> None:
        self.assertTrue(spec.has_canonical_peer("DiffSummary"))
        self.assertIs(spec.canonical_model("DiffSummary"), parser_models.DiffSummary)
        self.assertEqual(
            [item.name for item in fields(parser_models.DiffSummary)],
            [
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
            ],
        )
        self.assertEqual(
            [item.name for item in fields(spec_models.DiffSummary)],
            [item.name for item in fields(parser_models.DiffSummary)],
        )

    def test_diff_detail_matches_canonical_runtime_fields(self) -> None:
        self.assertTrue(spec.has_canonical_peer("DiffDetail"))
        self.assertIs(spec.canonical_model("DiffDetail"), parser_models.DiffDetail)
        expected = [
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
        ]
        self.assertEqual([item.name for item in fields(parser_models.DiffDetail)], expected)
        self.assertEqual(
            [item.name for item in fields(spec_models.DiffDetail)],
            [item.name for item in fields(parser_models.DiffDetail)],
        )

    def test_zero_full_signature_drift_or_explicitly_waived(self) -> None:
        full_signature_drift = _canonical_signature_drift()
        self.assertTrue(FULL_SIGNATURE_DRIFT_WAIVERS <= spec.CANONICAL_PEER_MODEL_NAMES)
        self.assertEqual(sorted(full_signature_drift), sorted(FULL_SIGNATURE_DRIFT_WAIVERS), full_signature_drift)
        if not FULL_SIGNATURE_DRIFT_WAIVERS:
            self.assertEqual(full_signature_drift, {})
        self.assertEqual(len(spec.CANONICAL_PEER_MODEL_NAMES), 26)
        self.assertEqual(len(full_signature_drift), 0)

    def test_all_canonical_peers_match_full_signature_or_are_explicitly_waived(self) -> None:
        for name in sorted(spec.CANONICAL_PEER_MODEL_NAMES - FULL_SIGNATURE_DRIFT_WAIVERS):
            with self.subTest(name=name):
                self.assertCanonicalRuntimeSignatureMatches(name)

    def test_exec_slice_matches_canonical_runtime_signature(self) -> None:
        self.assertCanonicalRuntimeSignatureMatches("ExecSlice")
        self.assertEqual(
            [item.name for item in fields(spec_models.ExecSlice)][3:5],
            ["job_id", "instance_id"],
        )

    def test_task_state_seg_matches_canonical_runtime_signature(self) -> None:
        self.assertCanonicalRuntimeSignatureMatches("TaskStateSeg")
        self.assertEqual(
            [item.name for item in fields(spec_models.TaskStateSeg)][3:5],
            ["job_id", "instance_id"],
        )

    def test_alert_matches_canonical_runtime_signature(self) -> None:
        self.assertCanonicalRuntimeSignatureMatches("Alert")
        support_level = fields(spec_models.Alert)[-1]
        self.assertEqual(support_level.name, "support_level")
        self.assertEqual(support_level.default, "exact")

    def test_global_header_matches_canonical_runtime_signature(self) -> None:
        self.assertCanonicalRuntimeSignatureMatches("GlobalHeader")
        self.assertEqual(fields(spec_models.GlobalHeader)[-1].name, "run_id")

    def test_rebuild_bundle_matches_canonical_runtime_signature(self) -> None:
        self.assertCanonicalRuntimeSignatureMatches("RebuildBundle")
        self.assertEqual(
            [item.name for item in fields(spec_models.RebuildBundle)][-4:],
            ["alignment", "segment_metas", "header", "index_bundle"],
        )

    def test_index_bundle_matches_formal_contract(self) -> None:
        self.assertCanonicalRuntimeSignatureMatches("IndexBundle")
        expected = ["time_index", "task_index", "core_index", "event_type_index", "summary"]
        self.assertEqual([item.name for item in fields(parser_models.IndexBundle)], expected)
        self.assertEqual([item.name for item in fields(spec_models.IndexBundle)], expected)
        self.assertNotIn("dataset_id", expected)
        self.assertNotIn("time_summary", expected)
        self.assertNotIn("resource_index", expected)
        self.assertNotIn("irq_index", expected)

    def test_task_state_query_matches_formal_contract(self) -> None:
        self.assertCanonicalRuntimeSignatureMatches("TaskStateQuery")
        expected = [
            "time_window",
            "lane_group",
            "state_mask",
            "task_filter",
            "anchor_ref",
            "include_summary",
        ]
        self.assertEqual([item.name for item in fields(parser_models.TaskStateQuery)], expected)
        self.assertEqual([item.name for item in fields(spec_models.TaskStateQuery)], expected)
        self.assertNotIn("filter", expected)

    def test_playback_state_matches_formal_contract(self) -> None:
        self.assertCanonicalRuntimeSignatureMatches("PlaybackState")
        expected = [
            "replay_id",
            "status",
            "rate",
            "cursor",
            "anchor_ref",
            "visible_window",
            "linked_views",
            "trusted",
        ]
        self.assertEqual([item.name for item in fields(parser_models.PlaybackState)], expected)
        self.assertEqual([item.name for item in fields(spec_models.PlaybackState)], expected)
        self.assertNotIn("current_index", expected)
        self.assertNotIn("cursor_ts", expected)
        self.assertNotIn("mode", expected)

    def test_resource_graph_matches_canonical_runtime_signature(self) -> None:
        self.assertCanonicalRuntimeSignatureMatches("ResourceGraph")
        expected = ["nodes", "hold_edges", "wait_edges", "hotspot_stats"]
        self.assertEqual([item.name for item in fields(parser_models.ResourceGraph)], expected)
        self.assertEqual([item.name for item in fields(spec_models.ResourceGraph)], expected)


if __name__ == "__main__":
    unittest.main()
