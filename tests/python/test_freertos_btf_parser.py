from pathlib import Path
import unittest
from parser.freertos_btf_parser import BtfParseError, parse_freertos_btf
ROOT=Path(__file__).resolve().parents[2]
RAW=ROOT/"tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw"
class BtfTests(unittest.TestCase):
 def test_frozen_samples_parse(self):
  for name,count in (("example.btf",2389),("example-4cores.btf",25228),("example-50k.btf",50001)):
   with self.subTest(name=name): self.assertEqual(len(parse_freertos_btf((RAW/name).read_bytes()).records),count)
 def test_fail_closed(self):
  with self.assertRaises(BtfParseError): parse_freertos_btf(b"$timescale 1us $end\n")
  with self.assertRaises(BtfParseError): parse_freertos_btf(b"#version 2.2.0\n#creator x\n#creationDate x\n#timeScale us\n1,a,0,X,b,0,x,\n")
if __name__=="__main__": unittest.main()
