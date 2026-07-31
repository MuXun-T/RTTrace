# Phase 1 H3 Semantic Evidence Review

Review type: independent, read-only offline semantic review. Reviewer
`INDEPENDENT_READ_ONLY_H3_AUDIT_RETRY` is distinct from the original
`SELF_REVIEW`; it verifies the meaning of retained evidence but does not
substitute for physical truth or create any new hardware observation.

## Exact Configuration

The only reviewed configuration is Alientek ATK-DNF103 V2,
STM32F103ZET6 UID `05d7ff34-334e5630-43057222`, FreeRTOS V10.3.1,
`H3_COLLECTOR_SMOKE`, firmware
`e67583ad9fb4adf1d8c1c719b24718046eb0e9e87576d69c2992ea3b3390dfe3`,
ELF `574d91f7a3841ec4fde19355b8836ae5d61dd92cf2051d13e3ae294154c0895e`,
STM32CubeF1 BSP, Arm GNU Toolchain 15.2.1, CMake 3.22.1, Ninja 1.13.0,
USART1 PA9/PA10 at 115200 8N1, and Capture A/B settings recorded in
`h3_preflash_metadata.json`. A clean temporary ARM build on 2026-07-31
reproduced both firmware and ELF hashes. This establishes reproducibility of
the retained build, not a new hardware result.

## PB0 Resolution

`phase1-pinmap-v2` already identifies Elite V2 PB0 as `LCD_BL` and PE5 as the
green LED. The exact firmware binds channel 7 to GPIOB pin 0 and drives it
high during the H3 recorder-pressure interval. Capture A's external raw CSV
is hash-bound by the H3 evidence manifest and has non-flat CH7. Therefore the
voltage marker is PB0 high; it is not a low-active green-LED assertion. The
legacy Fire V2 prose and the firmware source comment that call PB0 a LED are
retained as superseded mistakes. The corrective machine record is
`stm32f103zet6_alientek_elite_v2_phase1_v3.json`; it does not replace the v2
pin-map hash embedded in the captured firmware.

## Marker Semantics

The reviewed semantic set is complete for the bounded H3 smoke: F1 uses
PC4 target-ready/dispatch and PC5 higher-priority interference; F2 uses PC6
mutex holder and PC7 waiter; F3 uses PB5 TIM3 IRQ activity; CH7/PB0 marks the
gate-enabled H3 pressure interval. Capture A demonstrates non-flat CH0--CH7;
Capture B provides the separately sampled 100 MHz PB5 pulse-width witness
(57 pulses, minimum 512 samples). Firmware source and the hash-reproduced
binary show the intended producer for each marker. The waveform proves the
listed voltage transitions only; it does not prove a fault, causality,
manifestation, or diagnosis.

The direct external Capture A CSV review constrains the semantic claims: F1
has PC5 high from 0.07556075 s to 0.086196 s and PC4 ready high from
0.0777621 s to 0.08625735 s, with the PC4 falling transition following the
interferer end. Any later unclosed PC4-high interval is retained as ready and
not claimed as target execution. F2 has PC6 holder high from 0.0724458 s to
0.09397655 s and PC7 waiter high from 0.0829727 s to 0.0949727 s, including
about 11.00 ms overlap. These interval interpretations use the raw external
CSV named and hashed by `h3_evidence_manifest.json`; the repository's
normalized CSV is not treated as a complete F1/F2/F3 source.

## Transport And Observer

The exact UART log contains H3 BOOT, configuration, gate, Epoch, trace and
post-pressure counter records. The retained validator confirms capacity 64,
strictly increasing output sequence with a gap, high watermark 64, 62 dropped
and 62 overflow records. DSView raw files are independent external Observer
artifacts with their own sampling clock; the protocol excludes parser, metric,
lineage, diagnoser, Agent, CCM, CIR, OAR, CVR and Ledger from the Observer
truth path. Hashes identify these files but do not establish their semantics;
the semantic conclusion above uses source, exact configuration, raw CSV and
the independent transport record together.

## Alignment And Release

The offline alignment review parses a numeric one-session calibration
observation, but it is `DEFER`: only one matched H3 Epoch exists, while the
frozen protocol requires three independent real sessions for an accepted
alignment bound. This is the sole remaining H3 corrective blocking item.
License and release evidence remains `OWNER_CONTROLLED_RESEARCH_ACCESS` with
no automatic raw publication. No historical raw evidence was changed.
