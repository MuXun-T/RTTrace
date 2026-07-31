# Phase 1 H3 Corrective Closeout

Status: `BLOCKED_BY_HARDWARE_TIMER_ALIGNMENT`. This is a versioned corrective
supplement, not a rewrite of the historical Phase 1 freeze, v1 scorecard,
pin-map v2, raw captures, or firmware source.

## Corrected Result

The exact configuration C0 is ALIENTEK ATK-DNF103 V2 / STM32F103ZET6,
FreeRTOS V10.3.1, STM32CubeF1 v1.8.7, `H3_COLLECTOR_SMOKE`, firmware
`e67583...dfe3`, ELF `574d91...895e`, Arm GNU 15.2.1/CMake 3.22.1/Ninja
1.13.0, USART1 PA9/PA10 at 115200 8N1, and the H3 Capture A/B observer
settings. The independent offline reviewer
`INDEPENDENT_READ_ONLY_H3_AUDIT_RETRY` is distinct from the original
`SELF_REVIEW`.

`phase1_h3_scorecard_v2.json` records all eleven mandatory dimensions with an
integer numeric score and the required evidence, configuration, semantic,
reviewer, decision, and risk fields. Ten dimensions score 2. `hardware_timer`
scores 1 and is `DEFER`; a `PASS` label is never used as a substitute for a
numeric score.

## PB0 Decision

PB0 is `CLOSED` in the corrective v3 machine record. On the selected Elite V2,
PB0 is `LCD_BL`, while PE5 is the green LED. The exact H3 firmware drives PB0
high for the CH7 recorder-pressure interval; Capture A's hash-bound raw
observer evidence shows CH7 transitions. It is a voltage marker, not a
low-active green-LED claim.

`phase1_pin_map.md`, `phase1_pin_conflict_audit.md`, the old protocol prose,
and the historical firmware comment retain the Fire V2/LED mistake as
superseded references. They were not silently edited. The v3 corrective map,
semantic review, and scorecard make the selected-board interpretation explicit.

## Remaining Blocker

The retained H3 Capture A offline computation has 5,001 calibration intervals,
drift `-259.938 ppm`, and a maximum CH1 period absolute error of `1.10 us`.
The frozen bound is `0.75 us`. Its one matched GPIO/UART Epoch has residual
`0.0`, but only Capture A supplies such an Epoch; Capture B is an F3
pulse-width witness and cannot count as a second alignment session. The
protocol requires three independent real H3 sessions before accepting an
alignment bound. Therefore this item cannot become score 2 through an offline
document correction.

`blocking=1`; `major=0`. The preflight-scoped gate receipt and UART log that
contains early attempts/two Epochs are retained and explicitly limited in the
scorecard; neither is represented as a clean standalone formal capture.

## Evidence And Scope

Primary corrective assets are:

- `hardware/rtd_pilot/pinmaps/stm32f103zet6_alientek_elite_v2_phase1_v3.json`
- `docs/rtd_pilot/feasibility/phase1_semantic_evidence_review.md`
- `docs/rtd_pilot/feasibility/phase1_h3_offline_alignment_review.json`
- `docs/rtd_pilot/feasibility/phase1_h3_scorecard_v2.json`
- `tool/rtd_phase1_verify_h3_corrective.py`

The verifier resolves repository evidence, checks all mandatory scores and
identity consistency, PB0, F1/F2/F3, UART, Observer isolation, numeric
alignment data, reviewer separation, and the release route. It correctly fails
the current scorecard only because `hardware_timer=1`.

No hardware was connected, flashed, sampled, or collected for this closeout.
To close the blocker, explicit H2/supplementary hardware authorization is
required for at least three new independent H3 sessions under the predeclared
protocol, retaining raw DSView/UART/metadata and without weakening the frozen
0.75 us bound after observation.

Phase 1 corrective gate: `NOT PASSED`.
