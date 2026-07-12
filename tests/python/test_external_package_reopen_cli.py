from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tool.run_external_package_reopen import main
from tests.python.test_external_package_reopen_security import PackageValidatorTests


class PackageReopenCliTests(unittest.TestCase):
    def test_opened_report_is_canonical_and_repeatable(self) -> None:
        fixture = PackageValidatorTests(); temp, root = fixture.make_package()
        with temp, tempfile.TemporaryDirectory() as output:
            first, second = Path(output) / "first.json", Path(output) / "second.json"
            self.assertEqual(main(["--package", str(root), "--output", str(first)]), 0)
            self.assertEqual(main(["--package", str(root), "--output", str(second), "--offline"]), 0)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), hashlib.sha256(second.read_bytes()).hexdigest())
            report = json.loads(first.read_text(encoding="utf-8"))
            self.assertFalse(report["replay_evaluated"])
            self.assertFalse(report["hardware_validation"])
            self.assertNotIn("replay_pass", report)

    def test_invalid_package_writes_report_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "report.json"
            self.assertEqual(main(["--package", name, "--output", str(output)]), 3)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["open_result"], "invalid")
            self.assertEqual(report["reason_codes"], ["ERR_PACKAGE_MANIFEST_MISSING"])


if __name__ == "__main__": unittest.main()
