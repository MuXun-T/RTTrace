from pathlib import Path
import hashlib
import unittest
from parser.external_package_replay_adapter import replay_input
from parser.external_semantic_replay import replay
from parser.freertos_btf_parser import BtfParseError, parse_freertos_btf
ROOT=Path(__file__).resolve().parents[2]
RAW=ROOT/"tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw"
class SecurityTests(unittest.TestCase):
 def test_format_confusion_and_raw_immutability(self):
  raw=(RAW/"example.btf").read_bytes(); before=hashlib.sha256(raw).hexdigest()
  with self.assertRaises(BtfParseError): parse_freertos_btf(b"$timescale 1us $end\n")
  replay(replay_input(RAW/"example.btf","btf")); self.assertEqual(hashlib.sha256((RAW/"example.btf").read_bytes()).hexdigest(),before)
 def test_repeated_result_is_stable(self):
  source=replay_input(RAW/"example.btf","btf"); self.assertEqual(replay(source),replay(source))
if __name__=="__main__": unittest.main()
