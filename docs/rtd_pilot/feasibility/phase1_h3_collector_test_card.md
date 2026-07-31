# Phase 1 H3 collector test card: Alientek Elite V2

Status: `CAPTURED_SELF_REVIEWED_PASS`. The exact session was captured as H3
evidence, self-reviewed by the project owner, and is bound by
`hardware/rtd_pilot/formal_captures/20260730T095442Z_h3_collector_alientek_elite_v2_retry4/h3_evidence_manifest.json`.
This is a bounded Phase 1 feasibility smoke for the exact Alientek + FreeRTOS
configuration. It is not an H3 pass, platform selection, CCM, CIR, Ledger
record, diagnoser input, fault-injection result, or Phase 2 activity.

## Fixed firmware and target

- Board: `alientek-atk-dnf103-v2-f103zet6-05d7ff34-334e5630-43057222`.
- Target/probe: `stm32f103ze` / `0001A0000001`; MCU UID words
  `0x05d7ff34`, `0x334e5630`, `0x43057222`.
- Variant: `H3_COLLECTOR_SMOKE`; session
  `alientek-elite-h3-20260730T095442Z`.
- ELF SHA-256:
  `574d91f7a3841ec4fde19355b8836ae5d61dd92cf2051d13e3ae294154c0895e`.
- Gate: `capture_armed` at `0x20000000`, one Epoch per SWD write in the
  already attached UID-pinned session.
- Collector: fixed capacity 64, monotonic sequence, `drop_new`, USART1 PA9
  transport at 115200 8N1, IRQ trace decimation 20.

The target intentionally emits 96 pressure records after gate release. A
valid H3 UART witness must report `high_watermark=64`, `dropped>0`,
`overflow>0`, and a strictly increasing exported trace sequence containing a
gap. This controlled pressure is a collector-capability demonstration only.

## Manual DSView captures

Only the operator starts and saves DSView captures.

| Capture | Channels | Rate / duration | Trigger | Required purpose |
| --- | --- | --- | --- | --- |
| A | CH0--CH7 | 20 MHz / 5 s | CH0 rising | Exact F1/F2/F3 marker map and H3 enable interval |
| B | CH0, CH1, CH6 | 100 MHz / 500 ms | CH6 rising | At least two samples per retained PB5 IRQ pulse |

For both captures use logic-analyzer mode, 1.6 V threshold, no external
sampling clock, and the operator-confirmed common ground. Retain each `.dsl`,
CSV export, and screenshot outside git. Do not overwrite either source file.

Before each manual Start, the persistent UART reader must already have written
the matching `gate.ready` from the exact H3 BOOT line. After DSView waits on
its trigger, release exactly one Epoch through the existing SWD session with
the printed `write32 0x20000000 0x00000001` command. No reset, halt, debugger
disconnect, UART reopen, or target read is allowed while a capture window is
active.

## Acceptance and stop conditions

Capture A requires non-flat CH0--CH7, the UART gate/Epoch sequence, and the
collector proof above. Capture B requires a CH6 trigger and every retained
high pulse to span at least two samples. Any missing, mismatched, or partial
artifact is retained as invalid evidence and stops H3 review; it is not
reconstructed or promoted.
