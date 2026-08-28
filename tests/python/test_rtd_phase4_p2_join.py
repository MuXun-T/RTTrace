from __future__ import annotations

from copy import deepcopy

import pytest

from p4_capture.contracts import P4ContractError, make_test_only_envelope
from spec.rtd_pilot_contracts import ContractError, validate_capture_bundle
from test_rtd_pilot_contracts import bundle


def test_ephemeral_test_only_ledger_ccm_cir_oar_cvr_inventory_join() -> None:
    fixture = make_test_only_envelope("p2-join-fixture", bundle())
    payload = fixture["payload"]
    status = validate_capture_bundle(payload)
    assert status == {"capture:one": "valid"}
    capture_id = payload["ccm"][0]["capture_id"]
    identity = {
        record["capture_id"]
        for kind in ("ledger", "ccm", "cir", "oar", "inventory", "cvr")
        for record in payload[kind]
    }
    assert identity == {capture_id}
    assert fixture["test_only"] is True


def test_ephemeral_join_cross_capture_fails_closed() -> None:
    fixture = make_test_only_envelope("p2-join-fixture", bundle())
    crossed = deepcopy(fixture["payload"])
    crossed["inventory"][0]["capture_id"] = "capture:other"
    from spec.rtd_pilot_contracts import finalize_record

    crossed["inventory"][0] = finalize_record(crossed["inventory"][0])
    with pytest.raises(ContractError, match="capture set|capture_id"):
        validate_capture_bundle(crossed)


def test_non_test_join_envelope_is_rejected() -> None:
    with pytest.raises(P4ContractError, match="test_only=true"):
        from p4_capture.contracts import validate_test_only_envelope

        validate_test_only_envelope({"test_only": False, "kind": "p2-join-fixture", "payload": {}})
