# Phase 1 H2 limited bench-test card

Status: prepared, not executed. This card is the only permitted next hardware
activity after the operator connects the board and separately authorizes H2.
It is not Phase 4 collection, Case acquisition, fault injection, or a paper
experiment.

## Preconditions

- Exact board: `fire-f103zet6-v2-1`, STM32F103ZET6, single core.
- Probe: Fire CMSIS-DAP UID `0001A0000001`; target `stm32f103ze`.
- Observer: DSLogic with a separate capture clock, CH0--CH7 and common ground
  wired exactly as `phase1-pinmap-v2` specifies.
- UART: CH340 `/dev/ttyUSB0`, USART1 PA9/PA10, 115200 8N1, no flow control.
- The Keil-image backup exists, is 524288 bytes, and hashes to
  `222444b4bd6c551822acd673b3f2325c3c86bb0c280ea96afad98479ee4ecca4`.
- The operator confirms each header silk and the CH340-to-USART1 jumper before
  power-on. No signal is connected to PC0, PC1, PB10, SWD, reset, boot,
  oscillator, power, or unaudited peripheral pins.

## Evidence card

| Check | Firmware / independent evidence | Pass condition | Retain on failure |
| --- | --- | --- | --- |
| GPIO baseline | bare-metal `GPIO_ONLY` | CH0 inactive outside epochs; CH1 timer scheduled; no unexpected capture trigger | raw DSLogic file, UART file if present, metadata |
| Calibration and UART | bare-metal `GPIO_UART` | CH1 nominal 1 kHz from TIM2 scheduling; UART BOOT and contiguous matching CH0/UART epoch pairs | raw DSLogic, normalized CSV, UART log, hash inventory |
| Repetition | bare-metal `GPIO_UART` | three independent reset/session captures without overwrite; measured drift/alignment bound | every attempted Capture and analysis output |
| Task marker | FreeRTOS `TASK_SMOKE` | PC4/PC5 bracket only the tasks' active CPU work, not `vTaskDelay()` blocked time; UART records bootstrap and epoch cross-checks | DSLogic and UART artifacts |
| Mutex marker | FreeRTOS `MUTEX_SMOKE` | PC6 spans holder ownership and PC7 spans waiter blocking; both intervals are paired | DSLogic and UART artifacts |
| IRQ marker | FreeRTOS `IRQ_SMOKE` | PB5 rises only in the enabled TIM2 ISR and returns inactive on every normal ISR exit | DSLogic and UART artifacts |
| Combined/perturbation | FreeRTOS `BASE`, `GPIO_ONLY`, `GPIO_UART`, `GPIO_UART_RECORDER`, `COMBINED_SMOKE` | summary inputs are observed, not synthesized; relative perturbation is reported without a significance claim | all variants' raw artifacts and summaries |

## Frozen DSView setup

Use DSLogic in Logic Analyzer mode with CH0--CH7 enabled and the threshold set
to 1.65 V, or the nearest setting recorded verbatim in metadata. For
`calibration_20mhz`, use 20 MHz, 5 seconds, and a CH0 rising-edge trigger. For
`irq_precision_100mhz`, use 100 MHz, 500 ms, 20% pre-trigger, and a CH6
rising-edge trigger. Save the vendor raw file outside git before export. The
IRQ profile is invalid unless each retained PB5 high pulse spans at least two
samples; a lower rate or a missing CH6 trigger is not accepted as an IRQ
duration measurement.

## Semantic scope and H3 boundary

CH0 is the phase-1 epoch boundary, CH2/CH3 expose task work windows,
CH4/CH5 expose mutex hold/wait windows, CH6 exposes the IRQ interval, and CH7
is a recorder/controlled-pressure marker. These are H2 smoke signals only.
They do not by themselves prove all Phase 0 F1/F2/F3 Case-start, Case-end,
injection-enable, target-release, and manifestation-predicate markers. H3
remains blocked until a reviewed marker-capability map covers those registered
semantics, the target collector can expose its own capacity/sequence/overflow
state, and the exact release route is cleared.

No H2 outcome may be promoted to an OAR, CVR, CCM, CIR, Ledger record, fault
truth, diagnoser input, or paper experiment result.
