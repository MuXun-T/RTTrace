# Phase 1 H3 Corrective Scorecard v2

Status: `CORRECTIVE_CLOSEOUT_BLOCKED`. This versioned supplement retains the
historical v1 `PASS` scorecard unchanged and replaces neither its hashes nor
its claimed hardware evidence. The machine record is
`phase1_h3_scorecard_v2.json`.

The exact reviewed configuration is C0: ALIENTEK ATK-DNF103 V2 /
STM32F103ZET6, FreeRTOS V10.3.1, STM32CubeF1 v1.8.7,
`H3_COLLECTOR_SMOKE`, firmware `e67583...dfe3`, ELF `574d91...895e`, Arm GNU
15.2.1/CMake 3.22.1/Ninja 1.13.0, USART1 PA9/PA10 at 115200 8N1, and the
Capture A/B DSView settings in the JSON record. The independent reviewer is
`INDEPENDENT_READ_ONLY_H3_AUDIT_RETRY`, distinct from the original
`SELF_REVIEW`.

| Dimension | Numeric score | Decision | Remaining risk |
| --- | ---: | --- | --- |
| physical availability | 2 | `PASS` | Candidate-preflight receipt is not independent identity certification. |
| license | 2 | `PASS` | Recorded route is not third-party legal advice. |
| toolchain reproducibility | 2 | `PASS` | Evidence is limited to the recorded build environment. |
| GPIO | 2 | `PASS` | Legacy PB0 LED prose remains retained as superseded. |
| hardware timer | 1 | `DEFER` | 1.10 us > frozen 0.75 us; only 1/3 aligned H3 sessions. |
| UART/transport | 2 | `PASS` | Raw log contains early attempts and two Epochs. |
| priority control | 2 | `PASS` | Bounded smoke capability only. |
| mutex support | 2 | `PASS` | Bounded smoke capability only. |
| controllable IRQ | 2 | `PASS` | Capture B has no CH0 Epoch. |
| observer isolation | 2 | `PASS` | Normalized CSV is partial; raw CSV carries marker review. |
| data release rights | 2 | `PASS` | Controlled access is not automatic publication. |

PB0 is closed by the v3 corrective pin map: PB0 is `LCD_BL`, PE5 is the green
LED, and CH7 is a PB0-high recorder-pressure voltage marker. The prior Fire V2
prose and historical firmware comment are preserved as superseded mistakes.

The scorecard does not establish a frozen timer/alignment bound. New H3
hardware sessions require explicit user authorization; none were performed for
this corrective closeout.
