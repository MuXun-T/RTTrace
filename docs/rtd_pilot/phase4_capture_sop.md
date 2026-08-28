# RTD-Pilot P4 Capture SOP

Status: `PRE-HARDWARE PREPARATION ONLY`. This SOP is a future operator
procedure. No command in this repository connects USB/SWD/UART, flashes a
board, starts a logic analyser, or creates a formal Case.

## 0. Gate

The operator must have the selected ATK-DNF103 V2 / STM32F103ZET6 board,
approved FreeRTOS V10.3.1 firmware workspace, the exact ARM toolchain, the
approved PA9/PA10 trace path, and the approved observer pin profile. Verify
the firmware, ELF, build configuration, source commit, dictionary, and P4
export hashes before touching the board. Any mismatch is `STOP`.

Required identity is fixed before capture:

```text
capture_id=capture:<unique-no-reuse>
session_id=session:<unique-no-reuse>
capture_mode=hardware_smoke_noncase
case_id=null
```

Never reuse an identity, overwrite a prior directory, or repair a sealed
capture. Preserve all failed/partial material and start a new identity.

Hardware-smoke material is stored only as `<external-root>/<board-id>/<session-id>/hardware_smoke_noncase/<capture-id>/`; the layout is identity-derived, contains no Case directory, and must never be migrated or renamed.

Both configured buffer capacity and observed high-watermark use the unit `records`; bytes, events, null, and mixed units are invalid for a hardware smoke.

The future `record-wire` command accepts only the frozen whole-wire limit
`82,549` bytes: active 5 s transport (`5 × 11,520`), three bounded post-window
finalize UART frames (`3 × 8,204`), and fixed BOOT/CONFIG/COUNTER frames
(`337`). It enforces an 8 s minimum post-gate physical drain duration. Because
the recorder clock starts before the external SWD arm write, a no-write
preflight must measure and freeze the separate gate-arm budget before a future
hardware execution can claim a complete wall-duration budget; no guessed host
margin is permitted. This reserves space for finalize frames and must not be
shortened or replaced with a guessed cap.

## 1. Prepare (offline, executable now)

```text
python tool/rtd_p4_capture.py --root <external-capture-root> --test-only \
  prepare --export <test-only-or-future-export.json>
```

For the future board run, prepare the export in an authorized external
workspace and verify the printed capture directory and the P2 snapshot hash.
This step must not contain a `case_id` for a hardware smoke.

## 2. Start (hardware-only, not executed in this phase)

```text
# FUTURE OPERATOR PLACEHOLDER; do not run in this phase
<approved-board-tool> --target stm32f103 --firmware <firmware.elf> verify
<approved-trace-transport> --config <capture-dir>/collector_config_export.json start
```

Connect board ground first, then only the approved trace/UART/observer paths.
Visually verify pin mapping. Do not reset or reopen an active capture. Record
the exact firmware/ELF/config hashes and transport settings beside the session
manifest. A failed probe, unexpected port, or target mismatch is `STOP`.

## 3. Record (future import only; current executable path is synthetic)

```text
# FUTURE: obtain files using the separately authorized transport, then import
python tool/rtd_p4_capture.py --root <external-capture-root> --test-only \
  record --capture-dir <capture-dir> --source <already-created-raw.trace>
```

The tool only imports an existing file and uses exclusive creation. It never
opens a device. Import counter and observer sidecars through their P4 schemas;
do not fill unobserved counters with zero. Missing, partial, corrupt, or
unalignable files remain retained and cannot become a clean capture.

## 4. Stop, seal, validate

```text
python tool/rtd_p4_capture.py --root <external-capture-root> --test-only \
  stop --capture-dir <capture-dir>
python tool/rtd_p4_capture.py --root <external-capture-root> --test-only \
  seal --capture-dir <capture-dir>
python tool/rtd_p4_capture.py --root <external-capture-root> --test-only \
  validate --capture-dir <capture-dir>
```

`seal` re-hashes the imported raw artifact, writes a no-overwrite seal, and
marks retained files read-only where supported. `validate` must pass identity,
hash, schema, raw inventory, and seal checks. A failed validation is retained
as failed evidence and is not renamed or overwritten.

## 5. Post-capture assembly

An authorized offline job runs the P4 decoder and assembler with the exact
export, P2 snapshot, raw inventory, counter report, decoder report, and
observer/alignment sidecars. It must produce a CIR whose `immutable_input_refs`
bind every input and a P3 `CaptureLineageContext` from the existing P2 public
constructor. P4 does not write Ledger, OAR, CVR, diagnosis, verdict, or Agent
truth. A `hardware_smoke_noncase` result is never formal Case evidence.

## Stop conditions

Stop the current capture on identity/header/hash/schema mismatch, duplicate or
collision, transport/board mismatch, missing counter report, sequence gap,
LOSS, OVERFLOW, truncation, CRC corruption, unsupported filter/sampling,
missing/corrupt/unalignable observer, or failed seal. Preserve the complete
directory, record the stop reason, and create a new session for a retry.

## Mode-specific integrity assessment

`clean` and `pressure_without_overflow` both fail on any LOSS or OVERFLOW.
Clean additionally requires the T2 threshold hash and its clean HWM gate;
pressure requires the preregistered T2 pressure band below capacity. A natural
overflow is only an `expected degraded` non-Case smoke when the preregistered
mode, T2 hash, ordinary-workload attestation, and gap/LOSS/OVERFLOW/counter
integer reconciliation all match; its CIR remains degraded and is never
relabeled clean. Any mismatch is STOP. If ordinary workload does not naturally
produce overflow, retain the attempt and report `not naturally produced`.
This SOP neither creates a Case nor authorizes P5.
