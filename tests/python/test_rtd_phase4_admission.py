from __future__ import annotations

from p4_capture.admission import admit_before_rebuild


def _v():
    ccm={"capture_id":"capture:a","session_id":"session:a","capability_manifest_id":"ccm:a","record_digest":"d"}
    cir={"capture_id":"capture:a","session_id":"session:a","capability_manifest_id":"ccm:a","capability_manifest_digest":"d","integrity_status":"complete","loss":False,"overflow":False,"truncation":False,"corruption":False,"mapping_mismatch":False,"natural_overflow":False}
    x={"capture_id":"capture:a","session_id":"session:a"}; d={**x,"raw_state":"available","decoder_state":"complete"}; a={**x,"state":"aligned","alignment_error_bound":0.1,"threshold_hash":"t"}; return ccm,cir,x,d,a

def test_admission_calls_rebuild_only_after_clean_gate():
    ccm,cir,x,d,a=_v(); called=[]
    assert admit_before_rebuild(ccm=ccm,cir=cir,context=x,decoder=d,alignment=a,counter_reconciliation={"pass":True},threshold_hash="t",rebuild=lambda:called.append(1)).status == "admitted_clean"
    assert called == [1]
    d["raw_state"]="corrupt"; called.clear()
    assert admit_before_rebuild(ccm=ccm,cir=cir,context=x,decoder=d,alignment=a,counter_reconciliation={"pass":True},threshold_hash="t",rebuild=lambda:called.append(1)).status == "denied"
    assert not called
