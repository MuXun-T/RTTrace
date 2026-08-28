# RTD-Pilot Phase 4 Pre-Hardware Implementation Plan

Status: `APPROVED_FOR_PRE_HARDWARE_IMPLEMENTATION`

This plan prepares the Real Hardware Capture Chain without connecting a board,
opening a transport, flashing firmware, creating a formal Case, or claiming
hardware evidence. All offline fixtures are explicitly `test_only`.

## Authority Boundary

The selected target is the Alientek ATK-DNF103 V2 with STM32F103ZET6 and
FreeRTOS V10.3.1. Phase 1 assets are evidence of feasibility only. Phase 2
contracts remain authoritative as follows:

- CCM records configured capability only. It never receives observed sequence
  gaps, LOSS, OVERFLOW, high-watermark, truncation, or corruption.
- CIR records observed acquisition integrity and may bind immutable P4 input
  sidecars through `immutable_input_refs`.
- Ledger remains administrative, OAR remains observer/manifestation authority,
  and CVR remains capture-validity authority. P4 collectors and parsers do not
  write any of those objects.
- P3 receives only its existing `CaptureLineageContext`, constructed with
  `CaptureLineageContext.from_p2_records(CCM, CIR)`. It remains responsible
  only for structural lineage.

No existing P1/P2/P3 file is to be modified. In particular, this excludes
`hardware/rtd_pilot/**`, Phase 2 contracts/schemas/tools, and the P3 parser,
model, desktop, and regression files frozen at `c75e513`.

## A. Pre-Hardware Work

### Configuration and identity

Add a P4-only `CaptureSessionManifest` and `CollectorConfigExport` with a
canonical digest and strict schema. They carry `capture_id`, `session_id`,
optional `case_id`, `test_only`, `capture_mode`, target and pin-map references,
firmware/ELF/build/source hashes, collector version, enabled events, filters,
sampling, buffer/drop/high-watermark policy, flush, transport, timestamp, and
dictionary/mapping settings.

Only `test_only` and `hardware_smoke_noncase` modes are admissible in P4.
`case_bound` is rejected. The export is adapted to the existing P2 snapshot
with `adapt_collector_config_snapshot()`. The P2 snapshot hash covers only the
frozen configuration fields; the P4 export digest separately binds transport,
high-watermark policy, and firmware/ELF/source provenance. IDs must be valid
ASCII and the capture ID must fit the existing trace-header run ID without
truncation. Duplicate capture/session paths fail closed.

### Target collector contract

Add a portable, host-testable FreeRTOS/STM32F103 target-contract surface and a
FreeRTOS hook interface without modifying `trace_api.h` or the host collector.
It shall define fixed-ring `drop_new` semantics, monotonic sequence, LOSS and
OVERFLOW backfill, synchronous flush, real counter snapshot and real
high-watermark reporting, and trace bytes compatible with existing
`trace_api.h` disk structs and `parser.codec`.

The hook interface covers task switch/ready/block/wakeup, mutex lock/unlock,
IRQ enter/exit, shared epoch markers, and counter reports. A host stub build
can prove the interface and serialization only; it cannot prove the selected
FreeRTOS integration, ARM ABI, board behavior, or transport.

### Assembly and integrity

Define P4 immutable `CollectorCounterReport`, `DecoderIntegrityReport`, and
`CaptureAssemblyInput` objects, each capture/session/config/raw-bound and
schema/version checked. Unobserved counters are explicit `null` or
`not_observed`; they are never replaced with zero.

The only P4 CCM/CIR assembler must:

1. Verify the P4 export, P2 snapshot, raw inventory, counter report, decoder
   report, and observer/alignment sidecars.
2. Produce/validate P2 CCM and CIR without extending their schemas.
3. Bind P4 config/counter/decoder/observer input digests in the CIR's existing
   `immutable_input_refs` field.
4. Reject stale or mismatched identity, hash, schema, sampling, filter, raw,
   or counter/decoder state.

Missing raw/counter/decoder, collisions, partial/corrupt raw, or incompatible
inputs must result in a fail-closed degraded or invalid CIR, never a complete
one. A clean synthetic test result remains `test_only`, not a hardware PASS.

### Raw artifacts

Implement a P4 artifact store rooted outside the repository by default, using
`<root>/<session_id>/<capture_id>/`, exclusive create/no-overwrite, SHA-256 and
size inventory, logical names, retention/storage classes, collision rejection,
partial/corrupt/missing state, seal manifest, seal-time rehash, and best-effort
read-only permissions. Failed and invalid material is preserved; nothing is
deleted or replaced. P2's inventory schema is reused unchanged and richer P4
status remains a sidecar.

### Observer and alignment preparation

Define P4-only Observer Artifact Inventory, Shared Epoch Record, Alignment
Input, and Alignment Report. They bind the observer artifact hash, epoch ID,
trace sequence/timestamp reference, observer timestamp reference, algorithm
and version, and `alignment_error_bound`. States distinguish `pending`,
`missing`, `partial`, `corrupt`, `unalignable`, and `test_only`. Without an
actual observation the bound is `null`. P4 does not write an OAR or CVR.

### Join and parser path

Provide a fail-closed P4 join validator for export, snapshot, CCM, CIR, raw
inventory, counter report, decoder report, alignment sidecar, raw trace header,
and serialized `CaptureLineageContext`. It must reject A/B crossing, firmware
or ELF mismatch, config/export mismatch, stale snapshot, wrong CCM/CIR,
wrong raw hash, and unsupported P4/P2 schema versions.

Ledger/OAR/CVR joins are checked only with ephemeral test fixtures wrapped by
`test_only: true`; no formal Case, Ledger, OAR, or CVR is written. Synthetic
raw traces are created only under test temporary directories and must traverse
the existing codec, P4 decoder report/assembler, P3 context, and public parser
load path to prove backtraceability.

### CLI, SOP, and test card

Implement `prepare -> start -> record -> stop -> seal -> validate`. The CLI
does not open USB, SWD, UART, or DSView. During this phase executable flows
require `--test-only`; future hardware `start`/`record` require separate
authorization and explicit imported artifacts. The SOP must retain future
command templates, identity/hash checks, no-reset/no-reopen/no-overwrite rules,
and an explicit hardware-required label.

The hardware test card must define inputs, physical connection, commands,
expected evidence, PASS/FAIL, and stop conditions for healthy scheduling,
mutex lock/unlock, controllable IRQ, GPIO/shared epoch, logic-analyzer
alignment, loss-free capture, buffer pressure/natural overflow, and the full
raw-to-codec-to-CCM/CIR-to-lineage join. They are non-Case engineering smokes,
not F1/F2/F3 experiments.

## Required Focused Tests

Tests must cover capture A/B isolation, duplicate capture/session rejection,
config and firmware/ELF/source mismatch, wrong CCM/CIR, missing/partial/corrupt
raw, no-overwrite and seal rewrite detection, unsupported filter/sampling,
stale export/snapshot, sequence gap, LOSS, OVERFLOW, truncation, CRC corruption,
counter/decoder contradiction, missing/unalignable/corrupt observer,
unsupported schemas, test-only enforcement, synthetic codec-to-P3-lineage
backtrace, temporary full P2 join, and host target-contract compilation.

## B. Hardware-Only Work

After hardware availability and separate authorization: integrate the target
contract into the exact FreeRTOS source tree; compile and bind new target
firmware/ELF/build/source identities; connect board/ground/DSLogic/UART/debug
path; flash only when authorized; execute the eight test-card smokes; import
real raw/counter/observer artifacts; and calculate real CIR and P3 lineage.

Any identity, hash, schema, raw-header, counter/decoder, transport, observer,
alignment, or artifact-integrity fault stops that capture, preserves the
material, and prevents PASS. Actual Ledger/OAR/CVR case-bound joins remain a
P5 authorization gate.
