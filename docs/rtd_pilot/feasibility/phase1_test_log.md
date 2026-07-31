# Phase 1 non-physical test log

2026-07-25 local checks completed without flash/reset/target mutation:

* Arm GNU Toolchain 15.2.1, CMake 3.22.1, Ninja 1.13.0, pyOCD 0.45.1 detected.
* Bare-metal `BASE`, `GPIO_ONLY`, and `GPIO_UART` Release CMake/Ninja builds pass.
* FreeRTOS `BASE`, `GPIO_ONLY`, `GPIO_UART`, `GPIO_UART_RECORDER`, `TASK_SMOKE`, `MUTEX_SMOKE`, `IRQ_SMOKE`, and `COMBINED_SMOKE` Release builds pass.
* Debug and Release builds of bare-metal and FreeRTOS combined variants pass after correcting a duplicate SysTick wrapper and enabling the explicitly required FreeRTOS `vTaskDelay` API.
* `python3 -m unittest hardware/rtd_pilot/tests/test_tools.py` passes 14 tests, including exact board/target/probe fail-closed cases; Python byte compilation, shell syntax checks, and `git diff --check` pass.
* Pin-map-v2 safety tests bind PC2--PC7/PB5/PB0, USART1 PA9/PA10, and reject v1/USART3 metadata. Flash preflight rejects metadata lacking the pinned target/UID, board/session IDs, build/firmware/pin-map hashes, or the selected ELF hash.
* Combined FreeRTOS ELF has `.isr_vector` at `0x08000000`; `TIM2_IRQHandler`, task A/B, holder, and waiter symbols are present. Size: text 6388, data 4, bss 14820 bytes (pre-hardware feasibility only).
* Final v2 clean ARM-toolchain rebuild passed for bare-metal Debug/Release `BASE`, `GPIO_ONLY`, `GPIO_UART` (6 builds) and FreeRTOS Debug/Release `BASE`, `GPIO_ONLY`, `GPIO_UART`, `GPIO_UART_RECORDER`, `TASK_SMOKE`, `MUTEX_SMOKE`, `IRQ_SMOKE`, `COMBINED_SMOKE` (16 builds). Every artifact has a nonempty ELF/HEX/BIN/MAP; vector table and required symbols were statically checked.
* Canonical focused regression passed: 22 tests. Canonical full Python regression passed: 843 tests and 1195 subtests. No parser, metric, schema, diagnosis, CCM, Ledger, lineage, or Agent source was changed.

This is build/tool evidence, not target execution, logic-analyzer evidence, hardware truth, or a paper experiment.

2026-07-27 H2 readiness corrections completed without hardware access:

* The Phase 0 H2/H3 scorecard was reconciled with the feasibility assets. FreeRTOS is now explicitly an H2-prepared candidate, not an H3-selected platform; collector/transport, full registered F1/F2/F3 marker capability, independent Observer measurements, and data-release route remain open H3 evidence.
* Task A/B markers now enclose bounded CPU work only and exclude `vTaskDelay()` blocked time.
* TIM2 remains the 2 kHz calibration scheduler. `IRQ_SMOKE` and `COMBINED_SMOKE` now use a separately controllable TIM3 source, enabled for 250 ms and disabled for 750 ms by `task_irq_control`; PB5 is forced inactive while that source is disabled.
* Capture validation is variant-specific: `BASE` and `GPIO_ONLY` can be valid perturbation baselines without intentional UART/clock artifacts; UART-capable variants require matching epoch/clock evidence; expected nonflat channels and a frozen DSView profile are required. `calibration_20mhz` is at least 20 MHz and IRQ profiles are at least 100 MHz.
* Clean ARM-toolchain rebuild again passed for bare-metal Debug/Release `BASE`, `GPIO_ONLY`, `GPIO_UART` (6 builds) and FreeRTOS Debug/Release all eight variants (16 builds). ELF/HEX/BIN/MAP artifacts are nonempty; `.isr_vector`, `TIM2_IRQHandler`, and the new `TIM3_IRQHandler`/`task_irq_control` symbols in IRQ-capable variants were checked with `arm-none-eabi-objdump`/`nm`.
* `python3 -m unittest discover -s hardware/rtd_pilot/tests -v` passed 16 tests. Shell syntax, Python compilation, retired implementation-source pin/UART scan, and `git diff --check` passed.

This remains H2 preparation only. It does not contain a bench-test result, H3 platform selection, Capture, raw hardware dataset, CCM, CIR, Ledger, diagnoser, or paper experiment.

2026-07-28 replacement-board transition, no hardware action in this entry:

* The active candidate is the 正点原子精英 STM32F103ZE. Its preflash backup, candidate firmware write/readback, register observations, SWD gate release, and zero-byte CH340 observation are recorded only in `phase1_alientek_candidate_preflight.md` as candidate preflight facts.
* Fire V2 captures and failures remain Fire V2 history. No result, threshold, pin map, board ID, metadata, validator rule, or CH340 conclusion was transferred to the new board.
* The next action is blocked waiting for hardware connection and is limited to the new nonformal electrical/link preflight. No formal DSView window, new Capture metadata, board ID, pin map freeze, Phase 2 work, parser/diagnoser/CCM/Ledger/lineage/Agent work, commit, push, or tag is authorized by this entry.

2026-07-28 real-hardware continuation, retained as a blocker investigation:

* Bare-metal GPIO/UART calibration has three valid real captures in the external evidence workspace. The measured maxima are drift `3695.3956547738053 ppm`, alignment residual `0.014807050000000377 s`, and CH1 period absolute error `0.00000425 s`.
* RTOS `TASK_SMOKE` `formal_task03_single_gate` retained both DSLogic raw and CSV exports, but all three UART logs are empty. The capture is invalid under `phase1_capture_validity.md`; the GPIO trigger does not substitute for the required UART cross-check.
* Hardware reset and CH340 serial open have repeatedly entered STM32 System Boot ROM. A manually commanded Flash-vector start through SWD is permitted only as `session_start_method=swd_flash_vector_start`, never as repeatable reset evidence.
* A separate earlier task waveform that halted/read/continued the target inside the DSView window is retained as invalid and is not eligible for conversion into a Capture.
* The next Task smoke attempt is blocked on proving the persistent-reader preflight: open CH340 once, keep that exact reader alive, start Flash through the persistent SWD session, and observe a UART `BOOT` record before DSView is armed. No mutex, IRQ, combined, recorder-perturbation, metadata/validator completion, full regression, or final review follows until a valid Task smoke exists.
* Current host preflight: CMSIS-DAP UID `0001A0000001` and CH340 `/dev/ttyUSB0` enumerate; the installed pyOCD target catalogue contains `stm32f103ze`. DSLogic Plus `2a0e:0034` does not enumerate in this host snapshot, so no new formal DSView window was started. This is an environment observation, not target execution or Capture evidence.
* TASK04 persistent-UART BOOT preflight: after DSLogic later enumerated and DSView was configured but before sampling began, a fresh termios reader stayed open across `swd_flash_vector_start`. The target ran Flash text and pre-window reads confirmed PA9 alternate-function output and USART1 UE/TE/RE with 115200 BRR, but the UART log remained empty. No gate write or DSLogic trigger occurred. The retained external preflight is invalid and confirms the CH340-path blocker remains unresolved.
* TASK05--TASK09 modem-line preflights: CH340 opens with DTR/RTS asserted (`0x00000006`). Clearing DTR held the target in reset; clearing both lines, preserving both lines, and clearing RTS alone all retained empty UART logs. A same-fd candidate Flash-reset sequence (RTS low, DTR low for 50 ms, DTR high) reached normal FreeRTOS idle execution at `0x080006ce`, and the exact ELF statically writes the RTD1 BOOT string to USART1 before the scheduler. UART nevertheless remained empty, including after a deliberate invalid gate release. These preflights are retained outside git and establish PA9-to-CH340 physical routing/jumper as the remaining blocker. No DSView capture was started.
* TASK10--TASK12 post-reseat preflights: after a full power-off and reseating of PA9-RXD/PA10-TXD, UART remained empty with HSI 8 MHz and BRR `0x45` confirmed at runtime, with both read-only and read-write tty opens, and with RTS restored immediately after the Flash-select reset pulse. The remaining discriminating action is a nonformal electrical observation of PA9 using an unused DSLogic CH8 while preserving CH0--CH7 wiring; formal Capture remains prohibited.
* TASK18 PA9 runtime-gate electrical probe: the retained external DSLogic raw/CSV pair in `rtos_task_smoke_swd_gate/formal_task18_pa9_runtime_gate_probe` recorded 991 CH8 transitions from `0.0499842 s` through `0.06882115 s`; its shortest adjacent transition interval was `8.55 us`, consistent with USART1 115200-bit timing. The same diagnostic recorded one gated CH0 EPOCH from `0.0529106 s` through `0.05795075 s`. This proves that the MCU PA9 endpoint emits the gated firmware's UART electrical signal. It is nonformal diagnostic evidence only: CH8 is outside frozen observer CH0--CH7 and it does not substitute for `/dev/ttyUSB0` records.
* TASK19--TASK20 post-jumper-replacement UART preflights: TASK19 is retained invalid because its inherited CMSIS-DAP USB handle had become stale (`ENODEV`) before the gate write. With a new UID-pinned non-halting attach, TASK20 kept a single read-only termios CH340 reader open (`DTR/RTS=0x00000006`), accepted the one SWD `capture_armed=1` write, and still produced a zero-byte UART file. The replacement jumper therefore did not restore the required CH340 receive path. No DSView formal window was opened. The next discriminating action is simultaneous nonformal CH8 (PA9 MCU side) and CH9 (RXD/CH340 side of that same PA9-RXD jumper) observation during one gated release.

2026-07-30 H3 technical-evidence completion, retained without reopening any
historical failure:

* Exact H3 Capture A/B evidence is bound by
  `formal_captures/20260730T095442Z_h3_collector_alientek_elite_v2_retry4/h3_evidence_manifest.json`.
  Capture A has non-flat CH0--CH7; Capture B is a CH6-triggered 100 MHz
  pulse-width witness. The UART collector validator is `VALID` with capacity
  64, high-watermark 64, dropped 62, overflow 62, and a strict trace sequence
  containing a gap.
* This entry records the pre-freeze technical-evidence state. The subsequent
  owner-approved final freeze, H3 scorecard, data-release decision, and
  platform-selection decision are recorded in their dedicated 2026-07-30
  documents without changing this historical test result.
