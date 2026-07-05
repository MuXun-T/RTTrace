from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


FAKE_API_KEY_VALUE = "rttrace-deepseek-smoke-fake-key"


class DeepSeekAdvisorSmokeToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo_root = Path(__file__).resolve().parents[2]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run_tool(self, *args: str | Path, env: dict[str, str | None] | None = None) -> subprocess.CompletedProcess[str]:
        run_env = os.environ.copy()
        for key, value in dict(env or {}).items():
            if value is None:
                run_env.pop(key, None)
            else:
                run_env[key] = value
        return subprocess.run(
            [sys.executable, "tool/run_deepseek_advisor_smoke.py", *[str(arg) for arg in args]],
            cwd=self.repo_root,
            env=run_env,
            capture_output=True,
            text=True,
            check=False,
        )

    def _run_mock_smoke(
        self,
        name: str,
        *extra_args: str | Path,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object], Path]:
        output_root = self.root / name
        summary_path = self.root / f"{name}.json"
        proc = self._run_tool(
            "--mode",
            "mock",
            "--output-root",
            output_root,
            "--json-output",
            summary_path,
            *extra_args,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        summary = json.loads(proc.stdout)
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(json.loads(summary_path.read_text(encoding="utf-8")), summary)
        package_path = Path(str(summary["package_path"]))
        self.assertTrue(package_path.exists())
        return proc, summary, package_path

    def _assert_fake_key_absent_from_file(self, path: Path) -> None:
        self.assertNotIn(FAKE_API_KEY_VALUE.encode("utf-8"), path.read_bytes(), str(path))

    def test_mock_subprocess_succeeds_and_writes_summary(self) -> None:
        _proc, summary, package_path = self._run_mock_smoke("mock-success")
        self.assertEqual(summary["mode"], "mock")
        self.assertFalse(summary["validate_only"])
        self.assertGreaterEqual(summary["mock_server"]["request_count"], 1)
        self.assertEqual(package_path.name, "package")
        self.assertTrue(all(check["ok"] for check in summary["checks"]), summary["checks"])

    def test_mock_trace_list_runs_each_trace_and_writes_child_summaries(self) -> None:
        output_root = self.root / "mock-trace-list"
        summary_path = self.root / "mock-trace-list.json"
        trace_list_path = self.root / "traces.txt"
        trace_list_path.write_text(
            "\n".join(
                [
                    "# DeepSeek smoke trace list",
                    "tests/cpp/basic.trace",
                    "",
                    "tests/cpp/filter.trace",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        proc = self._run_tool(
            "--mode",
            "mock",
            "--trace-list",
            trace_list_path,
            "--output-root",
            output_root,
            "--json-output",
            summary_path,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        summary = json.loads(proc.stdout)
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(json.loads(summary_path.read_text(encoding="utf-8")), summary)
        self.assertEqual(len(summary["runs"]), 2)
        self.assertTrue(all(run["ok"] for run in summary["runs"]), summary["runs"])
        for run in summary["runs"]:
            child_summary = run["summary"]
            self.assertTrue(child_summary["ok"], child_summary)
            self.assertTrue(Path(str(child_summary["package_path"])).exists())

    def test_real_mode_requires_manual_gate_env_before_api_key_use(self) -> None:
        output_root = self.root / "real-gate"
        summary_path = self.root / "real-gate.json"
        proc = self._run_tool(
            "--mode",
            "real",
            "--trace",
            "tests/cpp/basic.trace",
            "--output-root",
            output_root,
            "--json-output",
            summary_path,
            env={
                "RTTRACE_RUN_REAL_DEEPSEEK_SMOKE": None,
                "DEEPSEEK_API_KEY": "fake-deepseek-key-for-gate-test",
            },
        )

        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("fake-deepseek-key-for-gate-test", proc.stdout)
        self.assertNotIn("fake-deepseek-key-for-gate-test", proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertFalse(summary["ok"], summary)
        checks = {check["name"]: check for check in summary["checks"]}
        self.assertFalse(checks["real.manual_gate.enabled"]["ok"])
        self.assertEqual(checks["real.manual_gate.enabled"]["details"]["env"], "RTTRACE_RUN_REAL_DEEPSEEK_SMOKE")

    def test_mock_mode_b_subprocess_writes_pre_execution_sidecar_artifacts(self) -> None:
        _proc, summary, package_path = self._run_mock_smoke(
            "mock-mode-b",
            "--embodiment-mode",
            "mode_b",
        )
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(summary["embodiment_mode"], "mode_b")
        self.assertGreaterEqual(summary["mock_server"]["request_count"], 2)

        pre_execution_checks = [
            check for check in summary["checks"] if "pre_execution" in str(check["name"])
        ]
        self.assertTrue(pre_execution_checks, summary["checks"])
        self.assertTrue(all(check["ok"] for check in pre_execution_checks), pre_execution_checks)

        control_dir = package_path / "control"
        self.assertTrue((control_dir / "pre_execution_advisor_trace.json").exists())
        self.assertTrue((control_dir / "pre_execution_runtime_advisor_agent_contract.json").exists())
        manifest_paths = {
            entry["path"]
            for entry in json.loads((package_path / "manifest.json").read_text(encoding="utf-8"))["entries"]
        }
        self.assertIn("control/pre_execution_advisor_trace.json", manifest_paths)
        self.assertIn("control/pre_execution_runtime_advisor_agent_contract.json", manifest_paths)

    def test_mock_proof_parity_with_disabled_subprocess_succeeds(self) -> None:
        output_root = self.root / "mock-proof-parity"
        summary_path = self.root / "mock-proof-parity.json"
        proc = self._run_tool(
            "--mode",
            "mock",
            "--proof-parity-with-disabled",
            "--output-root",
            output_root,
            "--json-output",
            summary_path,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        summary = json.loads(proc.stdout)
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(json.loads(summary_path.read_text(encoding="utf-8")), summary)
        self.assertGreaterEqual(summary["mock_server"]["request_count"], 1)

        proof_parity = summary["proof_parity"]
        self.assertTrue(proof_parity["ok"], proof_parity)
        self.assertEqual(proof_parity["field_diffs"], [])
        self.assertEqual(proof_parity["proof_hash_disabled"], proof_parity["proof_hash_enabled"])
        self.assertTrue(proof_parity["enabled_advisor_artifacts_present"])
        self.assertTrue(proof_parity["disabled_advisor_artifacts_absent"])

        disabled_package_path = Path(str(proof_parity["disabled_package_path"]))
        enabled_package_path = Path(str(proof_parity["enabled_package_path"]))
        self.assertEqual(disabled_package_path, output_root / "advisor_disabled" / "package")
        self.assertEqual(enabled_package_path, output_root / "advisor_enabled" / "package")
        for rel_path in (
            "control/advisor_report.json",
            "control/advisor_trace.json",
            "control/runtime_advisor_agent_contract.json",
        ):
            self.assertFalse((disabled_package_path / rel_path).exists())
            self.assertTrue((enabled_package_path / rel_path).exists())

    def test_validate_only_accepts_mock_package(self) -> None:
        _proc, _summary, package_path = self._run_mock_smoke("validate-source")
        validate_path = self.root / "validate-only.json"
        proc = self._run_tool("--package", package_path, "--json-output", validate_path)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        summary = json.loads(proc.stdout)
        self.assertTrue(summary["validate_only"])
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(json.loads(validate_path.read_text(encoding="utf-8")), summary)

    def test_validate_only_rejects_missing_advisor_report(self) -> None:
        _proc, _summary, package_path = self._run_mock_smoke("missing-report-source")
        (package_path / "control" / "advisor_report.json").unlink()

        proc = self._run_tool("--package", package_path)

        self.assertEqual(proc.returncode, 1)
        summary = json.loads(proc.stdout)
        self.assertFalse(summary["ok"])
        checks = {check["name"]: check for check in summary["checks"]}
        self.assertFalse(checks["file.advisor_report.exists"]["ok"])

    def test_validate_only_rejects_tampered_checksum(self) -> None:
        _proc, _summary, package_path = self._run_mock_smoke("tampered-source")
        proof_digest_path = package_path / "control" / "proof_digest.json"
        proof_digest = json.loads(proof_digest_path.read_text(encoding="utf-8"))
        proof_digest["window_hit_rate"] = 0.5
        proof_digest_path.write_text(json.dumps(proof_digest, ensure_ascii=False, indent=2), encoding="utf-8")

        proc = self._run_tool("--package", package_path)

        self.assertEqual(proc.returncode, 1)
        summary = json.loads(proc.stdout)
        self.assertFalse(summary["ok"])
        failed = [check["name"] for check in summary["checks"] if not check["ok"]]
        self.assertIn("manifest.entry.control/proof_digest.json.checksum", failed)

    def test_mock_fake_key_is_not_emitted(self) -> None:
        output_root = self.root / "redaction"
        summary_path = self.root / "redaction-summary.json"
        proc = self._run_tool(
            "--mode",
            "mock",
            "--output-root",
            output_root,
            "--json-output",
            summary_path,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertNotIn(FAKE_API_KEY_VALUE, proc.stdout)
        self.assertNotIn(FAKE_API_KEY_VALUE, proc.stderr)
        self._assert_fake_key_absent_from_file(summary_path)
        package_path = Path(str(json.loads(proc.stdout)["package_path"]))
        for path in package_path.rglob("*"):
            if path.is_file():
                self._assert_fake_key_absent_from_file(path)

        from tool.run_deepseek_advisor_smoke import FAKE_API_KEY_ENV, _build_export_command

        command = _build_export_command(
            trace_path=self.root / "trace.jsonl",
            package_dir=self.root / "package",
            timeout_s=1.0,
            max_output_tokens=3000,
            model="deepseek-v4-pro",
            api_key_env=FAKE_API_KEY_ENV,
            mode="mock",
            embodiment_mode="mode_a",
            mock_base_url="http://127.0.0.1:1",
            advisor_enabled=True,
        )
        argv_text = "\n".join(command)
        self.assertNotIn(FAKE_API_KEY_VALUE, argv_text)
        env_arg_index = command.index("--llm-advisor-api-key-env")
        self.assertEqual(command[env_arg_index + 1], FAKE_API_KEY_ENV)


if __name__ == "__main__":
    unittest.main()
