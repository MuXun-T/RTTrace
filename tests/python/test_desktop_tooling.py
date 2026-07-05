from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DesktopToolingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo_root = Path(__file__).resolve().parents[2]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _formal_input_fixture(
        *,
        size_bytes: int,
        verified: bool,
        input_provenance: str = "real_external",
    ) -> dict[str, object]:
        return {
            "baseline_size_bytes": size_bytes,
            "candidate_size_bytes": size_bytes,
            "baseline_sha256": "baseline-sha256",
            "candidate_sha256": "candidate-sha256",
            "baseline_parse_ok": True,
            "candidate_parse_ok": True,
            "baseline_event_count": 576,
            "candidate_event_count": 576,
            "baseline_input_provenance": input_provenance,
            "candidate_input_provenance": input_provenance,
            "input_provenance": input_provenance,
            "formal_1gb_verified": verified,
        }

    @classmethod
    def _desktop_perf_acceptance_fixture(
        cls,
        *,
        platform_name: str,
        input_bytes: int,
        limitations: list[str],
        first_screen_seconds: float,
        peak_memory_mb: float,
        formal_input_verified: bool,
        input_provenance: str = "real_external",
    ) -> dict[str, object]:
        return {
            "platform": platform_name,
            "input_bytes": input_bytes,
            "input_scope": f"{platform_name}_provided_{'formal' if formal_input_verified else 'fixture'}",
            "acceptance_scope": "perf_only",
            "skipped_steps": ["compare", "repeat_export", "repro_repeat", "short_soak"],
            "formal_input": cls._formal_input_fixture(
                size_bytes=input_bytes,
                verified=formal_input_verified,
                input_provenance=input_provenance,
            ),
            "limitations": limitations,
            "first_screen_lt_10s": {
                "observed_seconds": first_screen_seconds,
                "threshold_seconds": 10.0,
                "pass": True,
            },
            "peak_memory_lt_4gb": {
                "observed_mb": peak_memory_mb,
                "threshold_mb": 4096.0,
                "pass": True,
                "source": "linux_peak_rss_mb" if platform_name == "linux" else "windows_peak_working_set_mb",
            },
            "first_screen_peak_memory_lt_4gb": {
                "observed_mb": peak_memory_mb,
                "threshold_mb": 4096.0,
                "pass": True,
                "source": "python_peak_alloc_mb",
            },
        }

    def _run_summary_with_render_fps(
        self,
        *,
        render_fps_payload: dict[str, object],
    ) -> dict[str, object]:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        desktop_perf = self.root / "desktop-perf.json"
        render_fps = self.root / "render-fps.json"
        output = self.root / "summary.json"

        acceptance.write_text(
            json.dumps(
                {
                    "consistency": {
                        "repeat_export_consistent": True,
                        "repro_repeat_consistent": True,
                        "compare_scope_consistent": True,
                        "short_soak_passed": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_preflight.write_text(
            json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_env.write_text(
            json.dumps({"error": None}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_baseline",
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.12,
                        "task_state_preview_seconds": 0.12,
                        "event_table_first_page_seconds": 0.03,
                        "lod2_first_window_seconds": 0.02,
                        "peak_memory_mb": 18.5,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        render_fps.write_text(
            json.dumps(render_fps_payload, ensure_ascii=False),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--render-fps",
                str(render_fps),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        return json.loads(output.read_text(encoding="utf-8"))

    def test_desktop_validation_preflight_emits_report(self) -> None:
        report_path = self.root / "desktop-preflight.json"
        proc = subprocess.run(
            [
                sys.executable,
                "tool/desktop_validation_preflight.py",
                "--output",
                str(report_path),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn(proc.returncode, {0, 1})
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertIn("platform", payload)
        self.assertIn("packages", payload)
        self.assertIn("ready_for_runtime_smoke", payload)
        self.assertIn("large_input_preflight", payload)

    def test_desktop_validation_preflight_reports_large_input_blockers(self) -> None:
        report_path = self.root / "desktop-preflight-large.json"
        scratch_dir = self.root / "scratch"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [
                sys.executable,
                "tool/desktop_validation_preflight.py",
                "--baseline-input",
                str(self.repo_root / "docs/validation-fixture/baseline.trace"),
                "--candidate-input",
                str(self.repo_root / "docs/validation-fixture/candidate.trace"),
                "--scratch-dir",
                str(scratch_dir),
                "--load-timeout-s",
                "30",
                "--output",
                str(report_path),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        large_input = payload["large_input_preflight"]
        self.assertTrue(large_input["enabled"])
        self.assertFalse(large_input["ready_for_large_input_perf"])
        self.assertIn("formal_input_lt_1gb", large_input["blocking_reasons"])
        self.assertIn("load_timeout_too_small", large_input["blocking_reasons"])

    def test_build_formal_large_input_tool_produces_loadable_trace(self) -> None:
        output_path = self.root / "formal-large.trace"
        report_path = self.root / "formal-large-report.json"
        proc = subprocess.run(
            [
                sys.executable,
                "tool/build_formal_large_input.py",
                "--seed-input",
                str(self.repo_root / "docs/validation-fixture/baseline.trace"),
                "--output",
                str(output_path),
                "--target-size-bytes",
                str(4 * 1024 * 1024),
                "--filler-chunk-mb",
                "1",
                "--report",
                str(report_path),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(output_path.stat().st_size, 4 * 1024 * 1024)
        self.assertEqual(payload["output_size_bytes"], output_path.stat().st_size)
        self.assertGreater(payload["appended_chunk_count"], 0)

        from parser import decode_trace

        decoded = decode_trace(output_path)
        self.assertTrue(decoded.ok, decoded.message)

    def test_run_desktop_long_soak_emits_both_artifacts(self) -> None:
        soak_path = self.root / "desktop-soak.json"
        irrecoverable_path = self.root / "irrecoverable.json"
        proc = subprocess.run(
            [
                sys.executable,
                "tool/run_desktop_long_soak.py",
                "--input",
                str(self.repo_root / "docs/validation-fixture/baseline.trace"),
                "--duration-s",
                "0.1",
                "--output",
                str(soak_path),
                "--irrecoverable-output",
                str(irrecoverable_path),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        soak = json.loads(soak_path.read_text(encoding="utf-8"))
        irrecoverable = json.loads(irrecoverable_path.read_text(encoding="utf-8"))
        self.assertEqual(soak["status"], "ok")
        self.assertGreater(soak["duration_sec"], 0.0)
        self.assertFalse(irrecoverable["irrecoverable_error_detected"])
        self.assertIn(str(soak_path), irrecoverable["evidence_paths"])

    def test_check_desktop_env_emits_json_report_even_on_missing_runtime(self) -> None:
        report_path = self.root / "desktop-env-report.json"
        proc = subprocess.run(
            [
                sys.executable,
                "tool/check_desktop_env.py",
                "--output",
                str(report_path),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn(proc.returncode, {0, 1})
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertIn("platform", payload)
        self.assertIn("python_version", payload)
        self.assertIn("qt_qpa_platform", payload)
        self.assertIn("pyside6", payload)
        self.assertIn("pyqtgraph", payload)

    def test_desktop_runtime_report_emits_json_report(self) -> None:
        report_path = self.root / "desktop-runtime.json"
        proc = subprocess.run(
            [
                sys.executable,
                "tool/run_desktop_runtime_report.py",
                "--output",
                str(report_path),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn(proc.returncode, {0, 1})
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertIn("module", payload)
        self.assertIn("command", payload)
        self.assertIn("tests_run", payload)
        self.assertIn("executed", payload)
        self.assertIn("skipped", payload)
        self.assertIn("runtime_contract_ok", payload)

    def test_run_pytest_report_emits_json_report(self) -> None:
        report_path = self.root / "pytest-report.json"
        proc = subprocess.run(
            [
                sys.executable,
                "tool/run_pytest_report.py",
                "--output",
                str(report_path),
                "--pytest-args",
                "tests/python/test_desktop_runtime.py",
                "-q",
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn(proc.returncode, {0, 1}, proc.stderr or proc.stdout)
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["args"], ["tests/python/test_desktop_runtime.py", "-q"])
        self.assertIn("command", payload)
        self.assertIn("-m pytest tests/python/test_desktop_runtime.py -q", payload["command"])
        self.assertIn("generated_at", payload)
        self.assertIn("platform", payload)
        self.assertIn("python_version", payload)
        self.assertIn("pytest_version", payload)
        self.assertIn("executed", payload)
        self.assertIn("all_skipped", payload)

        if payload["pytest_version"] is None:
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(payload["tests_collected"], 0)
            self.assertEqual(payload["executed"], 0)
            self.assertFalse(payload["all_skipped"])
            self.assertFalse(payload["successful"])
            self.assertIn("pytest", payload["stderr"].lower())
            return

        self.assertGreater(payload["tests_collected"], 0)
        self.assertEqual(payload["failed"], 0)
        self.assertEqual(payload["errors"], 0)
        self.assertTrue(payload["successful"])
        self.assertEqual(
            payload["executed"],
            payload["passed"] + payload["failed"] + payload["errors"] + payload["xfailed"] + payload["xpassed"],
        )
        selected = max(0, payload["tests_collected"] - payload["deselected"])
        self.assertEqual(
            payload["all_skipped"],
            selected > 0 and payload["executed"] == 0 and payload["skipped"] == selected,
        )
        if payload["all_skipped"]:
            self.assertEqual(payload["passed"], 0)
            self.assertGreater(payload["skipped"], 0)
        else:
            self.assertGreater(payload["executed"], 0)

    def test_run_pytest_report_emits_json_when_pytest_missing(self) -> None:
        report_path = self.root / "pytest-report-missing.json"
        proc = subprocess.run(
            [
                sys.executable,
                "-S",
                "tool/run_pytest_report.py",
                "--output",
                str(report_path),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr or proc.stdout)
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertIsNone(payload["pytest_version"])
        self.assertEqual(payload["tests_collected"], 0)
        self.assertEqual(payload["executed"], 0)
        self.assertFalse(payload["all_skipped"])
        self.assertFalse(payload["successful"])
        self.assertEqual(payload["exit_code"], 1)
        self.assertIn("pytest", payload["stderr"].lower())

    def test_validation_summary_script_combines_reports(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        desktop_perf = self.root / "desktop-perf.json"
        output = self.root / "summary.json"

        acceptance.write_text(
            json.dumps(
                {
                    "consistency": {
                        "repeat_export_consistent": True,
                        "repro_repeat_consistent": True,
                        "compare_scope_consistent": True,
                        "short_soak_passed": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_preflight.write_text(
            json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_env.write_text(
            json.dumps({"error": None}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_baseline",
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.12,
                        "task_state_preview_seconds": 0.12,
                        "event_table_first_page_seconds": 0.03,
                        "lod2_first_window_seconds": 0.02,
                        "peak_memory_mb": 18.5,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(payload["local_smoke_ready"])
        self.assertTrue(payload["desktop_preflight_ready"])
        self.assertTrue(payload["desktop_env_ready"])
        self.assertTrue(payload["desktop_runtime_ready"])
        self.assertIsNone(payload["desktop_runtime_error"])
        self.assertEqual(payload["desktop_runtime_command"], "python -m unittest tests.python.test_desktop_runtime")
        self.assertEqual(payload["desktop_runtime_executed"], 7)
        self.assertEqual(payload["desktop_runtime_skipped"], 0)
        self.assertTrue(payload["desktop_perf_local_ready"])
        self.assertEqual(payload["desktop_perf_profile"], "medium")
        self.assertEqual(payload["artifacts"]["desktop_runtime"], str(desktop_runtime))
        self.assertEqual(payload["artifacts"]["desktop_perf"], str(desktop_perf))
        self.assertIn("external_status", payload)
        self.assertEqual(payload["external_status"]["windows_linux_consistency"]["status"], "linux_evidence_only")
        self.assertIn("desktop_1gb_first_screen_lt_10s", payload["external_pending"])
        self.assertIn("nfr_perf_status", payload)
        self.assertEqual(payload["nfr_perf_status"]["NFR-PERF-05"]["status"], "missing_artifact")

    def test_validation_summary_can_include_dense_blocker_artifact(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        desktop_perf = self.root / "desktop-perf.json"
        desktop_dense_blocker = self.root / "desktop-dense-blocker.json"
        output = self.root / "summary.json"

        acceptance.write_text(
            json.dumps(
                {
                    "consistency": {
                        "repeat_export_consistent": True,
                        "repro_repeat_consistent": True,
                        "compare_scope_consistent": True,
                        "short_soak_passed": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_preflight.write_text(
            json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_env.write_text(
            json.dumps({"error": None}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_baseline",
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.12,
                        "task_state_preview_seconds": 0.12,
                        "event_table_first_page_seconds": 0.03,
                        "lod2_first_window_seconds": 0.02,
                        "peak_memory_mb": 18.5,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_dense_blocker.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "blocked_stage": "export_write",
                    "last_completed_stage": "lod2_query",
                    "load_breakdown": {"last_completed_stage": "idx_Build"},
                    "scratch": {"fs_type": "ext4"},
                    "memory_guard": {"limit_mb": 3200.0},
                    "error": {
                        "type": "MemoryError",
                        "traceback": (
                            "Traceback (most recent call last):\n"
                            "  File \"desktop/services.py\", line 3293, in _source_alignment_offsets\n"
                            "  File \"parser/codec.py\", line 949, in feed\n"
                            "    chunk_payload = bytes(self.buffer[CHUNK_HEADER_STRUCT.size : total_bytes])\n"
                            "MemoryError\n"
                        ),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--desktop-dense-blocker",
                str(desktop_dense_blocker),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["artifacts"]["desktop_dense_blocker"], str(desktop_dense_blocker))
        self.assertEqual(payload["desktop_dense_blocker"]["blocked_stage"], "export_write")
        self.assertEqual(payload["desktop_dense_blocker"]["last_completed_stage"], "lod2_query")
        self.assertEqual(payload["desktop_dense_blocker"]["load_last_completed_stage"], "idx_Build")
        self.assertTrue(payload["desktop_dense_blocker"]["alignment_rescan_in_traceback"])
        self.assertTrue(payload["desktop_dense_blocker"]["codec_chunk_copy_in_traceback"])
        self.assertFalse(payload["desktop_dense_blocker"]["normalize_rebuild_bundle_reload_in_traceback"])

    def test_validation_summary_dense_blocker_note_tracks_export_normalize_sidecar(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        desktop_perf = self.root / "desktop-perf.json"
        desktop_dense_blocker = self.root / "desktop-dense-blocker.json"
        output = self.root / "summary.json"

        acceptance.write_text(
            json.dumps(
                {
                    "consistency": {
                        "repeat_export_consistent": True,
                        "repro_repeat_consistent": True,
                        "compare_scope_consistent": True,
                        "short_soak_passed": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_preflight.write_text(
            json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_env.write_text(
            json.dumps({"error": None}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_baseline",
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.12,
                        "task_state_preview_seconds": 0.12,
                        "event_table_first_page_seconds": 0.03,
                        "lod2_first_window_seconds": 0.02,
                        "peak_memory_mb": 18.5,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_dense_blocker.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "blocked_stage": "export_normalize_sidecar",
                    "last_completed_stage": "export_write",
                    "load_breakdown": {"last_completed_stage": "idx_Build"},
                    "scratch": {"fs_type": "ext4"},
                    "memory_guard": {"limit_mb": 3200.0},
                    "error": {
                        "type": "MemoryError",
                        "traceback": (
                            "Traceback (most recent call last):\n"
                            "  File \"tool/run_acceptance_baseline.py\", line 923, in _run_export_sidecar\n"
                            "    normalized = export.export_NormalizePackage(str(output_dir))\n"
                            "  File \"desktop/services.py\", line 4285, in export_NormalizePackage\n"
                            "    loaded = _load_validated_package(path_or_stream)\n"
                            "  File \"desktop/services.py\", line 1086, in _load_validated_package\n"
                            "    validation = _validate_package_refs(target, meta, manifest)\n"
                            "  File \"desktop/services.py\", line 1069, in _validate_package_refs\n"
                            "    raw_bundle = json_load(rebuild_path)\n"
                            "  File \"spec/io.py\", line 23, in json_load\n"
                            "    return json.load(handle)\n"
                            "MemoryError\n"
                        ),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--desktop-dense-blocker",
                str(desktop_dense_blocker),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["desktop_dense_blocker"]["blocked_stage"], "export_normalize_sidecar")
        self.assertEqual(payload["desktop_dense_blocker"]["last_completed_stage"], "export_write")
        self.assertTrue(payload["desktop_dense_blocker"]["normalize_rebuild_bundle_reload_in_traceback"])
        self.assertEqual(
            payload["desktop_dense_blocker"]["notes"],
            "dense blocker 已前移到 export_normalize_sidecar；export_write 已通过，traceback 指向 json_load(rebuild_bundle.json) 回读链路。",
        )

    def test_checked_in_runtime_artifacts_match_current_suite_size(self) -> None:
        runtime_report = json.loads(
            (self.repo_root / "docs/desktop_runtime_stage5_linux_venv.json").read_text(encoding="utf-8")
        )
        summary_report = json.loads(
            (self.repo_root / "docs/final_validation_status_20260314.json").read_text(encoding="utf-8")
        )
        readiness_text = (
            self.repo_root / "docs/final_acceptance_readiness_20260314.md"
        ).read_text(encoding="utf-8")

        suite = unittest.defaultTestLoader.loadTestsFromName("tests.python.test_desktop_runtime")
        expected_cases = suite.countTestCases()

        self.assertEqual(runtime_report["tests_run"], expected_cases)
        self.assertEqual(runtime_report["executed"], expected_cases)
        self.assertEqual(runtime_report["skipped"], 0)
        self.assertTrue(runtime_report["runtime_contract_ok"])
        self.assertIn(f"Ran {expected_cases} tests", runtime_report["output"])

        self.assertEqual(summary_report["desktop_runtime_executed"], expected_cases)
        self.assertEqual(summary_report["desktop_runtime_skipped"], 0)
        self.assertIn("docs/desktop_runtime_stage5_linux_venv.json", readiness_text)

    def test_checked_in_summary_tracks_current_linux_formal_perf_contract(self) -> None:
        summary_report = json.loads(
            (self.repo_root / "docs/final_validation_status_20260314.json").read_text(encoding="utf-8")
        )
        readiness_text = (
            self.repo_root / "docs/final_acceptance_readiness_20260314.md"
        ).read_text(encoding="utf-8")

        self.assertEqual(
            summary_report["artifacts"]["desktop_perf"],
            "docs/desktop_perf_acceptance_20260325_linux_formal_1gb_opaque.json",
        )
        self.assertNotIn("desktop_dense_blocker", summary_report["artifacts"])
        self.assertNotIn("desktop_dense_blocker", summary_report)
        self.assertEqual(summary_report["desktop_perf_profile"], "medium")
        self.assertTrue(summary_report["desktop_perf_linux_first_screen_pass"])
        self.assertTrue(summary_report["desktop_perf_linux_peak_memory_pass"])
        self.assertEqual(
            summary_report["nfr_perf_status"]["NFR-PERF-06"]["artifacts"],
            ["docs/desktop_perf_acceptance_20260325_linux_formal_1gb_opaque.json"],
        )
        self.assertIn("desktop_perf_acceptance_20260325_linux_formal_1gb_opaque.json", readiness_text)
        self.assertIn("`external_pending = []`", readiness_text)
        self.assertIn("NFR-STAB-01` 项目级正式关闭口径", readiness_text)
        self.assertNotIn("phase5_ready = true", readiness_text)
        self.assertNotIn("public_rtos_1gb_dense_desktop_perf_blocker_20260324_phase3.json", readiness_text)
        self.assertTrue(summary_report["windows_runtime_ready"])
        self.assertTrue(summary_report["desktop_perf_windows_formal_ready"])
        self.assertTrue(summary_report["acceptance_compare_match"])
        self.assertTrue(summary_report["package_compare_match"])
        self.assertEqual(
            summary_report["external_status"]["windows_linux_consistency"]["status"],
            "closed",
        )
        self.assertIn("acceptance_compare_windows_linux.json", readiness_text)
        self.assertIn("package_compare_windows_linux.json", readiness_text)
        self.assertIn("docs/desktop_runtime_windows.json", readiness_text)
        self.assertIn("`docs/pytest_windows.json` 继续作为 Windows 全量 `python -m pytest tests/python -q` 的 canonical audited artifact", readiness_text)
        self.assertIn("Windows `pytest` audited artifact 已回传", readiness_text)
        self.assertNotIn("进入 phase7", readiness_text)
        self.assertNotIn("Windows 产物回传后", readiness_text)
        self.assertEqual(
            summary_report["external_status"]["long_duration_stability"]["status"],
            "closed",
        )
        self.assertNotIn("long_duration_stability", summary_report["external_pending"])
        self.assertEqual(summary_report["nfr_perf_status"]["NFR-PERF-01"]["status"], "closed")
        self.assertEqual(
            summary_report["nfr_perf_status"]["NFR-PERF-01"]["per_platform"]["linux"]["status"],
            "closed",
        )
        self.assertEqual(
            summary_report["nfr_perf_status"]["NFR-PERF-01"]["per_platform"]["windows"]["status"],
            "closed",
        )
        self.assertEqual(summary_report["nfr_perf_status"]["NFR-PERF-05"]["status"], "closed")
        self.assertEqual(
            summary_report["external_pending"],
            [],
        )

    def test_checked_in_traceability_matrix_has_phase7_status_layer(self) -> None:
        matrix = json.loads(
            (self.repo_root / "docs/traceability_matrix.json").read_text(encoding="utf-8")
        )

        self.assertEqual(matrix["meta"]["snapshot_date"], "2026-03-29")
        self.assertEqual(matrix["requirements"]["FR-COL-02"]["status"], "closed")
        self.assertEqual(
            matrix["requirements"]["FR-COL-02"]["evidence_level"],
            "implemented_and_regressed",
        )
        self.assertEqual(matrix["requirements"]["FR-COL-02"]["remaining"], [])
        self.assertIn(
            "docs/collector_perf_baseline_20260316_linux.json",
            matrix["requirements"]["FR-COL-02"]["authoritative_artifacts"],
        )
        self.assertEqual(matrix["non_functional"]["NFR-PERF-03"]["status"], "closed")
        self.assertEqual(
            matrix["non_functional"]["NFR-PERF-03"]["evidence_level"],
            "linux_formal_closed",
        )
        self.assertEqual(
            matrix["non_functional"]["NFR-CONS-01"]["status"],
            "closed",
        )
        self.assertEqual(
            matrix["non_functional"]["NFR-CONS-01"]["evidence_level"],
            "closed_with_authoritative_artifacts",
        )
        self.assertEqual(
            matrix["non_functional"]["NFR-CONS-01"]["remaining"],
            [],
        )
        self.assertIn(
            "docs/package_compare_windows_linux.json",
            matrix["non_functional"]["NFR-CONS-01"]["authoritative_artifacts"],
        )
        self.assertEqual(matrix["non_functional"]["NFR-PERF-01"]["status"], "closed")
        self.assertEqual(
            matrix["non_functional"]["NFR-PERF-01"]["evidence_level"],
            "closed_with_authoritative_artifacts",
        )
        self.assertEqual(
            matrix["non_functional"]["NFR-PERF-01"]["remaining"],
            [],
        )
        self.assertIn(
            "docs/collector_perf_baseline_windows.json",
            matrix["non_functional"]["NFR-PERF-01"]["authoritative_artifacts"],
        )
        self.assertEqual(
            matrix["non_functional"]["NFR-PERF-05"]["evidence_level"],
            "closed_with_authoritative_artifacts",
        )
        self.assertEqual(matrix["non_functional"]["NFR-PERF-05"]["status"], "closed")
        self.assertEqual(matrix["non_functional"]["NFR-PERF-05"]["remaining"], [])
        self.assertIn(
            "docs/render_fps_report_windows_formal_gui.json",
            matrix["non_functional"]["NFR-PERF-05"]["authoritative_artifacts"],
        )
        self.assertEqual(matrix["non_functional"]["NFR-STAB-01"]["status"], "closed")
        self.assertEqual(matrix["non_functional"]["NFR-STAB-01"]["remaining"], [])

    def test_checked_in_external_checklist_is_phase0_frozen(self) -> None:
        checklist_text = (
            self.repo_root / "docs/external_validation_checklist_20260314.md"
        ).read_text(encoding="utf-8")

        self.assertIn("Final 收口同步版", checklist_text)
        self.assertIn("external_pending", checklist_text)
        self.assertIn("external_pending = []", checklist_text)
        self.assertIn("nfr_perf_status.NFR-PERF-01", checklist_text)
        self.assertIn("nfr_perf_status.NFR-PERF-05", checklist_text)
        self.assertIn("python3 -m pytest tests/python -q", checklist_text)
        self.assertIn("python tool/run_pytest_report.py --output docs/pytest_windows.json", checklist_text)
        self.assertIn("内部固定执行 `tests/python -q`", checklist_text)
        self.assertIn("`docs/desktop_runtime_windows.json` 仅为 runtime unittest 报告", checklist_text)
        self.assertIn("`docs/pytest_windows.json` 用于 Windows 全量 pytest audited 留档", checklist_text)
        self.assertIn("docs/package_windows_formal", checklist_text)
        self.assertIn("docs/package_linux_formal", checklist_text)
        self.assertNotIn(
            "当前对 `desktop_1gb_first_screen_lt_10s`、`desktop_peak_memory_lt_4gb` 与 `NFR-PERF-06` 的真实口径均为 `pending_external`",
            checklist_text,
        )

    def test_checked_in_historical_docs_are_snapshot_scoped(self) -> None:
        iteration_text = (
            self.repo_root / "docs/iteration_status_20260314.md"
        ).read_text(encoding="utf-8")
        closure_plan_text = (
            self.repo_root / "docs/项目收口执行计划_20260326.md"
        ).read_text(encoding="utf-8")

        self.assertIn("阶段快照", iteration_text)
        self.assertIn("当前项目状态以 `docs/final_validation_status_20260314.json` 为准", iteration_text)
        self.assertIn("阶段执行快照", closure_plan_text)
        self.assertIn("当前项目状态以 `docs/final_validation_status_20260314.json` 为唯一 SoT", closure_plan_text)
        self.assertNotIn(
            "当前 `external_pending = [NFR-PERF-01, NFR-PERF-05]`",
            closure_plan_text,
        )

    def test_validation_summary_consumes_render_fps_report(self) -> None:
        payload = self._run_summary_with_render_fps(
            render_fps_payload={
                "generated_at": "2026-03-19T00:00:00+00:00",
                "status": "ok",
                "environment": {
                    "platform": "Linux",
                    "python_version": "3.10.12",
                    "qt_qpa_platform": "offscreen",
                },
                "dependencies": {"pyside6": "6.x", "pyqtgraph": "0.x"},
                "workload": {"tile_count": 100000, "iterations": 120, "mode": "bargraph_batched"},
                "thresholds": {"target_fps": 30.0, "frame_budget_ms": 16.0},
                "metrics": {"fps_p95": 80.0},
                "verdict": {"pass": True},
                "limitations": ["synthetic_workload", "offscreen"],
            }
        )
        self.assertIn("nfr_perf_status", payload)
        node = payload["nfr_perf_status"]["NFR-PERF-05"]
        self.assertEqual(node["status"], "linux_evidence_only")
        self.assertIn("formal_gui_fps_report", node["remaining"])
        self.assertTrue(node["artifacts"])

    def test_validation_summary_closes_formal_onscreen_render_fps_report(self) -> None:
        payload = self._run_summary_with_render_fps(
            render_fps_payload={
                "generated_at": "2026-03-29T00:00:00+00:00",
                "status": "ok",
                "evidence_scope": "formal_gui_onscreen",
                "environment": {
                    "platform": "Windows",
                    "python_version": "3.10.12",
                    "qt_qpa_platform": "windows",
                },
                "execution": {
                    "qt_qpa_platform": "windows",
                    "onscreen_verified": True,
                    "widget_visible": True,
                    "screen_count": 1,
                },
                "dependencies": {"pyside6": "6.x", "pyqtgraph": "0.x"},
                "workload": {"tile_count": 100000, "iterations": 120, "mode": "bargraph_batched"},
                "thresholds": {"target_fps": 30.0, "frame_budget_ms": 16.0},
                "metrics": {"fps_p95": 80.0},
                "verdict": {"pass": True},
                "limitations": ["qpa:windows"],
            }
        )
        node = payload["nfr_perf_status"]["NFR-PERF-05"]
        self.assertEqual(node["status"], "closed")
        self.assertEqual(node["remaining"], [])
        self.assertEqual(node["execution"]["onscreen_verified"], True)

    def test_validation_summary_closes_legacy_onscreen_render_fps_report(self) -> None:
        payload = self._run_summary_with_render_fps(
            render_fps_payload={
                "generated_at": "2026-03-29T00:00:00+00:00",
                "status": "ok",
                "environment": {
                    "platform": "Windows",
                    "python_version": "3.10.8",
                    "qt_qpa_platform": "windows",
                },
                "dependencies": {"pyside6": "6.10.2", "pyqtgraph": "0.14.0"},
                "workload": {"tile_count": 100000, "iterations": 120, "mode": "bargraph_batched"},
                "thresholds": {"target_fps": 30.0, "frame_budget_ms": 16.0},
                "metrics": {"fps_p95": 204080.078687},
                "verdict": {"target_fps": 30.0, "fps_p95": 204080.078687, "pass": True},
                "limitations": ["synthetic_workload", "qpa:windows"],
                "notes": [
                    "This report provides synthetic render evidence for GUI performance evaluation.",
                    "Onscreen execution detected on the active Qt platform; results support GUI performance validation but do not replace a full interactive UI acceptance suite.",
                ],
            }
        )
        node = payload["nfr_perf_status"]["NFR-PERF-05"]
        self.assertEqual(node["status"], "closed")
        self.assertEqual(node["remaining"], [])
        self.assertEqual(node["execution"]["legacy_onscreen_inferred"], True)

    def test_validation_summary_does_not_close_legacy_offscreen_render_fps_report(self) -> None:
        payload = self._run_summary_with_render_fps(
            render_fps_payload={
                "generated_at": "2026-03-29T00:00:00+00:00",
                "status": "ok",
                "environment": {
                    "platform": "Linux",
                    "python_version": "3.10.12",
                    "qt_qpa_platform": "offscreen",
                },
                "dependencies": {"pyside6": "6.x", "pyqtgraph": "0.x"},
                "workload": {"tile_count": 100000, "iterations": 120, "mode": "bargraph_batched"},
                "thresholds": {"target_fps": 30.0, "frame_budget_ms": 16.0},
                "metrics": {"fps_p95": 80.0},
                "verdict": {"pass": True},
                "limitations": ["synthetic_workload", "qpa:offscreen", "offscreen"],
                "notes": [
                    "Onscreen execution detected on the active Qt platform; results support GUI performance validation but do not replace a full interactive UI acceptance suite."
                ],
            }
        )
        node = payload["nfr_perf_status"]["NFR-PERF-05"]
        self.assertEqual(node["status"], "linux_evidence_only")
        self.assertIn("formal_gui_fps_report", node["remaining"])
        self.assertEqual(node["execution"]["onscreen_verified"], False)

    def test_runtime_failure_is_reflected_in_validation_summary(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        output = self.root / "summary.json"

        acceptance.write_text(
            json.dumps(
                {
                    "consistency": {
                        "repeat_export_consistent": True,
                        "repro_repeat_consistent": True,
                        "compare_scope_consistent": True,
                        "short_soak_passed": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_preflight.write_text(
            json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_env.write_text(
            json.dumps({"error": None}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": False,
                    "failure_summary": "tests.python.test_desktop_runtime.DesktopRuntimeTests.test_offscreen_async_load_updates_runtime_state",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertFalse(payload["desktop_runtime_ready"])
        self.assertEqual(
            payload["desktop_runtime_error"],
            "tests.python.test_desktop_runtime.DesktopRuntimeTests.test_offscreen_async_load_updates_runtime_state",
        )

    def test_linux_perf_acceptance_is_reflected_in_validation_summary(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        desktop_perf = self.root / "desktop-perf-acceptance.json"
        output = self.root / "summary.json"

        acceptance.write_text(
            json.dumps(
                {
                    "consistency": {
                        "repeat_export_consistent": True,
                        "repro_repeat_consistent": True,
                        "compare_scope_consistent": True,
                        "short_soak_passed": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_preflight.write_text(
            json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_env.write_text(
            json.dumps({"error": None}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_acceptance",
                    "durations": {
                        "export_write_seconds": 1.2,
                        "export_normalize_seconds": 61.2,
                        "export_full_seconds": 62.4,
                        "export_clipped_write_seconds": 0.3,
                        "export_clipped_normalize_seconds": 16.3,
                        "export_clipped_seconds": 16.6,
                    },
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.12,
                        "task_state_preview_seconds": 0.12,
                        "event_table_first_page_seconds": 0.03,
                        "lod2_first_window_seconds": 0.02,
                        "peak_memory_mb": 18.5,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                    "desktop_perf_acceptance": {
                        **self._desktop_perf_acceptance_fixture(
                            platform_name="linux",
                            input_bytes=26880,
                            limitations=["input_lt_1gb", "windows_not_covered"],
                            first_screen_seconds=0.12,
                            peak_memory_mb=18.5,
                            formal_input_verified=False,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(payload["desktop_perf_local_ready"])
        self.assertTrue(payload["desktop_perf_linux_formal_ready"])
        self.assertTrue(payload["desktop_perf_linux_first_screen_pass"])
        self.assertTrue(payload["desktop_perf_linux_peak_memory_pass"])
        self.assertEqual(
            payload["external_status"]["desktop_1gb_first_screen_lt_10s"]["status"],
            "linux_formal_fixture_only",
        )
        self.assertEqual(
            payload["external_status"]["desktop_peak_memory_lt_4gb"]["status"],
            "linux_formal_fixture_only",
        )
        self.assertIn("formal_1gb_desktop_input", payload["external_status"]["desktop_1gb_first_screen_lt_10s"]["remaining"])
        self.assertIn("nfr_perf_status", payload)
        self.assertEqual(payload["nfr_perf_status"]["NFR-PERF-06"]["status"], "fixture_only")
        self.assertTrue(payload["nfr_perf_status"]["NFR-PERF-06"]["verdict"]["pass"])
        self.assertEqual(
            payload["nfr_perf_status"]["NFR-PERF-06"]["verdict"]["export_write_seconds"],
            1.2,
        )
        self.assertEqual(
            payload["nfr_perf_status"]["NFR-PERF-06"]["verdict"]["export_write_threshold_seconds"],
            60.0,
        )
        self.assertEqual(
            payload["nfr_perf_status"]["NFR-PERF-06"]["verdict"]["export_write_source"],
            "export_write_seconds",
        )
        self.assertEqual(
            payload["nfr_perf_status"]["NFR-PERF-06"]["verdict"]["export_clipped_source"],
            "export_clipped_write_seconds",
        )
        self.assertEqual(
            payload["nfr_perf_status"]["NFR-PERF-06"]["verdict"]["export_full_semantics"],
            "observed_only_includes_normalize_seconds",
        )
        self.assertNotIn("export_full_pass", payload["nfr_perf_status"]["NFR-PERF-06"]["verdict"])

    def test_validation_summary_does_not_fallback_when_v2_export_is_skipped(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        desktop_perf = self.root / "desktop-perf-v2-skipped.json"
        output = self.root / "summary.json"

        acceptance.write_text(json.dumps({"consistency": {"short_soak_passed": True}}, ensure_ascii=False), encoding="utf-8")
        desktop_preflight.write_text(json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False), encoding="utf-8")
        desktop_env.write_text(json.dumps({"error": None}, ensure_ascii=False), encoding="utf-8")
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_acceptance",
                    "durations": {
                        "export_write_seconds": None,
                        "export_normalize_seconds": None,
                        "export_full_seconds": 0.0,
                        "export_clipped_write_seconds": None,
                        "export_clipped_normalize_seconds": None,
                        "export_clipped_seconds": 0.0,
                    },
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.12,
                        "task_state_preview_seconds": 0.12,
                        "event_table_first_page_seconds": 0.03,
                        "lod2_first_window_seconds": 0.02,
                        "peak_memory_mb": 18.5,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                        "export_probe_skipped": True,
                    },
                    "desktop_perf_acceptance": {
                        **self._desktop_perf_acceptance_fixture(
                            platform_name="linux",
                            input_bytes=1073741824,
                            limitations=[],
                            first_screen_seconds=0.12,
                            peak_memory_mb=18.5,
                            formal_input_verified=False,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["nfr_perf_status"]["NFR-PERF-06"]["status"], "missing")
        self.assertIn("v2 export", payload["nfr_perf_status"]["NFR-PERF-06"]["notes"])

    def test_validation_summary_requires_formal_input_manifest_before_closed(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        desktop_perf = self.root / "desktop-perf.json"
        output = self.root / "summary.json"

        acceptance.write_text(json.dumps({"consistency": {"short_soak_passed": True}}, ensure_ascii=False), encoding="utf-8")
        desktop_preflight.write_text(json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False), encoding="utf-8")
        desktop_env.write_text(json.dumps({"error": None}, ensure_ascii=False), encoding="utf-8")
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_acceptance",
                    "durations": {
                        "export_full_seconds": 1.2,
                        "export_clipped_seconds": 0.3,
                    },
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.12,
                        "task_state_preview_seconds": 0.12,
                        "event_table_first_page_seconds": 0.03,
                        "lod2_first_window_seconds": 0.02,
                        "peak_memory_mb": 18.5,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                    "desktop_perf_acceptance": {
                        "platform": "linux",
                        "input_bytes": 1073741824,
                        "input_scope": "linux_provided_fixture",
                        "acceptance_scope": "perf_only",
                        "limitations": [],
                        "first_screen_lt_10s": {
                            "observed_seconds": 0.12,
                            "threshold_seconds": 10.0,
                            "pass": True,
                        },
                        "peak_memory_lt_4gb": {
                            "observed_mb": 18.5,
                            "threshold_mb": 4096.0,
                            "pass": True,
                            "source": "linux_peak_rss_mb",
                        },
                        "first_screen_peak_memory_lt_4gb": {
                            "observed_mb": 18.5,
                            "threshold_mb": 4096.0,
                            "pass": True,
                            "source": "python_peak_alloc_mb",
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertFalse(payload["desktop_perf_linux_formal_ready"])
        self.assertEqual(payload["external_status"]["desktop_1gb_first_screen_lt_10s"]["status"], "pending_external")
        self.assertEqual(payload["nfr_perf_status"]["NFR-PERF-06"]["status"], "pending_external")

    def test_validation_summary_rejects_synthetic_padded_input_for_formal_closure(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        desktop_perf = self.root / "desktop-perf.json"
        output = self.root / "summary.json"

        acceptance.write_text(json.dumps({"consistency": {"short_soak_passed": True}}, ensure_ascii=False), encoding="utf-8")
        desktop_preflight.write_text(json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False), encoding="utf-8")
        desktop_env.write_text(json.dumps({"error": None}, ensure_ascii=False), encoding="utf-8")
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_acceptance",
                    "durations": {
                        "export_full_seconds": 1.2,
                        "export_clipped_seconds": 0.3,
                    },
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.12,
                        "task_state_preview_seconds": 0.12,
                        "event_table_first_page_seconds": 0.03,
                        "lod2_first_window_seconds": 0.02,
                        "peak_memory_mb": 18.5,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                    "desktop_perf_acceptance": {
                        **self._desktop_perf_acceptance_fixture(
                            platform_name="linux",
                            input_bytes=1073741824,
                            limitations=["synthetic_padded_input"],
                            first_screen_seconds=0.12,
                            peak_memory_mb=18.5,
                            formal_input_verified=False,
                            input_provenance="synthetic_padded",
                        ),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(payload["desktop_perf_linux_formal_ready"])
        self.assertEqual(payload["external_status"]["desktop_1gb_first_screen_lt_10s"]["status"], "pending_external")
        self.assertEqual(payload["nfr_perf_status"]["NFR-PERF-06"]["status"], "pending_external")
        self.assertIn(
            "synthetic padded input cannot satisfy formal 1GB validation",
            payload["external_status"]["desktop_1gb_first_screen_lt_10s"]["notes"],
        )

    def test_validation_summary_keeps_public_rtos_prevalidation_input_pending(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        desktop_perf = self.root / "desktop-perf.json"
        output = self.root / "summary.json"

        acceptance.write_text(json.dumps({"consistency": {"short_soak_passed": True}}, ensure_ascii=False), encoding="utf-8")
        desktop_preflight.write_text(json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False), encoding="utf-8")
        desktop_env.write_text(json.dumps({"error": None}, ensure_ascii=False), encoding="utf-8")
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_acceptance",
                    "durations": {
                        "export_full_seconds": 1.4,
                        "export_clipped_seconds": 0.4,
                    },
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.15,
                        "task_state_preview_seconds": 0.15,
                        "event_table_first_page_seconds": 0.04,
                        "lod2_first_window_seconds": 0.03,
                        "peak_memory_mb": 22.0,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                    "desktop_perf_acceptance": {
                        **self._desktop_perf_acceptance_fixture(
                            platform_name="linux",
                            input_bytes=1073741824,
                            limitations=["public_rtos_seeded_padded_input"],
                            first_screen_seconds=0.15,
                            peak_memory_mb=22.0,
                            formal_input_verified=False,
                            input_provenance="public_rtos_seeded_padded",
                        ),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(payload["desktop_perf_linux_formal_ready"])
        self.assertEqual(payload["external_status"]["desktop_1gb_first_screen_lt_10s"]["status"], "pending_external")
        self.assertEqual(payload["nfr_perf_status"]["NFR-PERF-06"]["status"], "pending_external")
        self.assertIn(
            "public RTOS derived input is suitable for pre-validation",
            payload["external_status"]["desktop_1gb_first_screen_lt_10s"]["notes"],
        )

    def test_external_status_map_tracks_linux_evidence_and_pending_gaps(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        desktop_perf = self.root / "desktop-perf-acceptance.json"
        collector_soak = self.root / "collector-soak.json"
        collector_perf = self.root / "collector-perf.json"
        output = self.root / "summary.json"

        acceptance.write_text(
            json.dumps(
                {
                    "consistency": {
                        "repeat_export_consistent": True,
                        "repro_repeat_consistent": True,
                        "compare_scope_consistent": True,
                        "short_soak_passed": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_preflight.write_text(
            json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_env.write_text(
            json.dumps({"error": None}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_acceptance",
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.12,
                        "task_state_preview_seconds": 0.12,
                        "event_table_first_page_seconds": 0.03,
                        "lod2_first_window_seconds": 0.02,
                        "peak_memory_mb": 18.5,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                    "desktop_perf_acceptance": {
                        **self._desktop_perf_acceptance_fixture(
                            platform_name="linux",
                            input_bytes=26880,
                            limitations=[
                                "input_lt_1gb",
                                "windows_not_covered",
                                "windows_linux_consistency_not_covered",
                                "long_duration_stability_not_covered",
                            ],
                            first_screen_seconds=0.12,
                            peak_memory_mb=18.5,
                            formal_input_verified=False,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        collector_soak.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "scenario": {
                        "duration_sec": 5.0,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        collector_perf.write_text(
            json.dumps(
                {
                    "generated_at": "2026-03-19T00:00:00Z",
                    "environment": {"platform": "Linux"},
                    "scenarios": [
                        {"scenario": "single_core", "status": "ok", "events_per_sec": 1200000.0},
                        {"scenario": "multi_core", "status": "ok", "events_per_sec": 2000000.0},
                    ],
                    "status": "ok",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--collector-soak",
                str(collector_soak),
                "--collector-perf",
                str(collector_perf),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(payload["collector_soak_linux_ready"])
        self.assertEqual(
            payload["external_status"]["windows_linux_consistency"]["linux_artifacts"],
            [
                str(acceptance),
                str(desktop_runtime),
                str(desktop_perf),
                str(collector_soak),
            ],
        )
        self.assertEqual(
            payload["external_status"]["long_duration_stability"]["status"],
            "linux_short_soak_only",
        )
        self.assertEqual(
            payload["external_status"]["long_duration_stability"]["current_scope"]["linux_collector_soak_seconds"],
            5.0,
        )
        self.assertIn("windows_linux_consistency", payload["external_pending"])
        self.assertIn("long_duration_stability", payload["external_pending"])
        self.assertIn("nfr_perf_status", payload)
        self.assertEqual(payload["nfr_perf_status"]["NFR-PERF-01"]["status"], "linux_evidence_only")
        self.assertIn("windows_collector_perf_baseline", payload["nfr_perf_status"]["NFR-PERF-01"]["remaining"])
        self.assertIn("NFR-PERF-01", payload["external_pending"])
        self.assertIn("NFR-PERF-05", payload["external_pending"])
        self.assertIn("NFR-PERF-06", payload["external_pending"])

    def test_validation_summary_can_aggregate_windows_collector_perf_for_nfr_perf01(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        desktop_perf = self.root / "desktop-perf-acceptance.json"
        collector_soak = self.root / "collector-soak.json"
        collector_perf = self.root / "collector-perf-linux.json"
        windows_collector_perf = self.root / "collector-perf-windows.json"
        output = self.root / "summary.json"

        acceptance.write_text(
            json.dumps(
                {
                    "consistency": {
                        "repeat_export_consistent": True,
                        "repro_repeat_consistent": True,
                        "compare_scope_consistent": True,
                        "short_soak_passed": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_preflight.write_text(
            json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_env.write_text(
            json.dumps({"error": None}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_acceptance",
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.12,
                        "task_state_preview_seconds": 0.12,
                        "event_table_first_page_seconds": 0.03,
                        "lod2_first_window_seconds": 0.02,
                        "peak_memory_mb": 18.5,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                    "desktop_perf_acceptance": {
                        **self._desktop_perf_acceptance_fixture(
                            platform_name="linux",
                            input_bytes=26880,
                            limitations=[
                                "input_lt_1gb",
                                "windows_not_covered",
                                "windows_linux_consistency_not_covered",
                                "long_duration_stability_not_covered",
                            ],
                            first_screen_seconds=0.12,
                            peak_memory_mb=18.5,
                            formal_input_verified=False,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        collector_soak.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "scenario": {
                        "duration_sec": 5.0,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        collector_perf.write_text(
            json.dumps(
                {
                    "generated_at": "2026-03-19T00:00:00Z",
                    "environment": {"platform": "Linux"},
                    "scenarios": [
                        {"scenario": "single_core", "status": "ok", "events_per_sec": 1200000.0},
                        {"scenario": "multi_core", "status": "ok", "events_per_sec": 2000000.0},
                    ],
                    "status": "ok",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        windows_collector_perf.write_text(
            json.dumps(
                {
                    "generated_at": "2026-03-19T00:00:00Z",
                    "environment": {"platform": "Windows"},
                    "scenarios": [
                        {"scenario": "single_core", "status": "ok", "events_per_sec": 1500000.0},
                        {"scenario": "multi_core", "status": "ok", "events_per_sec": 2500000.0},
                    ],
                    "status": "ok",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--collector-soak",
                str(collector_soak),
                "--collector-perf",
                str(collector_perf),
                "--windows-collector-perf",
                str(windows_collector_perf),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        node = payload["nfr_perf_status"]["NFR-PERF-01"]
        self.assertEqual(node["status"], "closed")
        self.assertCountEqual(node["artifacts"], [str(collector_perf), str(windows_collector_perf)])
        self.assertEqual(node["remaining"], [])
        self.assertIn("per_platform", node)
        self.assertEqual(node["per_platform"]["linux"]["status"], "closed")
        self.assertEqual(node["per_platform"]["linux"]["remaining"], [])
        self.assertEqual(node["per_platform"]["windows"]["status"], "closed")
        self.assertEqual(node["per_platform"]["windows"]["remaining"], [])
        self.assertNotIn("NFR-PERF-01", payload["external_pending"])

    def test_validation_summary_keeps_nfr_perf01_intermediate_when_platform_metadata_is_invalid(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        desktop_perf = self.root / "desktop-perf-acceptance.json"
        collector_soak = self.root / "collector-soak.json"
        collector_perf = self.root / "collector-perf-linux.json"
        windows_collector_perf = self.root / "collector-perf-windows.json"
        output = self.root / "summary.json"

        acceptance.write_text(
            json.dumps(
                {
                    "consistency": {
                        "repeat_export_consistent": True,
                        "repro_repeat_consistent": True,
                        "compare_scope_consistent": True,
                        "short_soak_passed": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_preflight.write_text(
            json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_env.write_text(
            json.dumps({"error": None}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_acceptance",
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.12,
                        "task_state_preview_seconds": 0.12,
                        "event_table_first_page_seconds": 0.03,
                        "lod2_first_window_seconds": 0.02,
                        "peak_memory_mb": 18.5,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                    "desktop_perf_acceptance": {
                        **self._desktop_perf_acceptance_fixture(
                            platform_name="linux",
                            input_bytes=26880,
                            limitations=[
                                "input_lt_1gb",
                                "windows_not_covered",
                                "windows_linux_consistency_not_covered",
                                "long_duration_stability_not_covered",
                            ],
                            first_screen_seconds=0.12,
                            peak_memory_mb=18.5,
                            formal_input_verified=False,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        collector_soak.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "scenario": {
                        "duration_sec": 5.0,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        collector_perf.write_text(
            json.dumps(
                {
                    "generated_at": "2026-03-19T00:00:00Z",
                    "environment": {"platform": "Linux"},
                    "scenarios": [
                        {"scenario": "single_core", "status": "ok", "events_per_sec": 1200000.0},
                        {"scenario": "multi_core", "status": "ok", "events_per_sec": 2000000.0},
                    ],
                    "status": "ok",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        windows_collector_perf.write_text(
            json.dumps(
                {
                    "generated_at": "2026-03-19T00:00:00Z",
                    "environment": {"platform": "WinUnknown"},
                    "scenarios": [
                        {"scenario": "single_core", "status": "ok", "events_per_sec": 1500000.0},
                        {"scenario": "multi_core", "status": "ok", "events_per_sec": 2500000.0},
                    ],
                    "status": "ok",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--collector-soak",
                str(collector_soak),
                "--collector-perf",
                str(collector_perf),
                "--windows-collector-perf",
                str(windows_collector_perf),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        node = payload["nfr_perf_status"]["NFR-PERF-01"]
        self.assertEqual(node["status"], "cross_platform_evidence_ready")
        self.assertEqual(node["remaining"], ["collector_perf_platform_metadata"])
        self.assertEqual(node["platform"], "cross_platform")
        self.assertEqual(node["per_platform"]["linux"]["status"], "linux_evidence_only")
        self.assertEqual(node["per_platform"]["windows"]["status"], "evidence_only")
        self.assertIn("NFR-PERF-01", payload["external_pending"])

    def test_validation_summary_consumes_long_duration_artifacts_without_over_closing(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        desktop_perf = self.root / "desktop-perf-acceptance.json"
        collector_soak = self.root / "collector-soak.json"
        desktop_soak = self.root / "desktop-soak.json"
        irrecoverable_error = self.root / "irrecoverable-error.json"
        output = self.root / "summary.json"

        acceptance.write_text(
            json.dumps(
                {
                    "consistency": {
                        "repeat_export_consistent": True,
                        "repro_repeat_consistent": True,
                        "compare_scope_consistent": True,
                        "short_soak_passed": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_preflight.write_text(
            json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_env.write_text(
            json.dumps({"error": None}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_acceptance",
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.12,
                        "task_state_preview_seconds": 0.12,
                        "event_table_first_page_seconds": 0.03,
                        "lod2_first_window_seconds": 0.02,
                        "peak_memory_mb": 18.5,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                    "desktop_perf_acceptance": {
                        **self._desktop_perf_acceptance_fixture(
                            platform_name="linux",
                            input_bytes=26880,
                            limitations=[
                                "input_lt_1gb",
                                "windows_not_covered",
                                "windows_linux_consistency_not_covered",
                                "long_duration_stability_not_covered",
                            ],
                            first_screen_seconds=0.12,
                            peak_memory_mb=18.5,
                            formal_input_verified=False,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        collector_soak.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "scenario": {
                        "duration_sec": 5.0,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_soak.write_text(
            json.dumps(
                {
                    "generated_at": "2026-03-19T00:00:00Z",
                    "platform": "linux",
                    "scenario": "desktop_long_soak",
                    "duration_sec": 3600,
                    "status": "ok",
                    "failures": 0,
                    "errors": 0,
                    "notes": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        irrecoverable_error.write_text(
            json.dumps(
                {
                    "generated_at": "2026-03-19T00:00:00Z",
                    "platform": "linux",
                    "duration_sec": 3600,
                    "irrecoverable_error_detected": False,
                    "last_error_summary": None,
                    "evidence_paths": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--collector-soak",
                str(collector_soak),
                "--desktop-soak",
                str(desktop_soak),
                "--irrecoverable-error",
                str(irrecoverable_error),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        node = payload["external_status"]["long_duration_stability"]
        self.assertEqual(node["status"], "linux_long_soak_evidence_ready_pending_windows")
        self.assertIn("windows_desktop_soak_24h", node["remaining"])
        self.assertIn("windows_irrecoverable_error_observation", node["remaining"])
        self.assertIn("long_duration_stability", payload["external_pending"])

    def test_validation_summary_closes_long_duration_on_windows_24h_and_irrecoverable_false(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        desktop_runtime = self.root / "desktop-runtime.json"
        desktop_perf = self.root / "desktop-perf-acceptance.json"
        windows_desktop_soak = self.root / "desktop-soak-windows.json"
        windows_irrecoverable_error = self.root / "irrecoverable-error-windows.json"
        output = self.root / "summary.json"

        acceptance.write_text(
            json.dumps(
                {
                    "consistency": {
                        "repeat_export_consistent": True,
                        "repro_repeat_consistent": True,
                        "compare_scope_consistent": True,
                        "short_soak_passed": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_preflight.write_text(
            json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_env.write_text(
            json.dumps({"error": None}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_acceptance",
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.12,
                        "task_state_preview_seconds": 0.12,
                        "event_table_first_page_seconds": 0.03,
                        "lod2_first_window_seconds": 0.02,
                        "peak_memory_mb": 18.5,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                    "desktop_perf_acceptance": {
                        **self._desktop_perf_acceptance_fixture(
                            platform_name="linux",
                            input_bytes=26880,
                            limitations=[
                                "input_lt_1gb",
                                "windows_not_covered",
                                "windows_linux_consistency_not_covered",
                                "long_duration_stability_not_covered",
                            ],
                            first_screen_seconds=0.12,
                            peak_memory_mb=18.5,
                            formal_input_verified=False,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        windows_desktop_soak.write_text(
            json.dumps(
                {
                    "generated_at": "2026-03-29T00:00:00Z",
                    "platform": "windows",
                    "scenario": "desktop_long_soak",
                    "duration_sec": 86405.0,
                    "status": "ok",
                    "failures": 0,
                    "errors": 0,
                    "notes": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        windows_irrecoverable_error.write_text(
            json.dumps(
                {
                    "generated_at": "2026-03-29T00:00:00Z",
                    "platform": "windows",
                    "duration_sec": 86405.0,
                    "irrecoverable_error_detected": False,
                    "last_error_summary": None,
                    "evidence_paths": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--windows-desktop-soak",
                str(windows_desktop_soak),
                "--windows-irrecoverable-error",
                str(windows_irrecoverable_error),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        node = payload["external_status"]["long_duration_stability"]
        self.assertEqual(node["status"], "closed")
        self.assertEqual(node["remaining"], [])
        self.assertTrue(node["current_scope"]["windows_desktop_long_soak_24h_passed"])
        self.assertTrue(node["current_scope"]["windows_collector_soak_supporting_only"])
        self.assertNotIn("long_duration_stability", payload["external_pending"])

    def test_validation_summary_keeps_long_duration_open_when_windows_soak_lt_24h(self) -> None:
        acceptance = self.root / "acceptance.json"
        desktop_preflight = self.root / "desktop-preflight.json"
        desktop_env = self.root / "desktop-env.json"
        windows_desktop_soak = self.root / "desktop-soak-windows.json"
        windows_irrecoverable_error = self.root / "irrecoverable-error-windows.json"
        output = self.root / "summary.json"

        acceptance.write_text(
            json.dumps(
                {
                    "consistency": {
                        "repeat_export_consistent": True,
                        "repro_repeat_consistent": True,
                        "compare_scope_consistent": True,
                        "short_soak_passed": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_preflight.write_text(
            json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_env.write_text(
            json.dumps({"error": None}, ensure_ascii=False),
            encoding="utf-8",
        )
        windows_desktop_soak.write_text(
            json.dumps(
                {
                    "generated_at": "2026-03-29T00:00:00Z",
                    "platform": "windows",
                    "scenario": "desktop_long_soak",
                    "duration_sec": 7200.0,
                    "status": "ok",
                    "failures": 0,
                    "errors": 0,
                    "notes": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        windows_irrecoverable_error.write_text(
            json.dumps(
                {
                    "generated_at": "2026-03-29T00:00:00Z",
                    "platform": "windows",
                    "duration_sec": 7200.0,
                    "irrecoverable_error_detected": False,
                    "last_error_summary": None,
                    "evidence_paths": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--windows-desktop-soak",
                str(windows_desktop_soak),
                "--windows-irrecoverable-error",
                str(windows_irrecoverable_error),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        node = payload["external_status"]["long_duration_stability"]
        self.assertEqual(node["status"], "windows_long_soak_duration_not_24h")
        self.assertIn("windows_desktop_soak_24h", node["remaining"])
        self.assertNotIn("windows_irrecoverable_error_observation", node["remaining"])
        self.assertIn("long_duration_stability", payload["external_pending"])

    def test_validation_summary_can_aggregate_windows_artifacts_and_compares(self) -> None:
        acceptance = self.root / "acceptance-linux.json"
        desktop_preflight = self.root / "desktop-preflight-linux.json"
        desktop_env = self.root / "desktop-env-linux.json"
        desktop_runtime = self.root / "desktop-runtime-linux.json"
        desktop_perf = self.root / "desktop-perf-linux.json"
        collector_soak = self.root / "collector-soak-linux.json"
        windows_acceptance = self.root / "acceptance-windows.json"
        windows_runtime = self.root / "desktop-runtime-windows.json"
        windows_perf = self.root / "desktop-perf-windows.json"
        windows_collector_soak = self.root / "collector-soak-windows.json"
        acceptance_compare = self.root / "acceptance-compare.json"
        package_compare = self.root / "package-compare.json"
        output = self.root / "summary.json"

        acceptance.write_text(
            json.dumps(
                {
                    "consistency": {
                        "repeat_export_consistent": True,
                        "repro_repeat_consistent": True,
                        "compare_scope_consistent": True,
                        "short_soak_passed": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_preflight.write_text(
            json.dumps({"ready_for_runtime_smoke": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_env.write_text(
            json.dumps({"error": None}, ensure_ascii=False),
            encoding="utf-8",
        )
        desktop_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        desktop_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_acceptance",
                    "desktop_perf": {
                        "profile": "medium",
                        "load_preview_seconds": 0.12,
                        "task_state_preview_seconds": 0.12,
                        "event_table_first_page_seconds": 0.03,
                        "lod2_first_window_seconds": 0.02,
                        "peak_memory_mb": 18.5,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                    "desktop_perf_acceptance": {
                        **self._desktop_perf_acceptance_fixture(
                            platform_name="linux",
                            input_bytes=26880,
                            limitations=[
                                "input_lt_1gb",
                                "windows_not_covered",
                                "windows_linux_consistency_not_covered",
                                "long_duration_stability_not_covered",
                            ],
                            first_screen_seconds=0.12,
                            peak_memory_mb=18.5,
                            formal_input_verified=False,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        collector_soak.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "scenario": {
                        "duration_sec": 5.0,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        windows_acceptance.write_text(
            json.dumps(
                {
                    "consistency": {
                        "repeat_export_consistent": True,
                        "repro_repeat_consistent": True,
                        "compare_scope_consistent": True,
                        "short_soak_passed": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        windows_runtime.write_text(
            json.dumps(
                {
                    "command": "python -m unittest tests.python.test_desktop_runtime",
                    "tests_run": 7,
                    "executed": 7,
                    "skipped": 0,
                    "runtime_contract_ok": True,
                    "failure_summary": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        windows_perf.write_text(
            json.dumps(
                {
                    "mode": "desktop_perf_acceptance",
                    "desktop_perf": {
                        "profile": "large",
                        "load_preview_seconds": 1.25,
                        "task_state_preview_seconds": 1.25,
                        "event_table_first_page_seconds": 0.11,
                        "lod2_first_window_seconds": 0.08,
                        "peak_memory_mb": 512.0,
                        "cache_eviction_count": 0,
                        "cache_budget_bytes": 8388608,
                        "cache_namespaces": {
                            "task_states": {"entry_count": 1, "byte_size_estimate": 256},
                            "event_table": {"entry_count": 1, "byte_size_estimate": 256},
                            "timeline": {"entry_count": 1, "byte_size_estimate": 256},
                        },
                    },
                    "desktop_perf_acceptance": {
                        **self._desktop_perf_acceptance_fixture(
                            platform_name="windows",
                            input_bytes=1073741824,
                            limitations=[
                                "linux_not_covered",
                                "windows_linux_consistency_not_covered",
                                "long_duration_stability_not_covered",
                            ],
                            first_screen_seconds=1.25,
                            peak_memory_mb=512.0,
                            formal_input_verified=True,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        windows_collector_soak.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "scenario": {
                        "duration_sec": 5.0,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        acceptance_compare.write_text(
            json.dumps(
                {
                    "match": True,
                    "structural_match": True,
                    "consistency_match": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        package_compare.write_text(
            json.dumps(
                {
                    "match": True,
                    "mismatched_sections": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                "tool/summarize_validation_status.py",
                "--acceptance",
                str(acceptance),
                "--desktop-preflight",
                str(desktop_preflight),
                "--desktop-env",
                str(desktop_env),
                "--desktop-runtime",
                str(desktop_runtime),
                "--desktop-perf",
                str(desktop_perf),
                "--collector-soak",
                str(collector_soak),
                "--windows-acceptance",
                str(windows_acceptance),
                "--windows-desktop-runtime",
                str(windows_runtime),
                "--windows-desktop-perf",
                str(windows_perf),
                "--windows-collector-soak",
                str(windows_collector_soak),
                "--acceptance-compare",
                str(acceptance_compare),
                "--package-compare",
                str(package_compare),
                "--output",
                str(output),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(payload["windows_runtime_ready"])
        self.assertTrue(payload["desktop_perf_windows_formal_ready"])
        self.assertTrue(payload["desktop_perf_windows_first_screen_pass"])
        self.assertTrue(payload["desktop_perf_windows_peak_memory_pass"])
        self.assertTrue(payload["collector_soak_windows_ready"])
        self.assertTrue(payload["acceptance_compare_match"])
        self.assertTrue(payload["package_compare_match"])
        self.assertEqual(payload["external_status"]["desktop_1gb_first_screen_lt_10s"]["status"], "closed")
        self.assertEqual(payload["external_status"]["desktop_peak_memory_lt_4gb"]["status"], "closed")
        self.assertEqual(payload["external_status"]["windows_linux_consistency"]["status"], "closed")
        self.assertEqual(
            payload["external_status"]["long_duration_stability"]["status"],
            "cross_platform_short_soak_only",
        )
        self.assertIn(str(windows_perf), payload["external_status"]["desktop_1gb_first_screen_lt_10s"]["windows_artifacts"])
        self.assertEqual(payload["external_status"]["windows_linux_consistency"]["remaining"], [])
        self.assertIn("long_duration_stability", payload["external_pending"])


if __name__ == "__main__":
    unittest.main()
