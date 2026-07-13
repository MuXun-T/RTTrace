# Phase 7 P7.5 Layered Validation Contract

Version: `p7.5-layered-validation-v1`.

## Purpose And Boundary

P7.5 independently validates the frozen P7.2 source evidence, P7.3 package
evidence, P7.3/P7.4 per-case bindings, and P7.4 replay reports.  It produces
a new validation result; it neither changes nor reinterprets P7.4 replay.
`validation_pass` means that all applicable P7.5 checks reproduced and bound
the frozen result.  It is not a claim that P7.4 replay passed.

P7.5 may validate a reproduced `replay_fail`.  In particular,
`UNSUPPORTED_REQUIRED_RECORD` for `freertos_vcd_1core` and
`TIMESTAMP_REGRESSION` for `freertos_btf_50k` remain P7.4 failures while
being eligible for P7.5 `validation_pass`.  Zephyr and Zephelin remain
`reference_only`; P7.5 must not create trace, package, expected-output, or
comparison evidence for them.

P7.5 may read frozen P7.0--P7.4 evidence through adapters only.  It must not
modify P7.0--P7.4, Phase 6/proof files, raw traces, expected fixtures,
comparison profiles, collector, `desktop/evidence_export.py`, or
`parser/evidence_models.py`.  It adds no parser, normalizer, replay engine,
ground truth, comparison tolerance, proof semantics, diagnosis, hardware,
network, or P7.6 behavior.

## Frozen Inputs

The start commit is `49724c1456597cb1a9de5d0ad2c1e70328a7d549`.  The P6.4
canonical SHA-256 is
`fe6bb93191fc72e9584339878c0418b1005f3631f0e213e92b8dcbeb2bd2811e`.
The original P7.3 aggregate report SHA-256 is
`dd835f9a8595cd46d61ab1c3e05c6afa6473e4d38402ebacef82ded804ddbef1`.

For the four acquired FreeRTOS cases, the required closure input includes the
sealed P7.2 inventory/provenance/license/raw trace, a P7.3 per-case manifest
and canonical reopen report, a P7.3/P7.4 binding, the P7.4 expected fixture
and exact comparison profile, and the P7.4 canonical replay report.  The
binding fixtures are frozen as follows.

| case ID | embedded binding identity | raw binding fixture SHA-256 |
| --- | --- | --- |
| `freertos_btf_1core` | `f80803eea21de187d93baa871b27df0e99a9e6432334830644be5c2f71eb2cbe` | `1d9942dffbb8dde1cb89a65370095904b7c5929b187144a591d5561d247ede73` |
| `freertos_vcd_1core` | `47a8d8cca264d2b7740b33577d755d5c46b84e0af8ac09dabb12f27c07235805` | `85fc1795b2bed8a3e739cffb181e58fea557d2776fd1198587f8f7c0916b35e7` |
| `freertos_btf_4cores` | `673bf36b77b87779a57e0d3a3a7414b313a9cbf302c3208b508e58518556a1cf` | `f1efcca8c9b86fd373771bb20154767481ffd5112549a399f232af7d183161ea` |
| `freertos_btf_50k` | `201d7c7dca1ba19597a7f3295c3f1145e4fe6c276369769ed9f98224c63bfedc` | `bdf840f27f64d7a226b9141d67c9dcf74f052630482baab7209d7b06913b89b4` |

The two columns are deliberately different checks.  `binding_identity` is
embedded by the frozen binding model and equals the SHA-256 of its canonical
identity input, which excludes `binding_identity`.  The raw fixture SHA-256
is the SHA-256 of the complete stored canonical JSON bytes, including that
embedded field and its trailing LF.  P7.5 must recompute and validate both;
it must neither equate them nor substitute one for the other.

P7.4 FreeRTOS reports retain a legacy aggregate package identity.  The
binding's per-case P7.3 package identity is the authoritative per-case fact;
the P7.4 value is preserved only as a qualified legacy aggregate fact and
must never be equated with the per-case package identity.

For Zephyr and Zephelin, required evidence is limited to frozen source
inventory/provenance/license/checksum facts and the P7.4 reference-only
report.  Package, raw trace, expected fixture, comparison profile, per-case
binding, parser, replay, and comparator layers are not applicable.

## States, Layers, And Reasons

`ValidationState` is closed: `not_evaluated`, `reference_only`,
`validation_pass`, or `validation_fail`.  `source_replay_state` is a separate
unchanged P7.4 `ReplayState` field.  A supported acquired case is
`validation_pass` only when every applicable required layer passes; otherwise
it is `validation_fail`.  A fully checked reference-only case is
`reference_only`.  Unsupported/missing profiles are `not_evaluated` only
before validation starts; a failed applicable check is never downgraded to
`not_evaluated` or `reference_only`.

`LayerState` is closed: `passed`, `failed`, `not_applicable`, or
`not_evaluated`.  Ordered layers are `source_identity`, `artifact_identity`,
`checksum_integrity`, `package_completeness`, `evidence_closure`,
`p7_4_report_schema`, `cross_phase_identity_binding`,
`replay_reproduction`, `result_equivalence`, `replay_fact_drift`,
`proof_fact_comparability`, and `proof_parity_eligibility`.

Reasons are closed and priority ordered.  The contract reserves at least:
`VALIDATION_PROFILE_UNSUPPORTED`, `SOURCE_IDENTITY_MISMATCH`,
`ARTIFACT_IDENTITY_MISMATCH`, `CHECKSUM_MISMATCH`, `PACKAGE_INCOMPLETE`,
`EVIDENCE_CLOSURE_INCOMPLETE`, `P7_4_REPORT_SCHEMA_INVALID`,
`BINDING_IDENTITY_MISMATCH`, `BINDING_FIXTURE_HASH_MISMATCH`,
`FROZEN_INPUT_MUTATED`, `REPLAY_REPRODUCTION_MISMATCH`,
`RESULT_EQUIVALENCE_MISMATCH`, `REPLAY_FACT_DRIFT`, and
`SECURITY_BOUNDARY_VIOLATION`.  The report retains ordered P7.4 reason codes
as evidence; it does not map them to P7.5 reasons or weaken them.

## Identity, Closure, Reproduction, And Equivalence

No identity is inferred from a filename or absolute path.  The acquired-case
binding must establish P7.2 source/artifact/raw SHA-256 to P7.3 manifest
source/artifact/package identities to the P7.3 canonical reopen report to
P7.4 trace/expected/actual/profile/report identities.  The validator uses
no-follow reads, validates raw bytes and raw hashes, validates canonical raw
file hashes, and snapshots all frozen inputs before and after the validation.
Any missing required item, unexpected identity, symlink/TOCTOU condition, or
nonzero frozen-input mutation fails closed.

P7.5 reruns only the frozen P7.4 API/CLI contract into a temporary output and
compares it with the frozen report.  It compares canonical bytes and SHA-256,
replay state and attempts, primary and ordered reasons, source/package/trace/
expected/actual/profile identities, comparison result and mismatch classes,
mutation counts, and `hardware_validation`.  For reference-only cases it
reproduces the frozen reference-only report only and never invokes parser or
comparator.

## Drift And Proof Gate

The replay drift domain contains only versioned, machine-readable,
deterministic replay/report facts that are explicitly enumerated by the P7.5
profile.  It excludes elapsed time, paths, machine/user/host data, prose,
manual notes, LLM output, and report-only metrics.  Results carry ordered
`comparable_replay_fact_count`, `replay_fact_drift_count`, and
`replay_fact_drift_items`.

P7.5 adds no proof representation.  The proof comparability layer first
requires a frozen proof artifact, complete expected and actual field domains,
canonical proof serialization, and an explicit parity profile.  Unless all
are already frozen, it records `comparable_proof_fact_count=0`,
`proof_drift_count=0`, `proof_drift_items=[]`,
`proof_parity_eligible=false`, `proof_parity=not_evaluated`, and
`proof_correctness=not_evaluated`.  Empty proof drift is an empty comparison
domain, never proof parity.

## Canonical Report, CLI, And Claims

P7.5 reports/profiles use UTF-8 canonical JSON with ASCII escaping, sorted
compact keys, fixed ordering, finite values, and one trailing LF.  They
contain no absolute/temp path, current time, elapsed time, hostname, username,
locale, randomness, or environment value.  Repeated generation must produce
byte- and SHA-256-identical reports.

The P7.5 CLI has explicit inputs and stable JSON/exit codes.  Its production
truth path performs no network, shell, subprocess, environment read, LLM,
Advisor, Feedback, or write to frozen source/package/P7.3/P7.4/proof inputs.
`hardware_validation=false`, `proof_correctness=not_evaluated`, and
`diagnosis_correctness=not_evaluated` remain mandatory.  P7.5 may claim only
deterministic layered validation of these frozen artifacts; it makes no proof
correctness/parity, diagnosis, hardware, performance, general RTOS, or P7.6
claim.
