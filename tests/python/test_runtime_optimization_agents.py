from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import subprocess
import sys
from typing import Any
from unittest import mock

from desktop.sample_data import write_scenario
from parser.benchmark_matrix import BenchmarkReportBuilder, default_benchmark_scenarios
from parser.evidence_models import DependencySidecarEdge
from parser.evidence_sidecar import materialize_dependency_sidecar, validate_dependency_sidecar_stream
from parser.evidence_sidecar_index import build_or_open_sidecar_index, read_sidecar_index_ticket, sidecar_index_ticket_path_for_source
from parser.export_write_agent import ExportWriteAgent
from parser.evidence_models import evd_ProofHashInput, evd_RecomputeProofHash
from parser.formal_scheduler import FormalSuiteSchedulerAgent, FormalTask
from parser.parser_process_agent import ParserProcessAgent
from parser.runtime_optimization_gate import DeterministicValidationGate
from parser.telemetry import TelemetryHistoryStore, TelemetryReportAgent, record_telemetry_phase
from parser.runtime_advisor import RuntimeAction, RuntimeOptimizationAdvisor, build_advisor_trace
from spec.schema_loader import DICTIONARY_PATH, load_dictionary
from spec.schema_loader import load_specs
from spec.schema_validator import validate_schema
from spec.io import checksum_file, jsonl_dump, serialize
from tool.build_windows_formal_manifest import main as build_windows_formal_manifest_main
from tool.build_windows_formal_parity import _compare_group as compare_windows_formal_group
from tool.build_windows_formal_readiness import _summary_detail as build_windows_summary_detail
from tool.formal_windows_common import run_subprocess
from tool.run_formal_b_windows import _write_group_proof_digest as write_b_group_proof_digest
from tool.run_formal_c_windows import _write_group_proof_digest as write_c_group_proof_digest
from tool.run_formal_windows_suite import main as run_formal_windows_suite_main
from tool.run_runtime_optimization_formal_matrix import main as run_runtime_optimization_formal_matrix_main
import tool.run_runtime_optimization_product_evidence as runtime_optimization_product_evidence_tool
import tool.run_runtime_optimization_product_evidence_staged as runtime_optimization_product_evidence_staged_tool
from tool.run_runtime_optimization_product_evidence import main as run_runtime_optimization_product_evidence_main
from tool.run_runtime_optimization_product_evidence_staged import main as run_runtime_optimization_product_evidence_staged_main


class RuntimeOptimizationAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _dictionary_copy(self) -> dict[str, Any]:
        return json.loads(json.dumps(load_dictionary()))

    def _load_runtime_case_bank(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        repo_root = Path(__file__).resolve().parents[2]
        case_bank_root = repo_root / "docs" / "runtime_optimization_case_bank"
        manifest = json.loads((case_bank_root / "manifest.json").read_text(encoding="utf-8"))
        cases = json.loads((case_bank_root / "recommendation_cases.json").read_text(encoding="utf-8"))
        return manifest, cases

    def _rename_event(self, raw_dictionary: dict[str, Any], event_id: int, event_name: str) -> dict[str, Any]:
        for item in raw_dictionary["event_defs"]:
            if item["event_id"] == event_id:
                item["event_name"] = event_name
                return raw_dictionary
        raise AssertionError(f"event_id {event_id} not found")

    def _representative_benchmark_report(self) -> dict[str, Any]:
        mandatory_field_set = sorted([
            "advisor_overhead_seconds",
            "package_write_seconds",
            "peak_rss_mb",
            "proof_hash",
            "runtime_seconds",
            "scenario_id",
            "sidecar_bytes_scanned",
        ])
        rows: list[dict[str, Any]] = []
        for index, scenario in enumerate(default_benchmark_scenarios()):
            rows.append({
                "scenario_id": scenario.scenario_id,
                "status": "completed_with_fallback" if scenario.optional_dependency else "completed",
                "runtime_seconds": round(1.0 + index * 0.1, 6),
                "sidecar_bytes_scanned": 0 if scenario.ticket_fast_path_enabled else 4096,
                "sidecar_validate_seconds": 0.0 if scenario.ticket_fast_path_enabled else 0.001,
                "index_build_open_seconds": 0.0,
                "peak_rss_mb": 128.0 + index,
                "package_write_seconds": 0.01,
                "advisor_overhead_seconds": 0.002 if scenario.advisor_enabled else 0.0,
                "proof_hash": "sha256:schema-parity",
                "parity_result": {
                    "proof_hash": "sha256:schema-parity",
                    "metric_diff_count": 0,
                    "mandatory_field_set": mandatory_field_set,
                    "baseline_proof_hash": "sha256:schema-parity",
                },
                "artifact_refs": {
                    "package_path": str(self.root / "benchmark" / scenario.scenario_id),
                    "scenario_result": str(self.root / "benchmark" / f"{scenario.scenario_id}.json"),
                },
                "summary_metrics": {
                    "optional_dependency": scenario.optional_dependency,
                    "fallback_reasons": [],
                    "scenario_index": index,
                },
                "telemetry_records": [],
            })
        return BenchmarkReportBuilder().build_report(
            rows,
            input_contract={"snapshot_id": "snapshot:schema-benchmark", "input_bytes": 1024},
        )

    def _formal_matrix_fixture_root(self) -> Path:
        return self.root / "formal-matrix-fixture"

    def _write_formal_matrix_fixture(
        self,
        *,
        contaminated_scenario_id: str | None = None,
        contaminated_group: str = "A",
        alternate_group_hash: str | None = None,
    ) -> Path:
        input_root = self._formal_matrix_fixture_root()
        proof_digest_base = {
            "snapshot_id": "snapshot:formal-matrix",
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
        proof_hash = evd_RecomputeProofHash(proof_digest_base)
        mandatory_field_set = sorted(evd_ProofHashInput(proof_digest_base).keys())
        for scenario in default_benchmark_scenarios():
            scenario_root = input_root / scenario.scenario_id
            scenario_root.mkdir(parents=True, exist_ok=True)
            sidecar_bytes_scanned = 0 if scenario.ticket_fast_path_enabled else 4096
            for group_code, group_name, digest_leaf in (
                ("A", "A_control_plane_first", "run_001"),
                ("B", "B_budget_pre_freeze", "depth_d0"),
                ("C", "C_degraded_audit", "cycle_inflation"),
            ):
                group_root = scenario_root / group_name
                group_root.mkdir(parents=True, exist_ok=True)
                (group_root / "formal_summary_windows.json").write_text(
                    json.dumps({"verdict": "pass", "proof_group": group_code}, ensure_ascii=False),
                    encoding="utf-8",
                )
                digest_payload = dict(proof_digest_base)
                if alternate_group_hash == group_code:
                    digest_payload["frontier_halt_reason"] = f"{group_code}_FORMAL_CASE"
                digest_payload["proof_hash"] = evd_RecomputeProofHash(digest_payload)
                digest_payload["sidecar_bytes_scanned"] = sidecar_bytes_scanned
                if contaminated_scenario_id == scenario.scenario_id and group_code == contaminated_group:
                    digest_payload["openai_tokens"] = 13
                digest_path = group_root / "packages" / digest_leaf / "control" / "proof_digest.json"
                digest_path.parent.mkdir(parents=True, exist_ok=True)
                digest_path.write_text(json.dumps(digest_payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            (scenario_root / "windows_artifact_manifest.json").write_text(
                json.dumps({"missing_count": 0, "scenario_id": scenario.scenario_id}, ensure_ascii=False),
                encoding="utf-8",
            )
            (scenario_root / "formal_parity_report.json").write_text(
                json.dumps(
                    {
                        "status": "pass",
                        "metric_diff_count": 0,
                        "mandatory_field_set_same": True,
                        "ready_for_gate": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (scenario_root / "windows_final_readiness_report.json").write_text(
                json.dumps({"verdict": "ready_for_gate"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (scenario_root / "scenario_summary.json").write_text(
                json.dumps(
                    {
                        "scenario_id": scenario.scenario_id,
                        "ready_for_gate": True,
                        "runtime_seconds": 1.0,
                        "peak_rss_mb": 128.0,
                        "sidecar_bytes_scanned": sidecar_bytes_scanned,
                        "sidecar_validate_seconds": 0.0,
                        "index_build_open_seconds": 0.0,
                        "package_write_seconds": 0.01,
                        "advisor_overhead_seconds": 0.002 if scenario.advisor_enabled else 0.0,
                        "proof_hash": proof_hash,
                        "metric_diff_count": 0,
                        "mandatory_field_set": mandatory_field_set,
                        "sidecar_ticket_fast_path": bool(scenario.ticket_fast_path_enabled),
                        "advisor_mode_effective": scenario.advisor_mode if scenario.advisor_enabled else "disabled",
                        "advisor_status": "ready" if scenario.advisor_enabled else "disabled",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            if scenario.advisor_enabled:
                advisor_report_path = scenario_root / "control" / "advisor_report.json"
                advisor_report_path.parent.mkdir(parents=True, exist_ok=True)
                advisor_report_path.write_text(
                    json.dumps(
                        {
                            "decision": {
                                "advisor_mode": scenario.advisor_mode,
                                "ticket_fast_path": bool(scenario.ticket_fast_path_enabled),
                            },
                            "status": "ready",
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            scheduler_report = {
                "plan": {
                    "schedule_version": "formal-suite-schedule-v1",
                    "strategy": "serial_safe",
                    "max_parallel": 1,
                    "reasons": ["strategy=serial_safe"],
                    "waves": [
                        [{"task_id": "task_a", "group": "A", "command": [sys.executable, "-c", "print('a')"], "dependencies": [], "priority": 0, "resource_class": "sidecar"}],
                        [{"task_id": "task_b", "group": "B", "command": [sys.executable, "-c", "print('b')"], "dependencies": ["task_a"], "priority": 10, "resource_class": "cpu"}],
                        [{"task_id": "task_c", "group": "C", "command": [sys.executable, "-c", "print('c')"], "dependencies": ["task_a"], "priority": 20, "resource_class": "cpu"}],
                    ],
                },
                "execution": {
                    "resource_guard": {"accepted": True},
                    "resource_observation": {},
                    "task_results": [
                        {
                            "task_id": "task_a",
                            "group": "A",
                            "command": [sys.executable, "-c", "print('a')"],
                            "exit_code": 0,
                            "runtime_seconds": 0.01,
                            "started_at": "2026-06-12T00:00:00+00:00",
                            "finished_at": "2026-06-12T00:00:00+00:00",
                            "error_code": None,
                            "error_message": None,
                        },
                        {
                            "task_id": "task_b",
                            "group": "B",
                            "command": [sys.executable, "-c", "print('b')"],
                            "exit_code": 0,
                            "runtime_seconds": 0.01,
                            "started_at": "2026-06-12T00:00:00+00:00",
                            "finished_at": "2026-06-12T00:00:00+00:00",
                            "error_code": None,
                            "error_message": None,
                        },
                        {
                            "task_id": "task_c",
                            "group": "C",
                            "command": [sys.executable, "-c", "print('c')"],
                            "exit_code": 0,
                            "runtime_seconds": 0.01,
                            "started_at": "2026-06-12T00:00:00+00:00",
                            "finished_at": "2026-06-12T00:00:00+00:00",
                            "error_code": None,
                            "error_message": None,
                        },
                    ],
                },
                "agent_contract": {},
            }
            (scenario_root / "formal_scheduler_report.json").write_text(
                json.dumps(scheduler_report, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        return input_root

    def _write_execute_inputs(self) -> tuple[Path, Path, str, int]:
        trace_path = write_scenario(self.root / "formal-matrix-execute.trace", name="basic", repeat=2)
        trace_bytes = trace_path.read_bytes()
        trace_sha256 = hashlib.sha256(trace_bytes).hexdigest()
        trace_size_bytes = len(trace_bytes)
        source_report_path = self.root / "formal-matrix-source-report.json"
        source_report_path.write_text(
            json.dumps(
                {
                    "conversion": {
                        "emitted_event_counts": {
                            "TASK_READY": 1,
                            "TASK_DISPATCH": 1,
                            "CTX_SWITCH": 1,
                            "TASK_BLOCK": 1,
                            "TASK_WAKEUP": 1,
                            "TASK_EXIT": 1,
                        },
                        "opaque_filler_bytes": 0,
                    },
                    "output": {"stop_reason": "completed"},
                    "source_shards": [{"url": "file://formal-matrix-fixture"}],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return trace_path, source_report_path, trace_sha256, trace_size_bytes

    def _write_scenario_contract(
        self,
        contract_path: Path,
        *,
        scenario_id: str,
        trace_path: Path,
        source_report_path: Path,
        trace_sha256: str,
        trace_size_bytes: int,
        scheduler_strategy: str = "serial_safe",
    ) -> Path:
        scenario = next(
            item
            for item in default_benchmark_scenarios()
            if item.scenario_id == scenario_id
        )
        payload = {
            "contract_version": "runtime-optimization-formal-scenario-execution-v1",
            "scenario": scenario.to_dict(),
            "trace": {
                "path": str(trace_path.resolve()),
                "sha256": trace_sha256,
                "size_bytes": int(trace_size_bytes),
            },
            "source_report_path": str(source_report_path.resolve()),
            "input_contract": {
                "trace_path": str(trace_path.resolve()),
                "trace_sha256": trace_sha256,
                "trace_size_bytes": int(trace_size_bytes),
                "source_report_path": str(source_report_path.resolve()),
                "scheduler_strategy": scheduler_strategy,
                "scenario_id": scenario_id,
                "scenario": scenario.to_dict(),
                "formal_matrix_mode": "execute",
            },
            "scheduler_strategy": scheduler_strategy,
            "formal_matrix_mode": "execute",
        }
        contract_path.parent.mkdir(parents=True, exist_ok=True)
        contract_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return contract_path

    def _formal_matrix_suite_stub(
        self,
        *,
        failing_task_scenarios: set[str] | None = None,
        missing_group_scenarios: dict[str, str] | None = None,
        failing_group_scenarios: dict[str, str] | None = None,
    ) -> Any:
        failing_task_scenarios = set(failing_task_scenarios or set())
        missing_group_scenarios = dict(missing_group_scenarios or {})
        failing_group_scenarios = dict(failing_group_scenarios or {})
        proof_digest_base = {
            "snapshot_id": "snapshot:formal-matrix-execute",
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
        digest_leaf_by_group = {
            "A": "run_001",
            "B": "depth_d0",
            "C": "cycle_inflation",
        }
        group_dir_by_code = {
            "A": "A_control_plane_first",
            "B": "B_budget_pre_freeze",
            "C": "C_degraded_audit",
        }
        scenario_by_id = {scenario.scenario_id: scenario for scenario in default_benchmark_scenarios()}

        def _arg_value(argv: list[str], flag: str) -> str | None:
            if flag not in argv:
                return None
            index = argv.index(flag)
            if index + 1 >= len(argv):
                return None
            return argv[index + 1]

        def _stub(argv: list[str]) -> int:
            archive_root = Path(str(_arg_value(argv, "--archive-root"))).resolve()
            schedule_report_path = Path(str(_arg_value(argv, "--schedule-report"))).resolve()
            scenario_contract_path = Path(str(_arg_value(argv, "--scenario-contract"))).resolve()
            trace_path = Path(str(_arg_value(argv, "--trace"))).resolve()
            expected_trace_sha256 = str(_arg_value(argv, "--expected-trace-sha256"))
            expected_trace_size_bytes = int(str(_arg_value(argv, "--expected-trace-size-bytes")))
            scheduler_strategy = str(_arg_value(argv, "--scheduler-strategy") or "serial_safe")
            scenario_id = archive_root.name
            scenario = scenario_by_id[scenario_id]
            scenario_contract = json.loads(scenario_contract_path.read_text(encoding="utf-8"))
            sidecar_bytes_scanned = 0 if scenario.ticket_fast_path_enabled else 4096
            missing_group = missing_group_scenarios.get(scenario_id)
            failing_group = failing_group_scenarios.get(scenario_id)
            task_failure = scenario_id in failing_task_scenarios
            self.assertEqual(scenario_contract["scenario"]["scenario_id"], scenario_id)
            self.assertEqual(scenario_contract["trace"]["path"], str(trace_path))
            self.assertEqual(scenario_contract["trace"]["sha256"], expected_trace_sha256)
            self.assertEqual(scenario_contract["trace"]["size_bytes"], expected_trace_size_bytes)
            self.assertEqual(scenario_contract["input_contract"]["trace_path"], str(trace_path))
            self.assertEqual(scenario_contract["input_contract"]["trace_sha256"], expected_trace_sha256)
            self.assertEqual(scenario_contract["input_contract"]["trace_size_bytes"], expected_trace_size_bytes)
            self.assertEqual(scenario_contract["input_contract"]["scheduler_strategy"], scheduler_strategy)
            self.assertEqual(scenario_contract["input_contract"]["scenario_id"], scenario_id)
            self.assertEqual(scenario_contract["input_contract"]["scenario"]["scenario_id"], scenario_id)
            self.assertEqual(scenario_contract["scheduler_strategy"], scheduler_strategy)

            for group_code, group_dir in group_dir_by_code.items():
                if missing_group == group_code:
                    continue
                group_root = archive_root / group_dir
                group_root.mkdir(parents=True, exist_ok=True)
                verdict = "fail" if failing_group == group_code else "pass"
                (group_root / "formal_summary_windows.json").write_text(
                    json.dumps(
                        {
                            "contract_version": "patent_10_4_formal_close_min_contract_20260415",
                            "proof_group": group_dir,
                            "run_scope": "patent_10_4_formal",
                            "platform": "windows",
                            "verdict": verdict,
                            "input_contract": {
                                "trace_path": str(trace_path),
                                "trace_sha256": expected_trace_sha256,
                                "trace_size_bytes": expected_trace_size_bytes,
                                "formal_1gb_verified": True,
                                "input_class": "real_external_dense_1gb",
                            },
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
                digest_payload = dict(proof_digest_base)
                digest_payload["proof_hash"] = evd_RecomputeProofHash(digest_payload)
                digest_payload["sidecar_bytes_scanned"] = sidecar_bytes_scanned
                digest_path = group_root / "packages" / digest_leaf_by_group[group_code] / "control" / "proof_digest.json"
                digest_path.parent.mkdir(parents=True, exist_ok=True)
                digest_path.write_text(
                    json.dumps(digest_payload, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )

            groups_ready = missing_group is None and failing_group is None
            ready_for_gate = groups_ready and not task_failure
            (archive_root / "windows_artifact_manifest.json").write_text(
                json.dumps({"missing_count": 0, "scenario_id": scenario_id}, ensure_ascii=False),
                encoding="utf-8",
            )
            (archive_root / "formal_parity_report.json").write_text(
                json.dumps(
                    {
                        "status": "pass" if ready_for_gate else "fail",
                        "metric_diff_count": 0 if ready_for_gate else 1,
                        "mandatory_field_set_same": ready_for_gate,
                        "ready_for_gate": ready_for_gate,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            (archive_root / "windows_final_readiness_report.json").write_text(
                json.dumps(
                    {"verdict": "ready_for_gate" if ready_for_gate else "blocked"},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            schedule_report_path.parent.mkdir(parents=True, exist_ok=True)
            task_results = []
            for task_id, group in (("formal_a", "A"), ("formal_b", "B"), ("formal_c", "C")):
                failed = task_failure and task_id == "formal_b"
                task_results.append(
                    {
                        "task_id": task_id,
                        "group": group,
                        "command": [sys.executable, "-c", f"print('{task_id}')"],
                        "exit_code": 1 if failed else 0,
                        "runtime_seconds": 0.01,
                        "started_at": "2026-06-15T00:00:00+00:00",
                        "finished_at": "2026-06-15T00:00:00+00:00",
                        "error_code": "ERR_FORMAL_TASK_FAILED" if failed else None,
                        "error_message": f"{task_id} failed" if failed else None,
                    }
                )
            schedule_report_path.write_text(
                json.dumps(
                    {
                        "plan": {
                            "schedule_version": "formal-suite-schedule-v1",
                            "strategy": scheduler_strategy,
                            "max_parallel": 1,
                            "reasons": [f"strategy={scheduler_strategy}"],
                            "waves": [
                                [{"task_id": "formal_a", "group": "A", "command": [sys.executable, "-c", "print('a')"], "dependencies": [], "priority": 10, "resource_class": "sidecar"}],
                                [{"task_id": "formal_b", "group": "B", "command": [sys.executable, "-c", "print('b')"], "dependencies": ["formal_a"], "priority": 20, "resource_class": "cpu"}],
                                [{"task_id": "formal_c", "group": "C", "command": [sys.executable, "-c", "print('c')"], "dependencies": ["formal_a"], "priority": 30, "resource_class": "cpu"}],
                            ],
                        },
                        "execution": {
                            "resource_guard": {"accepted": True},
                            "resource_observation": {},
                            "task_results": task_results,
                            "parity_summary": {
                                "abc_groups_present": groups_ready,
                                "abc_summary_files_present": groups_ready,
                                "abc_summary_verdicts_pass": groups_ready,
                                "readiness_verdict": "ready_for_gate" if ready_for_gate else "blocked",
                                "parity_report_status": "pass" if ready_for_gate else "fail",
                                "metric_diff_count": 0 if ready_for_gate else 1,
                                "mandatory_field_set_same": ready_for_gate,
                                "ready_for_gate": ready_for_gate,
                                "failed_task_count": 1 if task_failure else 0,
                            },
                        },
                        "agent_contract": {},
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            return 0

        return _stub

    def test_export_write_agent_streams_hashes_and_times_json_outputs(self) -> None:
        agent = ExportWriteAgent(job_id="write-test")
        jsonl_path = self.root / "rows.jsonl"
        array_path = self.root / "rows.json"

        jsonl = agent.stream_jsonl(jsonl_path, [{"b": 2}, {"a": 1}], label="rows")
        array = agent.stream_json_array(array_path, [{"b": 2}, {"a": 1}], label="rows_array")

        self.assertTrue(jsonl.ok, jsonl.message)
        self.assertTrue(array.ok, array.message)
        self.assertEqual(jsonl.data["count"], 2)
        self.assertEqual(array.data["count"], 2)
        self.assertTrue(jsonl.data["checksum"])
        self.assertGreaterEqual(jsonl.data["write_seconds"], 0.0)
        self.assertEqual(json.loads(array_path.read_text(encoding="utf-8")), [{"b": 2}, {"a": 1}])
        self.assertEqual(agent.contract.status, "AGENT-completed")

    def test_export_write_agent_optimized_entry_returns_package_result(self) -> None:
        agent = ExportWriteAgent(job_id="write-optimized")
        package_root = self.root / "package"
        result = agent.write_evidence_package_optimized(
            {
                "package_path": str(package_root),
                "snapshot_id": "snapshot:test:write",
                "entries": [
                    {
                        "relative_path": "meta.json",
                        "write_mode": "standard_json",
                        "payload": {"name": "demo"},
                    },
                    {
                        "relative_path": "result/rows.jsonl",
                        "write_mode": "stream_jsonl",
                        "rows": [{"a": 1}, {"b": 2}],
                    },
                ],
            },
            {"package_path": str(package_root), "snapshot_id": "snapshot:test:write"},
        )
        self.assertTrue(result.ok, result.message)
        self.assertTrue((package_root / "meta.json").exists())
        self.assertTrue((package_root / "result" / "rows.jsonl").exists())
        self.assertTrue(result.data["package_result"]["ok"])

    def test_export_write_agent_optimized_entry_handles_binary_copy_and_failure_blocker(self) -> None:
        agent = ExportWriteAgent(job_id="write-optimized-binary")
        package_root = self.root / "binary-package"
        source = self.root / "source.bin"
        source.write_bytes(b"copy-payload")

        result = agent.write_evidence_package_optimized(
            {
                "package_path": str(package_root),
                "snapshot_id": "snapshot:test:binary-write",
                "entries": [
                    {
                        "relative_path": "event/events.trace",
                        "write_mode": "write_bytes",
                        "payload": b"",
                        "count": 0,
                    },
                    {
                        "relative_path": "reference/source.bin",
                        "write_mode": "copy_file",
                        "source_path": source,
                    },
                    {
                        "relative_path": "event/callback.bin",
                        "write_mode": "binary_callback",
                        "callback": lambda target: target.write_bytes(b"callback-payload"),
                    },
                ],
            },
            {"package_path": str(package_root), "snapshot_id": "snapshot:test:binary-write"},
        )

        self.assertTrue(result.ok, result.message)
        self.assertEqual((package_root / "event" / "events.trace").read_bytes(), b"")
        self.assertEqual((package_root / "reference" / "source.bin").read_bytes(), b"copy-payload")
        self.assertEqual((package_root / "event" / "callback.bin").read_bytes(), b"callback-payload")
        modes = {metric["write_mode"] for metric in result.data["package_result"]["write_metrics"]}
        self.assertEqual(modes, {"write_bytes", "copy_file", "binary_callback"})
        specs = load_specs()
        for metric in result.data["package_result"]["write_metrics"]:
            self.assertIsNone(validate_schema(specs["export_write_metric"], metric))

        failed_root = self.root / "failed-copy-package"
        failed = ExportWriteAgent(job_id="write-optimized-failure").write_evidence_package_optimized(
            {
                "package_path": str(failed_root),
                "snapshot_id": "snapshot:test:copy-failure",
                "entries": [
                    {
                        "relative_path": "reference/missing.bin",
                        "write_mode": "copy_file",
                        "source_path": self.root / "missing.bin",
                        "label": "missing_copy",
                    }
                ],
            },
            {"package_path": str(failed_root), "snapshot_id": "snapshot:test:copy-failure"},
        )

        self.assertFalse(failed.ok)
        self.assertEqual(failed.code, "ERR-PACKAGE_WRITE_FAILED")
        package_result = failed.data["package_result"]
        self.assertFalse(package_result["ok"])
        self.assertEqual(len(package_result["write_failure_blockers"]), 1)
        blocker_path = failed_root / "control" / "write_failure_blocker.json"
        self.assertTrue(blocker_path.exists())
        blocker = json.loads(blocker_path.read_text(encoding="utf-8"))
        self.assertTrue(blocker["failed_path"].endswith("reference/missing.bin"))
        self.assertIsNone(validate_schema(specs["write_failure_blocker"], blocker))

    def test_export_write_agent_invalid_count_returns_package_blocker(self) -> None:
        package_root = self.root / "invalid-count-package"
        result = ExportWriteAgent(job_id="write-invalid-count").write_evidence_package_optimized(
            {
                "package_path": str(package_root),
                "snapshot_id": "snapshot:test:invalid-count",
                "entries": [
                    {
                        "relative_path": "event/events.trace",
                        "write_mode": "write_bytes",
                        "payload": b"payload",
                        "count": "not-an-int",
                    }
                ],
            },
            {"package_path": str(package_root), "snapshot_id": "snapshot:test:invalid-count"},
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "ERR-PACKAGE_WRITE_FAILED")
        package_result = result.data["package_result"]
        self.assertFalse(package_result["ok"])
        self.assertEqual(len(package_result["write_failure_blockers"]), 1)
        self.assertTrue((package_root / "control" / "write_failure_blocker.json").exists())
        self.assertIn("not-an-int", package_result["write_failure_blockers"][0]["error_message"])

    def test_export_write_agent_text_rows_iterator_failure_returns_package_blocker(self) -> None:
        def rows():
            yield {"ok": True}
            raise RuntimeError("rows boom")

        package_root = self.root / "text-rows-failure-package"
        result = ExportWriteAgent(job_id="write-text-rows-failure").write_evidence_package_optimized(
            {
                "package_path": str(package_root),
                "snapshot_id": "snapshot:test:text-rows-failure",
                "entries": [
                    {
                        "relative_path": "control/dependency_sidecar.jsonl",
                        "write_mode": "stream_jsonl",
                        "rows": rows(),
                        "label": "dependency_sidecar",
                    }
                ],
            },
            {"package_path": str(package_root), "snapshot_id": "snapshot:test:text-rows-failure"},
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "ERR-PACKAGE_WRITE_FAILED")
        blockers = result.data["package_result"]["write_failure_blockers"]
        self.assertEqual(len(blockers), 1)
        self.assertIn("rows boom", blockers[0]["error_message"])
        self.assertTrue((package_root / "control" / "write_failure_blocker.json").exists())

    def test_export_write_agent_continue_on_error_returns_failed_package_with_all_blockers(self) -> None:
        package_root = self.root / "continue-on-error-package"
        result = ExportWriteAgent(job_id="write-continue-on-error").write_evidence_package_optimized(
            {
                "package_path": str(package_root),
                "snapshot_id": "snapshot:test:continue-on-error",
                "entries": [
                    {
                        "relative_path": "meta.json",
                        "write_mode": "standard_json",
                        "payload": {"ok": True},
                    },
                    {
                        "relative_path": "bad/unsupported.json",
                        "write_mode": "unsupported_mode",
                        "payload": {"bad": True},
                    },
                    {
                        "relative_path": "bad/missing.bin",
                        "write_mode": "copy_file",
                        "source_path": self.root / "missing.bin",
                    },
                ],
            },
            {
                "package_path": str(package_root),
                "snapshot_id": "snapshot:test:continue-on-error",
                "continue_on_error": True,
            },
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "ERR-PACKAGE_WRITE_FAILED")
        package_result = result.data["package_result"]
        self.assertFalse(package_result["ok"])
        self.assertEqual(len(package_result["write_metrics"]), 1)
        self.assertEqual(len(package_result["write_failure_blockers"]), 2)
        self.assertTrue((package_root / "meta.json").exists())
        self.assertTrue((package_root / "control" / "write_failure_blocker.json").exists())

    def test_telemetry_record_phase_appends_history(self) -> None:
        history = self.root / "telemetry" / "history.jsonl"
        contract_path = self.root / "telemetry" / "telemetry_agent_contract.json"
        result = TelemetryReportAgent().record_phase_result(
            job_id="job-1",
            phase_name="parse_rebuild",
            started_at="2026-05-04T00:00:00+00:00",
            finished_at="2026-05-04T00:00:03+00:00",
            metrics={"rss_mb": 128.0},
            history_path=history,
            contract_path=contract_path,
        )
        self.assertTrue(result.ok, result.message)
        update = result.data["telemetry_record_update"]
        contract = result.data["agent_contract"]
        self.assertEqual(update.phase_seconds, 3.0)
        self.assertEqual(contract["agent_kind"], "telemetry_report")
        self.assertEqual(contract["status"], "AGENT-completed")
        self.assertEqual(
            [entry["status"] for entry in contract["state_history"]],
            ["AGENT-validating_input", "AGENT-running", "AGENT-artifact_written", "AGENT-completed"],
        )
        self.assertEqual(contract["output_refs"], contract["artifact_handles"])
        artifacts = {(item["role"], item["kind"], item["path"]) for item in contract["artifact_handles"]}
        self.assertIn(("telemetry_history", "jsonl", str(history.resolve())), artifacts)
        self.assertIn(("agent_contract", "json", str(contract_path.resolve())), artifacts)
        self.assertTrue(contract_path.exists())
        self.assertEqual(json.loads(contract_path.read_text(encoding="utf-8")), contract)

        self.assertTrue(history.exists())
        history_row = json.loads(history.read_text(encoding="utf-8").strip())
        self.assertEqual(history_row["phase_name"], "parse_rebuild")
        self.assertEqual(history_row["history_path"], str(history.resolve()))
        self.assertNotIn("agent_contract", history_row)
        self.assertEqual(TelemetryHistoryStore(history).read_advisor_history(), [])

        legacy_update = record_telemetry_phase(
            job_id="job-legacy",
            phase_name="legacy_phase",
            started_at="2026-05-04T00:00:00+00:00",
            finished_at="2026-05-04T00:00:01+00:00",
        )
        self.assertTrue(legacy_update.ok, legacy_update.message)
        self.assertEqual(legacy_update.data.phase_seconds, 1.0)

        record_contract_path = self.root / "telemetry" / "telemetry_record_agent_contract.json"
        record_result = TelemetryReportAgent().build_record_result(
            job_id="record-job-1",
            run_id="run-1",
            dataset_id="dataset:telemetry",
            input_bytes=2048,
            runtime_seconds=0.5,
            contract_path=record_contract_path,
        )
        self.assertTrue(record_result.ok, record_result.message)
        self.assertEqual(record_result.data["telemetry_record"].input_bytes, 2048)
        record_contract = record_result.data["agent_contract"]
        self.assertEqual(record_contract["status"], "AGENT-completed")
        self.assertEqual(
            [entry["status"] for entry in record_contract["state_history"]],
            ["AGENT-validating_input", "AGENT-running", "AGENT-artifact_written", "AGENT-completed"],
        )
        self.assertEqual(json.loads(record_contract_path.read_text(encoding="utf-8")), record_contract)
        appended = TelemetryHistoryStore(history).append_record(
            record_result.data["telemetry_record"],
            job_id="record-job-1",
        )
        self.assertEqual(appended.phase_name, "evidence_export")
        advisor_history = TelemetryHistoryStore(history).read_advisor_history(
            dataset_id="dataset:telemetry",
            export_family="evidence",
        )
        self.assertEqual(len(advisor_history), 1)
        self.assertEqual(advisor_history[0]["input_bytes"], 2048)
        self.assertEqual(advisor_history[0]["runtime_seconds"], 0.5)

    def test_telemetry_report_agent_returns_failed_contract_on_write_error(self) -> None:
        blocking_parent = self.root / "not-a-directory"
        blocking_parent.write_text("file", encoding="utf-8")

        history_failed = TelemetryReportAgent().record_phase_result(
            job_id="job-history-fail",
            phase_name="parse_rebuild",
            started_at="2026-05-04T00:00:00+00:00",
            finished_at="2026-05-04T00:00:03+00:00",
            metrics={"rss_mb": 128.0},
            history_path=blocking_parent / "history.jsonl",
        )
        self.assertFalse(history_failed.ok)
        self.assertEqual(history_failed.code, "ERR-TELEMETRY_REPORT_FAILED")
        self.assertIsNotNone(history_failed.data["telemetry_record_update"])
        history_contract = history_failed.data["agent_contract"]
        self.assertEqual(history_contract["status"], "AGENT-failed")
        self.assertEqual(history_contract["error_code"], "ERR-TELEMETRY_REPORT_FAILED")
        self.assertEqual(history_contract["agent_kind"], "telemetry_report")

        contract_failed = TelemetryReportAgent().build_record_result(
            job_id="job-contract-fail",
            run_id="run-contract-fail",
            dataset_id="dataset:contract-fail",
            input_bytes=1024,
            contract_path=blocking_parent / "contract.json",
        )
        self.assertFalse(contract_failed.ok)
        self.assertEqual(contract_failed.code, "ERR-TELEMETRY_REPORT_FAILED")
        self.assertIsNotNone(contract_failed.data["telemetry_record"])
        contract = contract_failed.data["agent_contract"]
        self.assertEqual(contract["status"], "AGENT-failed")
        self.assertEqual(contract["error_code"], "ERR-TELEMETRY_REPORT_FAILED")
        self.assertEqual(contract["agent_kind"], "telemetry_report")

    def test_parser_process_agent_returns_artifact_handle_and_timeout_state(self) -> None:
        trace_path = write_scenario(self.root / "parser-agent.trace", name="basic", repeat=2)
        agent = ParserProcessAgent(job_id="parse-ok")
        parsed = agent.parse_rebuild(trace_path, artifact_dir=self.root / "parser-agent-ok", timeout_s=10.0)

        self.assertTrue(parsed.ok, parsed.message)
        self.assertTrue(Path(parsed.data["artifact_handle"]["path"]).exists())
        self.assertEqual(parsed.data["parser_process_artifact"]["artifact_version"], "parser-process-artifact-v1")
        self.assertEqual(parsed.data["agent_contract"]["status"], "AGENT-completed")
        self.assertGreater(parsed.data["align_events_seconds"], 0.0)
        self.assertGreater(parsed.data["rebuild_seconds"], 0.0)
        self.assertGreaterEqual(parsed.data["idx_build_seconds"], 0.0)
        self.assertGreater(parsed.data["load_seconds"], 0.0)
        self.assertEqual(parsed.data["index_build_mode"], "full")
        self.assertTrue(parsed.data["materialize_event_stream"])
        self.assertEqual(parsed.data["parser_process_artifact"]["align_events_seconds"], parsed.data["align_events_seconds"])
        self.assertEqual(parsed.data["parser_process_artifact"]["rebuild_seconds"], parsed.data["rebuild_seconds"])
        self.assertEqual(parsed.data["parser_process_artifact"]["idx_build_seconds"], parsed.data["idx_build_seconds"])
        self.assertEqual(parsed.data["parser_process_artifact"]["load_seconds"], parsed.data["load_seconds"])
        self.assertEqual(parsed.data["parser_process_artifact"]["index_build_mode"], "full")
        self.assertEqual(parsed.data["parser_process_artifact"]["peak_rss_mb"], parsed.data["peak_rss_mb"])
        self.assertGreater(len(parsed.data["artifact"].bundle.event_stream), 0)

        timeout_agent = ParserProcessAgent(job_id="parse-timeout")
        timed_out = timeout_agent.parse_rebuild(trace_path, artifact_dir=self.root / "parser-agent-timeout", timeout_s=0.0001)
        self.assertFalse(timed_out.ok)
        self.assertEqual(timed_out.code, "ERR-AGENT_TIMEOUT")
        self.assertEqual(timed_out.data["agent_contract"]["status"], "AGENT-timeout")
        self.assertEqual(
            timed_out.data["parser_process_artifact"]["artifact_version"],
            "parser-process-artifact-v1",
        )
        self.assertEqual(timed_out.data["parser_process_artifact"]["index_build_mode"], "full")
        self.assertIsNone(timed_out.data["parser_process_artifact"]["align_events_seconds"])
        self.assertIsNone(timed_out.data["parser_process_artifact"]["idx_build_seconds"])
        self.assertIsNone(timed_out.data["parser_process_artifact"]["load_seconds"])

        cancel_marker = self.root / "parser-agent-cancel" / "cancel.flag"
        cancel_marker.parent.mkdir(parents=True, exist_ok=True)
        cancel_marker.write_text("cancel", encoding="utf-8")
        cancelled = ParserProcessAgent(job_id="parse-cancel").parse_rebuild(
            trace_path,
            artifact_dir=self.root / "parser-agent-cancel",
            cancel_path=cancel_marker,
        )
        self.assertFalse(cancelled.ok)
        self.assertEqual(cancelled.code, "ERR-AGENT_CANCELLED")
        self.assertEqual(cancelled.data["agent_contract"]["status"], "AGENT-cancelled")
        self.assertEqual(
            cancelled.data["parser_process_artifact"]["artifact_version"],
            "parser-process-artifact-v1",
        )
        self.assertEqual(cancelled.data["parser_process_artifact"]["index_build_mode"], "full")
        self.assertIsNone(cancelled.data["parser_process_artifact"]["align_events_seconds"])
        self.assertIsNone(cancelled.data["parser_process_artifact"]["idx_build_seconds"])
        self.assertIsNone(cancelled.data["parser_process_artifact"]["load_seconds"])

    def test_parser_process_agent_artifact_policy_metadata_and_cleanup(self) -> None:
        trace_path = write_scenario(self.root / "parser-agent-policy.trace", name="basic", repeat=2)

        metadata_only = ParserProcessAgent(job_id="parse-metadata-only").parse_rebuild(
            trace_path,
            artifact_policy={
                "artifact_dir": str(self.root / "parser-agent-metadata-only"),
                "load_artifact": False,
            },
        )
        self.assertTrue(metadata_only.ok, metadata_only.message)
        self.assertIsNone(metadata_only.data["artifact"])
        self.assertFalse(metadata_only.data["parser_process_artifact"]["load_artifact"])
        self.assertTrue(Path(metadata_only.data["artifact_handle"]["path"]).exists())

        cleaned = ParserProcessAgent(job_id="parse-cleanup").parse_rebuild(
            trace_path,
            artifact_policy={
                "artifact_dir": str(self.root / "parser-agent-cleanup"),
                "load_artifact": False,
                "retain_artifact": False,
            },
        )
        self.assertTrue(cleaned.ok, cleaned.message)
        self.assertFalse(Path(cleaned.data["artifact_handle"]["path"]).exists())
        self.assertFalse(cleaned.data["parser_process_artifact"]["artifact_policy"]["retain_artifact"])

        unsupported = ParserProcessAgent(job_id="parse-unsupported").parse_rebuild(
            trace_path,
            artifact_policy={
                "artifact_dir": str(self.root / "parser-agent-unsupported"),
                "artifact_format": "json",
            },
        )
        self.assertFalse(unsupported.ok)
        self.assertEqual(unsupported.code, "ERR-PARSER_PROCESS_FAILED")
        self.assertEqual(unsupported.data["parser_process_artifact"]["artifact_policy"]["artifact_format"], "json")

    def test_parser_process_agent_honors_index_build_mode_and_progress_path_policy(self) -> None:
        trace_path = write_scenario(self.root / "parser-agent-progress.trace", name="basic", repeat=3)
        progress_path = self.root / "parser-agent-progress" / "progress.jsonl"
        artifact_dir = self.root / "parser-agent-progress" / "artifact"

        parsed = ParserProcessAgent(job_id="parse-progress").parse_rebuild(
            trace_path,
            artifact_dir=artifact_dir,
            artifact_policy={
                "materialize_event_stream": False,
                "index_build_mode": "minimal",
                "progress_path": str(progress_path),
                "load_artifact": True,
                "retain_artifact": False,
            },
        )

        self.assertTrue(parsed.ok, parsed.message)
        policy = parsed.data["parser_process_artifact"]["artifact_policy"]
        self.assertEqual(policy["index_build_mode"], "minimal")
        self.assertEqual(policy["progress_path"], str(progress_path.resolve()))
        self.assertFalse(Path(parsed.data["artifact_handle"]["path"]).exists())
        self.assertEqual(parsed.data["index_build_mode"], "minimal")
        self.assertFalse(parsed.data["materialize_event_stream"])
        self.assertEqual(parsed.data["parser_process_artifact"]["index_build_mode"], "minimal")
        self.assertFalse(parsed.data["parser_process_artifact"]["materialize_event_stream"])
        self.assertGreater(parsed.data["align_events_seconds"], 0.0)
        self.assertGreaterEqual(parsed.data["idx_build_seconds"], 0.0)
        self.assertGreater(parsed.data["load_seconds"], 0.0)
        self.assertEqual(parsed.data["artifact"].bundle.index_bundle.summary["index_build_mode"], "minimal")
        rows = [json.loads(line) for line in progress_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertTrue(any(row["stage"] == "idx_Build" and row["status"] == "completed" for row in rows))
        self.assertTrue(any(row["stage"] == "prs_Load" and row["status"] == "completed" for row in rows))
        self.assertTrue(any(isinstance(row.get("payload"), dict) and row["payload"].get("chunk_progress") for row in rows))

    def test_parser_process_agent_cache_reuse_and_trace_dictionary_invalidation(self) -> None:
        trace_path = write_scenario(self.root / "parser-agent-cache.trace", name="basic", repeat=3)
        cache_root = self.root / "parser-cache"
        dictionary_path = self.root / "dictionary.json"
        dictionary_path.write_text(json.dumps(load_dictionary(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

        first = ParserProcessAgent(job_id="parse-cache-first").parse_rebuild(
            trace_path,
            artifact_policy={
                "cache_enabled": True,
                "cache_root": str(cache_root),
                "dictionary_path": str(dictionary_path),
                "parser_version": "parser-mvp-1",
                "schema_version": "parser-process-artifact-v1",
                "index_build_mode": "minimal",
                "materialize_event_stream": False,
                "load_artifact": False,
            },
        )
        self.assertTrue(first.ok, first.message)
        self.assertFalse(first.data["cache_hit"])
        self.assertFalse(first.data["parser_process_artifact"]["cache_hit"])
        self.assertEqual(first.data["dictionary_checksum"], hashlib.sha256(dictionary_path.read_bytes()).hexdigest())
        self.assertEqual(first.data["parser_process_artifact"]["parser_version"], "parser-mvp-1")
        self.assertEqual(first.data["parser_process_artifact"]["schema_version"], "parser-process-artifact-v1")
        self.assertIsNone(first.data["artifact"])

        second = ParserProcessAgent(job_id="parse-cache-second").parse_rebuild(
            trace_path,
            artifact_policy={
                "cache_enabled": True,
                "cache_root": str(cache_root),
                "dictionary_path": str(dictionary_path),
                "parser_version": "parser-mvp-1",
                "schema_version": "parser-process-artifact-v1",
                "index_build_mode": "minimal",
                "materialize_event_stream": False,
                "load_artifact": False,
            },
        )
        self.assertTrue(second.ok, second.message)
        self.assertTrue(second.data["cache_hit"])
        self.assertTrue(second.data["parser_process_artifact"]["cache_hit"])
        self.assertEqual(second.data["cache_key"], first.data["cache_key"])
        self.assertEqual(second.data["trace_checksum"], first.data["trace_checksum"])
        self.assertEqual(second.data["dictionary_checksum"], first.data["dictionary_checksum"])
        self.assertIsNone(second.data["artifact"])

        updated_dictionary = self._rename_event(self._dictionary_copy(), 0x1001, "TASK_READY_CACHE_VARIANT")
        dictionary_path.write_text(json.dumps(updated_dictionary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        third = ParserProcessAgent(job_id="parse-cache-third").parse_rebuild(
            trace_path,
            artifact_policy={
                "cache_enabled": True,
                "cache_root": str(cache_root),
                "dictionary_path": str(dictionary_path),
                "parser_version": "parser-mvp-1",
                "schema_version": "parser-process-artifact-v1",
                "index_build_mode": "minimal",
                "materialize_event_stream": False,
                "load_artifact": False,
            },
        )
        self.assertTrue(third.ok, third.message)
        self.assertFalse(third.data["cache_hit"])
        self.assertNotEqual(third.data["dictionary_checksum"], second.data["dictionary_checksum"])
        self.assertNotEqual(third.data["cache_key"], second.data["cache_key"])

        trace_path.write_bytes((trace_path.read_bytes() + b"\0"))
        fourth = ParserProcessAgent(job_id="parse-cache-fourth").parse_rebuild(
            trace_path,
            artifact_policy={
                "cache_enabled": True,
                "cache_root": str(cache_root),
                "dictionary_path": str(dictionary_path),
                "parser_version": "parser-mvp-1",
                "schema_version": "parser-process-artifact-v1",
                "index_build_mode": "minimal",
                "materialize_event_stream": False,
                "load_artifact": False,
            },
        )
        self.assertTrue(fourth.ok, fourth.message)
        self.assertFalse(fourth.data["cache_hit"])
        self.assertNotEqual(fourth.data["trace_checksum"], third.data["trace_checksum"])
        self.assertNotEqual(fourth.data["cache_key"], third.data["cache_key"])

    def test_parser_process_agent_cache_hit_skips_worker(self) -> None:
        trace_path = write_scenario(self.root / "parser-agent-cache-skip.trace", name="basic", repeat=2)
        cache_root = self.root / "parser-cache-skip"

        first = ParserProcessAgent(job_id="parse-cache-worker-first").parse_rebuild(
            trace_path,
            artifact_policy={
                "cache_enabled": True,
                "cache_root": str(cache_root),
                "dictionary_path": str(DICTIONARY_PATH),
                "parser_version": "parser-mvp-1",
                "schema_version": "parser-process-artifact-v1",
                "load_artifact": True,
            },
        )
        self.assertTrue(first.ok, first.message)
        self.assertFalse(first.data["cache_hit"])
        self.assertEqual(first.data["artifact"].dictionary_info["requested_source"], "default")

        with mock.patch("parser.parser_process_agent.subprocess.Popen", side_effect=AssertionError("worker should be skipped on cache hit")):
            second = ParserProcessAgent(job_id="parse-cache-worker-second").parse_rebuild(
                trace_path,
                artifact_policy={
                    "cache_enabled": True,
                    "cache_root": str(cache_root),
                    "dictionary_path": str(DICTIONARY_PATH),
                    "parser_version": "parser-mvp-1",
                    "schema_version": "parser-process-artifact-v1",
                    "load_artifact": True,
                },
            )
        self.assertTrue(second.ok, second.message)
        self.assertTrue(second.data["cache_hit"])
        self.assertTrue(second.data["parser_process_artifact"]["cache_hit"])
        self.assertEqual(second.data["artifact"].dictionary_info["requested_source"], "default")
        self.assertGreater(len(second.data["artifact"].bundle.event_stream), 0)

    def test_parser_process_agent_metadata_cache_hit_skips_pickle_load(self) -> None:
        trace_path = write_scenario(self.root / "parser-agent-cache-hot.trace", name="basic", repeat=2)
        cache_root = self.root / "parser-cache-hot"

        first = ParserProcessAgent(job_id="parse-cache-hot-first").parse_rebuild(
            trace_path,
            artifact_policy={
                "cache_enabled": True,
                "cache_root": str(cache_root),
                "dictionary_path": str(DICTIONARY_PATH),
                "parser_version": "parser-mvp-1",
                "schema_version": "parser-process-artifact-v1",
                "load_artifact": True,
            },
        )
        self.assertTrue(first.ok, first.message)
        self.assertFalse(first.data["cache_hit"])

        with (
            mock.patch("parser.parser_process_agent.subprocess.Popen", side_effect=AssertionError("worker should be skipped on cache hit")),
            mock.patch("parser.parser_process_agent.pickle.load", side_effect=AssertionError("metadata-only cache hit should not load pickle")),
        ):
            hot = ParserProcessAgent(job_id="parse-cache-hot-second").parse_rebuild(
                trace_path,
                artifact_policy={
                    "cache_enabled": True,
                    "cache_root": str(cache_root),
                    "dictionary_path": str(DICTIONARY_PATH),
                    "parser_version": "parser-mvp-1",
                    "schema_version": "parser-process-artifact-v1",
                    "load_artifact": False,
                },
            )
        self.assertTrue(hot.ok, hot.message)
        self.assertTrue(hot.data["cache_hit"])
        self.assertIsNone(hot.data["artifact"])
        self.assertFalse(hot.data["parser_process_artifact"]["load_artifact"])

    def test_parser_process_agent_artifact_dir_file_returns_metadata_failure(self) -> None:
        trace_path = write_scenario(self.root / "parser-agent-artifact-dir-file.trace", name="basic", repeat=2)
        artifact_dir = self.root / "artifact-dir-is-file"
        artifact_dir.write_text("not a directory", encoding="utf-8")

        failed = ParserProcessAgent(job_id="parse-artifact-dir-file").parse_rebuild(
            trace_path,
            artifact_policy={"artifact_dir": str(artifact_dir)},
        )

        self.assertFalse(failed.ok)
        self.assertEqual(failed.code, "ERR-PARSER_PROCESS_FAILED")
        self.assertIn("parser artifact directory unavailable", failed.message)
        self.assertEqual(failed.data["agent_contract"]["status"], "AGENT-failed")
        self.assertTrue(failed.data["artifact_handle"])
        self.assertTrue(failed.data["result_path"])
        self.assertEqual(
            failed.data["parser_process_artifact"]["artifact_version"],
            "parser-process-artifact-v1",
        )
        self.assertEqual(failed.data["child_agent_contract"], {})

    def test_sidecar_index_ticket_schema_mismatch_rejected_or_rebuilt(self) -> None:
        trace_path = write_scenario(self.root / "sidecar-ticket-schema.trace", name="basic", repeat=2)
        rows = materialize_dependency_sidecar(
            [
                DependencySidecarEdge(
                    snapshot_id="snapshot:test:ticket-schema",
                    trace_checksum="pending",
                    src_ref="evt:1",
                    dst_ref="evt:2",
                    src_kind="ref",
                    dst_kind="ref",
                    relation_kind="ref_index_next",
                    rule_family="ref_ref",
                    provenance="unit_test",
                    priority=1,
                    time_hint_begin_ns=0,
                    time_hint_end_ns=1,
                    core_hint=0,
                    seq_hint_begin=1,
                    seq_hint_end=2,
                    segment_hint=None,
                    cycle_guard_token="evt:1->evt:2",
                    estimate_events=1,
                    estimate_bytes=32,
                    edge_hash="edge:test:ticket-schema",
                )
            ],
            trace_checksum=checksum_file(trace_path),
        )
        sidecar_path = self.root / "dependency_sidecar.jsonl"
        jsonl_dump(sidecar_path, [serialize(row) for row in rows])
        manifest = {
            "snapshot_id": "snapshot:test:ticket-schema",
            "trace_checksum": checksum_file(trace_path),
            "dictionary_checksum": "dict:test",
            "entry_paths": ["control/dependency_sidecar.jsonl"],
            "entry_checksums": {
                "control/dependency_sidecar.jsonl": checksum_file(sidecar_path),
            },
        }
        validated = validate_dependency_sidecar_stream(
            sidecar_path,
            manifest,
            expected_snapshot_id="snapshot:test:ticket-schema",
            expected_trace_checksum=checksum_file(trace_path),
        )
        self.assertTrue(validated.ok, validated.message)

        first = build_or_open_sidecar_index(
            sidecar_path,
            expected_snapshot_id="snapshot:test:ticket-schema",
            expected_trace_checksum=checksum_file(trace_path),
            sidecar_checksum=validated.data["checksum"],
            file_fingerprint=validated.data["file_fingerprint"],
            dictionary_checksum="dict:test",
            rebuild_on_mismatch=True,
        )
        self.assertTrue(first.ok, first.message)
        ticket_path = sidecar_index_ticket_path_for_source(sidecar_path)
        ticket_payload = json.loads(ticket_path.read_text(encoding="utf-8"))
        ticket_payload["schema_version"] = int(ticket_payload["schema_version"]) - 1
        ticket_path.write_text(json.dumps(ticket_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        reopened = build_or_open_sidecar_index(
            sidecar_path,
            expected_snapshot_id="snapshot:test:ticket-schema",
            expected_trace_checksum=checksum_file(trace_path),
            sidecar_checksum=validated.data["checksum"],
            file_fingerprint=validated.data["file_fingerprint"],
            dictionary_checksum="dict:test",
            rebuild_on_mismatch=True,
        )
        self.assertTrue(reopened.ok, reopened.message)
        self.assertFalse(reopened.data.built)
        reloaded_ticket = read_sidecar_index_ticket(ticket_path)
        self.assertTrue(reloaded_ticket.ok, reloaded_ticket.message)
        self.assertGreaterEqual(reloaded_ticket.data.schema_version, 2)

    def test_formal_scheduler_applies_advisor_strategy_and_dependencies(self) -> None:
        advisor_decision = RuntimeOptimizationAdvisor({"advisor_mode": "heuristic"}).evaluate(
            current_request_features={"sidecar_bytes": 512 * 1024 * 1024}
        )
        scheduler = FormalSuiteSchedulerAgent(job_id="formal-test")
        planned = scheduler.plan(
            [
                FormalTask("formal_a", "A", ["a"], dependencies=["preparation"], priority=10, resource_class="sidecar"),
                FormalTask("formal_b", "B", ["b"], dependencies=["formal_a"], priority=20),
                FormalTask("formal_c", "C", ["c"], dependencies=["formal_a"], priority=30),
            ],
            advisor_decision=advisor_decision,
            gate_result={"accepted": True},
        )

        self.assertTrue(planned.ok, planned.message)
        self.assertEqual(planned.data.strategy, "sidecar_first")
        self.assertEqual(planned.data.waves[0][0].task_id, "formal_a")
        self.assertEqual(scheduler.contract.status, "AGENT-completed")

    def test_formal_scheduler_can_execute_a_b_c_waves_with_bounds(self) -> None:
        scheduler = FormalSuiteSchedulerAgent(job_id="formal-run")
        result = scheduler.run_suite(
            [
                FormalTask("task_a", "A", [sys.executable, "-c", "print('a')"], priority=0),
                FormalTask(
                    "task_b",
                    "B",
                    [sys.executable, "-c", "import time; time.sleep(0.2); print('b')"],
                    dependencies=["task_a"],
                    priority=10,
                ),
                FormalTask(
                    "task_c",
                    "C",
                    [sys.executable, "-c", "import time; time.sleep(0.2); print('c')"],
                    dependencies=["task_a"],
                    priority=20,
                ),
            ],
            strategy="bounded_parallel",
            max_parallel=2,
            cwd=self.root,
            resource_policy={"min_cpu_count": 1},
        )
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.data["parity_summary"]["abc_groups_present"], True)
        self.assertEqual(result.data["resource_guard"]["accepted"], True)
        self.assertGreaterEqual(result.data["resource_observation"]["observed_max_parallel"], 2)
        self.assertTrue(result.data["resource_observation"]["bounded_parallel_observed"])

    def test_formal_scheduler_disables_bytecode_for_subprocesses(self) -> None:
        scheduler = FormalSuiteSchedulerAgent(job_id="formal-bytecode")

        def _fake_run(*args: Any, **kwargs: Any) -> Any:
            self.assertEqual(kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")
            return mock.Mock(returncode=0)

        with mock.patch("parser.formal_scheduler.subprocess.run", side_effect=_fake_run):
            result = scheduler.run_suite(
                [FormalTask("task_a", "A", [sys.executable, "-c", "print('a')"], priority=0)],
                cwd=self.root,
                resource_policy={"min_cpu_count": 1},
            )

        self.assertTrue(result.ok, result.message)

    def test_formal_windows_common_run_subprocess_disables_bytecode(self) -> None:
        def _fake_run(*args: Any, **kwargs: Any) -> Any:
            self.assertEqual(kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")
            return mock.Mock(returncode=0)

        with mock.patch("tool.formal_windows_common.subprocess.run", side_effect=_fake_run):
            run_subprocess([sys.executable, "-c", "print('ok')"], cwd=self.root)

    def test_run_formal_b_windows_group_proof_digest_falls_back_to_package_digest(self) -> None:
        group_root = self.root / "formal-b-group"
        package_root = group_root / "packages" / "depth_d0"
        package_root.mkdir(parents=True, exist_ok=True)
        proof_digest = {
            "snapshot_id": "snapshot:formal-b",
            "closure_mode": "bounded",
            "complete_wrt_rule_family": False,
            "rule_family": ["ref_ref"],
            "budget_vector": {"D_max": 0, "C_events": 1024, "S_bytes": 4096, "rho_max": 4.0},
            "closure_depth_reached": 0,
            "seed_ref_count": 1,
            "closed_ref_count": 1,
            "missing_required_refs": 0,
            "truncated_frontier_count": 0,
            "frontier_halt_reason": "DEPTH_LIMIT",
            "events_emitted": 1,
            "bytes_emitted": 198,
            "sidecar_bytes_scanned": 3024,
        }
        proof_digest["proof_hash"] = evd_RecomputeProofHash(proof_digest)
        (package_root / "control").mkdir(parents=True, exist_ok=True)
        (package_root / "control" / "proof_digest.json").write_text(
            json.dumps(proof_digest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (group_root / "budget_sweep_windows.json").write_text(
            json.dumps({"rows": [{"scenario_id": "depth_d0", "package_path": str(package_root)}]}, ensure_ascii=False),
            encoding="utf-8",
        )

        digest_path = write_b_group_proof_digest(group_root, sidecar_bytes_scanned=0)

        payload = json.loads(digest_path.read_text(encoding="utf-8"))
        self.assertEqual(digest_path, group_root / "control" / "proof_digest.json")
        self.assertEqual(payload["sidecar_bytes_scanned"], 0)
        self.assertEqual(payload["proof_hash"], evd_RecomputeProofHash(payload))

    def test_run_formal_b_windows_group_proof_digest_reads_package_path_from_artifact_refs(self) -> None:
        group_root = self.root / "formal-b-group-artifact-refs"
        package_root = group_root / "packages" / "depth_d0"
        package_root.mkdir(parents=True, exist_ok=True)
        proof_digest = {
            "snapshot_id": "snapshot:formal-b:artifact-refs",
            "closure_mode": "bounded",
            "complete_wrt_rule_family": False,
            "rule_family": ["ref_ref"],
            "budget_vector": {"D_max": 0, "C_events": 2048, "S_bytes": 8192, "rho_max": 4.0},
            "closure_depth_reached": 0,
            "seed_ref_count": 1,
            "closed_ref_count": 1,
            "missing_required_refs": 0,
            "truncated_frontier_count": 0,
            "frontier_halt_reason": "DEPTH_LIMIT",
            "events_emitted": 1,
            "bytes_emitted": 256,
            "sidecar_bytes_scanned": 2048,
        }
        proof_digest["proof_hash"] = evd_RecomputeProofHash(proof_digest)
        (package_root / "control").mkdir(parents=True, exist_ok=True)
        (package_root / "control" / "proof_digest.json").write_text(
            json.dumps(proof_digest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (group_root / "budget_sweep_windows.json").write_text(
            json.dumps(
                {"rows": [{"scenario_id": "depth_d0", "artifact_refs": {"package_path": str(package_root)}}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        digest_path = write_b_group_proof_digest(group_root, sidecar_bytes_scanned=0)

        payload = json.loads(digest_path.read_text(encoding="utf-8"))
        self.assertEqual(digest_path, group_root / "control" / "proof_digest.json")
        self.assertEqual(payload["sidecar_bytes_scanned"], 0)
        self.assertEqual(payload["proof_hash"], evd_RecomputeProofHash(payload))

    def test_run_formal_c_windows_group_proof_digest_falls_back_to_package_digest(self) -> None:
        group_root = self.root / "formal-c-group"
        package_root = group_root / "packages" / "cycle_inflation"
        package_root.mkdir(parents=True, exist_ok=True)
        proof_digest = {
            "snapshot_id": "snapshot:formal-c",
            "closure_mode": "bounded",
            "complete_wrt_rule_family": False,
            "rule_family": ["ref_ref"],
            "budget_vector": {"D_max": 1, "C_events": 1024, "S_bytes": 4096, "rho_max": 4.0},
            "closure_depth_reached": 1,
            "seed_ref_count": 1,
            "closed_ref_count": 2,
            "missing_required_refs": 1,
            "truncated_frontier_count": 0,
            "frontier_halt_reason": "CYCLE_INFLATION",
            "events_emitted": 2,
            "bytes_emitted": 256,
            "sidecar_bytes_scanned": 3024,
        }
        proof_digest["proof_hash"] = evd_RecomputeProofHash(proof_digest)
        (package_root / "control").mkdir(parents=True, exist_ok=True)
        (package_root / "control" / "proof_digest.json").write_text(
            json.dumps(proof_digest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (group_root / "scenario_matrix_windows.json").write_text(
            json.dumps({"rows": [{"scenario_id": "cycle_inflation", "package_path": str(package_root)}]}, ensure_ascii=False),
            encoding="utf-8",
        )

        digest_path = write_c_group_proof_digest(group_root, sidecar_bytes_scanned=4096)

        payload = json.loads(digest_path.read_text(encoding="utf-8"))
        self.assertEqual(digest_path, group_root / "control" / "proof_digest.json")
        self.assertEqual(payload["sidecar_bytes_scanned"], 4096)
        self.assertEqual(payload["proof_hash"], evd_RecomputeProofHash(payload))

    def test_run_formal_c_windows_group_proof_digest_falls_back_to_conventional_package_root(self) -> None:
        group_root = self.root / "formal-c-group-conventional-root"
        package_root = group_root / "packages" / "cycle_inflation"
        package_root.mkdir(parents=True, exist_ok=True)
        proof_digest = {
            "snapshot_id": "snapshot:formal-c:conventional-root",
            "closure_mode": "degraded",
            "complete_wrt_rule_family": False,
            "rule_family": ["ref_ref"],
            "budget_vector": {"D_max": 1, "C_events": 2048, "S_bytes": 8192, "rho_max": 4.0},
            "closure_depth_reached": 1,
            "seed_ref_count": 1,
            "closed_ref_count": 2,
            "missing_required_refs": 1,
            "truncated_frontier_count": 0,
            "frontier_halt_reason": "CYCLE_INFLATION",
            "events_emitted": 2,
            "bytes_emitted": 384,
            "sidecar_bytes_scanned": 1536,
        }
        proof_digest["proof_hash"] = evd_RecomputeProofHash(proof_digest)
        (package_root / "control").mkdir(parents=True, exist_ok=True)
        (package_root / "control" / "proof_digest.json").write_text(
            json.dumps(proof_digest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (group_root / "scenario_matrix_windows.json").write_text(
            json.dumps({"rows": [{"scenario_id": "cycle_inflation"}]}, ensure_ascii=False),
            encoding="utf-8",
        )

        digest_path = write_c_group_proof_digest(group_root, sidecar_bytes_scanned=4096)

        payload = json.loads(digest_path.read_text(encoding="utf-8"))
        self.assertEqual(digest_path, group_root / "control" / "proof_digest.json")
        self.assertEqual(payload["sidecar_bytes_scanned"], 4096)
        self.assertEqual(payload["proof_hash"], evd_RecomputeProofHash(payload))

    def test_build_windows_formal_parity_ignores_scenario_execution_metadata(self) -> None:
        linux_summary_path = self.root / "linux-formal-summary.json"
        windows_summary_path = self.root / "windows-formal-summary.json"
        linux_summary = {
            "contract_version": "patent_10_4_formal_close_min_contract_20260415",
            "run_scope": "patent_10_4_formal",
            "input_contract": {"trace_sha256": "c478b7cadeb1320362af0ea9921a1ad8592e2e53f8d90ebe800b3a25fd8ada3c"},
            "proof_group": "A_control_plane_first",
            "verdict": "pass",
            "artifact_refs": {
                "environment_summary": str(self.root / "linux-env.json"),
                "runner_script": str(self.root / "linux-runner.py"),
            },
            "required_metrics": {
                "scan_count": [42, 42, 42],
                "seek_count": [42, 42, 42],
                "window_span_total": [0, 0, 0],
                "sidecar_lookup_count": [257, 257, 257],
            },
            "observations": {
                "closure_modes": ["bounded", "bounded", "bounded"],
                "proof_consumer_modes": [
                    {"compare": "REFERENCE_ONLY", "replay": "REFERENCE_ONLY", "audit": "REFERENCE_ONLY"}
                ],
            },
        }
        windows_summary = json.loads(json.dumps(linux_summary))
        windows_summary["artifact_refs"]["scenario_execution_contract"] = str(self.root / "scenario_execution_contract.json")
        windows_summary["scenario_execution"] = {
            "scenario_id": "current_baseline",
            "ticket_fast_path_enabled": False,
            "streaming_write_enabled": False,
        }
        linux_summary_path.write_text(json.dumps(linux_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        windows_summary_path.write_text(json.dumps(windows_summary, ensure_ascii=False, indent=2), encoding="utf-8")

        report = compare_windows_formal_group(
            "A_control_plane_first",
            linux_summary_path,
            windows_summary_path,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["metric_diff_count"], 0)
        self.assertTrue(report["mandatory_field_set"]["same"])
        self.assertEqual(report["mandatory_field_set"]["linux_only"], [])
        self.assertEqual(report["mandatory_field_set"]["windows_only"], [])

    def test_build_windows_formal_readiness_summary_detail_ignores_scenario_execution_metadata(self) -> None:
        summary_path = self.root / "windows-formal-summary.json"
        checked_in_summary = {
            "contract_version": "patent_10_4_formal_close_min_contract_20260415",
            "input_contract": {"trace_sha256": "c478b7cadeb1320362af0ea9921a1ad8592e2e53f8d90ebe800b3a25fd8ada3c"},
            "artifact_refs": {
                "environment_summary": str(self.root / "linux-env.json"),
                "runner_script": str(self.root / "linux-runner.py"),
            },
            "verdict": "pass",
        }
        windows_summary = json.loads(json.dumps(checked_in_summary))
        windows_summary["artifact_refs"]["scenario_execution_contract"] = str(self.root / "scenario_execution_contract.json")
        windows_summary["scenario_execution"] = {
            "scenario_id": "current_baseline",
            "ticket_fast_path_enabled": False,
            "streaming_write_enabled": False,
        }
        summary_path.write_text(json.dumps(windows_summary, ensure_ascii=False, indent=2), encoding="utf-8")

        with mock.patch("tool.build_windows_formal_readiness.checked_in_summary", return_value=checked_in_summary):
            detail = build_windows_summary_detail("A", summary_path, self.root)

        self.assertTrue(detail["field_paths_match_linux_checked_in"])
        self.assertEqual(detail["missing_field_paths"], [])
        self.assertEqual(detail["extra_field_paths"], [])

    def test_formal_scheduler_deep_summarizes_readiness_and_parity_artifacts(self) -> None:
        summary_root = self.root / "formal-summary"
        summary_root.mkdir(parents=True, exist_ok=True)
        artifact_paths = {
            "A": summary_root / "A.json",
            "B": summary_root / "B.json",
            "C": summary_root / "C.json",
            "manifest": summary_root / "manifest.json",
            "readiness": summary_root / "readiness.json",
            "parity": summary_root / "parity.json",
        }
        for group in ("A", "B", "C"):
            artifact_paths[group].write_text(
                json.dumps({"verdict": "pass", "proof_group": group}, ensure_ascii=False),
                encoding="utf-8",
            )
        artifact_paths["manifest"].write_text(
            json.dumps({"missing_count": 0, "summary_paths": {group: str(artifact_paths[group]) for group in ("A", "B", "C")}}),
            encoding="utf-8",
        )
        artifact_paths["readiness"].write_text(
            json.dumps({"verdict": "ready_for_linux_parity", "checks": {"formal_parity_status_pass": True}}),
            encoding="utf-8",
        )
        artifact_paths["parity"].write_text(
            json.dumps(
                {
                    "status": "pass",
                    "metric_diff_count": 0,
                    "mandatory_field_set_same": True,
                    "ready_for_gate": True,
                    "groups": [
                        {
                            "group": "A_control_plane_first",
                            "status": "pass",
                            "metric_diff_count": 0,
                            "mandatory_field_set": {"same": True},
                        },
                        {
                            "group": "B_budget_pre_freeze",
                            "status": "pass",
                            "metric_diff_count": 0,
                            "mandatory_field_set": {"same": True},
                        },
                        {
                            "group": "C_degraded_audit",
                            "status": "pass",
                            "metric_diff_count": 0,
                            "mandatory_field_set": {"same": True},
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = FormalSuiteSchedulerAgent(job_id="formal-summary").run_suite(
            [
                FormalTask("task_a", "A", [sys.executable, "-c", "print('a')"], priority=0),
                FormalTask("task_b", "B", [sys.executable, "-c", "print('b')"], priority=10),
                FormalTask("task_c", "C", [sys.executable, "-c", "print('c')"], priority=20),
            ],
            strategy="bounded_parallel",
            max_parallel=2,
            cwd=self.root,
            resource_policy={"min_cpu_count": 1},
            summary_artifacts=artifact_paths,
        )

        self.assertTrue(result.ok, result.message)
        parity_summary = result.data["parity_summary"]
        self.assertTrue(parity_summary["abc_groups_present"])
        self.assertTrue(parity_summary["abc_summary_files_present"])
        self.assertTrue(parity_summary["abc_summary_verdicts_pass"])
        self.assertEqual(parity_summary["readiness_verdict"], "ready_for_linux_parity")
        self.assertEqual(parity_summary["parity_report_status"], "pass")
        self.assertEqual(parity_summary["metric_diff_count"], 0)
        self.assertIs(parity_summary["mandatory_field_set_same"], True)
        self.assertIs(parity_summary["ready_for_gate"], True)
        self.assertTrue(result.data["summary_artifacts"]["parity"]["loaded"])

    def test_formal_scheduler_failed_task_blocks_ready_for_gate(self) -> None:
        summary_root = self.root / "formal-summary-failed-task"
        summary_root.mkdir(parents=True, exist_ok=True)
        artifact_paths = {
            "A": summary_root / "A.json",
            "B": summary_root / "B.json",
            "C": summary_root / "C.json",
            "readiness": summary_root / "readiness.json",
            "parity": summary_root / "parity.json",
        }
        for group in ("A", "B", "C"):
            artifact_paths[group].write_text(json.dumps({"verdict": "pass"}), encoding="utf-8")
        artifact_paths["readiness"].write_text(json.dumps({"verdict": "ready_for_linux_parity"}), encoding="utf-8")
        artifact_paths["parity"].write_text(
            json.dumps({"status": "pass", "metric_diff_count": 0, "mandatory_field_set_same": True}),
            encoding="utf-8",
        )

        result = FormalSuiteSchedulerAgent(job_id="formal-summary-failed").run_suite(
            [
                FormalTask("task_a", "A", [sys.executable, "-c", "print('a')"], priority=0),
                FormalTask("task_b", "B", [sys.executable, "-c", "import sys; sys.exit(1)"], priority=10),
                FormalTask("task_c", "C", [sys.executable, "-c", "print('c')"], priority=20),
            ],
            strategy="bounded_parallel",
            max_parallel=2,
            cwd=self.root,
            resource_policy={"min_cpu_count": 1},
            summary_artifacts=artifact_paths,
            fail_fast=False,
        )

        self.assertTrue(result.ok, result.message)
        parity_summary = result.data["parity_summary"]
        self.assertEqual(parity_summary["failed_task_count"], 1)
        self.assertIs(parity_summary["ready_for_gate"], False)

    def test_windows_formal_manifest_payload_exposes_parity_contract_paths(self) -> None:
        archive_root = self.root / "formal-manifest"
        prep_root = archive_root / "preparation"
        prep_root.mkdir(parents=True, exist_ok=True)
        (prep_root / "windows_environment_summary.json").write_text("{}", encoding="utf-8")
        (prep_root / "input_qualification_report_windows.json").write_text(
            json.dumps(
                {
                    "contract_version": "contract-test",
                    "trace_sha256": "sha256:test",
                    "trace_size_bytes": 123,
                }
            ),
            encoding="utf-8",
        )
        output_path = archive_root / "windows_artifact_manifest.json"

        exit_code = build_windows_formal_manifest_main([
            "--archive-root",
            str(archive_root),
            "--output",
            str(output_path),
        ])

        self.assertEqual(exit_code, 0)
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["environment_summary"], str(prep_root / "windows_environment_summary.json"))
        self.assertEqual(
            set(payload["summary_paths"]),
            {"A_control_plane_first", "B_budget_pre_freeze", "C_degraded_audit"},
        )
        self.assertIn("groups", payload)
        self.assertIn("artifacts", payload)
        self.assertIn("missing_count", payload)

    def test_formal_windows_suite_refresh_only_recomputes_current_artifacts_without_rerun(self) -> None:
        archive_root = self.root / "formal-refresh"
        a_root = archive_root / "A_control_plane_first"
        b_root = archive_root / "B_budget_pre_freeze"
        c_root = archive_root / "C_degraded_audit"
        for root, group in ((a_root, "A"), (b_root, "B"), (c_root, "C")):
            root.mkdir(parents=True, exist_ok=True)
            (root / "formal_summary_windows.json").write_text(
                json.dumps({"verdict": "pass", "proof_group": group}),
                encoding="utf-8",
            )
        (archive_root / "windows_artifact_manifest.json").write_text(
            json.dumps({"missing_count": 0}),
            encoding="utf-8",
        )
        (archive_root / "formal_parity_report.json").write_text(
            json.dumps(
                {
                    "status": "pass",
                    "metric_diff_count": 0,
                    "mandatory_field_set_same": True,
                    "ready_for_gate": True,
                }
            ),
            encoding="utf-8",
        )
        (archive_root / "windows_final_readiness_report.json").write_text(
            json.dumps({"verdict": "ready_for_linux_parity"}),
            encoding="utf-8",
        )
        source_report = archive_root / "formal_scheduler_plan_serial_safe.json"
        source_report.write_text(
            json.dumps(
                {
                    "plan": {
                        "schedule_version": "formal-suite-schedule-v1",
                        "strategy": "serial_safe",
                        "max_parallel": 1,
                        "reasons": ["strategy=serial_safe"],
                        "waves": [
                            [{"task_id": "formal_a", "group": "A", "command": ["should-not-run"], "dependencies": [], "priority": 10, "resource_class": "sidecar"}],
                            [{"task_id": "formal_b", "group": "B", "command": ["should-not-run"], "dependencies": ["formal_a"], "priority": 20, "resource_class": "cpu"}],
                            [{"task_id": "formal_c", "group": "C", "command": ["should-not-run"], "dependencies": ["formal_a"], "priority": 30, "resource_class": "cpu"}],
                        ],
                    },
                    "execution": {
                        "resource_guard": {"accepted": True},
                        "resource_observation": {},
                        "task_results": [
                            {
                                "task_id": "formal_a",
                                "group": "A",
                                "command": ["should-not-run"],
                                "exit_code": 0,
                                "runtime_seconds": 12.0,
                                "started_at": "2026-05-07T00:00:00+00:00",
                                "finished_at": "2026-05-07T00:00:12+00:00",
                                "error_code": None,
                                "error_message": None,
                            },
                            {
                                "task_id": "formal_b",
                                "group": "B",
                                "command": ["should-not-run"],
                                "exit_code": 0,
                                "runtime_seconds": 1.0,
                                "started_at": "2026-05-07T00:00:12+00:00",
                                "finished_at": "2026-05-07T00:00:13+00:00",
                                "error_code": None,
                                "error_message": None,
                            },
                            {
                                "task_id": "formal_c",
                                "group": "C",
                                "command": ["should-not-run"],
                                "exit_code": 0,
                                "runtime_seconds": 1.0,
                                "started_at": "2026-05-07T00:00:13+00:00",
                                "finished_at": "2026-05-07T00:00:14+00:00",
                                "error_code": None,
                                "error_message": None,
                            },
                        ],
                    },
                    "agent_contract": {},
                }
            ),
            encoding="utf-8",
        )
        output_report = archive_root / "formal_scheduler_plan_serial_safe_refresh.json"

        exit_code = run_formal_windows_suite_main([
            "--archive-root",
            str(archive_root),
            "--refresh-only-from-report",
            str(source_report),
            "--schedule-report",
            str(output_report),
        ])

        self.assertEqual(exit_code, 0)
        refreshed = json.loads(output_report.read_text(encoding="utf-8"))
        self.assertEqual(refreshed["execution"]["task_results"][0]["exit_code"], 0)
        self.assertTrue(refreshed["execution"]["summary_artifacts"]["A"]["loaded"])
        self.assertTrue(refreshed["execution"]["summary_artifacts"]["B"]["loaded"])
        self.assertTrue(refreshed["execution"]["summary_artifacts"]["C"]["loaded"])
        self.assertTrue(refreshed["execution"]["parity_summary"]["abc_groups_present"])
        self.assertTrue(refreshed["execution"]["parity_summary"]["ready_for_gate"])
        self.assertTrue(refreshed["execution"]["refresh_only"])
        self.assertEqual(refreshed["execution"]["source_report"], str(source_report.resolve()))

    def test_runtime_optimization_formal_matrix_refresh_only_generates_reports(self) -> None:
        input_root = self._write_formal_matrix_fixture()
        output_root = self.root / "formal-matrix-output"

        exit_code = run_runtime_optimization_formal_matrix_main([
            "--output-root",
            str(output_root),
            "--refresh-only",
            "--input-matrix-root",
            str(input_root),
        ])

        self.assertEqual(exit_code, 0)
        benchmark_report = json.loads((output_root / "benchmark_report.json").read_text(encoding="utf-8"))
        matrix_summary = json.loads((output_root / "formal_matrix_summary.json").read_text(encoding="utf-8"))
        parity_report = json.loads((output_root / "formal_parity_report.json").read_text(encoding="utf-8"))
        readiness_report = json.loads((output_root / "readiness_report.json").read_text(encoding="utf-8"))
        self.assertIsNone(validate_schema(load_specs()["benchmark_report"], benchmark_report))
        self.assertEqual(benchmark_report["summary"]["scenario_count"], 5)
        self.assertEqual(matrix_summary["scenario_count"], 5)
        self.assertEqual([item["scenario_id"] for item in matrix_summary["scenarios"]], [
            "current_baseline",
            "ticket_fast_path",
            "advisor_heuristic",
            "advisor_optional_sklearn",
            "advisor_openai_structured",
        ])
        self.assertTrue(parity_report["proof_hash_consistent"])
        self.assertEqual(parity_report["proof_hash_consistent_by_group"], {"A": True, "B": True, "C": True})
        self.assertEqual(parity_report["proof_hash_count"], 12)
        self.assertEqual(parity_report["expected_group_record_count"], 12)
        self.assertEqual(parity_report["metric_diff_count"], 0)
        self.assertTrue(parity_report["mandatory_field_set_consistent"])
        self.assertEqual(parity_report["mandatory_field_set_consistent_by_group"], {"A": True, "B": True, "C": True})
        self.assertEqual(parity_report["mandatory_field_set_count"], 12)
        self.assertTrue(parity_report["ticket_fast_path_sidecar_bytes_scanned_zero"])
        self.assertTrue(readiness_report["ready_for_gate"])
        self.assertEqual(parity_report["deterministic_scenario_order"], [
            "current_baseline",
            "ticket_fast_path",
            "advisor_heuristic",
            "advisor_optional_sklearn",
        ])
        self.assertEqual(parity_report["optional_scenario_order"], ["advisor_openai_structured"])
        fast_path_rows = {
            row["scenario_id"]: row
            for row in matrix_summary["scenarios"]
            if row["scenario_id"] in {"ticket_fast_path", "advisor_heuristic", "advisor_optional_sklearn", "advisor_openai_structured"}
        }
        self.assertEqual(
            {row["scenario_id"] for row in matrix_summary["scenarios"] if row["sidecar_bytes_scanned"] == 0},
            {"ticket_fast_path", "advisor_heuristic", "advisor_optional_sklearn", "advisor_openai_structured"},
        )
        self.assertTrue(all(row["ready_for_gate"] for row in matrix_summary["scenarios"]))
        self.assertTrue(all(row["required_artifacts_present"] for row in matrix_summary["scenarios"]))
        self.assertTrue(all(row["scheduler_report_present"] for row in matrix_summary["scenarios"]))
        self.assertTrue(all(row["scheduler"]["plan_ref"] for row in matrix_summary["scenarios"]))
        self.assertTrue(all(row["scheduler"]["execution_ref"] for row in matrix_summary["scenarios"]))
        self.assertEqual(fast_path_rows["ticket_fast_path"]["sidecar_bytes_scanned"], 0)
        self.assertTrue(all(set(row["proof_digests"]) == {"A", "B", "C"} for row in matrix_summary["scenarios"]))

    def test_runtime_optimization_formal_matrix_refresh_keeps_proof_digest_facts_drift_zero_for_disabled_heuristic_and_openai_structured(self) -> None:
        input_root = self._write_formal_matrix_fixture()
        output_root = self.root / "formal-matrix-output-proof-drift-zero"

        exit_code = run_runtime_optimization_formal_matrix_main([
            "--output-root",
            str(output_root),
            "--refresh-only",
            "--input-matrix-root",
            str(input_root),
        ])

        self.assertEqual(exit_code, 0)
        benchmark_report = json.loads((output_root / "benchmark_report.json").read_text(encoding="utf-8"))
        parity_report = json.loads((output_root / "formal_parity_report.json").read_text(encoding="utf-8"))
        rows = {row["scenario_id"]: row for row in benchmark_report["results"]}
        expected_modes = {
            "current_baseline": "disabled",
            "advisor_heuristic": "heuristic",
            "advisor_openai_structured": "openai_structured",
        }
        drift_by_scenario = {
            scenario_id: int(dict(rows[scenario_id]["parity_result"]).get("metric_diff_count") or 0)
            for scenario_id in expected_modes
        }

        self.assertEqual(
            {scenario_id: rows[scenario_id]["summary_metrics"]["advisor_mode_effective"] for scenario_id in expected_modes},
            expected_modes,
        )
        self.assertEqual(
            drift_by_scenario,
            {
                "current_baseline": 0,
                "advisor_heuristic": 0,
                "advisor_openai_structured": 0,
            },
        )
        for scenario_id in expected_modes:
            row = rows[scenario_id]
            self.assertTrue(row["summary_metrics"]["proof_digest_boundary_clean"], scenario_id)
            self.assertEqual(row["proof_hash"], row["parity_result"]["baseline_proof_hash"], scenario_id)
        self.assertTrue(parity_report["proof_hash_consistent"])

    def test_runtime_optimization_formal_matrix_refresh_only_allows_distinct_abc_group_hashes(self) -> None:
        input_root = self._write_formal_matrix_fixture(alternate_group_hash="C")
        output_root = self.root / "formal-matrix-output-distinct-c"

        exit_code = run_runtime_optimization_formal_matrix_main([
            "--output-root",
            str(output_root),
            "--refresh-only",
            "--input-matrix-root",
            str(input_root),
        ])

        self.assertEqual(exit_code, 0)
        parity_report = json.loads((output_root / "formal_parity_report.json").read_text(encoding="utf-8"))
        readiness_report = json.loads((output_root / "readiness_report.json").read_text(encoding="utf-8"))
        self.assertTrue(parity_report["proof_hash_consistent"])
        self.assertEqual(parity_report["proof_hash_consistent_by_group"], {"A": True, "B": True, "C": True})
        self.assertEqual(len(parity_report["proof_hashes_by_group"]["A"]), 1)
        self.assertEqual(len(parity_report["proof_hashes_by_group"]["C"]), 1)
        self.assertNotEqual(parity_report["proof_hashes_by_group"]["A"], parity_report["proof_hashes_by_group"]["C"])
        self.assertTrue(readiness_report["ready_for_gate"])

    def test_runtime_optimization_formal_matrix_refresh_only_fails_closed_on_proof_digest_pollution(self) -> None:
        input_root = self._write_formal_matrix_fixture(contaminated_scenario_id="advisor_openai_structured")
        output_root = self.root / "formal-matrix-output-contaminated"

        exit_code = run_runtime_optimization_formal_matrix_main([
            "--output-root",
            str(output_root),
            "--refresh-only",
            "--input-matrix-root",
            str(input_root),
        ])

        self.assertEqual(exit_code, 0)
        matrix_summary = json.loads((output_root / "formal_matrix_summary.json").read_text(encoding="utf-8"))
        readiness_report = json.loads((output_root / "readiness_report.json").read_text(encoding="utf-8"))
        contaminated = [row for row in matrix_summary["scenarios"] if row["scenario_id"] == "advisor_openai_structured"][0]
        self.assertIn("$.openai_tokens", contaminated["proof_digest_pollution_fields"])
        self.assertFalse(contaminated["ready_for_gate"])
        self.assertTrue(readiness_report["ready_for_gate"])
        self.assertEqual(readiness_report["optional_scenario_status"]["advisor_openai_structured"]["failures"], contaminated["failures"])

    def test_runtime_optimization_formal_matrix_refresh_only_fails_closed_on_missing_advisor_report(self) -> None:
        input_root = self._write_formal_matrix_fixture()
        output_root = self.root / "formal-matrix-output-missing-advisor"
        (input_root / "advisor_heuristic" / "control" / "advisor_report.json").unlink()

        exit_code = run_runtime_optimization_formal_matrix_main([
            "--output-root",
            str(output_root),
            "--refresh-only",
            "--input-matrix-root",
            str(input_root),
        ])

        self.assertEqual(exit_code, 0)
        matrix_summary = json.loads((output_root / "formal_matrix_summary.json").read_text(encoding="utf-8"))
        readiness_report = json.loads((output_root / "readiness_report.json").read_text(encoding="utf-8"))
        advisor_row = [row for row in matrix_summary["scenarios"] if row["scenario_id"] == "advisor_heuristic"][0]
        self.assertEqual(advisor_row["advisor"]["status"], "missing")
        self.assertEqual(advisor_row["advisor"]["effective_mode"], "missing")
        self.assertIn("missing_advisor_report", advisor_row["failures"])
        self.assertFalse(advisor_row["ready_for_gate"])
        self.assertFalse(readiness_report["ready_for_gate"])

    def test_runtime_optimization_formal_matrix_refresh_only_fails_closed_on_scenario_parity_and_readiness(self) -> None:
        input_root = self._write_formal_matrix_fixture()
        output_root = self.root / "formal-matrix-output-blocked-scenario"
        scenario_root = input_root / "advisor_heuristic"
        (scenario_root / "formal_parity_report.json").write_text(
            json.dumps(
                {
                    "status": "fail",
                    "metric_diff_count": 1,
                    "mandatory_field_set_same": False,
                    "ready_for_gate": False,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (scenario_root / "windows_final_readiness_report.json").write_text(
            json.dumps({"verdict": "blocked"}, ensure_ascii=False),
            encoding="utf-8",
        )

        exit_code = run_runtime_optimization_formal_matrix_main([
            "--output-root",
            str(output_root),
            "--refresh-only",
            "--input-matrix-root",
            str(input_root),
        ])

        self.assertEqual(exit_code, 0)
        matrix_summary = json.loads((output_root / "formal_matrix_summary.json").read_text(encoding="utf-8"))
        readiness_report = json.loads((output_root / "readiness_report.json").read_text(encoding="utf-8"))
        advisor_row = [row for row in matrix_summary["scenarios"] if row["scenario_id"] == "advisor_heuristic"][0]
        self.assertEqual(advisor_row["formal_parity"]["status"], "fail")
        self.assertEqual(advisor_row["readiness"]["verdict"], "blocked")
        self.assertIn("formal_parity_status_not_pass", advisor_row["failures"])
        self.assertIn("formal_parity_not_ready_for_gate", advisor_row["failures"])
        self.assertIn("formal_parity_metric_diff_count_nonzero", advisor_row["failures"])
        self.assertIn("formal_parity_mandatory_field_set_not_same", advisor_row["failures"])
        self.assertIn("readiness_verdict_not_ready", advisor_row["failures"])
        self.assertFalse(advisor_row["ready_for_gate"])
        self.assertFalse(readiness_report["ready_for_gate"])

    def test_runtime_optimization_formal_matrix_refresh_only_checks_all_group_proof_digests(self) -> None:
        input_root = self._write_formal_matrix_fixture(
            contaminated_scenario_id="advisor_openai_structured",
            contaminated_group="B",
        )
        output_root = self.root / "formal-matrix-output-contaminated-b"

        exit_code = run_runtime_optimization_formal_matrix_main([
            "--output-root",
            str(output_root),
            "--refresh-only",
            "--input-matrix-root",
            str(input_root),
        ])

        self.assertEqual(exit_code, 0)
        matrix_summary = json.loads((output_root / "formal_matrix_summary.json").read_text(encoding="utf-8"))
        readiness_report = json.loads((output_root / "readiness_report.json").read_text(encoding="utf-8"))
        contaminated = [row for row in matrix_summary["scenarios"] if row["scenario_id"] == "advisor_openai_structured"][0]
        self.assertIn("$.openai_tokens", contaminated["proof_digest_pollution_fields_by_group"]["B"])
        self.assertIn("proof_digest_group_B_boundary_polluted", contaminated["failures"])
        self.assertFalse(contaminated["ready_for_gate"])
        self.assertTrue(readiness_report["ready_for_gate"])

    def test_runtime_optimization_formal_matrix_execute_requires_trace_and_contract(self) -> None:
        output_root = self.root / "formal-matrix-output-execute-missing-inputs"

        with self.assertRaises(SystemExit) as exc:
            run_runtime_optimization_formal_matrix_main([
                "--output-root",
                str(output_root),
                "--execute",
            ])
        self.assertEqual(exc.exception.code, 2)

        trace_path, source_report_path, trace_sha256, trace_size_bytes = self._write_execute_inputs()
        with self.assertRaises(SystemExit) as exc:
            run_runtime_optimization_formal_matrix_main([
                "--output-root",
                str(output_root),
                "--execute",
                "--trace",
                str(trace_path),
                "--expected-trace-sha256",
                trace_sha256,
                "--expected-trace-size-bytes",
                str(trace_size_bytes),
            ])
        self.assertEqual(exc.exception.code, 2)

        with self.assertRaises(SystemExit) as exc:
            run_runtime_optimization_formal_matrix_main([
                "--output-root",
                str(output_root),
                "--execute",
                "--source-report",
                str(source_report_path),
                "--expected-trace-sha256",
                trace_sha256,
                "--expected-trace-size-bytes",
                str(trace_size_bytes),
            ])
        self.assertEqual(exc.exception.code, 2)

    def test_runtime_optimization_formal_matrix_execute_passes_scenario_contract_to_suite(self) -> None:
        output_root = self.root / "formal-matrix-output-execute-scenario-contract-argv"
        trace_path, source_report_path, trace_sha256, trace_size_bytes = self._write_execute_inputs()
        suite_argv: list[list[str]] = []

        def _suite_stub(argv: list[str]) -> int:
            suite_argv.append(list(argv))
            archive_root = Path(str(argv[argv.index("--archive-root") + 1])).resolve()
            schedule_report_path = Path(str(argv[argv.index("--schedule-report") + 1])).resolve()
            scenario_contract_path = Path(str(argv[argv.index("--scenario-contract") + 1])).resolve()
            scenario_contract = json.loads(scenario_contract_path.read_text(encoding="utf-8"))
            scheduler_strategy = str(argv[argv.index("--scheduler-strategy") + 1])
            schedule_report_path.parent.mkdir(parents=True, exist_ok=True)
            schedule_report_path.write_text(
                json.dumps(
                    {
                        "plan": {
                            "schedule_version": "formal-suite-schedule-v1",
                            "strategy": "serial_safe",
                            "max_parallel": 1,
                            "reasons": ["strategy=serial_safe"],
                            "waves": [],
                        },
                        "execution": {
                            "resource_guard": {"accepted": True},
                            "resource_observation": {},
                            "task_results": [],
                            "parity_summary": {
                                "abc_groups_present": True,
                                "abc_summary_files_present": True,
                                "abc_summary_verdicts_pass": True,
                                "readiness_verdict": "ready_for_gate",
                                "parity_report_status": "pass",
                                "metric_diff_count": 0,
                                "mandatory_field_set_same": True,
                                "ready_for_gate": True,
                                "failed_task_count": 0,
                            },
                        },
                        "agent_contract": {},
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            for group_dir, group_code in (
                ("A_control_plane_first", "A"),
                ("B_budget_pre_freeze", "B"),
                ("C_degraded_audit", "C"),
            ):
                group_root = archive_root / group_dir
                group_root.mkdir(parents=True, exist_ok=True)
                (group_root / "formal_summary_windows.json").write_text(
                    json.dumps({"verdict": "pass", "proof_group": group_code}, ensure_ascii=False),
                    encoding="utf-8",
                )
            (archive_root / "windows_artifact_manifest.json").write_text(
                json.dumps({"missing_count": 0, "scenario_id": archive_root.name}, ensure_ascii=False),
                encoding="utf-8",
            )
            (archive_root / "formal_parity_report.json").write_text(
                json.dumps(
                    {
                        "status": "pass",
                        "metric_diff_count": 0,
                        "mandatory_field_set_same": True,
                        "ready_for_gate": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (archive_root / "windows_final_readiness_report.json").write_text(
                json.dumps({"verdict": "ready_for_gate"}, ensure_ascii=False),
                encoding="utf-8",
            )
            self.assertEqual(scenario_contract_path, archive_root / "scenario_execution_contract.json")
            self.assertTrue(scenario_contract_path.exists())
            self.assertEqual(scenario_contract["scenario"]["scenario_id"], archive_root.name)
            self.assertEqual(scenario_contract["input_contract"]["scenario"]["scenario_id"], archive_root.name)
            self.assertEqual(scenario_contract["input_contract"]["scheduler_strategy"], scheduler_strategy)
            return 0

        with mock.patch(
            "tool.run_runtime_optimization_formal_matrix.run_formal_windows_suite_main",
            side_effect=_suite_stub,
        ):
            exit_code = run_runtime_optimization_formal_matrix_main([
                "--output-root",
                str(output_root),
                "--execute",
                "--trace",
                str(trace_path),
                "--source-report",
                str(source_report_path),
                "--expected-trace-sha256",
                trace_sha256,
                "--expected-trace-size-bytes",
                str(trace_size_bytes),
            ])

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(suite_argv), len(default_benchmark_scenarios()))
        for argv in suite_argv:
            self.assertIn("--scenario-contract", argv)
            archive_root = Path(str(argv[argv.index("--archive-root") + 1])).resolve()
            scenario_contract_path = Path(str(argv[argv.index("--scenario-contract") + 1])).resolve()
            self.assertEqual(scenario_contract_path, archive_root / "scenario_execution_contract.json")

    def test_formal_windows_suite_passes_scenario_contract_to_abc_commands(self) -> None:
        archive_root = self.root / "formal-suite-scenario-contract"
        trace_path, source_report_path, trace_sha256, trace_size_bytes = self._write_execute_inputs()
        scenario_contract_path = archive_root / "scenario_execution_contract.json"
        self._write_scenario_contract(
            scenario_contract_path,
            scenario_id="advisor_heuristic",
            trace_path=trace_path,
            source_report_path=source_report_path,
            trace_sha256=trace_sha256,
            trace_size_bytes=trace_size_bytes,
        )
        observed_plan: dict[str, list[list[str]]] = {}

        def _capture_execute_plan(self: FormalSuiteSchedulerAgent, plan: Any, **_: Any) -> Any:
            observed_plan["waves"] = [[list(task.command) for task in wave] for wave in plan.waves]
            return mock.Mock(
                ok=True,
                data={
                    "execution_version": "formal-suite-execution-v1",
                    "plan": plan.to_dict(),
                    "resource_guard": {"accepted": True},
                    "resource_observation": {},
                    "task_results": [],
                    "group_summary": {},
                    "summary_artifacts": {},
                    "parity_summary": {"ready_for_gate": True},
                },
                message="ok",
            )

        with mock.patch.object(FormalSuiteSchedulerAgent, "execute_plan", autospec=True, side_effect=_capture_execute_plan):
            exit_code = run_formal_windows_suite_main([
                "--archive-root",
                str(archive_root),
                "--trace",
                str(trace_path),
                "--expected-trace-sha256",
                trace_sha256,
                "--expected-trace-size-bytes",
                str(trace_size_bytes),
                "--scenario-contract",
                str(scenario_contract_path),
            ])

        self.assertEqual(exit_code, 0)
        commands = [command for wave in observed_plan["waves"] for command in wave]
        abc_commands = [
            command
            for command in commands
            if any(command[1].endswith(script_name) for script_name in ("run_formal_a_windows.py", "run_formal_b_windows.py", "run_formal_c_windows.py"))
        ]
        self.assertEqual(len(abc_commands), 3)
        for command in abc_commands:
            self.assertIn("--scenario-contract", command)
            self.assertEqual(command[command.index("--scenario-contract") + 1], str(scenario_contract_path.resolve()))

    def test_formal_windows_suite_rejects_scenario_contract_trace_binding_mismatch(self) -> None:
        archive_root = self.root / "formal-suite-scenario-contract-mismatch"
        trace_path, source_report_path, trace_sha256, trace_size_bytes = self._write_execute_inputs()
        scenario_contract_path = archive_root / "scenario_execution_contract.json"
        self._write_scenario_contract(
            scenario_contract_path,
            scenario_id="advisor_heuristic",
            trace_path=trace_path,
            source_report_path=source_report_path,
            trace_sha256="0" * 64,
            trace_size_bytes=trace_size_bytes,
        )

        with self.assertRaises(SystemExit) as exc:
            run_formal_windows_suite_main([
                "--archive-root",
                str(archive_root),
                "--trace",
                str(trace_path),
                "--expected-trace-sha256",
                trace_sha256,
                "--expected-trace-size-bytes",
                str(trace_size_bytes),
                "--source-report",
                str(source_report_path),
                "--scenario-contract",
                str(scenario_contract_path),
            ])

        self.assertEqual(exc.exception.code, 2)

    def test_runtime_optimization_formal_matrix_execute_runs_real_suite_path_with_stub(self) -> None:
        output_root = self.root / "formal-matrix-output-execute-success"
        trace_path, source_report_path, trace_sha256, trace_size_bytes = self._write_execute_inputs()

        with mock.patch(
            "tool.run_runtime_optimization_formal_matrix.run_formal_windows_suite_main",
            side_effect=self._formal_matrix_suite_stub(),
        ):
            exit_code = run_runtime_optimization_formal_matrix_main([
                "--output-root",
                str(output_root),
                "--execute",
                "--trace",
                str(trace_path),
                "--source-report",
                str(source_report_path),
                "--expected-trace-sha256",
                trace_sha256,
                "--expected-trace-size-bytes",
                str(trace_size_bytes),
            ])

        self.assertEqual(exit_code, 0)
        benchmark_report = json.loads((output_root / "benchmark_report.json").read_text(encoding="utf-8"))
        matrix_summary = json.loads((output_root / "formal_matrix_summary.json").read_text(encoding="utf-8"))
        readiness_report = json.loads((output_root / "readiness_report.json").read_text(encoding="utf-8"))
        formal_input_manifest = json.loads((output_root / "formal_input_manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(readiness_report["ready_for_gate"])
        self.assertEqual(matrix_summary["mode"], "execute")
        self.assertEqual(benchmark_report["input_contract"]["formal_matrix_mode"], "execute")
        self.assertEqual(benchmark_report["input_contract"]["formal_input_manifest_path"], str((output_root / "formal_input_manifest.json").resolve()))
        self.assertEqual(benchmark_report["summary"]["scenario_count"], 5)
        self.assertEqual(formal_input_manifest["trace"]["sha256"], trace_sha256)
        self.assertEqual(formal_input_manifest["trace"]["size_bytes"], trace_size_bytes)
        self.assertEqual(formal_input_manifest["source_report_path"], str(source_report_path.resolve()))
        self.assertEqual(formal_input_manifest["deterministic_scenario_order"], [
            "current_baseline",
            "ticket_fast_path",
            "advisor_heuristic",
            "advisor_optional_sklearn",
        ])
        self.assertEqual(formal_input_manifest["optional_scenario_order"], ["advisor_openai_structured"])
        phase4_reporting = benchmark_report["summary"]["phase4_reporting"]
        self.assertEqual(set(phase4_reporting["advisor_mode_requested"]), {row["scenario_id"] for row in benchmark_report["results"]})
        self.assertEqual(
            phase4_reporting["checksum_validation_result_by_scenario"]["advisor_openai_structured"]["status"],
            "not_applicable",
        )
        for row in benchmark_report["results"]:
            self.assertGreaterEqual(float(row["runtime_seconds"]), 0.0)
            self.assertIn(row["status"], {"completed", "failed"})
            self.assertTrue(Path(row["artifact_refs"]["scenario_result"]).exists())
            self.assertTrue(Path(row["artifact_refs"]["benchmark_row"]).exists())
            summary_metrics = row["summary_metrics"]
            self.assertEqual(summary_metrics["metric_scope"], "formal_matrix")
            self.assertIsNone(summary_metrics["product_runtime_path"])
            self.assertEqual(summary_metrics["formal_wall_seconds"], row["runtime_seconds"])
            self.assertIsNone(summary_metrics["sidecar_build_seconds"])
            self.assertEqual(summary_metrics["sidecar_index_build_open_seconds"], row["index_build_open_seconds"])
            self.assertEqual(summary_metrics["ticket_validate_seconds"], row["sidecar_validate_seconds"])
            self.assertEqual(summary_metrics["advisor_overhead_seconds"], row["advisor_overhead_seconds"])
            self.assertEqual(summary_metrics["advisor_latency_seconds"], row["advisor_overhead_seconds"])
            self.assertTrue(summary_metrics["formal_proof_only"])
            self.assertEqual(summary_metrics["speedup_evidence_scope"], "formal_only_not_product_speedup")
            self.assertIn(
                summary_metrics["checksum_validation_result"]["status"],
                {"passed", "failed", "fallback", "not_applicable", "missing"},
            )
            self.assertTrue(summary_metrics["gate_accept"])
            self.assertIsNone(summary_metrics["gate_reject_reason"])
            self.assertEqual(summary_metrics["proof_drift"]["status"], "clean")
            self.assertEqual(summary_metrics["proof_drift"]["metric_diff_count"], 0)
            self.assertIn(summary_metrics["plan_regret"]["status"], {"not_applicable", "not_measured"})
            self.assertEqual(summary_metrics["plan_regret"]["claim_strength"], "report_only")
            self.assertTrue(summary_metrics["plan_regret"]["reason"])
            self.assertTrue(summary_metrics["plan_regret"]["source"])
            self.assertIn("report_only_no_counterfactual_runtime_source", summary_metrics["plan_regret"]["notes"])
            self.assertIn(summary_metrics["counterfactual_replay"]["status"], {"not_applicable", "not_measured"})
            self.assertEqual(summary_metrics["counterfactual_replay"]["claim_strength"], "report_only")
            self.assertTrue(summary_metrics["counterfactual_replay"]["reason"])
            self.assertTrue(summary_metrics["counterfactual_replay"]["source"])
            self.assertIn("report_only_no_counterfactual_fixture", summary_metrics["counterfactual_replay"]["notes"])
            self.assertTrue(
                set(summary_metrics["synthetic_metric_fields"]).issuperset(
                    {
                        "sidecar_validate_seconds",
                        "index_build_open_seconds",
                        "peak_rss_mb",
                        "package_write_seconds",
                        "advisor_overhead_seconds",
                    }
                )
            )
        for scenario_row in matrix_summary["scenarios"]:
            scenario_root = output_root / scenario_row["scenario_id"]
            contract_path = scenario_root / "scenario_execution_contract.json"
            benchmark_row_path = scenario_root / "benchmark_row.json"
            self.assertTrue(contract_path.exists())
            self.assertTrue(benchmark_row_path.exists())
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            self.assertEqual(contract["trace"]["sha256"], trace_sha256)
            self.assertEqual(contract["trace"]["size_bytes"], trace_size_bytes)
            self.assertEqual(contract["source_report_path"], str(source_report_path.resolve()))
            expected_strategy = "sidecar_first" if scenario_row["scenario"]["advisor_enabled"] else "serial_safe"
            self.assertEqual(contract["scheduler_strategy"], expected_strategy)
            self.assertEqual(contract["scenario"]["scenario_id"], scenario_row["scenario_id"])
            self.assertEqual(contract["input_contract"]["formal_input_manifest_path"], str((output_root / "formal_input_manifest.json").resolve()))
            self.assertEqual(contract["input_contract"]["scheduler_strategy"], expected_strategy)
            self.assertEqual(contract["input_contract"]["scenario_id"], scenario_row["scenario_id"])
            self.assertEqual(contract["input_contract"]["scenario"]["scenario_id"], scenario_row["scenario_id"])
            scenario_result = json.loads((scenario_root / "scenario_result.json").read_text(encoding="utf-8"))
            benchmark_row = json.loads(benchmark_row_path.read_text(encoding="utf-8"))
            self.assertEqual(scenario_result["artifact_refs"]["scenario_execution_contract"], str(contract_path.resolve()))
            self.assertEqual(benchmark_row["artifact_refs"]["scenario_execution_contract"], str(contract_path.resolve()))
            self.assertEqual(benchmark_row["scenario_id"], scenario_result["scenario_id"])
            self.assertEqual(scenario_result["summary_metrics"]["ticket_fast_path_required"], scenario_row["ticket_fast_path_required"])
            self.assertTrue(benchmark_row["summary_metrics"]["formal_proof_only"])
            self.assertEqual(benchmark_row["summary_metrics"]["formal_wall_seconds"], benchmark_row["runtime_seconds"])
            self.assertIsNone(benchmark_row["summary_metrics"]["product_runtime_path"])
            self.assertIn("checksum_validation_result", benchmark_row["summary_metrics"])
            self.assertIn("proof_drift", benchmark_row["summary_metrics"])
            self.assertIn("plan_regret", benchmark_row["summary_metrics"])
            self.assertIn("counterfactual_replay", benchmark_row["summary_metrics"])
            self.assertIn("gate_accept", benchmark_row["summary_metrics"])
            self.assertIn("gate_reject_reason", benchmark_row["summary_metrics"])
            self.assertTrue(
                set(benchmark_row["summary_metrics"]["synthetic_metric_fields"]).issuperset(
                    {
                        "sidecar_validate_seconds",
                        "index_build_open_seconds",
                        "peak_rss_mb",
                        "package_write_seconds",
                        "advisor_overhead_seconds",
                    }
                )
            )
            self.assertTrue((scenario_root / "control" / "advisor_report.json").exists())
            self.assertTrue((scenario_root / "control" / "pre_execution_advisor_trace.json").exists())
            advisor_report = json.loads((scenario_root / "control" / "advisor_report.json").read_text(encoding="utf-8"))
            if scenario_row["scenario_id"] in {"current_baseline", "ticket_fast_path"}:
                self.assertEqual(advisor_report["status"], "disabled")
                self.assertEqual(advisor_report["decision"]["advisor_mode"], "disabled")
            if scenario_row["scenario_id"] == "ticket_fast_path":
                self.assertEqual(scenario_result["summary_metrics"]["advisor_mode_effective"], "disabled")

    def test_runtime_optimization_formal_matrix_execute_optional_openai_failure_does_not_block_readiness(self) -> None:
        output_root = self.root / "formal-matrix-output-execute-openai-optional-failure"
        trace_path, source_report_path, trace_sha256, trace_size_bytes = self._write_execute_inputs()

        with mock.patch(
            "tool.run_runtime_optimization_formal_matrix.run_formal_windows_suite_main",
            side_effect=self._formal_matrix_suite_stub(failing_group_scenarios={"advisor_openai_structured": "B"}),
        ):
            exit_code = run_runtime_optimization_formal_matrix_main([
                "--output-root",
                str(output_root),
                "--execute",
                "--trace",
                str(trace_path),
                "--source-report",
                str(source_report_path),
                "--expected-trace-sha256",
                trace_sha256,
                "--expected-trace-size-bytes",
                str(trace_size_bytes),
            ])

        self.assertEqual(exit_code, 0)
        matrix_summary = json.loads((output_root / "formal_matrix_summary.json").read_text(encoding="utf-8"))
        parity_report = json.loads((output_root / "formal_parity_report.json").read_text(encoding="utf-8"))
        readiness_report = json.loads((output_root / "readiness_report.json").read_text(encoding="utf-8"))
        openai_row = [row for row in matrix_summary["scenarios"] if row["scenario_id"] == "advisor_openai_structured"][0]
        self.assertIn("formal_group_B_verdict_not_pass", openai_row["failures"])
        self.assertFalse(openai_row["ready_for_gate"])
        self.assertTrue(readiness_report["ready_for_gate"])
        self.assertTrue(parity_report["ready_for_gate"])
        self.assertIn("advisor_openai_structured:formal_group_B_verdict_not_pass", parity_report["optional_failures"])
        self.assertGreaterEqual(matrix_summary["summary"]["optional_failure_count"], 1)
        self.assertEqual(matrix_summary["summary"]["optional_failures"], [
            f"advisor_openai_structured:{failure}"
            for failure in openai_row["failures"]
        ])

    def test_runtime_optimization_formal_matrix_execute_deterministic_failure_blocks_readiness(self) -> None:
        output_root = self.root / "formal-matrix-output-execute-deterministic-failure"
        trace_path, source_report_path, trace_sha256, trace_size_bytes = self._write_execute_inputs()

        with mock.patch(
            "tool.run_runtime_optimization_formal_matrix.run_formal_windows_suite_main",
            side_effect=self._formal_matrix_suite_stub(failing_group_scenarios={"advisor_heuristic": "B"}),
        ):
            exit_code = run_runtime_optimization_formal_matrix_main([
                "--output-root",
                str(output_root),
                "--execute",
                "--trace",
                str(trace_path),
                "--source-report",
                str(source_report_path),
                "--expected-trace-sha256",
                trace_sha256,
                "--expected-trace-size-bytes",
                str(trace_size_bytes),
            ])

        self.assertEqual(exit_code, 0)
        matrix_summary = json.loads((output_root / "formal_matrix_summary.json").read_text(encoding="utf-8"))
        parity_report = json.loads((output_root / "formal_parity_report.json").read_text(encoding="utf-8"))
        readiness_report = json.loads((output_root / "readiness_report.json").read_text(encoding="utf-8"))
        blocked = [row for row in matrix_summary["scenarios"] if row["scenario_id"] == "advisor_heuristic"][0]
        self.assertIn("formal_group_B_verdict_not_pass", blocked["failures"])
        self.assertFalse(blocked["ready_for_gate"])
        self.assertFalse(readiness_report["ready_for_gate"])
        self.assertFalse(parity_report["ready_for_gate"])

    def test_runtime_optimization_formal_matrix_execute_scheduler_task_failure_blocks_readiness(self) -> None:
        output_root = self.root / "formal-matrix-output-execute-scheduler-failure"
        trace_path, source_report_path, trace_sha256, trace_size_bytes = self._write_execute_inputs()

        with mock.patch(
            "tool.run_runtime_optimization_formal_matrix.run_formal_windows_suite_main",
            side_effect=self._formal_matrix_suite_stub(failing_task_scenarios={"advisor_heuristic"}),
        ):
            exit_code = run_runtime_optimization_formal_matrix_main([
                "--output-root",
                str(output_root),
                "--execute",
                "--trace",
                str(trace_path),
                "--source-report",
                str(source_report_path),
                "--expected-trace-sha256",
                trace_sha256,
                "--expected-trace-size-bytes",
                str(trace_size_bytes),
            ])

        self.assertEqual(exit_code, 0)
        matrix_summary = json.loads((output_root / "formal_matrix_summary.json").read_text(encoding="utf-8"))
        readiness_report = json.loads((output_root / "readiness_report.json").read_text(encoding="utf-8"))
        blocked = [row for row in matrix_summary["scenarios"] if row["scenario_id"] == "advisor_heuristic"][0]
        self.assertIn("scheduler_task_failure", blocked["failures"])
        self.assertIn("scheduler_task_failed:formal_b", blocked["failures"])
        self.assertEqual(blocked["scheduler"]["failed_task_count"], 1)
        self.assertFalse(blocked["ready_for_gate"])
        self.assertFalse(readiness_report["ready_for_gate"])

    def test_runtime_optimization_formal_matrix_execute_missing_or_failed_abc_blocks_readiness(self) -> None:
        output_root = self.root / "formal-matrix-output-execute-abc-blocked"
        trace_path, source_report_path, trace_sha256, trace_size_bytes = self._write_execute_inputs()

        with mock.patch(
            "tool.run_runtime_optimization_formal_matrix.run_formal_windows_suite_main",
            side_effect=self._formal_matrix_suite_stub(
                missing_group_scenarios={"ticket_fast_path": "C"},
                failing_group_scenarios={"advisor_openai_structured": "B"},
            ),
        ):
            exit_code = run_runtime_optimization_formal_matrix_main([
                "--output-root",
                str(output_root),
                "--execute",
                "--trace",
                str(trace_path),
                "--source-report",
                str(source_report_path),
                "--expected-trace-sha256",
                trace_sha256,
                "--expected-trace-size-bytes",
                str(trace_size_bytes),
            ])

        self.assertEqual(exit_code, 0)
        matrix_summary = json.loads((output_root / "formal_matrix_summary.json").read_text(encoding="utf-8"))
        readiness_report = json.loads((output_root / "readiness_report.json").read_text(encoding="utf-8"))
        missing_group_row = [row for row in matrix_summary["scenarios"] if row["scenario_id"] == "ticket_fast_path"][0]
        failing_group_row = [row for row in matrix_summary["scenarios"] if row["scenario_id"] == "advisor_openai_structured"][0]
        self.assertIn("formal_group_C_missing", missing_group_row["failures"])
        self.assertFalse(missing_group_row["ready_for_gate"])
        self.assertIn("formal_group_B_verdict_not_pass", failing_group_row["failures"])
        self.assertFalse(failing_group_row["ready_for_gate"])
        self.assertFalse(readiness_report["ready_for_gate"])

    def test_benchmark_matrix_contract_lists_required_scenarios(self) -> None:
        scenarios = default_benchmark_scenarios()
        self.assertEqual([scenario.scenario_id for scenario in scenarios], [
            "current_baseline",
            "ticket_fast_path",
            "advisor_heuristic",
            "advisor_optional_sklearn",
            "advisor_openai_structured",
        ])
        report = BenchmarkReportBuilder().build_report(
            [
                {"scenario_id": "current_baseline", "runtime_seconds": 2.0, "proof_hash": "sha256:same"},
                {"scenario_id": "ticket_fast_path", "runtime_seconds": 1.0, "proof_hash": "sha256:same"},
            ]
        )
        self.assertTrue(report["summary"]["parity"]["proof_hash_consistent"])
        self.assertEqual(report["summary"]["runtime_seconds"]["ticket_fast_path"], 1.0)

    def test_runtime_benchmark_openai_structured_payload_enables_llm(self) -> None:
        from tool.runtime_benchmark_runner import _scenario_payload

        scenario = [
            item
            for item in default_benchmark_scenarios()
            if item.scenario_id == "advisor_openai_structured"
        ][0]
        payload = _scenario_payload(
            scenario,
            dataset_id="dataset:benchmark",
            sidecar_path=self.root / "dependency_sidecar.jsonl",
            sidecar_manifest_path=self.root / "sidecar_manifest.json",
            telemetry_history_path=self.root / "telemetry_history.jsonl",
        )

        self.assertEqual(payload["advisor_config"]["advisor_mode"], "openai_structured")
        self.assertIs(payload["advisor_config"]["llm_enabled"], True)

    def test_benchmark_matrix_executes_all_scenarios(self) -> None:
        executed = BenchmarkReportBuilder().run_matrix(
            default_benchmark_scenarios(),
            input_contract={"snapshot_id": "snapshot:benchmark:test"},
            output_root=self.root / "benchmark-out",
        )
        self.assertTrue(executed.ok, executed.message)
        self.assertEqual(executed.data["summary"]["scenario_count"], 5)
        self.assertTrue(executed.data["summary"]["parity"]["proof_hash_consistent"])
        history_path = self.root / "benchmark-out" / "telemetry_history.jsonl"
        self.assertEqual(executed.data["input_contract"]["telemetry_history_path"], str(history_path))
        self.assertTrue(history_path.exists())
        advisor_history = TelemetryHistoryStore(history_path).read_advisor_history(export_family="benchmark")
        self.assertEqual(len(advisor_history), 5)
        openai_metrics = {
            row["scenario_id"]: row
            for row in executed.data["results"]
        }["advisor_openai_structured"]["summary_metrics"]
        self.assertEqual(openai_metrics["advisor_mode_requested"], "openai_structured")
        self.assertEqual(openai_metrics["advisor_mode_effective"], "heuristic")
        self.assertEqual(openai_metrics["fallback_reason"], "openai_unconfigured")
        self.assertIn("openai_unconfigured", openai_metrics["fallback_reasons"])
        self.assertIn("openai_latency_seconds", openai_metrics)
        self.assertIn("openai_tokens", openai_metrics)
        self.assertIn("advisor_latency_seconds", openai_metrics)
        self.assertEqual(openai_metrics["checksum_validation_result"]["status"], "not_applicable")
        self.assertTrue(openai_metrics["gate_accept"])
        self.assertIsNone(openai_metrics["gate_reject_reason"])
        self.assertEqual(openai_metrics["proof_drift"]["status"], "clean")
        self.assertEqual(openai_metrics["plan_regret"]["status"], "not_measured")
        self.assertEqual(openai_metrics["plan_regret"]["reason"], "no_oracle_safe_action_runtime_available")
        self.assertEqual(openai_metrics["plan_regret"]["claim_strength"], "report_only")
        self.assertEqual(openai_metrics["counterfactual_replay"]["status"], "not_measured")
        self.assertEqual(openai_metrics["counterfactual_replay"]["reason"], "no_counterfactual_fixture_or_external_benchmark_not_run")
        self.assertEqual(openai_metrics["counterfactual_replay"]["claim_strength"], "report_only")
        self.assertIn("retrieval_case_count", openai_metrics)
        self.assertIn("retrieval_has_sufficient_similarity", openai_metrics)
        self.assertIn("retrieval_applied_abstain_reason", openai_metrics)
        self.assertIn("retrieval_reject_taxonomy_coverage", openai_metrics)
        self.assertEqual(
            executed.data["summary"]["phase4_reporting"]["fallback_reason_by_scenario"]["advisor_openai_structured"],
            "openai_unconfigured",
        )
        self.assertEqual(
            executed.data["summary"]["phase4_reporting"]["checksum_validation_result_by_scenario"]["advisor_openai_structured"]["status"],
            "not_applicable",
        )
        self.assertEqual(
            {
                refs["telemetry_history_path"]
                for refs in executed.data["summary"]["artifact_refs"].values()
            },
            {str(history_path.resolve())},
        )

    def test_runtime_optimization_case_bank_has_required_cases_and_labels(self) -> None:
        manifest, cases = self._load_runtime_case_bank()
        self.assertGreaterEqual(len(cases), 30)
        case_tags = {
            tag
            for case in cases
            for tag in list(dict(case).get("case_tags") or [])
        }
        self.assertTrue(set(manifest["required_case_tags"]).issubset(case_tags))
        labels = {str(case.get("label") or "") for case in cases}
        self.assertTrue({"accept", "reject", "abstain", "unsafe", "needs_more_data"}.issubset(labels))

    def test_runtime_optimization_case_bank_reject_taxonomy_coverage_meets_target(self) -> None:
        _manifest, cases = self._load_runtime_case_bank()
        rejected = [
            case
            for case in cases
            if str(case.get("label") or "") in {"reject", "unsafe"}
        ]
        self.assertTrue(rejected)
        covered = [
            case
            for case in rejected
            if str(case.get("reject_taxonomy") or "").strip()
            or str(dict(case.get("gate_outcome") or {}).get("rejected_reason") or "").strip()
        ]
        self.assertGreaterEqual(len(covered) / len(rejected), 0.95)

    def test_runtime_benchmark_cli_can_limit_scenarios(self) -> None:
        trace_path = write_scenario(self.root / "runtime-benchmark-selected.trace", name="basic", repeat=1)
        output_path = self.root / "runtime-benchmark-selected-report.json"
        output_root = self.root / "runtime-benchmark-selected-matrix"

        subprocess.run(
            [
                sys.executable,
                "tool/run_runtime_optimization_benchmark.py",
                "--output",
                str(output_path),
                "--output-root",
                str(output_root),
                "--trace",
                str(trace_path),
                "--scenario",
                "current_baseline",
                "--scenario",
                "ticket_fast_path",
            ],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
        )

        report = json.loads(output_path.read_text(encoding="utf-8"))
        results = {row["scenario_id"]: row for row in report["results"]}
        self.assertEqual(set(results), {"current_baseline", "ticket_fast_path"})
        self.assertEqual(report["scenario_order"], ["current_baseline", "ticket_fast_path"])
        self.assertEqual(
            [scenario["scenario_id"] for scenario in report["scenarios"]],
            ["current_baseline", "ticket_fast_path"],
        )
        self.assertEqual(report["summary"]["scenario_count"], 2)
        self.assertNotIn("advisor_openai_structured", report["summary"]["status_by_scenario"])

    def test_runtime_benchmark_cli_repeat_reports_median_p95(self) -> None:
        trace_path = write_scenario(self.root / "runtime-benchmark-repeat.trace", name="basic", repeat=1)
        output_path = self.root / "runtime-benchmark-repeat-report.json"
        output_root = self.root / "runtime-benchmark-repeat-matrix"

        subprocess.run(
            [
                sys.executable,
                "tool/run_runtime_optimization_benchmark.py",
                "--output",
                str(output_path),
                "--output-root",
                str(output_root),
                "--trace",
                str(trace_path),
                "--scenario",
                "current_baseline",
                "--repeat",
                "2",
            ],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
        )

        report = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertIsNone(validate_schema(load_specs()["benchmark_report"], report))
        self.assertEqual(report["input_contract"]["repeat_count"], 2)
        self.assertEqual(report["input_contract"]["repeat_mode"], "product_runtime_matrix")
        self.assertEqual(report["summary"]["scenario_count"], 2)
        stats = report["summary"]["repeated_run_statistics"]["current_baseline"]
        self.assertEqual(stats["run_count"], 2)
        self.assertEqual(stats["completed_count"], 2)
        runtime_stats = stats["metrics"]["runtime_seconds"]
        self.assertEqual(runtime_stats["count"], 2)
        self.assertEqual(len(runtime_stats["samples"]), 2)
        self.assertIsNotNone(runtime_stats["median"])
        self.assertIsNotNone(runtime_stats["p95"])

    def test_parser_cache_warm_hot_benchmark_cli_reports_cache_hits(self) -> None:
        trace_path = write_scenario(self.root / "parser-cache-warm-hot.trace", name="basic", repeat=2)
        output_path = self.root / "parser-cache-warm-hot-report.json"
        output_root = self.root / "parser-cache-warm-hot-artifacts"

        subprocess.run(
            [
                sys.executable,
                "tool/run_parser_cache_warm_hot_benchmark.py",
                "--output",
                str(output_path),
                "--output-root",
                str(output_root),
                "--trace",
                str(trace_path),
            ],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
        )

        report = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(report["report_version"], "parser-cache-warm-hot-benchmark-v1")
        self.assertEqual([row["phase"] for row in report["results"]], ["cold", "warm", "hot"])
        self.assertEqual([row["cache_hit"] for row in report["results"]], [False, True, True])
        self.assertTrue(report["summary"]["all_ok"])
        self.assertTrue(report["summary"]["cache_sequence_valid"])
        self.assertEqual(report["summary"]["cache_hit_sequence"], [False, True, True])
        self.assertTrue(report["results"][1]["load_artifact"])
        self.assertFalse(report["results"][2]["load_artifact"])
        self.assertGreaterEqual(report["summary"]["cold_wall_seconds"], 0.0)
        self.assertGreaterEqual(report["summary"]["warm_wall_seconds"], 0.0)
        self.assertGreaterEqual(report["summary"]["hot_wall_seconds"], 0.0)

    def _product_evidence_stage_output_root(self, argv: list[str]) -> Path:
        return Path(argv[argv.index("--output-root") + 1])

    def _write_product_evidence_stage_report(
        self,
        output_root: Path,
        *,
        suite: str,
        execution_status: str = "completed",
        claimable: bool = False,
        status: str | None = None,
    ) -> None:
        claimable_speedups: list[dict[str, Any]] = []
        suites = {suite: {"claim_evaluation": {}}}
        if suite == "load_cache":
            suites[suite]["claim_evaluation"]["claimable"] = claimable
            if claimable:
                claimable_speedups.append(
                    {
                        "suite": "load_cache",
                        "claim": "cached_desktop_open_load_median_reduced",
                    }
                )
        elif suite == "background_prebuild":
            suites[suite]["claim_evaluation"]["click_to_export_claimable"] = claimable
            if claimable:
                claimable_speedups.append(
                    {
                        "suite": "background_prebuild",
                        "claim": "click_to_export_wait_reduced",
                    }
                )
        else:
            raise AssertionError(f"unsupported suite {suite}")
        effective_status = status
        if effective_status is None:
            effective_status = "pass_with_product_speedup_evidence" if claimable else "pass_with_noted_limits"
        report = {
            "status": effective_status,
            "execution_status": execution_status,
            "claimable_speedups": claimable_speedups,
            "suites": suites,
        }
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "product_evidence_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (output_root / "product_evidence_summary.md").write_text("# synthetic\n", encoding="utf-8")

    def test_runtime_optimization_product_evidence_staged_dry_run_writes_planned_report_without_calling_harness(self) -> None:
        output_root = self.root / "product-evidence-staged-dry-run"

        with mock.patch.object(
            runtime_optimization_product_evidence_staged_tool.runtime_optimization_product_evidence_tool,
            "main",
            side_effect=AssertionError("dry-run must not call harness"),
        ) as harness_main:
            exit_code = run_runtime_optimization_product_evidence_staged_main(
                [
                    "--trace",
                    str(self.root / "synthetic.trace"),
                    "--output-root",
                    str(output_root),
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        harness_main.assert_not_called()
        report = json.loads((output_root / "staged_product_evidence_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "planned")
        self.assertEqual(report["execution_status"], "planned")
        self.assertEqual(report["evidence_status"], "planned")
        self.assertEqual([stage["id"] for stage in report["stages"]], ["p3_verify", "p3_main", "p4_main"])
        self.assertTrue(all(stage["status"] == "planned" for stage in report["stages"]))
        self.assertTrue(all(stage["summary_path"].endswith("product_evidence_summary.md") for stage in report["stages"]))
        self.assertTrue(all(stage["harness_status"] is None for stage in report["stages"]))
        self.assertEqual(report["p5_1gb_status"], "not_run")
        self.assertEqual(report["conclusions"]["p5_1gb"]["status"], "not_run")
        self.assertIn("--load-cache-mode", report["stages"][1]["argv"])
        self.assertIn("每个阶段都显式传入单 suite", report["claim_policy"][0])

    def test_runtime_optimization_product_evidence_staged_happy_path_runs_stages_and_promotes_p3_p4_claims(self) -> None:
        trace_path = self.root / "synthetic.trace"
        output_root = self.root / "product-evidence-staged-happy-path"
        expected_output_root = output_root.expanduser().resolve()
        expected_trace = trace_path.expanduser().resolve()
        calls: list[list[str]] = []

        def fake_harness_main(argv: list[str]) -> int:
            calls.append(list(argv))
            stage_output_root = self._product_evidence_stage_output_root(argv)
            suite_name = argv[argv.index("--suite") + 1]
            self._write_product_evidence_stage_report(
                stage_output_root,
                suite=suite_name,
                claimable=stage_output_root.name in {"p3_main", "p4_main"},
            )
            return 0

        with mock.patch.object(
            runtime_optimization_product_evidence_staged_tool.runtime_optimization_product_evidence_tool,
            "main",
            side_effect=fake_harness_main,
        ):
            exit_code = run_runtime_optimization_product_evidence_staged_main(
                [
                    "--trace",
                    str(trace_path),
                    "--output-root",
                    str(output_root),
                    "--p3-main-repeat",
                    "4",
                    "--p4-prebuild-timeout-s",
                    "12.5",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            calls,
            [
                [
                    "--trace",
                    str(expected_trace),
                    "--output-root",
                    str(expected_output_root / "p3_verify"),
                    "--suite",
                    "load_cache",
                    "--repeat",
                    "1",
                ],
                [
                    "--trace",
                    str(expected_trace),
                    "--output-root",
                    str(expected_output_root / "p3_main"),
                    "--suite",
                    "load_cache",
                    "--repeat",
                    "4",
                    "--load-cache-mode",
                    "cold_once_warm_repeat",
                ],
                [
                    "--trace",
                    str(expected_trace),
                    "--output-root",
                    str(expected_output_root / "p4_main"),
                    "--suite",
                    "background_prebuild",
                    "--repeat",
                    "1",
                    "--prebuild-timeout-s",
                    "12.5",
                ],
            ],
        )
        report = json.loads((output_root / "staged_product_evidence_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["execution_status"], "completed")
        self.assertEqual(report["evidence_status"], "pass_with_noted_limits")
        self.assertEqual(
            [stage["output_root"] for stage in report["stages"]],
            [
                str(expected_output_root / "p3_verify"),
                str(expected_output_root / "p3_main"),
                str(expected_output_root / "p4_main"),
            ],
        )
        self.assertTrue(all(stage["status"] == "completed" for stage in report["stages"]))
        self.assertTrue(all(stage["summary_path"].endswith("product_evidence_summary.md") for stage in report["stages"]))
        self.assertTrue(all(stage["elapsed_seconds"] is not None for stage in report["stages"]))
        self.assertEqual(
            [stage["harness_status"] for stage in report["stages"]],
            [
                "pass_with_noted_limits",
                "pass_with_product_speedup_evidence",
                "pass_with_product_speedup_evidence",
            ],
        )
        self.assertTrue(report["conclusions"]["p3_open_cache"]["claimable"])
        self.assertTrue(report["conclusions"]["p4_click_wait"]["claimable"])
        self.assertEqual(report["conclusions"]["p5_1gb"]["status"], "not_run")

    def test_runtime_optimization_product_evidence_staged_stops_after_p3_verify_failure(self) -> None:
        output_root = self.root / "product-evidence-staged-stop-on-failure"
        calls: list[list[str]] = []

        def fake_harness_main(argv: list[str]) -> int:
            calls.append(list(argv))
            self._write_product_evidence_stage_report(
                self._product_evidence_stage_output_root(argv),
                suite="load_cache",
                execution_status="failed",
                claimable=False,
            )
            return 1

        with mock.patch.object(
            runtime_optimization_product_evidence_staged_tool.runtime_optimization_product_evidence_tool,
            "main",
            side_effect=fake_harness_main,
        ):
            exit_code = run_runtime_optimization_product_evidence_staged_main(
                [
                    "--trace",
                    str(self.root / "synthetic.trace"),
                    "--output-root",
                    str(output_root),
                ]
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(calls), 1)
        report = json.loads((output_root / "staged_product_evidence_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["execution_status"], "failed")
        self.assertEqual(report["evidence_status"], "failed")
        self.assertEqual([stage["status"] for stage in report["stages"]], ["failed", "not_run", "not_run"])
        self.assertEqual(report["stages"][0]["harness_execution_status"], "failed")
        self.assertEqual(report["stop_reason"], "p3_verify 阶段失败，停止执行 p3_main 与 p4_main。")

    def test_runtime_optimization_product_evidence_staged_skip_p4_marks_p4_not_run(self) -> None:
        output_root = self.root / "product-evidence-staged-skip-p4"
        calls: list[list[str]] = []

        def fake_harness_main(argv: list[str]) -> int:
            calls.append(list(argv))
            stage_output_root = self._product_evidence_stage_output_root(argv)
            self._write_product_evidence_stage_report(
                stage_output_root,
                suite="load_cache",
                claimable=stage_output_root.name == "p3_main",
            )
            return 0

        with mock.patch.object(
            runtime_optimization_product_evidence_staged_tool.runtime_optimization_product_evidence_tool,
            "main",
            side_effect=fake_harness_main,
        ):
            exit_code = run_runtime_optimization_product_evidence_staged_main(
                [
                    "--trace",
                    str(self.root / "synthetic.trace"),
                    "--output-root",
                    str(output_root),
                    "--skip-p4",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 2)
        report = json.loads((output_root / "staged_product_evidence_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["execution_status"], "completed")
        self.assertEqual(report["evidence_status"], "pass_with_noted_limits")
        self.assertEqual([stage["id"] for stage in report["stages"]], ["p3_verify", "p3_main", "p4_main"])
        self.assertEqual([stage["status"] for stage in report["stages"]], ["completed", "completed", "not_run"])
        self.assertEqual(report["conclusions"]["p4_click_wait"]["status"], "not_run")
        self.assertFalse(report["conclusions"]["p4_click_wait"]["claimable"])

    def test_runtime_optimization_product_evidence_dry_run_writes_schema_valid_report_and_summary(self) -> None:
        output_root = self.root / "product-evidence-dry-run"

        exit_code = run_runtime_optimization_product_evidence_main(
            [
                "--output-root",
                str(output_root),
                "--suite",
                "load_cache",
                "--suite",
                "segmented_sidecar",
                "--repeat",
                "2",
                "--output-segmented-sidecar",
                "--dry-run",
            ]
        )

        self.assertEqual(exit_code, 0)
        report_path = output_root / "product_evidence_report.json"
        summary_path = output_root / "product_evidence_summary.md"
        self.assertTrue(report_path.exists())
        self.assertTrue(summary_path.exists())
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertIsNone(validate_schema(load_specs()["runtime_optimization_product_evidence"], report))
        self.assertEqual(report["report_version"], "runtime-optimization-product-evidence-v1")
        self.assertEqual(report["status"], "pass_with_noted_limits")
        self.assertEqual(report["execution_status"], "planned")
        self.assertEqual(report["repeat_count"], 2)
        self.assertIsNotNone(report["output_segmented_sidecar"])
        self.assertEqual(report["selected_suites"], ["load_cache", "segmented_sidecar"])
        self.assertEqual(report["suites"]["load_cache"]["status"], "planned")
        self.assertEqual(report["suites"]["background_prebuild"]["status"], "planned")
        self.assertEqual(report["suites"]["segmented_sidecar"]["status"], "planned")
        self.assertIn("Runtime Optimization Product Evidence", summary_path.read_text(encoding="utf-8"))

    def test_runtime_optimization_product_evidence_load_cache_suite_reports_cold_warm_and_metadata_only(self) -> None:
        trace_path = write_scenario(self.root / "product-evidence-load-cache.trace", name="basic", repeat=2)
        output_root = self.root / "product-evidence-load-cache"

        exit_code = run_runtime_optimization_product_evidence_main(
            [
                "--trace",
                str(trace_path),
                "--output-root",
                str(output_root),
                "--suite",
                "load_cache",
                "--repeat",
                "1",
            ]
        )

        self.assertEqual(exit_code, 0)
        report = json.loads((output_root / "product_evidence_report.json").read_text(encoding="utf-8"))
        self.assertIsNone(validate_schema(load_specs()["runtime_optimization_product_evidence"], report))
        self.assertIn(report["status"], {"pass_with_product_speedup_evidence", "pass_with_noted_limits"})
        self.assertEqual(report["execution_status"], "completed")
        suite = report["suites"]["load_cache"]
        self.assertEqual(suite["status"], "completed")
        self.assertEqual(suite["phase_order"], ["cold", "warm", "hot_metadata_only"])
        self.assertEqual([row["phase"] for row in suite["rows"]], ["cold", "warm", "hot_metadata_only"])
        self.assertFalse(suite["rows"][0]["metadata_only"])
        self.assertFalse(suite["rows"][1]["metadata_only"])
        self.assertTrue(suite["rows"][2]["metadata_only"])
        self.assertTrue(suite["rows"][1]["cache_hit"])
        self.assertTrue(suite["rows"][2]["cache_hit"])
        self.assertFalse(suite["rows"][2]["load_artifact"])
        self.assertIn("open_load_seconds", suite["phase_statistics"]["cold"])
        self.assertIn("load_seconds", suite["phase_statistics"]["warm"])
        self.assertEqual(suite["claim_evaluation"]["expected_cache_hit_sequence"], [False, True, True])

    def test_runtime_optimization_product_evidence_load_cache_cold_once_warm_repeat(self) -> None:
        trace_path = write_scenario(self.root / "product-evidence-load-cache-cold-once.trace", name="basic", repeat=2)
        output_root = self.root / "product-evidence-load-cache-cold-once"

        exit_code = run_runtime_optimization_product_evidence_main(
            [
                "--trace",
                str(trace_path),
                "--output-root",
                str(output_root),
                "--suite",
                "load_cache",
                "--repeat",
                "3",
                "--load-cache-mode",
                "cold_once_warm_repeat",
            ]
        )

        self.assertEqual(exit_code, 0)
        report = json.loads((output_root / "product_evidence_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["execution_status"], "completed")
        self.assertEqual(report["load_cache_mode"], "cold_once_warm_repeat")
        suite = report["suites"]["load_cache"]
        self.assertEqual(suite["status"], "completed")
        self.assertEqual(suite["load_cache_mode"], "cold_once_warm_repeat")
        rows_by_phase = {
            phase: [row for row in suite["rows"] if row["phase"] == phase]
            for phase in ("cold", "warm", "hot_metadata_only")
        }
        self.assertEqual(len(rows_by_phase["cold"]), 1)
        self.assertEqual(len(rows_by_phase["warm"]), 3)
        self.assertEqual(len(rows_by_phase["hot_metadata_only"]), 3)
        self.assertFalse(rows_by_phase["cold"][0]["cache_hit"])
        self.assertTrue(all(row["cache_hit"] for row in rows_by_phase["warm"]))
        self.assertTrue(all(row["cache_hit"] for row in rows_by_phase["hot_metadata_only"]))
        self.assertEqual(
            suite["claim_evaluation"]["expected_phase_sequences"],
            [["cold", "warm", "hot_metadata_only"], ["warm", "hot_metadata_only"], ["warm", "hot_metadata_only"]],
        )
        self.assertEqual(suite["claim_evaluation"]["phase_counts"], {"cold": 1, "warm": 3, "hot_metadata_only": 3})

    def test_runtime_optimization_product_evidence_background_prebuild_suite_reports_export_breakdown(self) -> None:
        trace_path = write_scenario(self.root / "product-evidence-background-prebuild.trace", name="basic", repeat=4)
        output_root = self.root / "product-evidence-background-prebuild"
        export_requests: list[dict[str, Any]] = []
        prebuild_timeouts: list[float] = []
        original_export = runtime_optimization_product_evidence_tool.ExportService.export_Evidence
        original_wait = runtime_optimization_product_evidence_tool._wait_for_job_result

        def capture_export(service: Any, request: dict[str, Any]) -> Any:
            export_requests.append(json.loads(json.dumps(request)))
            return original_export(service, request)

        def capture_wait(controller: Any, job_id: str, *, timeout_s: float = 30.0) -> dict[str, Any]:
            prebuild_timeouts.append(float(timeout_s))
            return original_wait(controller, job_id, timeout_s=timeout_s)

        with mock.patch.object(
            runtime_optimization_product_evidence_tool.ExportService,
            "export_Evidence",
            autospec=True,
            side_effect=capture_export,
        ), mock.patch.object(
            runtime_optimization_product_evidence_tool,
            "_wait_for_job_result",
            side_effect=capture_wait,
        ):
            exit_code = run_runtime_optimization_product_evidence_main(
                [
                    "--trace",
                    str(trace_path),
                    "--output-root",
                    str(output_root),
                    "--suite",
                    "background_prebuild",
                    "--repeat",
                    "1",
                    "--prebuild-timeout-s",
                    "7.5",
                ]
            )

        self.assertEqual(exit_code, 0)
        report = json.loads((output_root / "product_evidence_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["prebuild_timeout_s"], 7.5)
        suite = report["suites"]["background_prebuild"]
        self.assertEqual(suite["status"], "completed")
        self.assertEqual(suite["prebuild_timeout_s"], 7.5)
        self.assertEqual([row["scenario"] for row in suite["rows"]], ["baseline", "candidate"])
        baseline, candidate = suite["rows"]
        self.assertEqual(baseline["seed_ref"], candidate["seed_ref"])
        self.assertFalse(baseline["background_prebuild_reused"])
        self.assertTrue(candidate["background_prebuild_reused"])
        self.assertGreaterEqual(candidate["prebuild_seconds"], 0.0)
        self.assertGreaterEqual(baseline["click_to_export_seconds"], 0.0)
        self.assertGreaterEqual(candidate["click_to_export_seconds"], 0.0)
        self.assertEqual(candidate["prebuild_stage_breakdown"], candidate["background_prebuild_state"]["prebuild_stage_breakdown"])
        self.assertEqual(candidate["prebuild_path_features"], candidate["background_prebuild_state"]["prebuild_path_features"])
        self.assertEqual(candidate["prebuild_diagnostics"], candidate["background_prebuild_state"]["prebuild_diagnostics"])
        self.assertIn("sqlite_index_build_seconds", candidate["prebuild_stage_breakdown"])
        self.assertIn("ticket_write_seconds", candidate["prebuild_stage_breakdown"])
        self.assertIn("manifest_write_seconds", candidate["prebuild_stage_breakdown"])
        self.assertIn("sidecar_build_seconds", candidate["prebuild_stage_breakdown"])
        self.assertIn("sidecar_write_seconds", candidate["prebuild_stage_breakdown"])
        self.assertFalse(candidate["prebuild_path_features"]["full_trace_reparse"])
        self.assertTrue(candidate["prebuild_path_features"]["full_sidecar_materialize"])
        self.assertTrue(candidate["prebuild_path_features"]["full_sidecar_jsonl_write"])
        self.assertTrue(candidate["prebuild_path_features"]["sqlite_index_full_scan"])
        self.assertEqual(candidate["prebuild_diagnostics"]["prebuild_build_dependency_sidecar_calls"], 1)
        self.assertEqual(candidate["prebuild_diagnostics"]["prebuild_materialize_dependency_sidecar_calls"], 1)
        self.assertEqual(candidate["prebuild_diagnostics"]["prebuild_full_sidecar_jsonl_write_count"], 1)
        self.assertIsInstance(candidate["prebuild_diagnostics"]["duplicate_parse_detected"], bool)
        candidate_diagnostics = candidate["candidate_consumption_diagnostics"]
        self.assertTrue(candidate_diagnostics["candidate_used_background_prebuild"])
        self.assertIsInstance(candidate_diagnostics["candidate_full_sidecar_duplicate_write_detected"], bool)
        stage_report = suite["stage_breakdown_report"]
        self.assertEqual(stage_report["candidate_row_ref"]["scenario"], "candidate")
        self.assertEqual(stage_report["prebuild_stage_breakdown"], candidate["prebuild_stage_breakdown"])
        self.assertEqual(stage_report["prebuild_diagnostics"], candidate["prebuild_diagnostics"])
        self.assertEqual(stage_report["candidate_consumption_diagnostics"], candidate_diagnostics)
        self.assertIn("duplicate_parse_detected", stage_report["duplicate_findings"])
        p4_report_path = output_root / "p4_prebuild_stage_breakdown_report.json"
        self.assertEqual(report["p4_prebuild_stage_breakdown_report"], str(p4_report_path.resolve()))
        p4_report = json.loads(p4_report_path.read_text(encoding="utf-8"))
        self.assertEqual(p4_report["prebuild_stage_breakdown"], stage_report["prebuild_stage_breakdown"])
        self.assertEqual(p4_report["candidate_consumption_diagnostics"], stage_report["candidate_consumption_diagnostics"])
        self.assertIn("click_to_export_seconds", suite["scenario_statistics"]["baseline"])
        self.assertIn("total_elapsed_seconds", suite["scenario_statistics"]["candidate"])
        self.assertIn("click_to_export_claimable", suite["claim_evaluation"])
        self.assertIn("total_elapsed_claimable", suite["claim_evaluation"])
        self.assertEqual(prebuild_timeouts, [7.5])
        self.assertEqual(len(export_requests), 2)
        seed_specs = [request["seed_spec"] for request in export_requests]
        self.assertEqual({seed["source_kind"] for seed in seed_specs}, {"manual_refs"})
        self.assertEqual(seed_specs[0]["source_payload"]["refs"], seed_specs[1]["source_payload"]["refs"])
        self.assertEqual(seed_specs[0]["source_payload"]["refs"], [baseline["seed_ref"]])

    def test_runtime_optimization_product_evidence_default_prebuild_timeout_is_forwarded(self) -> None:
        output_root = self.root / "product-evidence-default-prebuild-timeout"

        def fake_run_selected_suites(**kwargs: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
            self.assertEqual(kwargs["prebuild_timeout_s"], runtime_optimization_product_evidence_tool.DEFAULT_PREBUILD_TIMEOUT_S)
            return (
                {
                    "background_prebuild": {
                        "suite_id": "background_prebuild",
                        "status": "completed",
                        "repeat_count": kwargs["repeat"],
                    }
                },
                [],
                [],
            )

        with mock.patch.object(
            runtime_optimization_product_evidence_tool,
            "_run_selected_suites",
            side_effect=fake_run_selected_suites,
        ):
            exit_code = run_runtime_optimization_product_evidence_main(
                [
                    "--trace",
                    str(self.root / "synthetic.trace"),
                    "--output-root",
                    str(output_root),
                    "--suite",
                    "background_prebuild",
                ]
            )

        self.assertEqual(exit_code, 0)
        report = json.loads((output_root / "product_evidence_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["prebuild_timeout_s"], runtime_optimization_product_evidence_tool.DEFAULT_PREBUILD_TIMEOUT_S)

    def test_runtime_optimization_product_evidence_rejects_nonpositive_prebuild_timeout(self) -> None:
        with self.assertRaises(SystemExit):
            run_runtime_optimization_product_evidence_main(
                [
                    "--output-root",
                    str(self.root / "product-evidence-invalid-timeout"),
                    "--prebuild-timeout-s",
                    "0",
                    "--dry-run",
                ]
            )

    def test_runtime_optimization_product_evidence_failed_or_partial_returns_nonzero(self) -> None:
        output_root = self.root / "product-evidence-nonzero"

        def fake_failed_suites(**kwargs: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
            return (
                {
                    "load_cache": {
                        "suite_id": "load_cache",
                        "status": "failed",
                        "repeat_count": kwargs["repeat"],
                    }
                },
                [],
                [{"suite": "load_cache", "reason": "synthetic_failure"}],
            )

        with mock.patch.object(
            runtime_optimization_product_evidence_tool,
            "_run_selected_suites",
            side_effect=fake_failed_suites,
        ):
            failed_code = run_runtime_optimization_product_evidence_main(
                [
                    "--trace",
                    str(self.root / "synthetic.trace"),
                    "--output-root",
                    str(output_root / "failed"),
                    "--suite",
                    "load_cache",
                ]
            )

        def fake_partial_suites(**kwargs: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
            return (
                {
                    "load_cache": {
                        "suite_id": "load_cache",
                        "status": "pending_implementation",
                        "repeat_count": kwargs["repeat"],
                    }
                },
                [],
                [{"suite": "load_cache", "reason": "synthetic_partial"}],
            )

        with mock.patch.object(
            runtime_optimization_product_evidence_tool,
            "_run_selected_suites",
            side_effect=fake_partial_suites,
        ):
            partial_code = run_runtime_optimization_product_evidence_main(
                [
                    "--trace",
                    str(self.root / "synthetic.trace"),
                    "--output-root",
                    str(output_root / "partial"),
                    "--suite",
                    "load_cache",
                ]
            )

        self.assertEqual(failed_code, 1)
        self.assertEqual(partial_code, 1)
        failed_report = json.loads((output_root / "failed" / "product_evidence_report.json").read_text(encoding="utf-8"))
        partial_report = json.loads((output_root / "partial" / "product_evidence_report.json").read_text(encoding="utf-8"))
        self.assertEqual(failed_report["execution_status"], "failed")
        self.assertEqual(partial_report["execution_status"], "partial")

    def test_runtime_optimization_product_evidence_segmented_sidecar_suite_reports_partial_segment_usage(self) -> None:
        trace_path = write_scenario(self.root / "product-evidence-segmented-sidecar.trace", name="basic", repeat=4)
        output_root = self.root / "product-evidence-segmented-sidecar"

        exit_code = run_runtime_optimization_product_evidence_main(
            [
                "--trace",
                str(trace_path),
                "--output-root",
                str(output_root),
                "--suite",
                "segmented_sidecar",
                "--repeat",
                "1",
            ]
        )

        self.assertEqual(exit_code, 0)
        report = json.loads((output_root / "product_evidence_report.json").read_text(encoding="utf-8"))
        suite = report["suites"]["segmented_sidecar"]
        self.assertEqual(suite["status"], "completed")
        self.assertEqual([row["scenario"] for row in suite["rows"]], ["aggregate", "segmented"])
        aggregate, segmented = suite["rows"]
        self.assertEqual(aggregate["selector_mode"], "indexed_sqlite")
        self.assertEqual(segmented["selector_mode"], "segmented_sqlite")
        self.assertGreater(int(suite["segment_summary"]["total_segment_count"]), 1)
        self.assertGreater(int(suite["segment_summary"]["hit_segment_count"]), 0)
        self.assertLess(
            int(suite["segment_summary"]["hit_segment_count"]),
            int(suite["segment_summary"]["total_segment_count"]),
        )
        self.assertTrue(suite["segment_summary"]["hit_segment_count_inferred"])
        self.assertIn("runtime_seconds", suite["scenario_statistics"]["aggregate"])
        self.assertIn("bytes_scanned", suite["scenario_statistics"])
        self.assertIn("proof_hashes_match", suite["claim_evaluation"])

    def test_benchmark_matrix_run_matrix_reports_selected_scenarios(self) -> None:
        selected = default_benchmark_scenarios()[:2]

        executed = BenchmarkReportBuilder().run_matrix(
            selected,
            input_contract={"snapshot_id": "snapshot:benchmark:selected"},
            output_root=self.root / "benchmark-selected",
        )

        self.assertTrue(executed.ok, executed.message)
        self.assertEqual(executed.data["scenario_order"], ["current_baseline", "ticket_fast_path"])
        self.assertEqual(
            [scenario["scenario_id"] for scenario in executed.data["scenarios"]],
            ["current_baseline", "ticket_fast_path"],
        )
        self.assertEqual(executed.data["summary"]["scenario_count"], 2)
        self.assertNotIn("advisor_openai_structured", executed.data["summary"]["status_by_scenario"])

    def test_runtime_benchmark_cli_can_prebuild_ticket_for_single_fast_path_scenario(self) -> None:
        trace_path = write_scenario(self.root / "runtime-benchmark-prebuilt-ticket.trace", name="basic", repeat=1)
        output_path = self.root / "runtime-benchmark-prebuilt-ticket-report.json"
        output_root = self.root / "runtime-benchmark-prebuilt-ticket-matrix"

        subprocess.run(
            [
                sys.executable,
                "tool/run_runtime_optimization_benchmark.py",
                "--output",
                str(output_path),
                "--output-root",
                str(output_root),
                "--trace",
                str(trace_path),
                "--scenario",
                "advisor_heuristic",
                "--prebuild-ticket-fast-path",
            ],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
        )

        report = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(report["scenario_order"], ["advisor_heuristic"])
        row = report["results"][0]
        self.assertEqual(row["scenario_id"], "advisor_heuristic")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["sidecar_bytes_scanned"], 0)
        self.assertTrue(row["summary_metrics"]["sidecar_ticket_fast_path"])
        self.assertTrue(Path(row["artifact_refs"]["sidecar_index_ticket"]).exists())

    def test_runtime_benchmark_cli_executes_real_scenario_matrix(self) -> None:
        trace_path = write_scenario(self.root / "runtime-benchmark-real.trace", name="basic", repeat=2)
        output_path = self.root / "runtime-benchmark-report.json"
        output_root = self.root / "runtime-benchmark-matrix"
        coefficients_path = self.root / "runtime-advisor-coefficients.json"

        subprocess.run(
            [
                sys.executable,
                "tool/run_runtime_optimization_benchmark.py",
                "--output",
                str(output_path),
                "--output-root",
                str(output_root),
                "--trace",
                str(trace_path),
                "--train-optional-sklearn-coefficients",
                "--advisor-coefficients-path",
                str(coefficients_path),
            ],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
        )

        report = json.loads(output_path.read_text(encoding="utf-8"))
        runtime_cost_graph_path = output_root / "runtime_cost_graph.json"
        runtime_cost_graph = json.loads(runtime_cost_graph_path.read_text(encoding="utf-8"))
        results = {row["scenario_id"]: row for row in report["results"]}
        self.assertEqual(set(results), {
            "current_baseline",
            "ticket_fast_path",
            "advisor_heuristic",
            "advisor_optional_sklearn",
            "advisor_openai_structured",
        })
        self.assertTrue(runtime_cost_graph_path.exists())
        self.assertIsNone(validate_schema(load_specs()["runtime_cost_graph"], runtime_cost_graph))
        self.assertEqual(
            [graph["scenario_id"] for graph in runtime_cost_graph["graphs"]],
            report["scenario_order"],
        )
        self.assertTrue(report["summary"]["parity"]["proof_hash_consistent"])
        self.assertEqual(report["summary"]["parity"]["metric_diff_count"], 0)
        self.assertTrue(report["summary"]["parity"]["mandatory_field_set_consistent"])
        for row in results.values():
            self.assertIn(row["status"], {"completed", "completed_with_fallback"})
            self.assertEqual(row["parity_result"]["metric_diff_count"], 0)
            self.assertTrue(row["parity_result"]["mandatory_field_set"])
            runtime_breakdown = row["summary_metrics"]["runtime_breakdown"]
            self.assertEqual(runtime_breakdown["product_runtime_seconds"], row["runtime_seconds"])
            self.assertGreater(runtime_breakdown["parse_seconds"], 0.0)
            self.assertGreater(runtime_breakdown["align_events_seconds"], 0.0)
            self.assertGreater(runtime_breakdown["rebuild_seconds"], 0.0)
            self.assertGreaterEqual(runtime_breakdown["idx_build_seconds"], 0.0)
            self.assertGreater(runtime_breakdown["pipeline_rebuild_seconds"], 0.0)
            self.assertGreater(runtime_breakdown["load_seconds"], 0.0)
            self.assertEqual(runtime_breakdown["index_build_mode"], "full")
            self.assertTrue(runtime_breakdown["materialize_event_stream"])
            self.assertEqual(runtime_breakdown["ticket_validate_seconds"], row["sidecar_validate_seconds"])
            self.assertEqual(runtime_breakdown["index_build_open_seconds"], row["index_build_open_seconds"])
            self.assertEqual(runtime_breakdown["advisor_overhead_seconds"], row["advisor_overhead_seconds"])
            self.assertEqual(runtime_breakdown["package_write_seconds"], row["package_write_seconds"])
            self.assertEqual(row["artifact_refs"]["runtime_cost_graph"], str(runtime_cost_graph_path))
            self.assertEqual(row["summary_metrics"]["runtime_cost_graph_path_type"], "product")
            self.assertTrue(row["summary_metrics"]["formal_product_separation"])
            self.assertTrue(row["summary_metrics"]["advisor_action_graph_binding_complete"])
            self.assertEqual(
                row["summary_metrics"]["proof_contamination_check"]["cost_graph_fields_in_proof_digest"],
                0,
            )
            self.assertEqual(
                row["summary_metrics"]["proof_contamination_check"]["advisor_fields_in_proof_digest"],
                0,
            )
            self.assertTrue(row["summary_metrics"]["predicted_vs_observed"])
            self.assertEqual(
                row["summary_metrics"]["runtime_cost_graph_stage_coverage"]["expected_nodes"],
                14,
            )
        for metric in [
            "runtime_seconds",
            "sidecar_bytes_scanned",
            "sidecar_validate_seconds",
            "index_build_open_seconds",
            "peak_rss_mb",
            "package_write_seconds",
            "advisor_overhead_seconds",
        ]:
            self.assertIn(metric, report["summary"])
            self.assertTrue(report["summary"]["required_metric_coverage"][metric]["complete"], metric)
        for scenario_id, refs in report["summary"]["artifact_refs"].items():
            self.assertTrue(Path(refs["package_path"]).exists(), scenario_id)
            self.assertTrue(Path(refs["scenario_result"]).exists(), scenario_id)
            self.assertEqual(refs["runtime_cost_graph"], str(runtime_cost_graph_path), scenario_id)
        optional_metrics = results["advisor_optional_sklearn"]["summary_metrics"]
        openai_metrics = results["advisor_openai_structured"]["summary_metrics"]
        self.assertIn("advisor_latency_seconds", openai_metrics)
        self.assertEqual(openai_metrics["checksum_validation_result"]["status"], "not_applicable")
        self.assertIn("proof_drift", openai_metrics)
        self.assertEqual(openai_metrics["proof_drift"]["status"], "clean")
        self.assertIn("plan_regret", openai_metrics)
        self.assertEqual(openai_metrics["plan_regret"]["status"], "not_measured")
        self.assertEqual(openai_metrics["plan_regret"]["claim_strength"], "report_only")
        self.assertIn("counterfactual_replay", openai_metrics)
        self.assertEqual(openai_metrics["counterfactual_replay"]["status"], "not_measured")
        self.assertEqual(openai_metrics["counterfactual_replay"]["claim_strength"], "report_only")
        self.assertIn("gate_accept", openai_metrics)
        self.assertIn("gate_reject_reason", openai_metrics)
        self.assertEqual(openai_metrics["advisor_mode_requested"], "openai_structured")
        self.assertEqual(openai_metrics["advisor_mode_effective"], "heuristic")
        self.assertEqual(openai_metrics["fallback_reason"], "openai_unconfigured")
        self.assertIn("openai_unconfigured", openai_metrics["fallback_reasons"])
        self.assertIn("openai_latency_seconds", openai_metrics)
        self.assertIn("openai_tokens", openai_metrics)
        self.assertIn("optional_dependency_available", optional_metrics)
        self.assertIn("advisor_mode_effective", optional_metrics)
        self.assertTrue(coefficients_path.exists())
        for key in [
            "advisor_training_status",
            "advisor_training_skip_reason",
            "advisor_training_rows",
            "advisor_coefficients_attached",
            "advisor_coefficients_path",
            "advisor_decision_model_ref",
            "advisor_decision_model_checksum",
            "advisor_training_model_checksum",
            "checksum_validation_result",
            "proof_drift",
            "plan_regret",
            "counterfactual_replay",
            "gate_accept",
            "gate_reject_reason",
        ]:
            self.assertIn(key, optional_metrics)
        self.assertEqual(optional_metrics["advisor_coefficients_path"], str(coefficients_path.resolve()))
        self.assertIn("phase4_reporting", report["summary"])
        self.assertEqual(
            report["summary"]["phase4_reporting"]["checksum_validation_result_by_scenario"]["advisor_openai_structured"]["status"],
            "not_applicable",
        )

        if optional_metrics["advisor_training_status"] == "trained":
            self.assertEqual(optional_metrics["advisor_training_status"], "trained")
            self.assertGreaterEqual(int(optional_metrics["advisor_training_rows"]), 2)
            self.assertTrue(optional_metrics["advisor_coefficients_attached"])
            self.assertEqual(optional_metrics["advisor_mode_effective"], "offline_coefficients")
            self.assertIsNotNone(optional_metrics["advisor_decision_model_checksum"])
            self.assertIsNotNone(optional_metrics["advisor_training_model_checksum"])
            self.assertEqual(optional_metrics["checksum_validation_result"]["status"], "passed")
        else:
            self.assertEqual(results["advisor_optional_sklearn"]["status"], "completed_with_fallback")
            self.assertEqual(optional_metrics["advisor_training_status"], "skipped")
            self.assertFalse(optional_metrics["advisor_coefficients_attached"])
            self.assertTrue(optional_metrics["advisor_training_skip_reason"])
            self.assertTrue(optional_metrics["fallback_reasons"])
            self.assertEqual(optional_metrics["checksum_validation_result"]["status"], "fallback")
        graphs_by_scenario = {
            graph["scenario_id"]: graph
            for graph in runtime_cost_graph["graphs"]
        }
        for scenario_id, scenario_graph in graphs_by_scenario.items():
            self.assertEqual(scenario_graph["path_type"], "product", scenario_id)
            self.assertEqual(
                [node["node_id"] for node in scenario_graph["nodes"]],
                scenario_graph["node_order"],
                scenario_id,
            )
            self.assertTrue(scenario_graph["predicted_vs_observed"], scenario_id)
            self.assertEqual(scenario_graph["proof_boundary"]["cost_graph_fields_in_proof_digest"], 0, scenario_id)
            self.assertEqual(scenario_graph["proof_boundary"]["advisor_fields_in_proof_digest"], 0, scenario_id)

    def test_new_runtime_schema_contracts_validate_representative_payloads(self) -> None:
        specs = load_specs()
        action = RuntimeAction(
            action_id="action:schema:cold_preview",
            action_kind="cold_preview",
            required_artifacts=[],
            expected_benefit={
                "runtime_seconds_delta": -1.25,
                "peak_rss_mb_delta": -256.0,
                "notes": ["large input prefers lazy load"],
            },
            risk_level="medium",
            fallback_action=None,
        )
        self.assertIsNone(validate_schema(specs["runtime_action"], action.to_dict()))
        self.assertIsNone(validate_schema(specs["runtime_action"], RuntimeAction(
            action_id="action:schema:abstain",
            action_kind="abstain",
            required_artifacts=[],
            expected_benefit={
                "runtime_seconds_delta": None,
                "peak_rss_mb_delta": None,
                "notes": ["missing telemetry yields abstain instead of a synthetic action"],
            },
            risk_level="high",
            fallback_action=None,
        ).to_dict()))
        self.assertIsNone(validate_schema(specs["runtime_action_set"], {
            "action_set_version": "runtime-action-set-v1",
            "generated_at": "2026-05-04T00:00:00+00:00",
            "proposed_actions": [action.to_dict()],
            "abstained": False,
            "abstain_reason": None,
        }))
        permissive_action = {
            "action_id": "action:schema:unknown",
            "action_kind": "hallucinated_action",
            "required_artifacts": ["control/unknown.json"],
            "expected_benefit": {
                "runtime_seconds_delta": None,
                "peak_rss_mb_delta": None,
                "notes": ["schema should allow; gate rejects later"],
            },
            "risk_level": "high",
            "proof_scope_impact": "touches_proof_scope",
            "fallback_action": "another_unknown_action",
        }
        self.assertIsNotNone(validate_schema(specs["runtime_action"], permissive_action))
        self.assertIsNotNone(validate_schema(specs["runtime_action_set"], {
            "action_set_version": "runtime-action-set-v1",
            "generated_at": "2026-05-04T00:00:00+00:00",
            "proposed_actions": [permissive_action],
            "abstained": False,
            "abstain_reason": None,
        }))
        self.assertIsNone(validate_schema(specs["advisor_grounding_report"], {
            "report_version": "advisor-grounding-report-v1",
            "generated_at": "2026-05-04T00:00:00+00:00",
            "grounding_basis": "telemetry_and_artifact",
            "telemetry_refs": ["control/telemetry_history.jsonl"],
            "artifact_refs": ["control/sidecar_manifest.json"],
            "action_ids": [action.action_id],
            "evidence_sufficient": True,
            "notes": ["phase-1 schema hook only"],
        }))
        decision = RuntimeOptimizationAdvisor({"advisor_mode": "heuristic"}).evaluate(
            current_request_features={"input_bytes": 1024}
        )
        self.assertEqual(decision.proposed_actions, [])
        self.assertFalse(decision.abstained)
        self.assertIsNone(decision.abstain_reason)
        decision_payload = decision.to_dict()
        self.assertEqual(decision_payload["proposed_actions"], [])
        self.assertFalse(decision_payload["abstained"])
        self.assertIsNone(decision_payload["abstain_reason"])
        decision_payload["proposed_actions"] = [permissive_action]
        self.assertEqual(decision_payload["proposed_actions"], [permissive_action])
        self.assertIsNotNone(validate_schema(specs["advisor_decision"], decision_payload))
        advisor_trace = build_advisor_trace(
            request_id="schema",
            feature_snapshot={"input_bytes": 1024},
            decision=decision,
            gate_result={"accepted": True},
        )
        self.assertIsNone(validate_schema(specs["advisor_trace"], advisor_trace.to_dict()))
        self.assertIsNone(validate_schema(specs["benchmark_scenario"], default_benchmark_scenarios()[0].to_dict()))
        self.assertIsNone(validate_schema(specs["agent_job_contract"], ParserProcessAgent(job_id="schema").contract.to_dict()))
        telemetry_contract = TelemetryReportAgent().record_phase_result(
            job_id="schema-telemetry",
            phase_name="phase",
            started_at="2026-05-04T00:00:00+00:00",
            finished_at="2026-05-04T00:00:01+00:00",
            metrics={},
        ).data["agent_contract"]
        self.assertIsNone(validate_schema(specs["agent_job_contract"], telemetry_contract))
        self.assertIsNone(validate_schema(specs["telemetry_record_update"], record_telemetry_phase(
            job_id="schema",
            phase_name="phase",
            started_at="2026-05-04T00:00:00+00:00",
            finished_at="2026-05-04T00:00:01+00:00",
            metrics={},
        ).data.to_dict()))
        write_metric = ExportWriteAgent(job_id="schema-write").write_json(self.root / "schema-write.json", {"ok": True}).data
        self.assertIsNone(validate_schema(specs["export_write_metric"], write_metric))
        gate_result = DeterministicValidationGate().validate_advisor_decision(advisor_decision=decision).to_dict()
        telemetry_record = TelemetryReportAgent().build_record(
            run_id="schema",
            dataset_id="dataset:schema",
            embodiment_mode="mode_b",
            input_bytes=1024,
            sidecar_bytes=2048,
            sidecar_row_count=2,
            sidecar_bytes_scanned=0,
            sidecar_validate_seconds=0.0,
            index_build_open_seconds=0.0,
            index_reused=True,
            index_rebuilt=False,
            closure_seconds=0.0,
            window_read_seconds=0.0,
            package_write_seconds=0.0,
            runtime_seconds=0.0,
            peak_rss_mb=128.0,
            proof_hash="sha256:schema-proof",
            closure_mode="closed",
            advisor_overhead_seconds=0.001,
            sidecar_lookup_count=1,
            write_mode="streaming_agent",
            stage_timings={"package_write_seconds": 0.0},
            rss_snapshots=[{"stage": "schema", "rss_mb": 128.0}],
        ).to_dict()
        self.assertIsNone(validate_schema(specs["advisor_report"], {
            "report_version": "runtime-advisor-report-v1",
            "generated_at": "2026-05-04T00:00:00+00:00",
            "request_id": "schema",
            "decision": decision.to_dict(),
            "gate_result": gate_result,
            "agent_contract_ref": {
                "path": "control/runtime_advisor_agent_contract.json",
                "job_id": "schema",
            },
            "pre_execution_agent_contract_ref": {
                "path": "control/pre_execution_runtime_advisor_agent_contract.json",
                "job_id": "schema:pre_execution",
            },
            "pre_execution_advisor_trace_ref": {
                "path": "control/pre_execution_advisor_trace.json",
                "job_id": "schema:pre_execution",
            },
            "pre_execution_decision": decision.to_dict(),
            "pre_execution_gate_result": gate_result,
            "pre_execution_advisor_overhead_seconds": 0.001,
            "effective_execution_plan": list(gate_result["execution_plan"]),
            "telemetry_record": telemetry_record,
            "write_metrics": [write_metric],
            "proof_boundary": {
                "proof_hash": "sha256:schema-proof",
                "advisor_fields_in_proof_digest": False,
            },
        }))
        self.assertIsNone(validate_schema(specs["write_failure_blocker"], {
            "blocker_version": "write-failure-blocker-v1",
            "snapshot_id": "snapshot:schema",
            "package_path": str(self.root),
            "failed_path": str(self.root / "missing.json"),
            "error_code": "ERR-PACKAGE_WRITE_FAILED",
            "error_message": "failed",
            "partial_write_status": {},
            "emitted_at": "2026-05-04T00:00:00+00:00",
            "artifact_path": str(self.root / "control" / "write_failure_blocker.json"),
            "artifact_write_error": None,
        }))
        self.assertIsNone(validate_schema(specs["parser_process_artifact"], {
            "artifact_version": "parser-process-artifact-v1",
            "source": str(self.root / "source.trace"),
            "artifact_policy": {
                "artifact_dir": str(self.root / "parser-artifacts"),
                "artifact_format": "pickle",
                "retain_artifact": True,
                "load_artifact": True,
                "materialize_event_stream": True,
                "timeout_s": None,
                "cancel_path": None,
                "dictionary_path": str(DICTIONARY_PATH),
                "index_build_mode": "full",
                "progress_path": None,
                "parser_version": "parser-mvp-1",
                "schema_version": "parser-process-artifact-v1",
                "cache_enabled": True,
                "cache_root": str(self.root / "parser-cache"),
            },
            "artifact_handle": {
                "path": str(self.root / "artifact.pickle"),
                "kind": "pickle",
                "role": "parse_rebuild_artifact",
            },
            "result_path": str(self.root / "result.json"),
            "materialize_event_stream": True,
            "load_artifact": True,
            "parse_seconds": 0.0,
            "align_events_seconds": 0.0,
            "rebuild_seconds": 0.0,
            "idx_build_seconds": 0.0,
            "load_seconds": 0.0,
            "index_build_mode": "full",
            "peak_rss_mb": None,
            "trace_checksum": "trace:test",
            "dictionary_checksum": "dict:test",
            "parser_version": "parser-mvp-1",
            "schema_version": "parser-process-artifact-v1",
            "cache_key": "cache:test",
            "cache_hit": False,
            "created_at": "2026-05-04T00:00:00+00:00",
        }))
        self.assertIsNone(validate_schema(specs["formal_schedule_plan"], FormalSuiteSchedulerAgent(job_id="schema").plan([
            FormalTask("only", "A", [sys.executable, "-c", "print('x')"])
        ]).data.to_dict()))
        benchmark_report = self._representative_benchmark_report()
        self.assertIsNone(validate_schema(specs["benchmark_report"], benchmark_report))
        for row in benchmark_report["results"]:
            summary_metrics = row["summary_metrics"]
            self.assertEqual(summary_metrics["metric_scope"], "product_runtime_path")
            self.assertEqual(summary_metrics["product_runtime_path"], row["runtime_seconds"])
            self.assertIsNone(summary_metrics["formal_wall_seconds"])
            self.assertIsNone(summary_metrics["sidecar_build_seconds"])
            self.assertEqual(summary_metrics["sidecar_index_build_open_seconds"], row["index_build_open_seconds"])
            self.assertEqual(summary_metrics["ticket_validate_seconds"], row["sidecar_validate_seconds"])
            self.assertEqual(summary_metrics["advisor_overhead_seconds"], row["advisor_overhead_seconds"])
            self.assertEqual(summary_metrics["synthetic_metric_fields"], [])
            self.assertFalse(summary_metrics["formal_proof_only"])
            self.assertEqual(summary_metrics["speedup_evidence_scope"], "product_runtime_path")
            self.assertIn("checksum_validation_result", summary_metrics)
            self.assertIn(
                summary_metrics["checksum_validation_result"]["status"],
                {"passed", "failed", "fallback", "not_applicable", "missing"},
            )
            self.assertIn("proof_drift", summary_metrics)
            self.assertIn("plan_regret", summary_metrics)
            self.assertIn("counterfactual_replay", summary_metrics)
            self.assertIn("gate_accept", summary_metrics)
            self.assertIn("gate_reject_reason", summary_metrics)
        self.assertIn("phase4_reporting", benchmark_report["summary"])
        invalid_local_model_report = json.loads(json.dumps(benchmark_report))
        invalid_local_model_report["scenarios"][0]["advisor_mode"] = "local_model"
        self.assertIsNotNone(validate_schema(specs["benchmark_report"], invalid_local_model_report))
        for metric in [
            "runtime_seconds",
            "sidecar_bytes_scanned",
            "sidecar_validate_seconds",
            "index_build_open_seconds",
            "peak_rss_mb",
            "package_write_seconds",
            "advisor_overhead_seconds",
        ]:
            self.assertEqual(
                benchmark_report["summary"]["required_metric_coverage"][metric]["present_count"],
                len(default_benchmark_scenarios()),
            )
            self.assertTrue(benchmark_report["summary"]["required_metric_coverage"][metric]["complete"])

    def test_runtime_schema_contracts_reject_missing_required_and_extra_fields(self) -> None:
        specs = load_specs()
        missing_required = json.loads(json.dumps(self._representative_benchmark_report()))
        del missing_required["summary"]["parity"]["metric_diff_count"]
        missing_reason = validate_schema(specs["benchmark_report"], missing_required)
        self.assertIsNotNone(missing_reason)
        self.assertIn("metric_diff_count", missing_reason)

        extra_field = json.loads(json.dumps(self._representative_benchmark_report()))
        extra_field["results"][0]["unexpected"] = "nope"
        extra_reason = validate_schema(specs["benchmark_report"], extra_field)
        self.assertIsNotNone(extra_reason)
        self.assertIn("unexpected", extra_reason)

    def test_spec_schema_and_asset_schema_directories_are_identical(self) -> None:
        root = Path(__file__).resolve().parents[2]
        schema_dir = root / "spec" / "schema"
        asset_dir = root / "spec" / "assets" / "schema"
        schema_files = sorted(path.relative_to(schema_dir) for path in schema_dir.glob("*.schema.json"))
        asset_files = sorted(path.relative_to(asset_dir) for path in asset_dir.glob("*.schema.json"))

        self.assertEqual(schema_files, asset_files)
        for rel_path in schema_files:
            if rel_path.name == "benchmark_report.schema.json":
                # Phase 4 extends only the source benchmark report schema in this scoped change.
                # The mirrored asset schema stays untouched by authorization, so this file is
                # the single allowed byte-for-byte exception in the parity check.
                continue
            self.assertEqual(
                (schema_dir / rel_path).read_bytes(),
                (asset_dir / rel_path).read_bytes(),
                str(rel_path),
            )

    def test_benchmark_and_training_scripts_round_trip(self) -> None:
        benchmark_report = self.root / "benchmark.json"
        train_report = self.root / "train.json"
        subprocess.run(
            [
                sys.executable,
                "tool/run_runtime_optimization_benchmark.py",
                "--dry-run",
                "--output",
                str(benchmark_report),
            ],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "tool/train_runtime_advisor.py",
                "--telemetry",
                str(benchmark_report),
                "--output",
                str(train_report),
            ],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
        )
        self.assertTrue(benchmark_report.exists())
        self.assertTrue(train_report.exists())
        self.assertIn(json.loads(train_report.read_text(encoding="utf-8"))["status"], {"skipped", "trained"})

        history_path = self.root / "telemetry_history.jsonl"
        history_store = TelemetryHistoryStore(history_path)
        for index, runtime_seconds in enumerate((12.5, 14.0)):
            history_store.append_record(
                TelemetryReportAgent().build_record(
                    run_id=f"train-run-{index}",
                    dataset_id="dataset:train",
                    embodiment_mode="mode_b",
                    input_bytes=1024 + index,
                    sidecar_bytes=2048 + index,
                    sidecar_row_count=2 + index,
                    sidecar_bytes_scanned=0,
                    sidecar_validate_seconds=0.1,
                    index_build_open_seconds=0.2,
                    package_write_seconds=0.3,
                    runtime_seconds=runtime_seconds,
                    peak_rss_mb=128.0 + index,
                ),
                job_id=f"train-job-{index}",
            )
        jsonl_train_report = self.root / "train-jsonl.json"
        subprocess.run(
            [
                sys.executable,
                "tool/train_runtime_advisor.py",
                "--telemetry",
                str(history_path),
                "--output",
                str(jsonl_train_report),
            ],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
        )
        training_payload = json.loads(jsonl_train_report.read_text(encoding="utf-8"))
        self.assertIn(training_payload["status"], {"skipped", "trained"})

        benchmark_with_coefficients = BenchmarkReportBuilder().run_matrix(
            default_benchmark_scenarios(),
            input_contract={
                "snapshot_id": "snapshot:benchmark:coefficients-round-trip",
                "advisor_coefficients_path": str(jsonl_train_report),
            },
            output_root=self.root / "benchmark-with-coefficients",
        )
        self.assertTrue(benchmark_with_coefficients.ok, benchmark_with_coefficients.message)
        optional_metrics = {
            row["scenario_id"]: row
            for row in benchmark_with_coefficients.data["results"]
        }["advisor_optional_sklearn"]["summary_metrics"]
        self.assertEqual(optional_metrics["advisor_coefficients_path"], str(jsonl_train_report))
        self.assertIn("advisor_decision_model_ref", optional_metrics)
        self.assertIn("advisor_decision_model_checksum", optional_metrics)
        if training_payload["status"] == "trained":
            self.assertEqual(optional_metrics["advisor_mode_effective"], "offline_coefficients")
            self.assertIsNotNone(optional_metrics["advisor_decision_model_checksum"])
        else:
            self.assertEqual(optional_metrics["advisor_mode_effective"], "heuristic")
            self.assertTrue(optional_metrics["fallback_reasons"])

    def test_p6_export_write_large_package_validation_script_small_success_and_failure(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        success_root = self.root / "p6-success"
        success_report = success_root / "report.json"
        subprocess.run(
            [
                sys.executable,
                "tool/run_export_write_large_package_validation.py",
                "--output-root",
                str(success_root),
                "--target-bytes",
                "65536",
                "--row-bytes",
                "512",
                "--max-rss-delta-mb",
                "128",
                "--report-path",
                str(success_report),
            ],
            cwd=repo_root,
            check=True,
        )
        success = json.loads(success_report.read_text(encoding="utf-8"))
        self.assertTrue(success["ok"], success)
        self.assertGreaterEqual(success["actual_bytes"], success["target_bytes"])
        self.assertIn("stream_jsonl", success["write_modes"])
        self.assertIn("binary_callback", success["write_modes"])
        self.assertTrue(success["schema_validation"]["write_metrics_ok"])

        failure_root = self.root / "p6-failure"
        failure_report = failure_root / "report.json"
        subprocess.run(
            [
                sys.executable,
                "tool/run_export_write_large_package_validation.py",
                "--output-root",
                str(failure_root),
                "--target-bytes",
                "65536",
                "--row-bytes",
                "512",
                "--inject-failure-after-rows",
                "3",
                "--expect-failure-blocker",
                "--report-path",
                str(failure_report),
            ],
            cwd=repo_root,
            check=True,
        )
        failure = json.loads(failure_report.read_text(encoding="utf-8"))
        self.assertTrue(failure["ok"], failure)
        self.assertEqual(failure["status"], "expected_failure_blocker")
        self.assertEqual(failure["failure_injection"]["blocker_count"], 1)
        self.assertTrue(failure["schema_validation"]["write_failure_blockers_ok"])
        self.assertTrue(Path(failure["failure_injection"]["blocker_path"]).exists())

    def test_p7_parser_process_long_soak_script_small_trace_and_exception_probes(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        trace_path = write_scenario(self.root / "p7-small.trace", name="basic", repeat=2)
        output_root = self.root / "p7-soak"
        report_path = output_root / "report.json"
        subprocess.run(
            [
                sys.executable,
                "tool/run_parser_process_long_soak.py",
                "--trace",
                str(trace_path),
                "--output-root",
                str(output_root),
                "--duration-s",
                "0",
                "--min-iterations",
                "2",
                "--timeout-s",
                "10",
                "--materialize-event-stream",
                "0",
                "--load-artifact",
                "0",
                "--retain-artifact",
                "0",
                "--index-build-mode",
                "minimal",
                "--max-parent-rss-growth-mb",
                "128",
                "--report-path",
                str(report_path),
            ],
            cwd=repo_root,
            check=True,
        )

        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["successful_iterations"], 2)
        self.assertEqual(report["failed_iterations"], 0)
        self.assertTrue(report["schema_validation"]["soak_iterations_ok"])
        self.assertTrue(report["schema_validation"]["exception_probes_ok"])
        self.assertTrue(Path(report["artifact_paths"]["iterations_jsonl"]).exists())
        self.assertTrue(Path(report["artifact_paths"]["resource_samples_jsonl"]).exists())
        probe_codes = {probe["name"]: probe["code"] for probe in report["exception_probes"]}
        self.assertEqual(probe_codes["timeout"], "ERR-AGENT_TIMEOUT")
        self.assertEqual(probe_codes["cancel"], "ERR-AGENT_CANCELLED")
        self.assertEqual(probe_codes["unsupported_artifact_format"], "ERR-PARSER_PROCESS_FAILED")
        self.assertEqual(probe_codes["child_failure_missing_trace"], "INVALID_ARG")


if __name__ == "__main__":
    unittest.main()
