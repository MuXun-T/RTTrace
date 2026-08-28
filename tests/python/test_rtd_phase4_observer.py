from __future__ import annotations

from copy import deepcopy

import pytest

from p4_capture.contracts import P4ContractError
from p4_capture.observer import (
    bind_alignment_input,
    make_alignment_input,
    make_alignment_report,
    make_observer_artifact_inventory,
    make_shared_epoch_record,
    validate_alignment_input,
    validate_alignment_report,
)


def _observer_epoch(*, capture_id: str = "capture:p4-observer", session_id: str = "session:p4-observer", state: str = "test_only"):
    observer = make_observer_artifact_inventory(
        capture_id=capture_id,
        session_id=session_id,
        observer_artifact_hash="d" * 64 if state == "test_only" else None,
        state=state,
        test_only=True,
        logical_name="observer/test-only.logic",
    )
    epoch = make_shared_epoch_record(
        capture_id=capture_id,
        session_id=session_id,
        epoch_id="epoch:p4-test",
        trace_sequence=7 if state == "test_only" else None,
        trace_timestamp=700 if state == "test_only" else None,
        observer_timestamp=7.0 if state == "test_only" else None,
        test_only=True,
    )
    return observer, epoch


def test_test_only_alignment_input_and_output_are_explicitly_non_hardware() -> None:
    observer, epoch = _observer_epoch()
    alignment_input = make_alignment_input(
        observer,
        epoch,
        raw_trace_id="artifact:test-only-raw",
        raw_trace_hash="a" * 64,
        algorithm_id="alignment:test-only",
        algorithm_version="1.0",
        test_only=True,
    )
    report = make_alignment_report(
        observer,
        epoch,
        state="test_only",
        alignment_error_bound=0.0,
        algorithm="alignment:test-only",
        test_only=True,
    )
    bound = bind_alignment_input(report, alignment_input, observer=observer, epoch=epoch)

    validate_alignment_input(alignment_input, observer=observer, epoch=epoch)
    validate_alignment_report(bound, observer=observer, epoch=epoch)
    assert bound["test_only"] is True
    assert bound["alignment_error_bound"] == 0.0
    assert bound["alignment_input_digest"] == alignment_input["record_digest"]


@pytest.mark.parametrize("state", ["missing", "partial", "corrupt", "unalignable", "pending"])
def test_missing_or_unalignable_observer_has_no_alignment_bound(state: str) -> None:
    observer, epoch = _observer_epoch(state=state)
    report = make_alignment_report(
        observer,
        epoch,
        state=state,
        alignment_error_bound=None,
        algorithm="alignment:future-hardware",
        test_only=True,
    )

    validate_alignment_report(report, observer=observer, epoch=epoch)
    assert report["alignment_error_bound"] is None


def test_cross_capture_and_fake_test_only_alignment_fail_closed() -> None:
    observer, epoch = _observer_epoch()
    other_observer, _ = _observer_epoch(capture_id="capture:p4-other", session_id="session:p4-other")

    with pytest.raises(P4ContractError, match="identity mismatch"):
        make_alignment_report(other_observer, epoch, state="test_only", alignment_error_bound=0.0, algorithm="alignment:test", test_only=True)
    with pytest.raises(P4ContractError, match="never aligned"):
        make_alignment_report(observer, epoch, state="aligned", alignment_error_bound=0.0, algorithm="alignment:test", test_only=True)

    report = make_alignment_report(observer, epoch, state="test_only", alignment_error_bound=0.0, algorithm="alignment:test", test_only=True)
    tampered = deepcopy(report)
    tampered["shared_epoch_digest"] = "b" * 64
    with pytest.raises(P4ContractError, match="epoch digest mismatch"):
        validate_alignment_report(tampered, observer=observer, epoch=epoch)
