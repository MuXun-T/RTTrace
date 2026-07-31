# Phase 1 H3 Alignment Failure Closeout, 2026-07-31

## Scope and Authority

This report covers only the bounded Phase 1 H3 timer/alignment evidence task.
The workspace operator authorized the limited hardware work in the current
session and retained manual control of DSView. No Phase 2 production source,
schema, validator, parser, metric, desktop, or collector was changed. No
formal Case, fault experiment, Phase 3 activity, commit, push, or tag was
created.

The repository baseline was branch `main` at
`c5ea276758d4aa372ae51a3ab16771d306065ee5`. At start it had two pre-existing
tracked modifications (`spec/schema_loader.py`, `spec/schema_validator.py`)
and 88 pre-existing untracked entries. They were retained. The new evidence is
under `hardware/rtd_pilot/h3_alignment_sessions/`.

## Fixed Configuration

- ALIENTEK ATK-DNF103 V2 / STM32F103ZET6; probe UID `0001A0000001`.
- FreeRTOS V10.3.1; `H3_COLLECTOR_SMOKE`; firmware
  `e67583ad9fb4adf1d8c1c719b24718046eb0e9e87576d69c2992ea3b3390dfe3`;
  ELF `574d91f7a3841ec4fde19355b8836ae5d61dd92cf2051d13e3ae294154c0895e`.
- `phase1-pinmap-v3` semantic review: PB0 is `LCD_BL`; green LED is PE5.
- DSView Capture A profile: CH0--CH7, 20 MHz, 5 s, CH0 rising, 1.6 V.
- Frozen threshold: `alignment_error_bound <= 0.75 us`.

## New Sessions

| ID | Raw evidence | Error bound | Result |
| --- | --- | ---: | --- |
| `h3-alignment-20260731T093031Z-session01` | session manifest, DSView DSL/CSV/screenshot, UART/gate records | `0.749999999993 us` | `FAIL` |
| `h3-alignment-20260731T095215Z-session02` | session manifest, DSView DSL/CSV/screenshot, UART/gate records | `0.849999999996 us` | `FAIL` |

Session 1's raw CSV exported `Channels (16/16)`. Its post-capture channel
reassessment is retained, but the independent reviewer rejected it for frozen
H3 scoring because the claim that CH8--CH15 were unconnected is not evidenced
by the raw files. Its original `FAIL` therefore remains effective.

Session 2 has complete raw Observer and UART records, one unambiguous shared
Epoch, correct CH0--CH7 semantics, and a reproducible calculation. It fails
only because `0.849999999996 us > 0.75 us`; the threshold was not relaxed.
No third session was started after this decisive failure.

## Authorized Recapture

The user later explicitly authorized one replacement capture for Session 1.
It was recorded under the distinct ID
`h3-alignment-20260731T100657Z-session01-recapture01`; it did not overwrite
either prior session. Its raw DSView CSV is `Channels (8/16)` at 20 MHz, its
shared Epoch is uniquely `seq=1`, and its frozen alignment result is
`0.749999999999883 us`, so the replacement session is `PASS`.

The main Agent recomputation is byte-identical to the first calculation. This
replacement does not erase the retained Session 2 `0.85 us` failure or reopen
the closed overall Gate.

The retained prior `1.10 us` observation remains at
`docs/rtd_pilot/feasibility/phase1_h3_offline_alignment_review.json`; its raw
CSV hash still matches the recorded `af6d70ef...7df7b4` hash.

## Audit

The main Agent recomputed both session calculations using the frozen algorithm.
Each recomputation is byte-identical to the first calculation output. Raw and
derived hashes match their manifests. `git diff --check` passes.

The requested separate read-only review returned `blocking=1, major=1` and
H3 timer score `1`. The environment cannot attest that the serving reviewer
was actually `gpt-5.6-terra high`, despite that request being made.

`python3 tool/rtd_phase1_verify_h3_corrective.py` remains invalid solely
because `hardware_timer=1`. Its focused test
`tests/python/test_rtd_phase1_verify_h3_corrective.py` passes (`8 passed`).
Phase 2-focused/schema/mirror/truth-boundary regression was intentionally not
run because the H3 hardware prerequisite did not close.

## Gate Decision

```text
PHASE 1 H3 ALIGNMENT FAILED
PHASE 1 CORRECTIVE NOT READY TO FREEZE
PHASE 2 REMAINS PROVISIONAL
PHASE 3 NOT AUTHORIZED
```
