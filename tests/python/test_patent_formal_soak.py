from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tool.run_patent_formal_soak as formal_soak
from tool.run_patent_formal_soak import EXPECTED_CONSUMER_MODE, _path_bytes, _summarize_write_result


class PatentFormalSoakSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def test_path_bytes_tolerates_entries_deleted_during_scan(self) -> None:
        stable_file = self.root / "stable.bin"
        stable_file.write_bytes(b"stable")
        vanishing_file = self.root / "vanishing.bin"
        vanishing_file.write_bytes(b"deleted")
        vanishing_dir = self.root / "vanishing-dir"
        vanishing_dir.mkdir()
        original_scandir = formal_soak.os.scandir
        deleted: set[str] = set()

        class RacingScandir:
            def __init__(self, scanner: object) -> None:
                self._scanner = scanner

            def __enter__(self) -> "RacingScandir":
                self._scanner.__enter__()
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
                return bool(self._scanner.__exit__(exc_type, exc, tb))

            def __iter__(self) -> "RacingScandir":
                return self

            def __next__(self) -> object:
                entry = next(self._scanner)
                if entry.name == "vanishing.bin" and "file" not in deleted:
                    Path(entry.path).unlink()
                    deleted.add("file")
                elif entry.name == "vanishing-dir" and "dir" not in deleted:
                    Path(entry.path).rmdir()
                    deleted.add("dir")
                return entry

        def racing_scandir(path: object) -> object:
            scanner = original_scandir(path)
            if Path(path) == self.root:
                return RacingScandir(scanner)
            return scanner

        with mock.patch.object(formal_soak.os, "scandir", side_effect=racing_scandir):
            self.assertEqual(_path_bytes(self.root), stable_file.stat().st_size)

    def test_write_result_summary_drops_large_meta_and_manifest_payloads(self) -> None:
        package_dir = self.root / "package"
        (package_dir / "control").mkdir(parents=True)
        large_windows = [
            {
                "window_id": f"uw-{index}",
                "reason_code": "WAIT_WITHOUT_WAKEUP",
                "t_begin": float(index),
                "t_end": float(index + 1),
                "payload_marker": "must-not-retain",
            }
            for index in range(1000)
        ]
        meta = {
            "run_id": "run-large",
            "parser_ver": "parser-evidence-v1",
            "closure_mode": "bounded",
            "untrusted_windows": large_windows,
            "analysis_context": {"huge": ["must-not-retain"] * 1000},
        }
        manifest_entries = [
            {
                "path": f"large/payload-{index}.json",
                "category": "large",
                "count": index,
                "checksum": "a" * 64,
                "ref_keys": ["must-not-retain"] * 100,
            }
            for index in range(500)
        ]
        manifest_entries.extend(
            [
                {
                    "path": "event/events.trace",
                    "category": "event",
                    "count": 7,
                    "format": "trace",
                    "checksum": "b" * 64,
                },
                {
                    "path": "control/proof_digest.json",
                    "category": "control",
                    "count": 1,
                    "format": "json",
                    "checksum": "c" * 64,
                },
                {
                    "path": "control/frontier_snapshot.json",
                    "category": "control",
                    "count": 1,
                    "format": "json",
                    "checksum": "d" * 64,
                },
            ]
        )
        manifest = {
            "package_version": "rttrace-package-2",
            "snapshot_id": "snapshot-large",
            "entries": manifest_entries,
        }
        proof_digest = {"proof_hash": "sha256:proof", "closure_mode": "bounded"}
        self._write_json(package_dir / "meta.json", meta)
        self._write_json(package_dir / "manifest.json", manifest)
        self._write_json(package_dir / "control" / "proof_digest.json", proof_digest)
        self._write_json(package_dir / "control" / "frontier_snapshot.json", {"closure_mode": "bounded"})
        self._write_json(package_dir / "control" / "sidecar_manifest.json", {"snapshot_id": "snapshot-large"})

        write_result = SimpleNamespace(
            ok=True,
            code="OK",
            message="",
            data={
                "package_path": str(package_dir),
                "entry_count": len(manifest_entries),
                "event_count": 7,
                "alert_count": 0,
                "diagnosis_count": 0,
                "snapshot_id": "snapshot-large",
                "closure_mode": "bounded",
                "meta": meta,
                "manifest": manifest,
            },
        )

        summary = _summarize_write_result(
            write_result,
            package_path=package_dir,
            package_bytes=12345,
            sidecar_bytes=234,
            result_validity_count=3,
            proof_digest=proof_digest,
            consumer_mode=EXPECTED_CONSUMER_MODE,
        )
        rendered = json.dumps(summary, ensure_ascii=False, sort_keys=True)

        self.assertEqual(summary["run_id"], "run-large")
        self.assertEqual(summary["evidence_version"], "rttrace-package-2")
        self.assertEqual(summary["proof_hash"], "sha256:proof")
        self.assertTrue(str(summary["manifest_hash"]).startswith("sha256:"))
        self.assertTrue(str(summary["closure_hash"]).startswith("sha256:"))
        self.assertEqual(summary["event_count"], 7)
        self.assertNotIn("untrusted_windows", rendered)
        self.assertNotIn("must-not-retain", rendered)
        self.assertNotIn("large/payload-499.json", rendered)
        self.assertLess(len(rendered), 6000)

    def test_build_report_includes_parent_child_resource_tracks(self) -> None:
        package_root = self.root / "packages"
        package_root.mkdir()
        environment_summary = self.root / "environment.json"
        environment_summary.write_text("{}", encoding="utf-8")
        samples = [
            {
                "sampler_mode": "parent_child_dual",
                "process": {"role": "child", "rss_bytes": 333, "cpu_percent_interval": 12.5},
                "processes": {
                    "parent": {"role": "orchestrator", "rss_bytes": 111, "hwm_bytes": 222, "cpu_percent_interval": 3.5},
                    "child": {"role": "child", "rss_bytes": 333, "hwm_bytes": 444, "cpu_percent_interval": 12.5},
                },
                "host": {"mem_available_bytes": 1000, "soak_output_bytes": 2000},
            },
            {
                "sampler_mode": "parent_child_dual",
                "process": {"role": "child", "rss_bytes": 555, "cpu_percent_interval": 8.0},
                "processes": {
                    "parent": {"role": "orchestrator", "rss_bytes": 222, "hwm_bytes": 333, "cpu_percent_interval": 2.0},
                    "child": {"role": "child", "rss_bytes": 555, "hwm_bytes": 666, "cpu_percent_interval": 8.0},
                },
                "host": {"mem_available_bytes": 900, "soak_output_bytes": 2500},
            },
        ]
        iteration_rows = [
            {
                "status": "ok",
                "timings": {"total_seconds": 1.5},
                "package_bytes": 2048,
                "post_gc_rss_bytes": 1024,
                "proof_hash": "sha256:test-proof",
                "closure_hash": "sha256:test-closure",
                "closure_mode": "bounded",
                "consumer_mode_matches_expected": True,
                "package_contract_validation_passed": True,
                "proof_digest": {
                    "sidecar_selector_mode": "indexed_sqlite",
                    "sidecar_selector_calls": 7,
                    "sidecar_lookup_count": 9,
                    "sidecar_bytes_scanned": 123456,
                    "sidecar_index_build_seconds": 0.0,
                },
            }
        ]

        report = formal_soak._build_report(
            started_at="2026-04-21T00:00:00+0800",
            finished_at="2026-04-21T00:00:03+0800",
            duration_seconds=3.0,
            target_duration_seconds=1.0,
            input_contract={},
            environment_summary_path=environment_summary,
            linked_proof_groups=[
                {"proof_group": "A_control_plane_first"},
                {"proof_group": "B_budget_pre_freeze"},
                {"proof_group": "C_degraded_audit"},
            ],
            artifact_refs={"package_root": str(package_root), "retained_packages": []},
            iteration_rows=iteration_rows,
            samples=samples,
            error_count=0,
            first_error=None,
            stop_requested=False,
        )

        trends = report["resource_trends"]
        self.assertEqual(trends["sampler_mode"], "parent_child_dual")
        self.assertEqual(trends["parent_rss_bytes"]["max"], 222)
        self.assertEqual(trends["child_rss_bytes"]["max"], 555)
        self.assertEqual(trends["parent_peak_rss_bytes"]["max"], 333)
        self.assertEqual(trends["child_peak_rss_bytes"]["max"], 666)
        self.assertEqual(trends["parent_cpu_percent"]["max"], 3.5)
        self.assertEqual(trends["child_cpu_percent"]["max"], 12.5)
        self.assertEqual(report["mode_b_sidecar_summary"]["selector_modes"], ["indexed_sqlite"])
        self.assertEqual(report["verdict"], "pass")

    def test_build_parent_artifact_refs_includes_command_and_timestamp(self) -> None:
        refs = formal_soak._build_parent_artifact_refs(
            package_root=self.root / "packages",
            raw_root=self.root / "raw",
            resource_samples_path=self.root / "resource.jsonl",
            iterations_path=self.root / "iterations.jsonl",
            progress_path=self.root / "progress.json",
            report_path=self.root / "report.json",
            environment_summary_path=self.root / "environment.json",
            seed_selection_path=self.root / "seed_selection.json",
            sidecar_source=self.root / "dependency_sidecar.jsonl",
            sidecar_manifest_source=self.root / "sidecar_manifest.json",
            retained_packages=["/tmp/run_000001"],
            sidecar_preflight={"sidecar_bytes": 123},
            command="python3 /tmp/run_patent_formal_soak.py --duration-s 60",
            timestamp="2026-04-23T09:23:59+0800",
        )

        self.assertEqual(refs["command"], "python3 /tmp/run_patent_formal_soak.py --duration-s 60")
        self.assertEqual(refs["timestamp"], "2026-04-23T09:23:59+0800")
        self.assertEqual(refs["retained_packages"], ["/tmp/run_000001"])
        self.assertEqual(refs["sidecar_preflight"]["sidecar_bytes"], 123)

    def test_child_iteration_mode_writes_success_row(self) -> None:
        config_path = self.root / "child-config.json"
        output_path = self.root / "child-row.json"
        package_root = self.root / "packages"
        trace_path = self.root / "trace.trace"
        trace_path.write_text("trace", encoding="utf-8")
        self._write_json(
            config_path,
            {
                "iteration": 3,
                "trace_path": str(trace_path),
                "package_root": str(package_root),
                "context_delta": {"time_window": [0.0, 1.0]},
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 1},
                "closure_policy": {"allow_bounded": True},
                "embodiment_mode": "mode_a",
                "expected_consumer_mode": EXPECTED_CONSUMER_MODE,
            },
        )

        child_row = {
            "captured_at": "2026-04-20T12:00:00+0000",
            "iteration": 3,
            "package_path": str(package_root / "run_000003"),
            "timings": {"total_seconds": 1.25},
            "package_bytes": 128,
            "result_validity_count": 0,
        }

        with mock.patch.object(formal_soak, "_build_iteration", return_value=child_row) as build_iteration:
            with mock.patch.object(formal_soak, "_measure_post_gc_rss_bytes", return_value=321):
                exit_code = formal_soak._run_child_iteration(config_path=config_path, output_path=output_path)

        self.assertEqual(exit_code, 0)
        build_iteration.assert_called_once()
        rendered = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(rendered["status"], "ok")
        self.assertEqual(rendered["iteration"], 3)
        self.assertEqual(rendered["post_gc_rss_bytes"], 321)
        self.assertEqual(rendered["package_path"], str(package_root / "run_000003"))
        self.assertIn("iteration_started_at", rendered)
        self.assertIn("iteration_finished_at", rendered)

    def test_child_iteration_mode_writes_error_row(self) -> None:
        config_path = self.root / "child-config-error.json"
        output_path = self.root / "child-row-error.json"
        package_root = self.root / "packages"
        trace_path = self.root / "trace-error.trace"
        trace_path.write_text("trace", encoding="utf-8")
        self._write_json(
            config_path,
            {
                "iteration": 4,
                "trace_path": str(trace_path),
                "package_root": str(package_root),
                "context_delta": {"time_window": [0.0, 1.0]},
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": 1},
                "closure_policy": {"allow_bounded": True},
                "embodiment_mode": "mode_a",
                "expected_consumer_mode": EXPECTED_CONSUMER_MODE,
            },
        )

        with mock.patch.object(formal_soak, "_build_iteration", side_effect=RuntimeError("boom")):
            with mock.patch.object(formal_soak, "_measure_post_gc_rss_bytes", return_value=654):
                exit_code = formal_soak._run_child_iteration(config_path=config_path, output_path=output_path)

        self.assertEqual(exit_code, 1)
        rendered = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(rendered["status"], "error")
        self.assertEqual(rendered["iteration"], 4)
        self.assertEqual(rendered["error_type"], "RuntimeError")
        self.assertEqual(rendered["error_message"], "boom")
        self.assertEqual(rendered["post_gc_rss_bytes"], 654)
        self.assertEqual(rendered["package_path"], str(package_root / "run_000004"))

    def test_parent_iteration_runner_uses_subprocess_child_mode(self) -> None:
        raw_root = self.root / "raw"
        raw_root.mkdir()
        state: dict[str, object] = {"iteration": 7, "stage": "spawn_child_iteration"}
        command_seen: list[str] = []

        class FakePopen:
            def __init__(self, cmd: list[str], **_: object) -> None:
                command_seen[:] = cmd
                self.pid = 43210
                self.returncode = 0
                self._output_path = Path(cmd[cmd.index("--child-iteration-output") + 1])

            def poll(self) -> int:
                return 0

            def communicate(self) -> tuple[str, str]:
                self._output_path.write_text(
                    json.dumps({"iteration": 7, "status": "ok", "package_path": "/tmp/run_000007"}),
                    encoding="utf-8",
                )
                return ("", "")

        with mock.patch.object(formal_soak.subprocess, "Popen", FakePopen):
            result = formal_soak._run_iteration_subprocess(
                child_config={"iteration": 7},
                raw_root=raw_root,
                state=state,
                stop_requested=lambda: False,
                python_executable="/usr/bin/python-test",
                script_path=Path("/tmp/run_patent_formal_soak.py"),
            )

        self.assertEqual(command_seen[0], "/usr/bin/python-test")
        self.assertEqual(command_seen[1], "/tmp/run_patent_formal_soak.py")
        self.assertIn("--child-iteration-config", command_seen)
        self.assertIn("--child-iteration-output", command_seen)
        self.assertEqual(result["row"]["iteration"], 7)
        self.assertEqual(result["returncode"], 0)
        self.assertNotIn("sample_pid", state)
        self.assertFalse(any(raw_root.iterdir()))


if __name__ == "__main__":
    unittest.main()
