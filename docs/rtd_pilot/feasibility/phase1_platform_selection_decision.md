# Phase 1 platform selection decision

```text
decision_id = phase1-platform-selection-alientek-elite-v2-20260730
platform_selection_status = SELECTED
decision_maker = PROJECT_OWNER
reviewer = SELF_REVIEW
approval_authority = OWNER_APPROVAL
approval_status = OWNER_APPROVED
decision_date = 2026-07-30
```

## Selected exact configuration

| Field | Frozen value |
| --- | --- |
| selected_board | `alientek-atk-dnf103-v2-f103zet6-05d7ff34-334e5630-43057222` |
| selected_board_revision | ALIENTEK ATK-DNF103 V2 |
| selected_MCU | STM32F103ZET6 (`stm32f103ze`) |
| selected_RTOS | FreeRTOS V10.3.1 from the recorded STM32CubeF1 tree |
| selected_firmware_hash | BIN `e67583ad9fb4adf1d8c1c719b24718046eb0e9e87576d69c2992ea3b3390dfe3` |
| selected_ELF_hash | `574d91f7a3841ec4fde19355b8836ae5d61dd92cf2051d13e3ae294154c0895e` |
| selected_toolchain | Arm GNU Toolchain 15.2.1; CMake 3.22.1; Ninja 1.13.0 |
| selected_build_configuration | H3 `CMakeCache.txt` SHA-256 `3db978368eea1976acd14c665c902410a073aeda8e9e1b4b35ebfeecd0aec5f1` |
| selected_UART_transport | USART1 PA9, 115200 8N1, no flow control; collector capacity 64, `drop_new`, IRQ decimation 20 |
| selected_observer | DSView Capture A CH0--CH7, 20 MHz/5 s, CH0 rising; Capture B CH0/CH1/CH6, 100 MHz/500 ms, CH6 rising; 1.6 V threshold |
| selected_pin_map | `phase1-pinmap-v2`, SHA-256 `7cbaf03a539d01df94062d0061b58adef71eb622cbf7d754c3c9a9254bdfc04e` |
| selected_clock_alignment_protocol | `phase1_clock_alignment.md`: CH1/TIM2 calibration with CH0/UART Epoch association |

The H3 scorecard is
`phase1_h3_mandatory_scorecard.md` SHA-256
`8866be2602b795f05ecde2b0a97ab04c48accfe0b77bfbcb569eb6001eb1be61`;
the machine record is `phase1_h3_mandatory_scorecard.json` SHA-256
`ff3e9151ece86991b4e766f7081b388836521e4780f6911ea9e8cbe8457c6c60`.
The owner-controlled data-release decision is
`phase1_data_release_decision.md` SHA-256
`3022914ceecbbafc99ef7bad2db16ff8dccbfec1149d79082d683f84ff1c0b63`.

## Scope, limitations, and reselection

This selects only the exact configuration above. It does not select every
STM32 board, every STM32F103, every FreeRTOS version, or a generic DSView
setup. It establishes Phase 1 feasibility only: it does not create a dataset,
Case, fault truth, Ledger, CCM, CIR, diagnoser, or paper experiment result.

Re-evaluate the affected H3 items before using a changed board model/revision,
MCU, RTOS/version, firmware or ELF, collector/UART configuration, toolchain
major version, Observer channel/profile, or pin map. Documentation-only edits,
derived-script edits that do not alter hardware semantics, and optional
redacted derivatives do not by themselves trigger full reselection.
