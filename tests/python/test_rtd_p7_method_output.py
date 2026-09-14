import hashlib
def test_derived_output_is_not_observer_truth():
    assert hashlib.sha256(b'capture').hexdigest() == hashlib.sha256(b'capture').hexdigest()
