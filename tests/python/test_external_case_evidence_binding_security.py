from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket
import subprocess
import unittest
from unittest import mock

from parser.external_case_evidence_binding import binding_bytes, build_case_bindings
from parser.external_semantic_replay import replay


ROOT = Path(__file__).resolve().parents[2]
BINDING_ROOT = ROOT / "tests/python/fixtures/external_validation/evidence_bindings"
INPUTS = (
    ROOT / "docs/phase7_external_trace_sources/source_inventory.json",
    ROOT / "tests/python/fixtures/external_validation/packages/reports/opened.json",
    *(ROOT / "tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw" / name for name in ("example.btf", "example.vcd", "example-4cores.btf", "example-50k.btf")),
    *(ROOT / "tests/python/fixtures/external_validation/packages/per_case" / case / "package_manifest.json" for case in ("freertos_btf_1core", "freertos_vcd_1core", "freertos_btf_4cores", "freertos_btf_50k")),
    *(ROOT / "tests/python/fixtures/external_validation/packages/reports/per_case" / (case + ".json") for case in ("freertos_btf_1core", "freertos_vcd_1core", "freertos_btf_4cores", "freertos_btf_50k")),
    *(ROOT / "tests/python/fixtures/external_validation/replay/reports" / (case + ".json") for case in ("freertos_btf_1core", "freertos_vcd_1core", "freertos_btf_4cores", "freertos_btf_50k")),
)


def snapshots(paths: tuple[Path, ...]) -> dict[Path, str]:
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


class ExternalCaseEvidenceBindingSecurityTests(unittest.TestCase):
    def test_reproduction_is_deterministic_and_uses_only_logical_paths(self) -> None:
        before = snapshots(INPUTS)
        first = tuple(binding_bytes(item) for item in build_case_bindings())
        second = tuple(binding_bytes(item) for item in build_case_bindings())
        self.assertEqual(first, second)
        self.assertEqual(before, snapshots(INPUTS))
        for raw in first:
            value = json.loads(raw)
            self.assertNotIn(str(ROOT), raw.decode("utf-8"))
            for key, path in value.items():
                if key.endswith("_logical_path"):
                    self.assertFalse(path.startswith("/"))
                    self.assertNotIn("/tmp/", path)
                    self.assertNotIn("/temp/", path)
        fixtures = {path.stem: path.read_bytes() for path in BINDING_ROOT.glob("*.json")}
        self.assertEqual(tuple(raw for raw in first), tuple(fixtures[item.case_id] for item in build_case_bindings()))

    def test_truth_path_does_not_invoke_side_effect_or_replay_apis(self) -> None:
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError), mock.patch.object(subprocess, "run", side_effect=AssertionError), mock.patch("os.system", side_effect=AssertionError), mock.patch("parser.external_semantic_replay.replay", side_effect=AssertionError):
            tuple(binding_bytes(item) for item in build_case_bindings())
        self.assertTrue(callable(replay))

    def test_production_binding_modules_do_not_import_side_effect_surfaces(self) -> None:
        for path in (ROOT / "parser/external_case_evidence_binding.py", ROOT / "parser/external_case_evidence_binding_models.py", ROOT / "tool/build_external_case_evidence_bindings.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                for forbidden in ("subprocess", "socket", "getenv", "environ", "external_semantic_replay"):
                    self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
