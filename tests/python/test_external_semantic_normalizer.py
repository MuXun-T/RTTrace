from parser.external_semantic_normalizer import normalize_btf
from parser.freertos_btf_parser import parse_freertos_btf
def test_source_order_is_retained():
 events=normalize_btf(parse_freertos_btf(b"#version 2.2.0\n#creator x\n#creationDate x\n#timeScale us\n1,Core_0,0,C,x,0,set_frequency,1\n1,Core_0,0,C,x,0,set_frequency,2\n")); assert [x.source_record_index for x in events]==[0,1]
