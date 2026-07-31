# Candidate gate-sequence configuration retention

This directory retains the exact DSView, UART, firmware, channel mapping, and
input hashes selected after the 2026-07-29 waveform review. It references the
original artifacts and does not copy, replace, or normalize them.

The candidate remains outside the frozen Fire V2 formal Capture contract.
Readiness here means the gate sequence is reproducible and ready for a
candidate capture run. A separate candidate-board freeze review is still
required before `formal_capture_started` may become true.

The required order is UART reader open, DSView armed, the candidate modem policy
settled, matching BOOT observed, SWD `write32` in an attached session without
reset, then ordered `CAPTURE_ARMED`, `EPOCH_BEGIN`, and matching `EPOCH_END`
confirmation.

## Candidate capture run

Keep DSView at the retained 20 MHz, 1.6 V, 5 s configuration and arm its CH0
rising-edge trigger. In a terminal, start the persistent reader before resetting
the target:

```bash
RUN_DIR=$(mktemp -d /tmp/rtd-candidate-gate.XXXXXX)
META=/home/zzq/embedded/stm32f103_env/workspace/rtd_phase1_feasibility/evidence/h2_20260728T082308Z_baremetal_gpio_uart_alignment_fix/alientek_elite_candidate_preflight/candidate_preflash_metadata.json
ELF=/home/zzq/embedded/stm32f103_env/workspace/rtd_phase1_feasibility/rtos_smoke/build/alientek_elite_candidate_task_smoke/artifacts/rtd_phase1_freertos_TASK_SMOKE.elf
python3 ../../scripts/capture_gate_uart.py \
  --metadata "$META" --elf "$ELF" --seconds 90 --modem-policy explicit-mask-0x2 \
  --output "$RUN_DIR/uart_raw.log" \
  --ready-file "$RUN_DIR/uart_reader.ready" \
  --gate-ready-file "$RUN_DIR/gate.ready" \
  --verified-file "$RUN_DIR/gate.verified" \
  --receipt "$RUN_DIR/gate_receipt.json"
```

The explicit candidate modem policy may reset the target while it settles DTR/RTS,
so do not issue a separate SWD reset after the reader starts. Wait for
`GATE_WRITE_READY`; then attach without reset and derive the only permitted write
command from the observed BOOT identity:

```bash
../../scripts/arm_capture_gate.sh --metadata "$META" \
  --gate-ready-file "$RUN_DIR/gate.ready" \
  --emit-persistent-command "$ELF"
```

Enter the emitted `write32` command in that same SWD session. Do not reset,
detach, or reopen UART afterward. A candidate run passes only when
`gate.verified` exists and `gate_receipt.json` records
`gate_sequence_verified: true`. Then save the newly generated DSView `.dsl`,
CSV export, and screenshot beside the UART artifacts without overwriting the
2026-07-29 retained inputs.
