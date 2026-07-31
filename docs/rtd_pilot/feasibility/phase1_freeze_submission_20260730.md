# Phase 1 freeze submission: Alientek Elite V2

`PHASE1_STATUS=SUBMITTED_FOR_FREEZE_REVIEW` on 2026-07-30. The submission
receipt is `hardware/rtd_pilot/phase1_freeze/20260730T084803Z_alientek_elite_v2/freeze_submission_receipt.json`.
It is the closeout submission for Phase 1 H2 hardware feasibility evidence,
not an approved final freeze, commit, tag, H3 platform selection, or Phase 2
entry. Independent review remains pending.

The active formal board is
`alientek-atk-dnf103-v2-f103zet6-05d7ff34-334e5630-43057222`, bound to target
`stm32f103ze`, probe `0001A0000001`, and MCU UID words `0x05d7ff34`,
`0x334e5630`, and `0x43057222`. Fire V2 remains retained as a formal board
profile but is not the active capture board. The frozen mapping is
`phase1-pinmap-v2` at
`hardware/rtd_pilot/pinmaps/stm32f103zet6_alientek_elite_v2_phase1.json`
with SHA-256
`7cbaf03a539d01df94062d0061b58adef71eb622cbf7d754c3c9a9254bdfc04e`.

The machine-readable freeze manifest is
`hardware/rtd_pilot/phase1_freeze/20260730T084803Z_alientek_elite_v2/freeze_manifest.json`.
It binds the contracts, tools, perturbation inputs and output, and every
accepted capture metadata hash.

## Accepted capture set

All eight directories below validate as `VALID` at 20 MHz or above using the
frozen Alientek pin map:

| Variant | Capture directory |
| --- | --- |
| `TASK_SMOKE` | `20260729T092604Z_formal_task_smoke_alientek_elite_v2_retry1` |
| `MUTEX_SMOKE` | `20260729T120049Z_formal_mutex_smoke_alientek_elite_v2_retry1` |
| `IRQ_SMOKE` | `20260729T123610Z_formal_irq_smoke_alientek_elite_v2` |
| `GPIO_UART_RECORDER` | `20260729T131225Z_formal_gpio_uart_recorder_alientek_elite_v2_retry1` |
| `COMBINED_SMOKE` | `20260729T133954Z_formal_combined_smoke_alientek_elite_v2` |
| `BASE` | `20260730T080622Z_formal_base_alientek_elite_v2` |
| `GPIO_ONLY` | `20260730T082425Z_formal_gpio_only_alientek_elite_v2_retry1` |
| `GPIO_UART` | `20260730T083627Z_formal_gpio_uart_alientek_elite_v2_retry2` |

The last entry is the raw DSView set at
`/home/zzq/embedded/ds/gpio_uart_retry2/DSLogic PLus-la-260730-163911.dsl`.
Its DSL SHA-256 is
`d454d119ef42221bf92cb24639061dd10485b459e780b7f9e8b89fc7afe38a4f`.
Its CSV SHA-256 is
`619e6ae4444fc94a42c9640bf32260f0f622dc9f4729c46271932e4f2505ca05`.
It is valid despite resembling the preceding GPIO-only waveform: the
GPIO_UART contract intentionally requires only CH0 epoch and CH1 calibration
transitions in the observed window. It records one CH0 epoch at sequence 1,
a 4.99978945 s capture duration, and nonflat channels only CH0/CH1; CH6 has
no in-window initialization transition because capture gating is active.

For that capture, CH1 rising-edge analysis reports mean period
`0.000999970924184837 s`, P95 `0.0010001000000001703 s`, maximum absolute
period error `4.5e-07 s`, drift `-29.075815162970684 ppm`, and alignment
residual `0 s`. These satisfy the frozen Alientek bounds.

## Validation record

The following was rerun on 2026-07-30:

```bash
for capture in <the eight directories above>; do
  python3 hardware/rtd_pilot/scripts/validate_capture.py \
    "$capture/capture_metadata.json" \
    --pinmap hardware/rtd_pilot/pinmaps/stm32f103zet6_alientek_elite_v2_phase1.json \
    --min-sample-rate-hz 20000000
done
python3 hardware/rtd_pilot/tests/test_tools.py
python3 hardware/rtd_pilot/scripts/analyze_perturbation.py \
  --output <new-output.json> BASE.json GPIO_ONLY.json GPIO_UART.json GPIO_UART_RECORDER.json
```

Result: all eight validator runs returned `VALID`; the tool regression suite
passed 37 tests; perturbation analysis reproduced the retained output
byte-for-byte, SHA-256
`c67283d40341f4c3fefa0274f362b4a753dda09060e4bd8865190805d790ec32`.

`BASE` intentionally exposes no GPIO/UART/timer metrics. Therefore the
observed CH1 values for `GPIO_ONLY`, `GPIO_UART`, and `GPIO_UART_RECORDER`
are labeled `baseline_not_observable`; they are not a zero-baseline or
relative performance claim. ISR/window/drop/overflow fields are `null` when
the relevant compiled variant exposes no such counter.

## Retained limitations

The accepted GPIO_UART UART log has one older `h9` BOOT record and repeated
selected `h10` BOOT records before the gate release. They are retained as
pre-window serial-buffer/reset artifacts. The formal relation is the unique
selected `h10` identity before the gate, followed once each by
`CAPTURE_ARMED`, `EPOCH_BEGIN seq=1`, and `EPOCH_END seq=1`, aligned with
CH0 sequence 1. This does not claim reset provenance beyond that evidence.

The rejected directories
`20260730T081737Z_formal_gpio_only_alientek_elite_v2`,
`20260730T083121Z_formal_gpio_uart_alientek_elite_v2`, and
`20260730T083348Z_formal_gpio_uart_alientek_elite_v2_retry1` remain retained
with their rejection records and are not reused.

No further hardware action or DSView operation is required for this freeze
submission. The scope excludes H3, Phase 2, parser/diagnoser, CCM, Ledger,
lineage, fault truth, fault injection, Case creation, and paper-experiment
claims.
