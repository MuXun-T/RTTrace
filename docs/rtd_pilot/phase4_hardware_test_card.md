# RTD-Pilot P4 Hardware Test Card

Every row is a future, non-Case engineering smoke. It is not F1/F2/F3 and
must not produce a diagnosis, verdict, or Agent-truth claim. `PASS` is allowed
only with real board, raw trace, counter, decoder, observer, hash, and join
evidence. Until hardware is available every row is `BLOCKED`, never `PASS`.
Capacity and observed high-watermark are both recorded in `records`; bytes,
events, null, `not_observed`, or mixed units fail a hardware smoke.

| ID | Physical input and command | Required evidence | PASS / FAIL / stop |
|---|---|---|---|
| H1 healthy scheduling | Approved board/ground and firmware; run the existing healthy scheduling workload through the future `start/record/stop` operator commands. | Raw header identity; task ready/block/wakeup/dispatch/context-switch events; zero LOSS/OVERFLOW; decoder continuous; valid CIR and P3 lineage. | PASS only when all hashes and joins agree. Any missing event, counter, or identity mismatch: FAIL and stop. |
| H2 mutex smoke | Same board, mutex lock/unlock workload, no injected fault. | Matching try/lock/unlock and wait/hold records; raw/counter/decode/CIR join. | This is an engineering smoke, not F2. Unexpected lock sequence or loss: FAIL and stop. |
| H3 controllable IRQ smoke | Approved controllable IRQ source and its trace/epoch marker; no fault experiment. | IRQ enter/exit, nesting, marker, and raw sequence correspondence. | This is not F3. Missing IRQ edge, wrong pin, or unalignable marker: FAIL and stop. |
| H4 GPIO/shared epoch | Approved PC2 shared-epoch output, observer input, and PA9/PA10 trace path. | Shared epoch record, observer inventory, trace sequence/timestamp references, matching capture/session IDs. | No physical edge or wrong pinmap: FAIL and stop. |
| H5 observer alignment | Approved logic-analyser profile and observer raw artifact. | Independent observer inventory/hash, alignment input/report, algorithm/version, finite `alignment_error_bound`. | `missing`, `partial`, `corrupt`, or `unalignable` is FAIL; never infer a bound. |
| H6 clean capture | Capacity/filter/sampling settings from the sealed export; healthy workload. | T2 threshold hash; counter report observed with zero LOSS/OVERFLOW/backpressure/CRC/truncation; decoder continuous; complete raw inventory; CIR `complete`; P3 lineage. | Any LOSS or OVERFLOW means FAIL for clean capture; preserve degraded material. |
| H7 pressure / natural overflow | Preregistered non-Case pressure mode and T2 hash. | `pressure_without_overflow`: HWM in the T2 band below capacity and zero LOSS/OVERFLOW. `natural_overflow`: ordinary-workload attestation plus exact gap/LOSS/OVERFLOW/counter reconciliation, HWM=capacity, degraded CIR. | Pressure early overflow is FAIL. Natural overflow is only expected degraded, never H6/clean; mismatch is STOP. If not naturally produced, retain the attempt and report `not naturally produced`. |
| H8 end-to-end join | Sealed raw, config export, P2 snapshot, counter/decoder/observer sidecars. | SHA-256 inventory, raw header `run_id`, CCM/CIR immutable refs, `CaptureLineageContext` backtrace. | Any cross-capture, schema, firmware/ELF/source, or CCM/CIR mismatch: FAIL and stop. |

## Evidence bundle

Retain the session manifest, full P4 config export, P2 snapshot projection, raw
inventory and seal, raw trace, counter report, decoder report, observer
inventory, shared epoch, alignment input/report, P2 CCM/CIR, and serialized P3
lineage context. Mark synthetic fixtures `test_only=true`; do not mix them with
the hardware bundle. No row authorizes a formal Case or P5 work.
