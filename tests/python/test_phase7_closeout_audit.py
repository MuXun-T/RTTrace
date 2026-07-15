import json
from pathlib import Path
import tempfile
import unittest

from parser.phase7_closeout_runner import canonical_audit


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/python/fixtures/phase7_closeout/valid_manifest.json"


class CloseoutAuditTests(unittest.TestCase):
    def test_valid_manifest_and_repeat(self) -> None:
        value = json.loads(FIXTURE.read_bytes())
        first = canonical_audit(value, ROOT)
        second = canonical_audit(value, ROOT)
        self.assertEqual(first, second)

    def test_checksum_mismatch_fails_closed(self) -> None:
        value = json.loads(FIXTURE.read_bytes())
        value["frozen_artifacts"][0]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            canonical_audit(value, ROOT)

    def test_missing_artifact_fails_closed(self) -> None:
        value = json.loads(FIXTURE.read_bytes())
        value["frozen_artifacts"][0]["relative_path"] = "missing/file.json"
        with self.assertRaises(ValueError):
            canonical_audit(value, ROOT)
