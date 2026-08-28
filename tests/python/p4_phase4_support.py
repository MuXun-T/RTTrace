"""Explicitly test-only Phase 4 fixture builders.

No value in this module is a hardware artifact or a formal Case.
"""

from __future__ import annotations

from p4_capture.contracts import make_collector_config_export


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
COMMIT = "c" * 40


def config_export(
    *,
    capture_id: str = "capture:p4-a",
    session_id: str = "session:p4-a",
    test_only: bool = True,
) -> dict[str, object]:
    return make_collector_config_export(
        capture_id=capture_id,
        session_id=session_id,
        test_only=test_only,
        board_ref="board:unit",
        platform_ref="platform:stm32f103",
        pinmap_ref="pinmap:unit",
        firmware_hash=HASH_A,
        elf_hash=HASH_B,
        build_config_hash=HASH_C,
        source_commit=COMMIT,
        event_types_enabled=[
            "TASK_READY",
            "TASK_BLOCK",
            "TASK_WAKEUP",
            "TASK_DISPATCH",
            "CTX_SWITCH",
            "SCHED_DECISION",
            "SYNC_TRY",
            "SYNC_LOCK",
            "SYNC_UNLOCK",
            "IRQ_ENTER",
            "IRQ_EXIT",
            "LOSS",
            "OVERFLOW",
        ],
        task_filter=[],
        resource_filter=[],
        irq_filter=[],
        core_filter=["0", "1"],
        filter_configuration={"event_domain_mask": 0xF, "drop_unknown": True, "drop_integrity": False},
        sampling_configuration={"enabled": False, "mode": "disabled"},
        buffer_capacity=64,
        high_watermark_policy={"report_on_stop": True, "warning_threshold": 48},
        flush_policy="drop_new",
        flush_configuration={
            "flush_threshold_bytes": 128,
            "flush_interval_ms": 10,
            "segment_size_limit": 0,
            "segment_duration_ns": 0,
            "auto_flush": False,
        },
        transport_configuration={
            "type": "offline_import" if test_only else "serial",
            "endpoint": "test://fixture" if test_only else "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
            "baud_rate": 0 if test_only else 115200,
            "framing": "none" if test_only else "8N1-no-flow",
            "retry_limit": 0,
            "retry_backoff_ms": 0,
        },
        timestamp_source="dwt_cycle_counter",
        clock_resolution=1,
        payload_fields=["task_id", "obj_id", "irq_id"],
        dictionary_version="v1",
        mapping_version="v1",
        collector_version="p4-target-contract-v1",
        trace_start_boundary="capture_start",
        trace_end_boundary="capture_end",
        case_id="case:test-only-a" if test_only else None,
    )
