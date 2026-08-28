# RTD-Pilot P4 Hardware Execution Plan — 2026-08-24

Status: `FROZEN_FOR_LIMITED_P4_HARDWARE_EXECUTION`

This is a non-Case engineering validation plan. It authorizes only the P4
build, flash, smoke, capture, sealing, integrity, CCM/CIR, and P3 lineage work
requested by the workspace owner. It does not authorize F1/F2/F3 experiments,
a formal `case_id`, Ledger/OAR/CVR truth, diagnosis, verdict, P5, or P6.

## Start state

- Repository: `/media/zzq/新加卷/patent/realization`
- Branch / START_HEAD: `main` / `c75e513cb445c53fc7833c90eea9eb8995e4cf01`
- Initial tracked inventory: 1,262 paths; tracked-name stream SHA-256
  `c6d9b465eb4e41d3d43a023cb51a791cc68cd13626e415cd2cd0697d7a7d457a`.
- Initial untracked inventory: 104 paths; untracked-name stream SHA-256
  `47d1945f81ccad4174729218045e87cceec1ef7209c008f87f699026cfe37dd8`.
- Initial tracked modifications: `collector/CMakeLists.txt`, `pyproject.toml`,
  `tests/cpp/CMakeLists.txt`. All initial untracked assets are preserved.
- P1/P2 receipt verifier: `valid=true`; P1 standalone verifier:
  `valid=true`; P3 lineage/production-context focused regression: `43 passed`.
- Initial USB visibility: CH340 `1a86:7523` on `/dev/ttyUSB0`; Fire
  CMSIS-DAP `c251:f001` / UID `0001A0000001`; DreamSource DSL Instrument v2
  `2a0e:0034`. USB enumeration does not itself prove signal wiring.
- Toolchain: Arm GNU Toolchain `15.2.Rel1` (`15.2.1 20251203`); pyOCD
  available. openocd, st-flash, pulseview, and sigrok-cli were not found.
- Agent 1 requested as `gpt-5.6-sol high`; no independently verifiable serving
  identity was exposed, so exact runtime identity is `unable to verify`.

## Fixed configuration and source identity

- Board ID:
  `alientek-atk-dnf103-v2-f103zet6-05d7ff34-334e5630-43057222`.
- MCU / target / probe: STM32F103ZET6 / `stm32f103ze` /
  `0001A0000001`; expected UID words `05d7ff34 334e5630 43057222`.
- FreeRTOS V10.3.1 root:
  `/home/zzq/embedded/sdk/STM32CubeF1/Middlewares/Third_Party/FreeRTOS`,
  submodule commit `cdc07b553ee9a9676988e5089a1d484da5d37c9c`.
- Required kernel files and SHA-256:
  - `tasks.c`: `6f540b927668e4c87a064749c03f6ee0ca7370b9dfae7d9d5e96ebd0fdf12b8d`
  - `queue.c`: `4613e2dd8c099ee689ff4fabc1d4979c0c22683a95f3e85283c6ef1b92a32bd8`
  - `list.c`: `2c7593ec5c67bcdb371d5c3fda60f240e594dd3a7b60f72947f53e8c01971e24`
  - `portable/GCC/ARM_CM3/port.c`:
    `7473d722e0875be084392ab464a5b69385e0ebc6b0da4dde14f45581bbe8b600`
  - `portable/MemMang/heap_4.c`:
    `d91f47f6338a4501835989582ae87fd344b6d92b37b5853fa32fd2fffbfaee59`
- ARM ABI: `-mcpu=cortex-m3 -mthumb -mfloat-abi=soft`. Optimization,
  linker/startup/CMSIS/config/source hashes are frozen with the clean build.
- P1 firmware workspace and `hardware/rtd_pilot/**` are read-only. P4 firmware
  is built in a new isolated external workspace and receives new identities.

## Main-Agent approval constraints

The following are approved only as minimal P4 prerequisites:

1. Replace the host-only dynamic `std::vector` contract at the board boundary
   with a fixed-capacity, no-allocation, bounded critical-section ARM backend.
2. Emit a complete canonical trace containing global header, segment/chunk
   metadata, CRCs, and an untruncated `run_id` bound to the capture ID.
3. Allocate sequence on every source-event attempt before `drop_new`; clean
   capture gaps must be zero and overflow gap lengths must reconcile exactly.
4. Encode LOSS/OVERFLOW marker payloads as burst deltas while counters remain
   cumulative.
5. Derive `natural_overflow` only from real producer/drain competition plus a
   source/workload attestation that rejects force-pressure calls and masks.
6. Use typed length+CRC UART framing; retain the original wire bytes and
   deterministically reconstruct canonical raw trace. No unframed text is
   inserted after binary trace streaming begins.
7. Report attempted, accepted, dropped, integrity markers, flushed/decoded,
   wire frame/byte, CRC, truncation, backpressure, and high-watermark facts.
8. Add a P4 admission wrapper: unaligned, corrupt, identity-mismatched, or
   otherwise inadmissible inputs are retained but cannot enter rebuild PASS.
   P3 frozen code is not modified.
9. Make stop behavior mode-specific: any LOSS/OVERFLOW fails the clean gate;
   a preregistered, attributable natural overflow is an expected degraded
   pressure result and still must close all integer invariants.
10. Add only the minimum P4 hardware recorder/import surface; it must not
    create a Case or OAR/CVR truth and must be no-overwrite.
11. External artifact layout is
    `<root>/<board-id>/<session-id>/hardware_smoke_noncase/<capture-id>/` with
    `case_id=null`.
12. Buffer capacity and high-watermark use the explicit unit `records`.

If a required fix would change `collector/include/trace_api.h`, any frozen
P1/P2/P3 asset, or P5+ content, stop and report the API gap. No unrelated
development is approved.

## T0 prospective boundary registration

All formulas, calibration cohort rules, algorithms, hard ceilings, and exact
integer invariants below are frozen before P4 calibration. Values marked
`TBD_AFTER_CALIBRATION` are not acceptance thresholds yet. Three independent,
fully retained calibration sessions are required. After calibration, T2 is a
new no-overwrite threshold record that applies the formula mechanically and
is hash-bound before the first corresponding measurement. Seeing smoke results
cannot change T2. A change requires a new calibration cohort and new capture
IDs; old captures remain classified under their original threshold hash.

| Boundary | Value/unit and derivation | Resolution/uncertainty/evidence | Decision |
|---|---|---|---|
| Full Observer | 20 MHz, 50 ns/sample, 5 s, CH0–CH7, CH0 rise trigger, 1.6/nearest documented 1.65 V, internal clock | edge ±25 ns; interval ±50 ns; DSLogic raw DSL+CSV+profile+screenshot | fixed P1 exact-configuration profile |
| IRQ Observer | 100 MHz, 10 ns/sample, 0.5 s, CH0/CH1/CH6, CH6 rise, at least 2 samples/pulse | edge ±5 ns; width ±10 ns; raw/profile evidence | fixed precision witness, not a substitute for full capture |
| UART physical limit | 115200 bit/s, 8N1, no flow, theoretical 11,520 byte/s | 10 ns diagnostic edge plus MCU divider/clock and host clock budget; raw PA9 and wire log | ceiling, not sustained acceptance rate |
| UART baud error | `TBD_AFTER_CALIBRATION` percent; use the stricter of the STM32F1 documented tolerance and the three-session error-free bound | measured over multiple bits at 100 MHz; divider, oscillator and analyzer uncertainty recorded | no guessed percentage |
| Sustainable transport | `R_limit=floor(0.8*min(R0_i))` byte/s, where each `R0_i` is the session's maximum zero-wire-loss/zero-CRC-error/zero-host-drop rate | 1 byte/frame; wire counters, frame decoder, host timing; 20% factor fixed now | T2 only after three sweeps |
| Alignment | hard ceiling 1.50 us; `E_P4=min(1.50 us,max(E_i)+U_forward)` | `E_i=max residual + q_obs/2 + q_trace/2 + marker skew + fit/extrapolation`; observer 50 ns; trace resolution measured; raw epoch pairs | T2 must not exceed P1 exact-config ceiling |
| Drift | hard ceiling 314.07 ppm; `D_P4=min(314.07 ppm,max(abs(D_i))+U_drift)` | 20 MHz CH1 long-window edges; quantization about `2*50 ns/window`; ISR jitter/warm-up retained | T2 only after three sessions |
| Sequence | clean gap=0 events; overflow `sum(gaps)=sum(LOSS deltas)=counter.lost` | exact 1-event integers; no statistical uncertainty; raw/counter/decoder | fixed invariant |
| Buffer | first P4 capacity 64 records | 1 record; ELF map proves RAM/stack/heap/ring fit | any change is a new config/calibration |
| Clean HWM | `H_clean=64-Rreserve`; `Rreserve=max(3,ceil(lambda_peak*(T_drain_worst+U)))` records | event-rate and worst drain-latency calibration, scheduler uncertainty, counter HWM | if reserve >=64, stop/reconfigure before smoke |
| Clean integrity | loss=overflow=backpressure=CRC=truncation=0; attempted=accepted; flushed=decoded | exact event/frame/byte equality | fixed H7-clean rule |
| Natural overflow | lost>0, overflow>0, HWM=64 records; gap/marker/counter deltas exactly equal | source/workload hash and no-force/no-mask attestation | CIR must be degraded, never relabelled clean |
| Pressure without overflow | band `TBD_AFTER_CALIBRATION` records below 64; no loss/overflow | HWM 1 record; band derived prospectively from rate/drain sweep | separate capture from natural overflow |
| Smoke window | full capture 5 s; IRQ precision 0.5 s; at least 3 complete workload cycles | observer 50/10 ns and trace tick measured; scheduler/alignment uncertainty | periods and usable epoch are T2 values |
| Ordering margin | `M_min=2*E_P4+q_trace+q_observer_interval+U_marker` | same calibration inputs | intervals <= margin are ambiguous/unalignable |
| Recorder perturbation | per metric `P_j=max_i(abs(OFF_i-median(OFF)))+U_j` from three alternating OFF/ON pairs | independent Observer metrics, baseline jitter, quantization and pairing; unobservable is not zero | ON outside median OFF ±P_j fails |
| Artifact identity | SHA-256 exact, size exact, duplicate/collision count 0 | 1 byte; seal-time rehash and no-overwrite store | fixed invariant |
| Counter/decoder | attempted=accepted+dropped; decoded/flushed and integrity-marker equations exact for the selected mode | 1 event/frame/byte; no inequality-only acceptance | fixed invariant |

Every T2 entry records `value`, `unit`, `derivation`,
`measurement_resolution`, `uncertainty_budget`, `evidence`, and
`decision_rationale`. Missing calibration evidence leaves the value null and
blocks the corresponding smoke.

## Strict one-item run order

Each item ends with its focused validator. A failure stops that path; the
Agent reports to the main Agent, who classifies wiring/config/software/
environment/protocol and approves at most a minimal repair. Failed artifacts
are sealed and never deleted or selected away.

1. Implement and host-test the twelve minimal P4 prerequisites.
2. Integrate the exact FreeRTOS/STM32 source in an isolated P4 workspace.
3. Perform two clean ARM builds and freeze source/config/ELF/BIN/MAP hashes.
4. Record a no-write probe/board/MCU identity preflight and current connection
   limits. Stop before flash on UID/target/BOOT/wiring ambiguity.
5. Record a current 512 KiB preflash backup; flash/verify/readback; establish
   stable Flash boot/recovery without reopening UART in an active window.
6. Run and seal three UART/transport calibration sessions.
7. Run and seal three Observer/alignment/drift/perturbation calibration pairs.
8. Mechanically derive, independently review, and freeze T2 thresholds.
9. Shared epoch/alignment capture.
10. Healthy scheduling capture.
11. Mutex lock/unlock/wait capture.
12. Controllable IRQ capture plus the separate 100 MHz witness.
13. Loss-free clean integrity capture.
14. Controlled pressure-without-overflow capture.
15. Natural-overflow capture if produced by the preregistered ordinary
    workload; otherwise retain the attempt and report `not naturally produced`.
16. Seal raw/wire/counter/Observer material and reconcile decoder integrity.
17. Assemble CCM/CIR and `CaptureLineageContext` for every retained capture.
18. Run raw → codec → rebuild → lineage for admissible clean and degraded
    bundles; fail-close invalid/partial/corrupt/unalignable bundles.
19. Run the full final validation and audits.
20. Obtain an independent read-only review.

## Authoritative H1–H8 mapping for this run

- H1 Build: items 1–3.
- H2 Board/Debug: items 4–5.
- H3 Observer/Epoch: items 6–9 and T2 alignment acceptance.
- H4 Scheduling: item 10.
- H5 Mutex: item 11.
- H6 IRQ: item 12.
- H7 Capture Integrity: items 13–16; clean and pressure are mandatory;
  natural overflow is reported honestly if it cannot naturally occur.
- H8 End-to-End: items 17–18.

No historical P1 capture substitutes for a new P4 hardware capture. A missing,
partial, corrupt, identity/hash-mismatched, CCM/CIR-inconsistent, or unalignable
capture is fail-closed, retained, and excluded from PASS without becoming
negative or manifestation truth.

## Per-item evidence, PASS/FAIL, stop and rollback template

For every item the execution record contains:

`input -> operation -> evidence -> PASS/FAIL -> stop condition -> rollback`

- Input: exact hashes, capture/session IDs, threshold-contract hash, device
  identities, and the one selected workload.
- Operation: exact command, environment, start/end time, operator/Agent, and
  whether it changes hardware state.
- Evidence: stdout/stderr, source/build artifacts, raw wire/trace/Observer,
  counters/decoder, inventories/seals, and focused-test result.
- PASS/FAIL: the preregistered rule only; null or missing evidence is FAIL.
- Stop: identity/hash/schema/collision/wiring/transport/alignment/counter/
  decoder contradiction, forbidden path need, or unexpected reset/boot.
- Rollback: preserve and seal partial material; do not edit a sealed capture;
  use a new ID after the main Agent approves a minimal repair. Restore firmware
  only from the current verified backup and only as an explicit hardware item.

## Allowed and forbidden paths

Allowed: existing P4 paths (`collector/hook/freertos/**`,
`collector/target/freertos_stm32f103/**`, `p4_capture/**`, P4 tests,
`tool/rtd_p4_capture.py`, `docs/rtd_pilot/phase4_*`), the three existing P4
build-registration edits, and new isolated external P4 firmware/build/capture
roots.

Forbidden: `hardware/rtd_pilot/**`, `docs/rtd_pilot/feasibility/**`,
`docs/rtd_pilot/contracts/**`, P2 schemas/validators/tools, frozen P3 parser/
models/rebuild/lineage/desktop assets, formal Case/Ledger/OAR/CVR truth, P5+,
diagnosis/verdict, historical artifacts, and all unrelated existing changes.

## Final validation and disposition

Run P4 hardware validation, all P4 focused tests, P2 contract regression, P3
lineage/production-context regression, parser/pipeline tests, collector C++
tests, full Python regression, both freeze verifiers, raw/CCM/CIR/lineage
identity audit, artifact/no-overwrite audit, `git diff --check`, and allowed/
forbidden path audit. Compare the known Python PTY `invalid trace magic` and
C++ async-flush flake to START_HEAD; only `new_regression_delta=0` is accepted.

Only H1–H8 all PASS, reproducible configuration, full lineage closure,
deterministic invalid retention, no threshold relaxation, intact freezes,
zero new regression delta, and independent review `blocking=0, major=0` permit
`PHASE 4 READY TO FREEZE`. Otherwise the mandatory final disposition is:

`PHASE 4 NOT READY TO FREEZE`

`P5 NOT AUTHORIZED`

No commit, push, or tag is authorized.
