from __future__ import annotations

from dataclasses import asdict
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from parser.evidence_models import DependencySidecarEdge
from parser.evidence_sidecar_index import build_or_open_sidecar_index, sidecar_index_ticket_path_for_source
from parser.openai_advisor_client import sanitize_advisor_features
from parser.advisor_retrieval import retrieve_runtime_cases
from parser.openai_advisor_client import (
    LLMAdvisorClient,
    LLMAdvisorClientError,
    OpenAIAdvisorClient,
    OpenAIAdvisorClientResult,
)
from parser.runtime_advisor import (
    ADVISOR_MODES,
    AdvisorDecision,
    RuntimeLoadPlan,
    RuntimeOptimizationAdvisor,
    advisor_Evaluate,
    build_advisor_trace,
)
from parser.runtime_optimization_gate import (
    DeterministicValidationGate,
    ValidationGateResult,
    gate_ValidateAdvisorDecision,
    gate_ValidateRuntimeLoadPlan,
    gate_ValidateTicketFastPath,
)
from parser.telemetry import TelemetryReportAgent
from spec.schema_loader import load_schema
from spec.io import checksum_file
from spec.schema_validator import validate_schema


class RuntimeOptimizationAdvisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _sidecar_row(self) -> dict[str, object]:
        edge = DependencySidecarEdge(
            snapshot_id="snapshot:test:advisor",
            trace_checksum="trace:test",
            src_ref="evt:src",
            dst_ref="evt:dst",
            src_kind="ref",
            dst_kind="ref",
            relation_kind="ref_index_next",
            rule_family="ref_ref",
            provenance="unit_test",
            priority=10,
            time_hint_begin_ns=1,
            time_hint_end_ns=2,
            core_hint=0,
            seq_hint_begin=1,
            seq_hint_end=2,
            segment_hint="core:0",
            cycle_guard_token="token:test",
            estimate_events=1,
            estimate_bytes=16,
            edge_hash="0" * 64,
        )
        return asdict(edge)

    def _prepare_sidecar(self) -> tuple[Path, dict[str, object], str]:
        sidecar_path = self.root / "sidecar.jsonl"
        sidecar_path.write_text(json.dumps(self._sidecar_row(), ensure_ascii=False) + "\n", encoding="utf-8")
        sidecar_checksum = checksum_file(sidecar_path)
        manifest = {
            "snapshot_id": "snapshot:test:advisor",
            "trace_checksum": "trace:test",
            "dictionary_checksum": "dict:test",
            "entry_paths": ["control/dependency_sidecar.jsonl"],
            "entry_checksums": {"control/dependency_sidecar.jsonl": sidecar_checksum},
        }
        return sidecar_path, manifest, sidecar_checksum

    def _write_case_bank(self, cases: list[dict[str, object]]) -> Path:
        root = self.root / "case-bank"
        root.mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "case_bank_version": "runtime-optimization-case-bank-v1",
                    "case_files": ["recommendation_cases.json"],
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        (root / "recommendation_cases.json").write_text(
            json.dumps(cases, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return root

    def _case_entry(
        self,
        *,
        case_id: str,
        label: str,
        action_kind: str,
        risk_level: str = "high",
        similarity_features: dict[str, object] | None = None,
        reject_taxonomy: str | None = None,
    ) -> dict[str, object]:
        return {
            "case_id": case_id,
            "label": label,
            "action_kind": action_kind,
            "risk_level": risk_level,
            "source_ref": "tests:unit-case-bank",
            "case_tags": ["unit_test"],
            "similarity_features": {
                "input_bytes_bucket": "small",
                "sidecar_bytes_bucket": "large",
                "sidecar_row_count_bucket": "medium",
                "peak_rss_bucket": "high",
                "ticket_present": False,
                "ticket_validated": False,
                "advisor_phase": "",
                "export_family": "",
                "embodiment_mode": "",
                "platform": "",
                "missing_telemetry_fields": [],
                **dict(similarity_features or {}),
            },
            "expected_decision": {
                "proposed_actions": [action_kind] if action_kind else [],
                "abstained": label in {"abstain", "unsafe", "needs_more_data", "reject"},
                "abstain_reason": None,
            },
            "gate_outcome": {
                "accepted": label not in {"reject", "unsafe"},
                "rejected_reason": reject_taxonomy,
            },
            "reject_taxonomy": reject_taxonomy,
        }

    def _repo_case_bank_root(self) -> Path:
        return Path(__file__).resolve().parents[2] / "docs" / "runtime_optimization_case_bank"

    def _repo_case_bank_cases(self) -> list[dict[str, object]]:
        return json.loads((self._repo_case_bank_root() / "recommendation_cases.json").read_text(encoding="utf-8"))

    def _materialize_case_features(
        self,
        similarity_features: dict[str, object],
    ) -> tuple[dict[str, object], list[dict[str, object]], dict[str, object] | None]:
        input_bytes = {
            "tiny": 256 * 1024,
            "small": 8 * 1024 * 1024,
            "medium": 128 * 1024 * 1024,
            "large": 768 * 1024 * 1024,
            "xlarge": 2 * 1024 * 1024 * 1024,
            "missing": None,
        }[str(similarity_features.get("input_bytes_bucket") or "small")]
        sidecar_bytes = {
            "tiny": 256 * 1024,
            "small": 8 * 1024 * 1024,
            "large": 768 * 1024 * 1024,
            "xlarge": 8 * 1024 * 1024 * 1024,
            "huge": 24 * 1024 * 1024 * 1024,
            "missing": None,
        }[str(similarity_features.get("sidecar_bytes_bucket") or "small")]
        sidecar_row_count = {
            "tiny": 4,
            "small": 128,
            "medium": 2000,
            "large": 400000,
            "xlarge": 8000000,
            "missing": None,
        }[str(similarity_features.get("sidecar_row_count_bucket") or "small")]
        peak_rss_mb = {
            "low": 256.0,
            "medium": 1024.0,
            "high": 3072.0,
            "critical": 5120.0,
            "missing": None,
        }[str(similarity_features.get("peak_rss_bucket") or "medium")]
        missing = set(str(item) for item in list(similarity_features.get("missing_telemetry_fields") or []))
        features: dict[str, object] = {
            "advisor_phase": str(similarity_features.get("advisor_phase") or ""),
            "export_family": str(similarity_features.get("export_family") or ""),
            "embodiment_mode": str(similarity_features.get("embodiment_mode") or ""),
            "ticket_present": bool(similarity_features.get("ticket_present")),
            "ticket_validated": bool(similarity_features.get("ticket_validated")),
        }
        if input_bytes is not None:
            features["input_bytes"] = input_bytes
        if sidecar_bytes is not None:
            features["sidecar_bytes"] = sidecar_bytes
        if sidecar_row_count is not None:
            features["sidecar_row_count"] = sidecar_row_count
        if peak_rss_mb is not None and "peak_rss_mb" not in missing:
            features["peak_rss_mb"] = peak_rss_mb
        if "runtime_seconds" not in missing:
            features["runtime_seconds"] = 12.5
        telemetry_history: list[dict[str, object]] = []
        if "telemetry_history" not in missing:
            telemetry_row = {
                "runtime_seconds": 12.5,
                "peak_rss_mb": peak_rss_mb if peak_rss_mb is not None else 1024.0,
                "input_bytes": input_bytes if input_bytes is not None else 8 * 1024 * 1024,
                "sidecar_bytes": sidecar_bytes if sidecar_bytes is not None else 8 * 1024 * 1024,
                "sidecar_row_count": sidecar_row_count if sidecar_row_count is not None else 128,
                "export_family": str(similarity_features.get("export_family") or ""),
                "embodiment_mode": str(similarity_features.get("embodiment_mode") or ""),
            }
            telemetry_history = [telemetry_row]
        sidecar_ticket = None
        if bool(similarity_features.get("ticket_present")):
            sidecar_ticket = {
                "row_count": sidecar_row_count if sidecar_row_count is not None else 1,
                "sidecar_bytes": sidecar_bytes if sidecar_bytes is not None else 8 * 1024 * 1024,
            }
        return features, telemetry_history, sidecar_ticket

    def _action_kinds(self, decision: AdvisorDecision) -> list[str]:
        return [action.action_kind for action in decision.proposed_actions]

    def _typed_action_set_payload(
        self,
        *,
        actions: list[dict[str, object]] | None = None,
        abstained: bool = False,
        abstain_reason: str | None = None,
    ) -> dict[str, object]:
        return {
            "action_set_version": "runtime-action-set-v1",
            "generated_at": "2026-07-06T00:00:00+00:00",
            "proposed_actions": list(actions or []),
            "abstained": abstained,
            "abstain_reason": abstain_reason,
        }

    def _runtime_action_payload(self, action_kind: str, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "action_id": f"action:test:{action_kind}",
            "action_kind": action_kind,
            "required_artifacts": ["sidecar_index_ticket"] if action_kind == "sidecar_index_reuse" else [],
            "expected_benefit": {
                "runtime_seconds_delta": None,
                "peak_rss_mb_delta": None,
                "notes": [f"unit test action: {action_kind}"],
            },
            "risk_level": "medium",
            "proof_scope_impact": "none",
            "fallback_action": None,
        }
        payload.update(overrides)
        return payload

    def _advisor_decision_payload(
        self,
        *,
        actions: list[dict[str, object]] | None = None,
        recommend_index_prebuild: bool | None = None,
        recommend_index_reuse_attempt: bool | None = None,
        recommend_streaming_write: bool | None = None,
        recommended_schedule: str | None = None,
        abstained: bool = False,
        abstain_reason: str | None = None,
    ) -> dict[str, object]:
        action_payloads = list(actions or [])
        action_kinds = {str(item.get("action_kind") or "") for item in action_payloads}
        prebuild = (
            bool(recommend_index_prebuild)
            if recommend_index_prebuild is not None
            else "sidecar_index_prebuild" in action_kinds
        )
        reuse = (
            bool(recommend_index_reuse_attempt)
            if recommend_index_reuse_attempt is not None
            else "sidecar_index_reuse" in action_kinds
        )
        streaming = (
            bool(recommend_streaming_write)
            if recommend_streaming_write is not None
            else "streaming_package_write" in action_kinds
        )
        schedule = recommended_schedule
        if schedule is None:
            schedule = "sidecar_first" if prebuild or reuse else "serial_safe"
        return {
            "decision_version": "runtime-advisor-decision-v1",
            "advisor_mode": "openai_structured",
            "model_ref": "gpt-test",
            "model_checksum": None,
            "feature_snapshot_hash": "sha256:test-feature",
            "recommend_index_prebuild": prebuild,
            "recommend_index_reuse_attempt": reuse,
            "recommend_streaming_write": streaming,
            "recommended_schedule": schedule,
            "predicted_runtime_seconds": 1.0,
            "predicted_peak_rss_mb": 128.0,
            "risk_level": "medium",
            "reasons": ["unit test gate payload"],
            "proposed_actions": action_payloads,
            "abstained": abstained,
            "abstain_reason": abstain_reason,
        }

    def test_heuristic_advisor_prefers_ticket_fast_path_for_large_sidecar(self) -> None:
        advisor = RuntimeOptimizationAdvisor({"advisor_mode": "heuristic"})
        decision = advisor.evaluate(
            telemetry_history=[{"input_bytes": 512 * 1024 * 1024, "peak_rss_mb": 3072.0}],
            current_request_features={"input_bytes": 512 * 1024 * 1024, "sidecar_bytes": 512 * 1024 * 1024},
            sidecar_ticket={"row_count": 1000, "sidecar_bytes": 512 * 1024 * 1024},
        )
        self.assertTrue(decision.recommend_index_reuse_attempt)
        self.assertTrue(decision.recommend_streaming_write)
        self.assertEqual(decision.recommended_schedule, "sidecar_first")
        self.assertEqual(self._action_kinds(decision), ["sidecar_index_reuse", "streaming_package_write"])
        self.assertFalse(decision.abstained)
        self.assertIsNone(decision.abstain_reason)

    def test_heuristic_advisor_emits_cold_preview_and_prebuild_for_large_input_without_ticket(self) -> None:
        decision = RuntimeOptimizationAdvisor({"advisor_mode": "heuristic"}).evaluate(
            current_request_features={"input_bytes": 512 * 1024 * 1024, "sidecar_bytes": 1024},
        )

        self.assertEqual(
            self._action_kinds(decision),
            ["cold_preview", "sidecar_index_prebuild", "streaming_package_write"],
        )
        self.assertTrue(decision.recommend_index_prebuild)
        self.assertFalse(decision.recommend_index_reuse_attempt)
        self.assertTrue(decision.recommend_streaming_write)
        self.assertEqual(decision.recommended_schedule, "sidecar_first")
        self.assertFalse(decision.abstained)
        self.assertIsNone(decision.abstain_reason)

    def test_heuristic_advisor_emits_streaming_write_for_high_rss(self) -> None:
        decision = RuntimeOptimizationAdvisor({"advisor_mode": "heuristic"}).evaluate(
            telemetry_history=[{"peak_rss_mb": 3072.0, "runtime_seconds": 12.5}],
            current_request_features={"input_bytes": 1024, "sidecar_bytes": 2048},
        )

        self.assertEqual(self._action_kinds(decision), ["streaming_package_write"])
        self.assertFalse(decision.recommend_index_prebuild)
        self.assertFalse(decision.recommend_index_reuse_attempt)
        self.assertTrue(decision.recommend_streaming_write)
        self.assertEqual(decision.recommended_schedule, "serial_safe")
        self.assertFalse(decision.abstained)
        self.assertIsNone(decision.abstain_reason)

    def test_heuristic_advisor_abstains_for_high_risk_rss_without_history(self) -> None:
        decision = RuntimeOptimizationAdvisor({"advisor_mode": "heuristic"}).evaluate(
            current_request_features={"input_bytes": 1024, "peak_rss_mb": 3072.0},
        )

        self.assertEqual(
            self._action_kinds(decision),
            ["abstain"],
        )
        self.assertFalse(decision.recommend_index_prebuild)
        self.assertFalse(decision.recommend_index_reuse_attempt)
        self.assertFalse(decision.recommend_streaming_write)
        self.assertEqual(decision.recommended_schedule, "serial_safe")
        self.assertTrue(decision.abstained)
        self.assertEqual(decision.abstain_reason, "need_more_telemetry")

    def test_retrieval_top_k_is_stable_for_matching_case_bank(self) -> None:
        case_bank_root = self._write_case_bank(
            [
                self._case_entry(
                    case_id="case:accept:001",
                    label="accept",
                    action_kind="sidecar_index_prebuild",
                    similarity_features={"input_bytes_bucket": "small", "sidecar_bytes_bucket": "large"},
                ),
                self._case_entry(
                    case_id="case:accept:002",
                    label="accept",
                    action_kind="sidecar_index_prebuild",
                    similarity_features={"input_bytes_bucket": "small", "sidecar_bytes_bucket": "large"},
                ),
                self._case_entry(
                    case_id="case:accept:003",
                    label="accept",
                    action_kind="cold_preview",
                    similarity_features={"input_bytes_bucket": "small", "sidecar_bytes_bucket": "small"},
                ),
            ]
        )
        features = {"input_bytes": 1024, "sidecar_bytes": 1024 * 1024 * 1024}
        summary_a = retrieve_runtime_cases(
            current_features=features,
            proposed_actions=[self._runtime_action_payload("sidecar_index_prebuild", risk_level="high")],
            case_bank_root=case_bank_root,
        ).to_dict()
        summary_b = retrieve_runtime_cases(
            current_features=features,
            proposed_actions=[self._runtime_action_payload("sidecar_index_prebuild", risk_level="high")],
            case_bank_root=case_bank_root,
        ).to_dict()
        self.assertEqual(summary_a["retrieved_case_refs"], summary_b["retrieved_case_refs"])
        self.assertEqual(summary_a["retrieved_case_refs"][:2], ["case:accept:001", "case:accept:002"])

    def test_retrieval_abstains_without_similar_case_for_high_risk_action(self) -> None:
        case_bank_root = self._write_case_bank(
            [
                self._case_entry(
                    case_id="case:accept:001",
                    label="accept",
                    action_kind="sidecar_index_reuse",
                    risk_level="medium",
                    similarity_features={"ticket_present": True, "ticket_validated": True, "sidecar_bytes_bucket": "small"},
                )
            ]
        )
        decision = RuntimeOptimizationAdvisor(
            {"advisor_mode": "heuristic", "advisor_case_bank_root": str(case_bank_root)}
        ).evaluate(current_request_features={"input_bytes": 1024, "sidecar_bytes": 1024 * 1024 * 1024})
        self.assertTrue(decision.abstained)
        self.assertEqual(decision.abstain_reason, "insufficient_similar_case")
        self.assertEqual(self._action_kinds(decision), ["abstain"])

    def test_retrieval_marks_conflicting_evidence_and_abstains(self) -> None:
        case_bank_root = self._write_case_bank(
            [
                self._case_entry(case_id="case:accept:001", label="accept", action_kind="sidecar_index_prebuild"),
                self._case_entry(
                    case_id="case:reject:001",
                    label="reject",
                    action_kind="sidecar_index_prebuild",
                    reject_taxonomy="ERR-ACTION_POLICY_DENIED",
                ),
            ]
        )
        decision = RuntimeOptimizationAdvisor(
            {"advisor_mode": "heuristic", "advisor_case_bank_root": str(case_bank_root)}
        ).evaluate(current_request_features={"input_bytes": 1024, "sidecar_bytes": 1024 * 1024 * 1024})
        self.assertTrue(decision.abstained)
        self.assertEqual(decision.abstain_reason, "conflicting_evidence")
        self.assertEqual(self._action_kinds(decision), ["abstain"])

    def test_openai_output_is_overridden_by_retrieval_fail_closed_policy(self) -> None:
        class FakeOpenAIAdvisorClient:
            def request_advisor_decision(
                self,
                *,
                feature_payload: dict[str, object],
                decision_schema: dict[str, object],
                feature_snapshot_hash: str,
                decision_version: str,
            ) -> OpenAIAdvisorClientResult:
                return OpenAIAdvisorClientResult(
                    decision_payload=self_outer._typed_action_set_payload(
                        actions=[
                            self_outer._runtime_action_payload(
                                "sidecar_index_prebuild",
                                risk_level="high",
                                required_artifacts=[],
                            )
                        ]
                    ),
                    response_id="resp-unsafe",
                    model="gpt-test",
                    usage={},
                    latency_seconds=0.0,
                    request_payload={},
                )

        self_outer = self
        case_bank_root = self._write_case_bank(
            [
                self._case_entry(
                    case_id="case:unsafe:001",
                    label="unsafe",
                    action_kind="sidecar_index_prebuild",
                    reject_taxonomy="ERR-ACTION_POLICY_DENIED",
                )
            ]
        )
        decision = RuntimeOptimizationAdvisor(
            {
                "advisor_mode": "openai_structured",
                "advisor_case_bank_root": str(case_bank_root),
                "openai_client": FakeOpenAIAdvisorClient(),
            }
        ).evaluate(current_request_features={"input_bytes": 1024, "sidecar_bytes": 1024 * 1024 * 1024})
        self.assertEqual(decision.advisor_mode, "openai_structured")
        self.assertTrue(decision.abstained)
        self.assertEqual(decision.abstain_reason, "unsafe_case_match")
        self.assertEqual(self._action_kinds(decision), ["abstain"])

    def test_sanitize_advisor_features_redacts_evidence_context_paths_and_hashes(self) -> None:
        sanitized = sanitize_advisor_features(
            {
                "dataset_id": "dataset:test",
                "run_id": "run:test",
                "input_bytes": 1024,
                "evidence_context": {
                    "advisor_phase": "post_execution_report",
                    "trace_checksum_prefix": "sha256:secret-proof-hash",
                    "dictionary_checksum_prefix": "sha256:dictionary",
                    "retrieved_case_refs": ["case:p3_cache_hit_accept:001"],
                    "case_similarity_features": {
                        "input_bytes_bucket": "small",
                        "missing_telemetry_fields": ["telemetry_history"],
                    },
                    "gate_policy_summary": {
                        "sidecar_manifest_path": "/tmp/private/manifest.json",
                        "advisor_enabled": True,
                    },
                    "proof_digest_path": "/tmp/proof_digest.json",
                },
            }
        )
        evidence_context = dict(sanitized.get("evidence_context") or {})
        self.assertIn("retrieved_case_refs", evidence_context)
        self.assertIn("trace_checksum_prefix", evidence_context)
        self.assertNotIn("proof_digest_path", evidence_context)
        self.assertNotIn("sidecar_manifest_path", json.dumps(evidence_context, ensure_ascii=False))

    def test_sanitize_advisor_features_redacts_secret_like_text_from_free_text_fields(self) -> None:
        secret_env = "DEEPSEEK_TEST_SECRET_SHOULD_NOT_LEAK"
        secret_key = "sk-test-should-not-leak"
        secret_path = "/tmp/proof_digest_should_not_be_exposed.json"
        sanitized = sanitize_advisor_features(
            {
                "failure_reason": (
                    f"Bearer bearer-secret {secret_key} {secret_env} "
                    f"{secret_path} sha256:proof-secret"
                ),
                "ticket_status": {
                    "api_token": secret_env,
                    "status": "ready",
                },
                "evidence_context": {
                    "advisor_phase": "post_execution_report",
                    "gate_policy_summary": {
                        "api_token": secret_env,
                        "proof_digest_path": secret_path,
                    },
                },
            }
        )

        sanitized_json = json.dumps(sanitized, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("bearer-secret", sanitized_json)
        self.assertNotIn(secret_key, sanitized_json)
        self.assertNotIn(secret_env, sanitized_json)
        self.assertNotIn(secret_path, sanitized_json)
        self.assertNotIn("proof-secret", sanitized_json)
        self.assertIn("[REDACTED]", sanitized_json)

    def test_case_bank_missing_evidence_scenarios_abstain_at_least_ninety_percent(self) -> None:
        cases = [
            case
            for case in self._repo_case_bank_cases()
            if str(case.get("label") or "") in {"abstain", "needs_more_data"}
        ]
        self.assertTrue(cases)
        successful = 0
        for case in cases:
            features, telemetry_history, sidecar_ticket = self._materialize_case_features(
                dict(case.get("similarity_features") or {})
            )
            decision = RuntimeOptimizationAdvisor(
                {"advisor_mode": "heuristic", "advisor_case_bank_root": str(self._repo_case_bank_root())}
            ).evaluate(
                telemetry_history=telemetry_history,
                current_request_features=features,
                sidecar_ticket=sidecar_ticket,
            )
            action_kinds = self._action_kinds(decision)
            if decision.abstained or "abstain" in action_kinds:
                successful += 1
        self.assertGreaterEqual(successful / len(cases), 0.9)

    def test_case_bank_unsafe_scenarios_keep_false_accept_rate_zero(self) -> None:
        cases = [
            case
            for case in self._repo_case_bank_cases()
            if str(case.get("label") or "") == "unsafe"
        ]
        self.assertTrue(cases)
        accepted_high_risk = 0
        for case in cases:
            features, telemetry_history, sidecar_ticket = self._materialize_case_features(
                dict(case.get("similarity_features") or {})
            )
            decision = RuntimeOptimizationAdvisor(
                {"advisor_mode": "heuristic", "advisor_case_bank_root": str(self._repo_case_bank_root())}
            ).evaluate(
                telemetry_history=telemetry_history,
                current_request_features=features,
                sidecar_ticket=sidecar_ticket,
            )
            if any(
                action.action_kind == str(case.get("action_kind") or "")
                and action.risk_level in {"high", "critical"}
                for action in decision.proposed_actions
            ):
                accepted_high_risk += 1
        self.assertEqual(accepted_high_risk, 0)

    def test_runtime_load_plan_prefers_minimal_for_large_input(self) -> None:
        advisor = RuntimeOptimizationAdvisor()
        plan = advisor.plan_load(current_request_features={"input_bytes": 256 * 1024 * 1024})

        self.assertIsInstance(plan, RuntimeLoadPlan)
        self.assertEqual(plan.load_mode, "cold_preview")
        self.assertEqual(plan.index_build_mode, "minimal")
        self.assertFalse(plan.materialize_event_stream)
        self.assertFalse(plan.try_parser_artifact_reuse)
        self.assertFalse(plan.try_sidecar_index_reuse)
        self.assertTrue(plan.background_sidecar_prebuild)

    def test_runtime_load_plan_keeps_full_for_small_input(self) -> None:
        advisor = RuntimeOptimizationAdvisor()
        plan = advisor.plan_load(current_request_features={"input_bytes": 1024})

        self.assertEqual(plan.load_mode, "full")
        self.assertEqual(plan.index_build_mode, "full")
        self.assertTrue(plan.materialize_event_stream)
        self.assertFalse(plan.try_parser_artifact_reuse)
        self.assertFalse(plan.try_sidecar_index_reuse)
        self.assertFalse(plan.background_sidecar_prebuild)

    def test_raw_telemetry_update_row_affects_prediction(self) -> None:
        advisor = RuntimeOptimizationAdvisor({"advisor_mode": "heuristic"})
        decision = advisor.evaluate(
            telemetry_history=[
                {
                    "update_version": "telemetry-record-update-v1",
                    "job_id": "parse-job",
                    "phase_name": "parse_rebuild",
                    "phase_seconds": 1.0,
                    "metrics": {"rss_mb": 999.0},
                },
                {
                    "update_version": "telemetry-record-update-v1",
                    "job_id": "evidence-job",
                    "phase_name": "evidence_export",
                    "phase_seconds": 99.0,
                    "metrics": {
                        "dataset_id": "dataset:advisor-history",
                        "export_family": "evidence",
                        "embodiment_mode": "mode_b",
                        "input_bytes": 1024,
                        "sidecar_bytes": 2048,
                        "sidecar_row_count": 4,
                        "sidecar_index_build_seconds": 0.25,
                        "runtime_seconds": 12.5,
                        "peak_rss_mb": 256.0,
                    },
                },
            ],
            current_request_features={"input_bytes": 1024, "sidecar_bytes": 2048},
        )
        self.assertEqual(decision.predicted_runtime_seconds, 12.5)

    def test_local_model_is_deferred_and_rejected_by_current_contracts(self) -> None:
        self.assertNotIn("local_model", ADVISOR_MODES)
        decision = RuntimeOptimizationAdvisor({"advisor_mode": "local_model"}).evaluate(
            current_request_features={"input_bytes": 1024}
        )
        self.assertEqual(decision.advisor_mode, "disabled")

        invalid_decision = {**decision.to_dict(), "advisor_mode": "local_model"}
        self.assertIsNotNone(validate_schema(load_schema("advisor_decision.schema.json"), invalid_decision))

        invalid_benchmark_scenario = {
            "scenario_version": "benchmark-scenario-v1",
            "scenario_id": "advisor_heuristic",
            "scenario_kind": "advisor_heuristic",
            "advisor_enabled": True,
            "advisor_mode": "local_model",
            "ticket_fast_path_enabled": True,
            "streaming_write_enabled": True,
            "optional_dependency": None,
            "expected_execution_plan": [],
        }
        self.assertIsNotNone(
            validate_schema(load_schema("benchmark_scenario.schema.json"), invalid_benchmark_scenario)
        )

    def test_skipped_offline_coefficients_payload_falls_back_to_heuristic(self) -> None:
        coefficients_path = self.root / "skipped-coefficients.json"
        coefficients_path.write_text(
            json.dumps(
                {
                    "status": "skipped",
                    "skip_reason": "optional sklearn unavailable",
                    "optional_dependency": "sklearn",
                    "training_rows": 0,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        decision = RuntimeOptimizationAdvisor(
            {
                "advisor_mode": "offline_coefficients",
                "coefficients_path": str(coefficients_path),
            }
        ).evaluate(current_request_features={"input_bytes": 1024, "sidecar_bytes": 2048})

        self.assertEqual(decision.advisor_mode, "heuristic")
        self.assertEqual(decision.model_ref, "heuristic-runtime-advisor-v1")
        self.assertIn("offline coefficients payload is not trained", decision.reasons)

    def test_openai_structured_without_key_falls_back_to_heuristic(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "OPENAI_ADVISOR_ENABLED": "true"}):
            decision = RuntimeOptimizationAdvisor({"advisor_mode": "openai_structured"}).evaluate(
                current_request_features={"input_bytes": 1024, "sidecar_bytes": 2048}
            )

        self.assertEqual(decision.advisor_mode, "heuristic")
        self.assertEqual(decision.model_ref, "heuristic-runtime-advisor-v1")
        self.assertIn("openai fallback: openai_unconfigured", decision.reasons)

    def test_openai_structured_provider_fallback_metadata_uses_resolved_client(self) -> None:
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
            advisor = RuntimeOptimizationAdvisor(
                {
                    "advisor_mode": "openai_structured",
                    "llm_enabled": True,
                    "llm_provider": "deepseek",
                }
            )
            decision = advisor.evaluate(current_request_features={"input_bytes": 1024})

        self.assertEqual(decision.advisor_mode, "heuristic")
        self.assertEqual(advisor.last_advisor_metadata["fallback_reason"], "openai_unconfigured")
        self.assertEqual(advisor.last_advisor_metadata["llm_backend"], "openai_compatible_chat")
        self.assertEqual(advisor.last_advisor_metadata["openai_model"], "deepseek-v4-pro")
        self.assertEqual(advisor.last_advisor_metadata["llm_provider"], "deepseek")

    def test_llm_client_reads_max_output_tokens_from_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "deepseek-key",
                "LLM_ADVISOR_MAX_OUTPUT_TOKENS": "3000",
            },
            clear=True,
        ):
            client = LLMAdvisorClient.from_config(
                {
                    "llm_enabled": True,
                    "llm_provider": "deepseek",
                }
            )

        self.assertEqual(client.max_output_tokens, 3000)

    def test_openai_structured_fallback_metadata_redacts_secret_message(self) -> None:
        calls = {"count": 0}

        def transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> tuple[int, dict[str, str], bytes]:
            calls["count"] += 1
            raise LLMAdvisorClientError(
                "openai_timeout",
                "secret sk-live123 Bearer bearer-secret llm-config-secret openai-config-secret",
            )

        client = LLMAdvisorClient(
            api_key="provider-key",
            model="gpt-test",
            enabled=True,
            transport=transport,
        )
        advisor = RuntimeOptimizationAdvisor(
            {
                "advisor_mode": "openai_structured",
                "openai_client": client,
                "llm_api_key": "llm-config-secret",
                "openai_api_key": "openai-config-secret",
            }
        )
        decision = advisor.evaluate(current_request_features={"input_bytes": 1024})

        self.assertEqual(calls["count"], 1)
        self.assertEqual(decision.advisor_mode, "heuristic")
        self.assertEqual(advisor.last_advisor_metadata["fallback_reason"], "openai_timeout")
        self.assertEqual(advisor.last_advisor_metadata["fallback_exception_type"], "LLMAdvisorClientError")
        metadata_json = json.dumps(advisor.last_advisor_metadata, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("sk-live123", metadata_json)
        self.assertNotIn("bearer-secret", metadata_json)
        self.assertNotIn("llm-config-secret", metadata_json)
        self.assertNotIn("openai-config-secret", metadata_json)
        self.assertIn("[REDACTED]", advisor.last_advisor_metadata["fallback_message"])

    def test_openai_structured_mock_client_returns_advisor_decision(self) -> None:
        class FakeOpenAIAdvisorClient:
            def request_advisor_decision(
                self,
                *,
                feature_payload: dict[str, object],
                decision_schema: dict[str, object],
                feature_snapshot_hash: str,
                decision_version: str,
            ) -> OpenAIAdvisorClientResult:
                payload = self_outer._typed_action_set_payload(
                    actions=[
                        {
                            "action_id": "action:openai:cold_preview",
                            "action_kind": "cold_preview",
                            "required_artifacts": [],
                            "expected_benefit": {"runtime_seconds_delta": None, "peak_rss_mb_delta": None, "notes": ["mock action"]},
                            "risk_level": "medium",
                            "proof_scope_impact": "none",
                            "fallback_action": None,
                        },
                        {
                            "action_id": "action:openai:sidecar_index_prebuild",
                            "action_kind": "sidecar_index_prebuild",
                            "required_artifacts": [],
                            "expected_benefit": {"runtime_seconds_delta": None, "peak_rss_mb_delta": None, "notes": ["mock action"]},
                            "risk_level": "medium",
                            "proof_scope_impact": "none",
                            "fallback_action": None,
                        },
                        {
                            "action_id": "action:openai:streaming_package_write",
                            "action_kind": "streaming_package_write",
                            "required_artifacts": [],
                            "expected_benefit": {"runtime_seconds_delta": None, "peak_rss_mb_delta": None, "notes": ["mock action"]},
                            "risk_level": "medium",
                            "proof_scope_impact": "none",
                            "fallback_action": None,
                        },
                    ],
                )
                return OpenAIAdvisorClientResult(
                    decision_payload=payload,
                    response_id="resp-test",
                    model="gpt-test",
                    usage={},
                    latency_seconds=0.0,
                    request_payload={},
                )

        self_outer = self
        decision = RuntimeOptimizationAdvisor(
            {"advisor_mode": "openai_structured", "openai_client": FakeOpenAIAdvisorClient()}
        ).evaluate(
            current_request_features={"input_bytes": 512 * 1024 * 1024, "sidecar_bytes": 1024}
        )

        self.assertEqual(decision.advisor_mode, "openai_structured")
        self.assertEqual(decision.model_ref, "gpt-test")
        self.assertTrue(decision.recommend_index_prebuild)
        self.assertFalse(decision.recommend_index_reuse_attempt)
        self.assertTrue(decision.recommend_streaming_write)
        self.assertEqual(decision.recommended_schedule, "sidecar_first")
        self.assertEqual(
            self._action_kinds(decision),
            ["cold_preview", "sidecar_index_prebuild", "streaming_package_write"],
        )
        self.assertFalse(decision.abstained)
        self.assertIsNone(decision.abstain_reason)

    def test_openai_structured_hallucinated_action_fails_closed_on_schema_validation(self) -> None:
        class FakeOpenAIAdvisorClient:
            def request_advisor_decision(
                self,
                *,
                feature_payload: dict[str, object],
                decision_schema: dict[str, object],
                feature_snapshot_hash: str,
                decision_version: str,
            ) -> OpenAIAdvisorClientResult:
                return OpenAIAdvisorClientResult(
                    decision_payload=self_outer._typed_action_set_payload(
                        actions=[
                            {
                                "action_id": "action:openai:hallucinated",
                                "action_kind": "hallucinated_action_kind",
                                "required_artifacts": ["control/unknown.json"],
                                "expected_benefit": {"runtime_seconds_delta": None, "peak_rss_mb_delta": None, "notes": ["unknown action"]},
                                "risk_level": "high",
                                "proof_scope_impact": "none",
                                "fallback_action": None,
                            }
                        ],
                    ),
                    response_id="resp-hallucinated",
                    model="gpt-test",
                    usage={},
                    latency_seconds=0.0,
                    request_payload={},
                )

        self_outer = self
        decision = RuntimeOptimizationAdvisor(
            {"advisor_mode": "openai_structured", "openai_client": FakeOpenAIAdvisorClient()}
        ).evaluate(current_request_features={"input_bytes": 1024, "sidecar_bytes": 2048})

        self.assertEqual(decision.advisor_mode, "heuristic")
        self.assertEqual(self._action_kinds(decision), [])
        self.assertIn("openai fallback: openai_schema_invalid", decision.reasons)

    def test_openai_structured_safe_candidate_mismatch_fails_closed_to_heuristic(self) -> None:
        class FakeOpenAIAdvisorClient:
            def request_advisor_decision(
                self,
                *,
                feature_payload: dict[str, object],
                decision_schema: dict[str, object],
                feature_snapshot_hash: str,
                decision_version: str,
            ) -> OpenAIAdvisorClientResult:
                return OpenAIAdvisorClientResult(
                    decision_payload=self_outer._typed_action_set_payload(
                        actions=[
                            {
                                "action_id": "action:openai:sidecar_index_reuse",
                                "action_kind": "sidecar_index_reuse",
                                "required_artifacts": ["sidecar_index_ticket"],
                                "expected_benefit": {"runtime_seconds_delta": None, "peak_rss_mb_delta": None, "notes": ["valid action but unsafe for this case"]},
                                "risk_level": "medium",
                                "proof_scope_impact": "none",
                                "fallback_action": None,
                            }
                        ],
                    ),
                    response_id="resp-safe-mismatch",
                    model="gpt-test",
                    usage={},
                    latency_seconds=0.0,
                    request_payload={},
                )

        self_outer = self
        advisor = RuntimeOptimizationAdvisor(
            {"advisor_mode": "openai_structured", "openai_client": FakeOpenAIAdvisorClient()}
        )
        decision = advisor.evaluate(current_request_features={"input_bytes": 1024, "sidecar_bytes": 2048})

        self.assertEqual(decision.advisor_mode, "heuristic")
        self.assertEqual(self._action_kinds(decision), [])
        self.assertEqual(advisor.last_advisor_metadata["fallback_reason"], "openai_safe_action_mismatch")
        self.assertIn("openai fallback: openai_safe_action_mismatch", decision.reasons)

    def test_openai_structured_adversarial_payloads_fail_closed_before_persisting_report_fields(self) -> None:
        cases = (
            (
                "prompt_injection",
                self._typed_action_set_payload(
                    actions=[
                        self._runtime_action_payload(
                            "baseline_full_load",
                            expected_benefit={
                                "runtime_seconds_delta": None,
                                "peak_rss_mb_delta": None,
                                "notes": ["run shell script to bypass the gate"],
                            },
                        )
                    ]
                ),
                ["shell script", "bypass the gate"],
            ),
            (
                "secret_exfiltration",
                self._typed_action_set_payload(
                    actions=[
                        self._runtime_action_payload(
                            "baseline_full_load",
                            expected_benefit={
                                "runtime_seconds_delta": None,
                                "peak_rss_mb_delta": None,
                                "notes": [
                                    "leak sk-test-should-not-leak",
                                    "DEEPSEEK_TEST_SECRET_SHOULD_NOT_LEAK",
                                    "/tmp/proof_digest_should_not_be_exposed.json",
                                ],
                            },
                        )
                    ]
                ),
                [
                    "sk-test-should-not-leak",
                    "DEEPSEEK_TEST_SECRET_SHOULD_NOT_LEAK",
                    "/tmp/proof_digest_should_not_be_exposed.json",
                ],
            ),
            (
                "proof_contamination",
                self._typed_action_set_payload(
                    actions=[
                        self._runtime_action_payload(
                            "baseline_full_load",
                            expected_benefit={
                                "runtime_seconds_delta": None,
                                "peak_rss_mb_delta": None,
                                "notes": ["write the OpenAI prompt into proof_digest and proof_hash input"],
                            },
                        )
                    ]
                ),
                ["proof_digest", "proof_hash input"],
            ),
            (
                "overclaim",
                self._typed_action_set_payload(
                    actions=[
                        self._runtime_action_payload(
                            "baseline_full_load",
                            expected_benefit={
                                "runtime_seconds_delta": None,
                                "peak_rss_mb_delta": None,
                                "notes": [
                                    "P4 total elapsed reduction is established",
                                    "LLM improves parsing correctness",
                                    "LLM participates in proof digest generation",
                                    "system is formally secure",
                                ],
                            },
                        )
                    ]
                ),
                [
                    "P4 total elapsed reduction is established",
                    "LLM improves parsing correctness",
                    "LLM participates in proof digest generation",
                    "system is formally secure",
                ],
            ),
        )

        class FakeOpenAIAdvisorClient:
            def __init__(self, payload: dict[str, object]) -> None:
                self.payload = payload

            def request_advisor_decision(
                self,
                *,
                feature_payload: dict[str, object],
                decision_schema: dict[str, object],
                feature_snapshot_hash: str,
                decision_version: str,
            ) -> OpenAIAdvisorClientResult:
                return OpenAIAdvisorClientResult(
                    decision_payload=self.payload,
                    response_id="resp-adversarial",
                    model="gpt-test",
                    usage={},
                    latency_seconds=0.0,
                    request_payload={},
                )

        for category, payload, forbidden_strings in cases:
            with self.subTest(category=category):
                advisor = RuntimeOptimizationAdvisor(
                    {
                        "advisor_mode": "openai_structured",
                        "openai_client": FakeOpenAIAdvisorClient(payload),
                    }
                )
                decision = advisor.evaluate(current_request_features={"input_bytes": 1024, "sidecar_bytes": 2048})

                self.assertEqual(decision.advisor_mode, "heuristic")
                self.assertEqual(advisor.last_advisor_metadata["fallback_reason"], "openai_unsafe_output")
                self.assertTrue(any(reason.startswith("openai fallback:") for reason in decision.reasons))
                persisted_json = json.dumps(
                    {
                        "decision": decision.to_dict(),
                        "advisor_metadata": advisor.last_advisor_metadata,
                        "advisor_evidence_context": advisor.last_evidence_context,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                for forbidden in forbidden_strings:
                    self.assertNotIn(forbidden, persisted_json)

    def test_openai_client_uses_structured_outputs_and_redacted_features(self) -> None:
        captured: dict[str, object] = {}

        def transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> tuple[int, dict[str, str], bytes]:
            request_payload = json.loads(body.decode("utf-8"))
            captured["url"] = url
            captured["headers"] = headers
            captured["request_payload"] = request_payload
            decision_payload = self._typed_action_set_payload(
                actions=[
                    {
                        "action_id": "action:openai:sidecar_index_reuse",
                        "action_kind": "sidecar_index_reuse",
                        "required_artifacts": ["sidecar_index_ticket"],
                        "expected_benefit": {"runtime_seconds_delta": None, "peak_rss_mb_delta": None, "notes": ["redacted structured output"]},
                        "risk_level": "medium",
                        "proof_scope_impact": "none",
                        "fallback_action": None,
                    }
                ],
            )
            response_payload = {
                "id": "resp-test",
                "status": "completed",
                "model": "gpt-test",
                "output_text": json.dumps(decision_payload, ensure_ascii=False),
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            return 200, {}, json.dumps(response_payload, ensure_ascii=False).encode("utf-8")

        result = OpenAIAdvisorClient(
            api_key="test-key",
            model="gpt-test",
            enabled=True,
            transport=transport,
        ).request_advisor_decision(
            feature_payload={
                "dataset_id": "private-dataset-name",
                "run_id": "private-run-id",
                "input_bytes": 1024,
                "stage_timings": {
                    "package_write_seconds": 1.25,
                    "trace_path": str(self.root / "private" / "trace.bin"),
                },
                "benchmark_delta": {
                    "runtime_seconds": -2.5,
                    "proof_hash": "sha256:proof-secret-benchmark",
                },
                "ticket_status": {
                    "state": "ready",
                    "ticket_path": str(self.root / "private" / "ticket.json"),
                },
                "failure_reason": f"sidecar timeout at {self.root / 'private' / 'trace.bin'} proof sha256:proof-secret-failure",
                "background_prebuild_reused": True,
                "sidecar_ticket_fast_path": True,
                "proof_hash": "sha256:proof-secret",
                "sidecar_source": str(self.root / "private" / "dependency_sidecar.jsonl"),
                "raw_trace": "raw trace bytes should not leave process",
                "sidecar_row": {"edge_hash": "x" * 64},
                "event_content": {"message": "private event payload"},
            },
            decision_schema=load_schema("runtime_action_set.schema.json"),
            feature_snapshot_hash="sha256:feature",
            decision_version="runtime-action-set-v1",
        )

        self.assertEqual(result.decision_payload["action_set_version"], "runtime-action-set-v1")
        self.assertEqual(result.decision_payload["proposed_actions"][0]["action_kind"], "sidecar_index_reuse")
        request_payload = captured["request_payload"]
        self.assertIsInstance(request_payload, dict)
        user_text = request_payload["input"][1]["content"][0]["text"]
        self.assertIn("allowed_action_kinds", user_text)
        self.assertIn("dataset_id_hash", user_text)
        self.assertIn("run_id_hash", user_text)
        self.assertIn("stage_timings", user_text)
        self.assertIn("benchmark_delta", user_text)
        self.assertIn("ticket_status", user_text)
        self.assertIn("failure_reason", user_text)
        self.assertIn("background_prebuild_reused", user_text)
        self.assertIn("sidecar_ticket_fast_path", user_text)
        self.assertNotIn("private-dataset-name", user_text)
        self.assertNotIn("private-run-id", user_text)
        self.assertNotIn("proof-secret", user_text)
        self.assertNotIn("proof-secret-benchmark", user_text)
        self.assertNotIn("proof-secret-failure", user_text)
        self.assertNotIn("dependency_sidecar.jsonl", user_text)
        self.assertNotIn("trace.bin", user_text)
        self.assertNotIn("ticket.json", user_text)
        self.assertNotIn("raw trace bytes should not leave process", user_text)
        self.assertNotIn("private event payload", user_text)
        self.assertEqual(request_payload["text"]["format"]["type"], "json_schema")
        self.assertTrue(request_payload["text"]["format"]["strict"])

    def test_openai_structured_invalid_json_and_schema_fail_closed_to_heuristic(self) -> None:
        def invalid_json_transport(
            url: str,
            headers: dict[str, str],
            body: bytes,
            timeout_s: float,
        ) -> tuple[int, dict[str, str], bytes]:
            response_payload = {
                "id": "resp-invalid-json",
                "status": "completed",
                "model": "gpt-test",
                "output_text": "{not valid json",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            return 200, {}, json.dumps(response_payload, ensure_ascii=False).encode("utf-8")

        advisor = RuntimeOptimizationAdvisor(
            {
                "advisor_mode": "openai_structured",
                "openai_client": LLMAdvisorClient(
                    api_key="test-key",
                    model="gpt-test",
                    enabled=True,
                    transport=invalid_json_transport,
                ),
            }
        )
        decision = advisor.evaluate(current_request_features={"input_bytes": 1024, "sidecar_bytes": 2048})

        self.assertEqual(decision.advisor_mode, "heuristic")
        self.assertEqual(decision.model_ref, "heuristic-runtime-advisor-v1")
        self.assertEqual(advisor.last_advisor_metadata["fallback_reason"], "openai_json_invalid")
        self.assertIn("openai fallback: openai_json_invalid", decision.reasons)

        def schema_invalid_transport(
            url: str,
            headers: dict[str, str],
            body: bytes,
            timeout_s: float,
        ) -> tuple[int, dict[str, str], bytes]:
            decision_payload = {
                "action_set_version": "runtime-action-set-v1",
                "generated_at": "2026-07-06T00:00:00+00:00",
                "proposed_actions": "not-an-array",
                "abstained": False,
                "abstain_reason": None,
            }
            response_payload = {
                "id": "resp-schema-invalid",
                "status": "completed",
                "model": "gpt-test",
                "output_text": json.dumps(decision_payload, ensure_ascii=False),
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            return 200, {}, json.dumps(response_payload, ensure_ascii=False).encode("utf-8")

        advisor = RuntimeOptimizationAdvisor(
            {
                "advisor_mode": "openai_structured",
                "openai_client": LLMAdvisorClient(
                    api_key="test-key",
                    model="gpt-test",
                    enabled=True,
                    transport=schema_invalid_transport,
                ),
            }
        )
        decision = advisor.evaluate(current_request_features={"input_bytes": 1024, "sidecar_bytes": 2048})

        self.assertEqual(decision.advisor_mode, "heuristic")
        self.assertEqual(advisor.last_advisor_metadata["fallback_reason"], "openai_schema_invalid")
        self.assertIn("openai fallback: openai_schema_invalid", decision.reasons)

    def test_openai_compatible_chat_backend_uses_chat_completions_and_redacted_features(self) -> None:
        captured: dict[str, object] = {}

        def transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> tuple[int, dict[str, str], bytes]:
            request_payload = json.loads(body.decode("utf-8"))
            captured["url"] = url
            captured["headers"] = headers
            captured["request_payload"] = request_payload
            decision_payload = self._typed_action_set_payload(
                actions=[
                    {
                        "action_id": "action:chat:abstain",
                        "action_kind": "abstain",
                        "required_artifacts": ["telemetry_history"],
                        "expected_benefit": {"runtime_seconds_delta": None, "peak_rss_mb_delta": None, "notes": ["chat compatible structured output"]},
                        "risk_level": "medium",
                        "proof_scope_impact": "none",
                        "fallback_action": None,
                    }
                ],
                abstained=True,
                abstain_reason="need more telemetry",
            )
            response_payload = {
                "id": "chatcmpl-test",
                "model": "qwen-test",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(decision_payload, ensure_ascii=False),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            }
            return 200, {}, json.dumps(response_payload, ensure_ascii=False).encode("utf-8")

        result = LLMAdvisorClient(
            api_key="provider-key",
            model="qwen-test",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            enabled=True,
            backend="openai_compatible_chat",
            transport=transport,
        ).request_advisor_decision(
            feature_payload={
                "dataset_id": "private-dataset-name",
                "run_id": "private-run-id",
                "input_bytes": 1024,
                "proof_hash": "sha256:proof-secret",
                "sidecar_source": str(self.root / "private" / "dependency_sidecar.jsonl"),
            },
            decision_schema=load_schema("runtime_action_set.schema.json"),
            feature_snapshot_hash="sha256:chat-feature",
            decision_version="runtime-action-set-v1",
        )

        self.assertEqual(result.backend, "openai_compatible_chat")
        self.assertEqual(result.model, "qwen-test")
        self.assertEqual(result.decision_payload["abstain_reason"], "need more telemetry")
        self.assertEqual(captured["url"], "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        request_payload = captured["request_payload"]
        self.assertIsInstance(request_payload, dict)
        self.assertEqual(request_payload["response_format"], {"type": "json_object"})
        user_text = request_payload["messages"][1]["content"]
        self.assertIn("json_schema", user_text)
        self.assertIn("dataset_id_hash", user_text)
        self.assertNotIn("private-dataset-name", user_text)
        self.assertNotIn("proof-secret", user_text)
        self.assertNotIn("dependency_sidecar.jsonl", user_text)

    def test_openai_compatible_chat_backend_rejects_fenced_json_with_surrounding_text(self) -> None:
        def transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> tuple[int, dict[str, str], bytes]:
            decision_payload = self._typed_action_set_payload(
                actions=[
                    {
                        "action_id": "action:fenced:streaming",
                        "action_kind": "streaming_package_write",
                        "required_artifacts": [],
                        "expected_benefit": {"runtime_seconds_delta": None, "peak_rss_mb_delta": None, "notes": ["fenced JSON compatible output"]},
                        "risk_level": "low",
                        "proof_scope_impact": "none",
                        "fallback_action": None,
                    }
                ],
            )
            response_payload = {
                "id": "chatcmpl-fenced-test",
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": (
                                "Here is the advisor decision.\n"
                                "```json\n"
                                f"{json.dumps(decision_payload, ensure_ascii=False)}\n"
                                "```\n"
                                "No proof fields were changed."
                            ),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9},
            }
            return 200, {}, json.dumps(response_payload, ensure_ascii=False).encode("utf-8")

        client = LLMAdvisorClient(
            api_key="provider-key",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            enabled=True,
            backend="openai_compatible_chat",
            transport=transport,
            chat_completions_path="/chat/completions",
        )
        with self.assertRaises(LLMAdvisorClientError) as raised:
            client.request_advisor_decision(
                feature_payload={"input_bytes": 1024},
                decision_schema=load_schema("runtime_action_set.schema.json"),
                feature_snapshot_hash="sha256:fenced-feature",
                decision_version="runtime-action-set-v1",
            )

        self.assertEqual(raised.exception.reason, "openai_json_invalid")

    def test_llm_client_from_config_uses_provider_key_env(self) -> None:
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "dashscope-key"}, clear=False):
            client = LLMAdvisorClient.from_config(
                {
                    "llm_enabled": True,
                    "llm_backend": "openai_compatible_chat",
                    "llm_api_key_env": "DASHSCOPE_API_KEY",
                    "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "llm_model": "qwen-test",
                }
            )

        self.assertTrue(client.enabled)
        self.assertEqual(client.api_key, "dashscope-key")
        self.assertEqual(client.backend, "openai_compatible_chat")
        self.assertEqual(client.base_url, "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(client.model, "qwen-test")

    def test_deepseek_provider_uses_official_chat_endpoint_defaults(self) -> None:
        captured: dict[str, object] = {}

        def transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> tuple[int, dict[str, str], bytes]:
            request_payload = json.loads(body.decode("utf-8"))
            captured["url"] = url
            captured["request_payload"] = request_payload
            decision_payload = self._typed_action_set_payload(
                actions=[
                    {
                        "action_id": "action:deepseek:reuse",
                        "action_kind": "sidecar_index_reuse",
                        "required_artifacts": ["sidecar_index_ticket"],
                        "expected_benefit": {"runtime_seconds_delta": None, "peak_rss_mb_delta": None, "notes": ["deepseek compatible structured output"]},
                        "risk_level": "low",
                        "proof_scope_impact": "none",
                        "fallback_action": None,
                    }
                ],
            )
            response_payload = {
                "id": "chatcmpl-deepseek-test",
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(decision_payload, ensure_ascii=False),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9},
            }
            return 200, {}, json.dumps(response_payload, ensure_ascii=False).encode("utf-8")

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "deepseek-key"}, clear=False):
            client = LLMAdvisorClient.from_config(
                {
                    "llm_enabled": True,
                    "llm_provider": "deepseek",
                    "llm_transport": transport,
                }
            )

        result = client.request_advisor_decision(
            feature_payload={"input_bytes": 1024},
            decision_schema=load_schema("runtime_action_set.schema.json"),
            feature_snapshot_hash="sha256:deepseek-feature",
            decision_version="runtime-action-set-v1",
        )

        self.assertEqual(client.api_key, "deepseek-key")
        self.assertEqual(client.backend, "openai_compatible_chat")
        self.assertEqual(client.base_url, "https://api.deepseek.com")
        self.assertEqual(client.model, "deepseek-v4-pro")
        self.assertEqual(client.max_retries, 2)
        self.assertEqual(result.backend, "openai_compatible_chat")
        self.assertEqual(captured["url"], "https://api.deepseek.com/chat/completions")
        request_payload = captured["request_payload"]
        self.assertIsInstance(request_payload, dict)
        self.assertEqual(request_payload["response_format"], {"type": "json_object"})

    def test_llm_client_from_config_ignores_deepseek_key_without_provider_or_enable(self) -> None:
        calls = {"count": 0}

        def transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> tuple[int, dict[str, str], bytes]:
            calls["count"] += 1
            raise AssertionError("disabled advisor must not call transport")

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "deepseek-key"}, clear=True):
            client = LLMAdvisorClient.from_config({})

        client.transport = transport
        self.assertFalse(client.enabled)
        self.assertIsNone(client.provider)
        self.assertEqual(client.api_key, "")
        with self.assertRaises(LLMAdvisorClientError) as raised:
            client.request_advisor_decision(
                feature_payload={"input_bytes": 1024},
                decision_schema=load_schema("runtime_action_set.schema.json"),
                feature_snapshot_hash="sha256:deepseek-disabled",
                decision_version="runtime-action-set-v1",
            )
        self.assertEqual(raised.exception.reason, "openai_unconfigured")
        self.assertEqual(calls["count"], 0)

    def test_deepseek_provider_without_llm_enabled_is_unconfigured_and_does_not_call_transport(self) -> None:
        calls = {"count": 0}

        def transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> tuple[int, dict[str, str], bytes]:
            calls["count"] += 1
            raise AssertionError("disabled advisor must not call transport")

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "deepseek-key"}, clear=True):
            client = LLMAdvisorClient.from_config(
                {
                    "llm_provider": "deepseek",
                    "llm_transport": transport,
                }
            )

        self.assertFalse(client.enabled)
        self.assertEqual(client.provider, "deepseek")
        self.assertEqual(client.api_key, "deepseek-key")
        with self.assertRaises(LLMAdvisorClientError) as raised:
            client.request_advisor_decision(
                feature_payload={"input_bytes": 1024},
                decision_schema=load_schema("runtime_action_set.schema.json"),
                feature_snapshot_hash="sha256:deepseek-provider-disabled",
                decision_version="runtime-action-set-v1",
            )
        self.assertEqual(raised.exception.reason, "openai_unconfigured")
        self.assertEqual(calls["count"], 0)

    def test_llm_client_retries_transient_transport_failure(self) -> None:
        calls = {"count": 0}

        def transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> tuple[int, dict[str, str], bytes]:
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.URLError("temporary dns failure")
            decision_payload = self._typed_action_set_payload(
                actions=[
                    {
                        "action_id": "action:retry:reuse",
                        "action_kind": "sidecar_index_reuse",
                        "required_artifacts": ["sidecar_index_ticket"],
                        "expected_benefit": {"runtime_seconds_delta": None, "peak_rss_mb_delta": None, "notes": ["retry structured output"]},
                        "risk_level": "low",
                        "proof_scope_impact": "none",
                        "fallback_action": None,
                    }
                ],
            )
            response_payload = {
                "id": "chatcmpl-retry-test",
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(decision_payload)},
                    }
                ],
                "usage": {"total_tokens": 5},
            }
            return 200, {}, json.dumps(response_payload).encode("utf-8")

        result = LLMAdvisorClient(
            api_key="provider-key",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            enabled=True,
            backend="openai_compatible_chat",
            transport=transport,
            chat_completions_path="/chat/completions",
            max_retries=1,
            retry_delay_s=0,
        ).request_advisor_decision(
            feature_payload={"input_bytes": 1024},
            decision_schema=load_schema("runtime_action_set.schema.json"),
            feature_snapshot_hash="sha256:retry-feature",
            decision_version="runtime-action-set-v1",
        )

        self.assertEqual(calls["count"], 2)
        self.assertEqual(result.decision_payload["proposed_actions"][0]["action_kind"], "sidecar_index_reuse")

    def test_advisor_result_facade_wraps_decision_without_changing_legacy_api(self) -> None:
        advisor = RuntimeOptimizationAdvisor({"advisor_mode": "heuristic"})
        legacy_decision = advisor.evaluate(current_request_features={"input_bytes": 1024, "sidecar_bytes": 2048})
        wrapped_decision = advisor.evaluate_result(
            current_request_features={"input_bytes": 1024, "sidecar_bytes": 2048},
            job_id="advisor-job-1",
        )
        module_decision = advisor_Evaluate(
            advisor_config={"advisor_mode": "heuristic"},
            current_request_features={"input_bytes": 1024, "sidecar_bytes": 2048},
            request_id="advisor-request-1",
        )

        self.assertIsInstance(legacy_decision, AdvisorDecision)
        self.assertTrue(wrapped_decision.ok, wrapped_decision.message)
        self.assertIsInstance(wrapped_decision.data, dict)
        self.assertEqual(wrapped_decision.data["advisor_decision"].to_dict(), legacy_decision.to_dict())
        contract = wrapped_decision.data["agent_contract"]
        self.assertEqual(contract["job_id"], "advisor-job-1")
        self.assertEqual(contract["agent_state"], "AGENT-completed")
        self.assertEqual(contract["status"], "AGENT-completed")
        self.assertIsNone(contract["error_code"])
        self.assertEqual(
            [item["status"] for item in contract["state_history"]],
            ["AGENT-queued", "AGENT-validating_input", "AGENT-running", "AGENT-completed"],
        )
        self.assertTrue(module_decision.ok, module_decision.message)
        self.assertIsInstance(module_decision.data, dict)
        self.assertIsInstance(module_decision.data["advisor_decision"], AdvisorDecision)
        self.assertEqual(module_decision.data["agent_contract"]["job_id"], "advisor-request-1")

    def test_advisor_result_facade_reports_unavailable_on_exception(self) -> None:
        class ExplodingAdvisor(RuntimeOptimizationAdvisor):
            def evaluate(self, **_: object) -> AdvisorDecision:
                raise RuntimeError("advisor failed")

        result = ExplodingAdvisor({"advisor_mode": "heuristic"}).evaluate_result(job_id="advisor-boom")

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "ERR-ADVISOR_UNAVAILABLE")
        self.assertIsInstance(result.data, dict)
        self.assertIsNone(result.data["advisor_decision"])
        contract = result.data["agent_contract"]
        self.assertEqual(contract["job_id"], "advisor-boom")
        self.assertEqual(contract["agent_state"], "AGENT-failed")
        self.assertEqual(contract["status"], "AGENT-failed")
        self.assertEqual(contract["error_code"], result.code)
        self.assertEqual(contract["failed_at_state"], "AGENT-running")

    def test_telemetry_and_gate_accept_ticket_fast_path(self) -> None:
        sidecar_path, manifest, sidecar_checksum = self._prepare_sidecar()
        fingerprint = (
            int(sidecar_path.stat().st_dev),
            int(sidecar_path.stat().st_ino),
            int(sidecar_path.stat().st_size),
            int(sidecar_path.stat().st_mtime_ns),
        )
        indexed = build_or_open_sidecar_index(
            sidecar_path,
            expected_snapshot_id="snapshot:test:advisor",
            expected_trace_checksum="trace:test",
            sidecar_checksum=sidecar_checksum,
            file_fingerprint=fingerprint,
            dictionary_checksum="dict:test",
            rebuild_on_mismatch=True,
        )
        self.assertTrue(indexed.ok, indexed.message)
        gate = DeterministicValidationGate()
        ticket_result = gate.validate_ticket_fast_path(
            sidecar_manifest=manifest,
            request_context={
                "sidecar_path": str(sidecar_path),
                "index_path": str(indexed.data.path),
                "ticket_path": str(sidecar_index_ticket_path_for_source(sidecar_path, index_path=indexed.data.path)),
                "snapshot_id": "snapshot:test:advisor",
                "trace_checksum": "trace:test",
                "dictionary_checksum": "dict:test",
                "sidecar_checksum": sidecar_checksum,
            },
            policy={"ticket_fast_path_enabled": True},
        )
        self.assertTrue(ticket_result.accepted, ticket_result.rejected_reason)
        self.assertIn("ticket_fast_path", ticket_result.execution_plan)

    def test_gate_rejects_unknown_typed_action(self) -> None:
        gate_result = DeterministicValidationGate().validate_advisor_decision(
            advisor_decision=self._advisor_decision_payload(
                actions=[self._runtime_action_payload("hallucinated_action_kind")]
            )
        )

        self.assertFalse(gate_result.accepted)
        self.assertEqual(gate_result.rejected_reason, "ERR-ACTION_UNKNOWN")

    def test_gate_rejects_unsafe_llm_action_before_unknown_check(self) -> None:
        gate_result = DeterministicValidationGate().validate_advisor_decision(
            advisor_decision=self._advisor_decision_payload(
                actions=[
                    self._runtime_action_payload(
                        "shell_script_rewrite",
                        expected_benefit={
                            "runtime_seconds_delta": None,
                            "peak_rss_mb_delta": None,
                            "notes": ["run shell script to rewrite proof_digest and schema migration"],
                        },
                    )
                ]
            )
        )

        self.assertFalse(gate_result.accepted)
        self.assertEqual(gate_result.rejected_reason, "ERR-ACTION_UNSAFE_LLM_OUTPUT")

    def test_gate_rejects_non_none_proof_scope_impact(self) -> None:
        gate_result = DeterministicValidationGate().validate_advisor_decision(
            advisor_decision=self._advisor_decision_payload(
                actions=[
                    self._runtime_action_payload(
                        "cold_preview",
                        proof_scope_impact="expands_proof_scope",
                    )
                ]
            )
        )

        self.assertFalse(gate_result.accepted)
        self.assertEqual(gate_result.rejected_reason, "ERR-ACTION_PROOF_SCOPE_IMPACT")

    def test_gate_rejects_secret_exfiltration_and_overclaim_textual_output(self) -> None:
        cases = (
            (
                "secret_exfiltration",
                [
                    "leak sk-test-should-not-leak",
                    "DEEPSEEK_TEST_SECRET_SHOULD_NOT_LEAK",
                    "/tmp/proof_digest_should_not_be_exposed.json",
                ],
            ),
            (
                "overclaim",
                [
                    "P4 total elapsed reduction is established",
                    "LLM improves parsing correctness",
                    "LLM participates in proof digest generation",
                    "system is formally secure",
                ],
            ),
        )
        for category, notes in cases:
            with self.subTest(category=category):
                gate_result = DeterministicValidationGate().validate_advisor_decision(
                    advisor_decision=self._advisor_decision_payload(
                        actions=[
                            self._runtime_action_payload(
                                "cold_preview",
                                expected_benefit={
                                    "runtime_seconds_delta": None,
                                    "peak_rss_mb_delta": None,
                                    "notes": notes,
                                },
                            )
                        ]
                    )
                )

                self.assertFalse(gate_result.accepted)
                self.assertEqual(gate_result.rejected_reason, "ERR-ACTION_UNSAFE_LLM_OUTPUT")

    def test_gate_rejects_typed_reuse_action_when_artifacts_missing(self) -> None:
        gate_result = DeterministicValidationGate().validate_advisor_decision(
            advisor_decision=self._advisor_decision_payload(
                actions=[self._runtime_action_payload("sidecar_index_reuse")]
            )
        )

        self.assertFalse(gate_result.accepted)
        self.assertEqual(gate_result.rejected_reason, "ERR-ACTION_MISSING_ARTIFACT")

    def test_gate_rejects_typed_reuse_action_when_checksum_mismatches(self) -> None:
        sidecar_path, manifest, sidecar_checksum = self._prepare_sidecar()
        fingerprint = (
            int(sidecar_path.stat().st_dev),
            int(sidecar_path.stat().st_ino),
            int(sidecar_path.stat().st_size),
            int(sidecar_path.stat().st_mtime_ns),
        )
        indexed = build_or_open_sidecar_index(
            sidecar_path,
            expected_snapshot_id="snapshot:test:advisor",
            expected_trace_checksum="trace:test",
            sidecar_checksum=sidecar_checksum,
            file_fingerprint=fingerprint,
            dictionary_checksum="dict:test",
            rebuild_on_mismatch=True,
        )
        self.assertTrue(indexed.ok, indexed.message)

        gate_result = DeterministicValidationGate().validate_advisor_decision(
            advisor_decision=self._advisor_decision_payload(
                actions=[self._runtime_action_payload("sidecar_index_reuse")]
            ),
            sidecar_manifest=manifest,
            request_context={
                "sidecar_path": str(sidecar_path),
                "index_path": str(indexed.data.path),
                "ticket_path": str(sidecar_index_ticket_path_for_source(sidecar_path, index_path=indexed.data.path)),
                "snapshot_id": "snapshot:test:advisor",
                "trace_checksum": "trace:test",
                "dictionary_checksum": "dict:test",
                "sidecar_checksum": "sha256:wrong-sidecar",
            },
        )

        self.assertFalse(gate_result.accepted)
        self.assertEqual(gate_result.rejected_reason, "ERR-ACTION_CHECKSUM_MISMATCH")

    def test_gate_rejects_typed_action_when_policy_denied(self) -> None:
        gate_result = DeterministicValidationGate().validate_advisor_decision(
            advisor_decision=self._advisor_decision_payload(
                actions=[self._runtime_action_payload("streaming_package_write")]
            ),
            policy={
                "advisor_enabled": True,
                "streaming_package_write_enabled": False,
            },
        )

        self.assertFalse(gate_result.accepted)
        self.assertEqual(gate_result.rejected_reason, "ERR-ACTION_POLICY_DENIED")

    def test_gate_accepts_abstain_without_artifact_binding(self) -> None:
        gate_result = DeterministicValidationGate().validate_advisor_decision(
            advisor_decision=self._advisor_decision_payload(
                actions=[
                    self._runtime_action_payload(
                        "abstain",
                        required_artifacts=["telemetry_history"],
                    )
                ],
                abstained=True,
                abstain_reason="telemetry is insufficient",
            )
        )

        self.assertTrue(gate_result.accepted, gate_result.rejected_reason)
        self.assertIn("abstain", gate_result.execution_plan)

    def test_gate_accepts_new_safe_action_space_members(self) -> None:
        for action_kind, expected_step in (
            ("baseline_full_load", "baseline"),
            ("deferred_index_build", "deferred_index_build"),
            ("abstain", "abstain"),
        ):
            gate_result = DeterministicValidationGate().validate_advisor_decision(
                advisor_decision=self._advisor_decision_payload(
                    actions=[self._runtime_action_payload(action_kind)]
                )
            )
            self.assertTrue(gate_result.accepted, action_kind)
            self.assertIn(expected_step, gate_result.execution_plan)

    def test_gate_result_facade_accepts_and_rejects_with_audit_data(self) -> None:
        advisor = RuntimeOptimizationAdvisor({"advisor_mode": "heuristic"})
        gate = DeterministicValidationGate()
        accepted = gate.validate_advisor_decision_result(
            advisor_decision=advisor.evaluate(current_request_features={"input_bytes": 1024})
        )
        rejected = gate_ValidateAdvisorDecision(
            advisor_decision={"recommend_index_reuse_attempt": True},
        )
        ticket_rejected = gate_ValidateTicketFastPath(
            sidecar_manifest={},
            request_context={},
            policy={"ticket_fast_path_enabled": False},
        )
        legacy_gate = gate.validate_advisor_decision(
            advisor_decision={"recommend_index_reuse_attempt": True},
        )

        self.assertTrue(accepted.ok, accepted.message)
        self.assertIsInstance(accepted.data, ValidationGateResult)
        self.assertTrue(accepted.data.accepted)
        self.assertFalse(rejected.ok)
        self.assertEqual(rejected.code, "ERR-SIDECAR_INDEX_MISSING")
        self.assertIsInstance(rejected.data, ValidationGateResult)
        self.assertFalse(rejected.data.accepted)
        self.assertEqual(rejected.data.rejected_reason, "ERR-SIDECAR_INDEX_MISSING")
        self.assertFalse(ticket_rejected.ok)
        self.assertEqual(ticket_rejected.code, "ERR-ADVISOR_REJECTED_BY_GATE")
        self.assertIsInstance(legacy_gate, ValidationGateResult)

    def test_gate_legacy_bool_path_still_accepts_with_empty_proposed_actions(self) -> None:
        gate_result = DeterministicValidationGate().validate_advisor_decision(
            advisor_decision={
                "recommend_streaming_write": True,
                "recommended_schedule": "serial_safe",
                "proposed_actions": [],
            }
        )

        self.assertTrue(gate_result.accepted, gate_result.rejected_reason)
        self.assertEqual(gate_result.execution_plan, ["stream_write", "serial_safe"])

    def test_runtime_load_gate_rejects_reuse_without_ticket(self) -> None:
        rejected = gate_ValidateRuntimeLoadPlan(
            runtime_load_plan=RuntimeLoadPlan(
                plan_version="runtime-load-plan-v1",
                load_mode="warm_reuse",
                index_build_mode="minimal",
                materialize_event_stream=False,
                try_parser_artifact_reuse=False,
                try_sidecar_index_reuse=True,
                background_sidecar_prebuild=False,
                reasons=["unit test"],
            )
        )

        self.assertFalse(rejected.ok)
        self.assertEqual(rejected.code, "ERR-SIDECAR_INDEX_MISSING")
        self.assertIsInstance(rejected.data, ValidationGateResult)
        self.assertFalse(rejected.data.accepted)

    def test_runtime_load_gate_accepts_full_and_cold_preview(self) -> None:
        gate = DeterministicValidationGate()
        full_result = gate.validate_runtime_load_plan(
            runtime_load_plan=RuntimeLoadPlan(
                plan_version="runtime-load-plan-v1",
                load_mode="full",
                index_build_mode="full",
                materialize_event_stream=True,
                try_parser_artifact_reuse=False,
                try_sidecar_index_reuse=False,
                background_sidecar_prebuild=True,
                reasons=["unit test"],
            )
        )
        cold_result = gate.validate_runtime_load_plan(
            runtime_load_plan=RuntimeLoadPlan(
                plan_version="runtime-load-plan-v1",
                load_mode="cold_preview",
                index_build_mode="minimal",
                materialize_event_stream=False,
                try_parser_artifact_reuse=False,
                try_sidecar_index_reuse=False,
                background_sidecar_prebuild=True,
                reasons=["unit test"],
            )
        )

        self.assertTrue(full_result.accepted, full_result.rejected_reason)
        self.assertTrue(cold_result.accepted, cold_result.rejected_reason)
        self.assertIn("background_sidecar_prebuild", full_result.execution_plan)
        self.assertIn("background_sidecar_prebuild", cold_result.execution_plan)

    def test_advisor_trace_and_telemetry_serialization(self) -> None:
        advisor = RuntimeOptimizationAdvisor({"advisor_mode": "heuristic"})
        decision = advisor.evaluate(
            telemetry_history=[
                {
                    "input_bytes": 1024,
                    "sidecar_bytes": 4096,
                    "sidecar_row_count": 32,
                    "peak_rss_mb": 256.0,
                    "runtime_seconds": 12.5,
                }
            ],
            current_request_features={"input_bytes": 2048, "sidecar_bytes": 8192},
            sidecar_ticket={"row_count": 128, "sidecar_bytes": 8192},
        )
        trace = build_advisor_trace(
            request_id="job-1",
            feature_snapshot={"input_bytes": 2048},
            decision=decision,
            gate_result={"accepted": True},
            telemetry_refs=["sha256:proof"],
            started_at=None,
            agent_contract_ref={"path": "control/runtime_advisor_agent_contract.json", "job_id": "job-1"},
        )
        telemetry = TelemetryReportAgent().build_record(
            run_id="run-1",
            dataset_id="dataset-1",
            input_bytes=2048,
            sidecar_bytes=8192,
            sidecar_row_count=128,
            sidecar_bytes_scanned=0,
            sidecar_validate_seconds=0.0,
            index_build_open_seconds=0.0,
            index_reused=True,
            index_rebuilt=False,
            package_write_seconds=1.0,
            runtime_seconds=2.0,
            peak_rss_mb=256.0,
            proof_hash="sha256:proof",
            closure_mode="exact",
            advisor_overhead_seconds=0.01,
        )
        self.assertTrue(decision.recommend_index_reuse_attempt)
        self.assertNotIn("proof_hash", trace.to_dict()["decision"])
        self.assertEqual(trace.to_dict()["agent_contract_ref"]["path"], "control/runtime_advisor_agent_contract.json")
        self.assertEqual(trace.to_dict()["agent_contract_ref"]["job_id"], "job-1")
        self.assertEqual(telemetry.to_dict()["proof_hash"], "sha256:proof")
        self.assertEqual(telemetry.to_dict()["input_bytes"], 2048)
        self.assertEqual(telemetry.to_dict()["sidecar_row_count"], 128)
