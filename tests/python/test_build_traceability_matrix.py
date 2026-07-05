from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class BuildTraceabilityMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]

    def _run_builder(self, output_path: Path) -> dict[str, object]:
        proc = subprocess.run(
            [
                sys.executable,
                "tool/build_traceability_matrix.py",
                "--output",
                str(output_path),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        return json.loads(output_path.read_text(encoding="utf-8"))

    def test_build_traceability_matrix_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "traceability_matrix.generated.json"
            matrix = self._run_builder(output)

        self.assertEqual(set(matrix.keys()), {"meta", "requirements", "non_functional"})
        self.assertEqual(len(matrix["requirements"]), 29)
        self.assertEqual(len(matrix["non_functional"]), 13)

        def assert_no_stage_or_phase_artifact(path: str) -> None:
            lowered = path.lower()
            self.assertNotIn("stage", lowered)
            self.assertNotIn("phase", lowered)

        for fr_id, item in matrix["requirements"].items():
            derived_from = item.get("derived_from")
            self.assertIsInstance(derived_from, dict, f"{fr_id} missing derived_from")
            tc_ids = derived_from.get("tc_ids")
            self.assertIsInstance(tc_ids, list, f"{fr_id} derived_from.tc_ids missing")
            self.assertGreater(len(tc_ids), 0, f"{fr_id} derived_from.tc_ids empty")

            artifacts = item.get("authoritative_artifacts") or []
            for artifact in artifacts:
                assert_no_stage_or_phase_artifact(str(artifact))

            if item.get("status") == "closed":
                self.assertEqual(item.get("remaining"), [], f"{fr_id} closed with non-empty remaining")

        for nfr_id, item in matrix["non_functional"].items():
            derived_from = item.get("derived_from")
            self.assertIsInstance(derived_from, dict, f"{nfr_id} missing derived_from")

            artifacts = item.get("authoritative_artifacts") or []
            for artifact in artifacts:
                assert_no_stage_or_phase_artifact(str(artifact))

            if item.get("status") == "closed":
                self.assertEqual(item.get("remaining"), [], f"{nfr_id} closed with non-empty remaining")


if __name__ == "__main__":
    unittest.main()
