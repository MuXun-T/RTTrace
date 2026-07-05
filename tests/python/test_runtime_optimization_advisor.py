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
                payload = {
                    "decision_version": decision_version,
                    "advisor_mode": "openai_structured",
                    "model_ref": "gpt-test",
                    "model_checksum": None,
                    "feature_snapshot_hash": feature_snapshot_hash,
                    "recommend_index_prebuild": True,
                    "recommend_index_reuse_attempt": False,
                    "recommend_streaming_write": True,
                    "recommended_schedule": "sidecar_first",
                    "predicted_runtime_seconds": 12.0,
                    "predicted_peak_rss_mb": 256.0,
                    "risk_level": "medium",
                    "reasons": ["mock structured advisor decision"],
                }
                return OpenAIAdvisorClientResult(
                    decision_payload=payload,
                    response_id="resp-test",
                    model="gpt-test",
                    usage={},
                    latency_seconds=0.0,
                    request_payload={},
                )

        decision = RuntimeOptimizationAdvisor(
            {"advisor_mode": "openai_structured", "openai_client": FakeOpenAIAdvisorClient()}
        ).evaluate(
            current_request_features={"input_bytes": 512 * 1024 * 1024, "sidecar_bytes": 1024}
        )

        self.assertEqual(decision.advisor_mode, "openai_structured")
        self.assertEqual(decision.model_ref, "gpt-test")
        self.assertTrue(decision.recommend_index_prebuild)
        self.assertEqual(decision.recommended_schedule, "sidecar_first")

    def test_openai_client_uses_structured_outputs_and_redacted_features(self) -> None:
        captured: dict[str, object] = {}

        def transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> tuple[int, dict[str, str], bytes]:
            request_payload = json.loads(body.decode("utf-8"))
            captured["url"] = url
            captured["headers"] = headers
            captured["request_payload"] = request_payload
            decision_payload = {
                "decision_version": "runtime-advisor-decision-v1",
                "advisor_mode": "openai_structured",
                "model_ref": "gpt-test",
                "model_checksum": None,
                "feature_snapshot_hash": "sha256:feature",
                "recommend_index_prebuild": False,
                "recommend_index_reuse_attempt": False,
                "recommend_streaming_write": False,
                "recommended_schedule": "serial_safe",
                "predicted_runtime_seconds": 1.0,
                "predicted_peak_rss_mb": 128.0,
                "risk_level": "low",
                "reasons": ["redacted structured output"],
            }
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
            decision_schema=load_schema("advisor_decision.schema.json"),
            feature_snapshot_hash="sha256:feature",
            decision_version="runtime-advisor-decision-v1",
        )

        self.assertEqual(result.decision_payload["advisor_mode"], "openai_structured")
        request_payload = captured["request_payload"]
        self.assertIsInstance(request_payload, dict)
        user_text = request_payload["input"][1]["content"][0]["text"]
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
            request_payload = json.loads(body.decode("utf-8"))
            feature_snapshot_hash = json.loads(request_payload["input"][1]["content"][0]["text"])["feature_snapshot_hash"]
            decision_payload = {
                "decision_version": "runtime-advisor-decision-v1",
                "advisor_mode": "openai_structured",
                "model_ref": "gpt-test",
                "model_checksum": None,
                "feature_snapshot_hash": feature_snapshot_hash,
                "recommend_index_prebuild": False,
                "recommend_index_reuse_attempt": False,
                "recommend_streaming_write": False,
                "recommended_schedule": "serial_safe",
                "predicted_runtime_seconds": 1.0,
                "predicted_peak_rss_mb": 128.0,
                "risk_level": "not-a-risk-level",
                "reasons": ["invalid schema response"],
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
            decision_payload = {
                "decision_version": "runtime-advisor-decision-v1",
                "advisor_mode": "openai_structured",
                "model_ref": "qwen-test",
                "model_checksum": None,
                "feature_snapshot_hash": "sha256:chat-feature",
                "recommend_index_prebuild": False,
                "recommend_index_reuse_attempt": False,
                "recommend_streaming_write": False,
                "recommended_schedule": "serial_safe",
                "predicted_runtime_seconds": 2.0,
                "predicted_peak_rss_mb": 256.0,
                "risk_level": "medium",
                "reasons": ["chat compatible structured output"],
            }
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
            decision_schema=load_schema("advisor_decision.schema.json"),
            feature_snapshot_hash="sha256:chat-feature",
            decision_version="runtime-advisor-decision-v1",
        )

        self.assertEqual(result.backend, "openai_compatible_chat")
        self.assertEqual(result.decision_payload["model_ref"], "qwen-test")
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

    def test_openai_compatible_chat_backend_accepts_fenced_json_with_surrounding_text(self) -> None:
        def transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> tuple[int, dict[str, str], bytes]:
            decision_payload = {
                "decision_version": "runtime-advisor-decision-v1",
                "advisor_mode": "openai_structured",
                "model_ref": "deepseek-v4-flash",
                "model_checksum": None,
                "feature_snapshot_hash": "sha256:fenced-feature",
                "recommend_index_prebuild": False,
                "recommend_index_reuse_attempt": False,
                "recommend_streaming_write": True,
                "recommended_schedule": "serial_safe",
                "predicted_runtime_seconds": 1.0,
                "predicted_peak_rss_mb": 128.0,
                "risk_level": "low",
                "reasons": ["fenced JSON compatible output"],
            }
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

        result = LLMAdvisorClient(
            api_key="provider-key",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            enabled=True,
            backend="openai_compatible_chat",
            transport=transport,
            chat_completions_path="/chat/completions",
        ).request_advisor_decision(
            feature_payload={"input_bytes": 1024},
            decision_schema=load_schema("advisor_decision.schema.json"),
            feature_snapshot_hash="sha256:fenced-feature",
            decision_version="runtime-advisor-decision-v1",
        )

        self.assertEqual(result.decision_payload["advisor_mode"], "openai_structured")
        self.assertEqual(result.decision_payload["model_ref"], "deepseek-v4-flash")

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
            decision_payload = {
                "decision_version": "runtime-advisor-decision-v1",
                "advisor_mode": "openai_structured",
                "model_ref": "deepseek-v4-pro",
                "model_checksum": None,
                "feature_snapshot_hash": "sha256:deepseek-feature",
                "recommend_index_prebuild": False,
                "recommend_index_reuse_attempt": False,
                "recommend_streaming_write": False,
                "recommended_schedule": "serial_safe",
                "predicted_runtime_seconds": 1.25,
                "predicted_peak_rss_mb": 128.0,
                "risk_level": "low",
                "reasons": ["deepseek compatible structured output"],
            }
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
            decision_schema=load_schema("advisor_decision.schema.json"),
            feature_snapshot_hash="sha256:deepseek-feature",
            decision_version="runtime-advisor-decision-v1",
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
                decision_schema=load_schema("advisor_decision.schema.json"),
                feature_snapshot_hash="sha256:deepseek-disabled",
                decision_version="runtime-advisor-decision-v1",
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
                decision_schema=load_schema("advisor_decision.schema.json"),
                feature_snapshot_hash="sha256:deepseek-provider-disabled",
                decision_version="runtime-advisor-decision-v1",
            )
        self.assertEqual(raised.exception.reason, "openai_unconfigured")
        self.assertEqual(calls["count"], 0)

    def test_llm_client_retries_transient_transport_failure(self) -> None:
        calls = {"count": 0}

        def transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> tuple[int, dict[str, str], bytes]:
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.URLError("temporary dns failure")
            decision_payload = {
                "decision_version": "runtime-advisor-decision-v1",
                "advisor_mode": "openai_structured",
                "model_ref": "deepseek-v4-pro",
                "model_checksum": None,
                "feature_snapshot_hash": "sha256:retry-feature",
                "recommend_index_prebuild": False,
                "recommend_index_reuse_attempt": False,
                "recommend_streaming_write": False,
                "recommended_schedule": "serial_safe",
                "predicted_runtime_seconds": 1.0,
                "predicted_peak_rss_mb": 128.0,
                "risk_level": "low",
                "reasons": ["retry structured output"],
            }
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
            decision_schema=load_schema("advisor_decision.schema.json"),
            feature_snapshot_hash="sha256:retry-feature",
            decision_version="runtime-advisor-decision-v1",
        )

        self.assertEqual(calls["count"], 2)
        self.assertEqual(result.decision_payload["advisor_mode"], "openai_structured")

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
