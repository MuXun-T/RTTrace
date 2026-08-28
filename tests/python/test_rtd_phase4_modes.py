from p4_capture.modes import assess_capture_mode

def _f(): return {"threshold_hash":"t","observation_state":"observed","lost":0,"overflow":0,"backpressure":0,"crc":0,"truncation":0,"seq":"continuous","hwm":5,"capacity":64,"force":False,"mask":False,"offline_deletion":False,"synthetic_marker":False}
def _t(): return {"threshold_hash":"t","clean_hwm":10,"pressure_low":4,"pressure_high":8,"workload_config_hash":"w"}
def test_three_mutually_exclusive_modes():
 f=_f(); t=_t(); r={"pass":True}
 assert assess_capture_mode("clean",f,t,r)["status"]=="PASS/admitted_clean"
 assert assess_capture_mode("pressure_without_overflow",f,t,r)["status"]=="PASS/pressure_without_overflow"
 n={**f,"lost":2,"overflow":2,"hwm":64,"natural_attestation":True,"workload_config_hash":"w","delta_total":2,"gap_total":2}
 assert assess_capture_mode("natural_overflow",n,t,r)["status"]=="expected_degraded/admitted_degraded"
 assert assess_capture_mode("natural_overflow",{**n,"threshold_hash":"bad"},t,r)["status"]=="denied"
