# P7.3/P7.4 Per-Case Evidence Binding Corrective Plan

Status: approved corrective implementation plan.

## Purpose

P7.2 freezes four raw FreeRTOS trace identities, P7.3 freezes an aggregate
package-open report, and P7.4 freezes six replay reports.  The aggregate P7.3
report is not sufficient to prove which validated package artifact supplied a
particular P7.4 replay case.  This corrective work adds that missing
per-case evidence binding only.  It does not change P7.3 package-open or
P7.4 replay semantics.

## Fixed Inputs

The authoritative P7.2 source is `freertos_btf_trace`, at commit
`791410f5ebb05a9fdf77401228140c60275b5d27`, with `hardware_validation=false`.
P7.2 inventory/provenance, source bytes, licenses, and raw trace bytes remain
read-only.  The existing P7.3 report
`tests/python/fixtures/external_validation/packages/reports/opened.json` and
the six P7.4 reports under
`tests/python/fixtures/external_validation/replay/reports/` remain byte-for-byte
read-only.

| P7.4 case ID | P7.3 package ID | P7.2 artifact ID | raw logical name |
| --- | --- | --- | --- |
| `freertos_btf_1core` | `p7.3.freertos_btf_1core.v1` | `btf_1core` | `example.btf` |
| `freertos_vcd_1core` | `p7.3.freertos_vcd_1core.v1` | `vcd_1core` | `example.vcd` |
| `freertos_btf_4cores` | `p7.3.freertos_btf_4cores.v1` | `btf_4cores` | `example-4cores.btf` |
| `freertos_btf_50k` | `p7.3.freertos_btf_50k.v1` | `btf_50k` | `example-50k.btf` |

Exact bytes and SHA-256 values are always read from the frozen P7.2 inventory
and the no-follow raw files; no plan document invents a new identity value.

## Corrective Sequence

| stage | approved writes | result and immediate check |
| --- | --- | --- |
| C7.3.0 | this plan and the binding contract | freeze scope, identities, whitelist, claims, and test matrix |
| C7.3.1 | per-case package descriptors/manifests and their tests | schema, inventory projection, size/hash, wrong-source/artifact/bytes/hash, stable serialization |
| C7.3.2 | deterministic temporary-package builder, per-case reports, reopen tests | copy each immutable raw input to a temporary directory, invoke existing `read_directory_package()` then `validate_package()` through `tool/run_external_package_reopen.py`, reproduce report bytes/hash twice |
| C7.4.1 | binding schema mirrors, immutable model, model tests | closed v1 record, canonical JSON, extras/absolute/temp paths rejected |
| C7.4.2 | binding builder/validator/CLI and tests | validate inventory/raw/manifest/P7.3-report/P7.4-report identities and fail closed on swaps, stale hashes, source replacement, links, and TOCTOU |
| C7.4.3 | four canonical binding fixtures and reproducibility/security tests | byte/hash reproducibility, zero writes to frozen inputs, zero truth-path network/shell/subprocess/environment/LLM/Advisor/Feedback calls |
| C7.4.4 | corrective integration tests and reproducibility documentation | full P7.3/P7.4/external/schema/Python regressions and frozen-hash audit |
| C7.4.5 | claim boundary and closeout documentation | record the correction without beginning P7.5 or P7.6 |

Every stage is independently tested, reviewed by the primary agent, committed
by the primary agent, and followed by a clean-worktree audit.  No stage may
advance on a blocking or major finding.

## Implementation Interfaces

Per-case manifests use the existing closed P7.3
`external-evidence-package-v1` schema, package profile
`p7.3-package-open-only-v1`, identity projections, and canonical JSON.  A
self-contained P7.3 package requires its local artifact, so package creation
copies the one read-only P7.2 raw file into a fresh temporary package; large
trace bytes are never committed a second time.  The builder produces only a
temporary package tree and canonical output outside that tree.  The existing
P7.3 reader and validator are the only authority for an `opened` result;
reports must never be handwritten as `opened`.

The binding builder will consume a canonical per-case P7.3 report, frozen
manifest descriptor, P7.2 inventory/raw input, and an existing P7.4 report.
It will bind by source/artifact/trace identities, not matching filenames or
paths.  It must record both canonical report paths and their raw-byte
SHA-256.  Input paths in persisted artifacts are repository-relative logical
paths only.

Existing P7.4 FreeRTOS reports carry the single legacy aggregate P7.3
`package_identity` from `opened.json`.  That field is frozen evidence of the
original aggregate package open, not the identity of a new per-case package.
The v1 binding records it as a qualified legacy aggregate fact and proves the
new per-case linkage by equality of the P7.2 raw SHA-256, P7.3 artifact
identity projection, and P7.4 `trace_identity`.  It must not claim that the
legacy aggregate package ID equals a new per-case package ID.

Zephyr and Zephelin have no frozen raw trace/artifact.  They are excluded from
this correction: no package, per-case report, binding record, or purported
reference-only closure is created for either source.

## File Boundary

The planned write whitelist is limited to new files below:

```
docs/phase7_p7_3_p7_4_evidence_binding_*.md
parser/external_case_evidence_binding_models.py
parser/external_case_evidence_binding.py
parser/external_case_package_evidence.py
spec/schema/external_case_evidence_binding.schema.json
spec/assets/schema/external_case_evidence_binding.schema.json
tool/build_external_case_evidence_bindings.py
tests/python/fixtures/external_validation/packages/per_case/
tests/python/fixtures/external_validation/packages/reports/per_case/
tests/python/fixtures/external_validation/evidence_bindings/
tests/python/test_external_case_evidence_binding_models.py
tests/python/test_external_case_evidence_binding.py
tests/python/test_external_case_evidence_binding_security.py
tests/python/test_external_case_evidence_binding_reproducibility.py
tests/python/test_external_case_evidence_binding_schema.py
tests/python/test_external_case_package_reopen.py
tests/python/test_external_case_package_manifests.py
tests/python/test_external_case_evidence_binding_integration.py
```

The primary agent must approve any additional path before it is touched.
Forbidden paths include `collector/`, `desktop/evidence_export.py`,
`parser/evidence_models.py`, proof code/schema/digest, Phase 6, P7.0-P7.2
frozen facts, raw trace and third-party license files, every existing P7.3
schema/model/reader/validator/report/CLI artifact, `opened.json`, every
existing P7.4 parser/normalizer/replay/comparator/report/CLI artifact,
expected/profile fixture, and canonical replay report.

## Regression And Freeze

Targeted tests must cover valid records plus swapped cases, wrong raw hash,
manifest/source/artifact/package/report identity mismatches, same-name
different-content inputs, stale bindings, source replacement, symlinks, and
TOCTOU.  Integration must reproduce four package reports and four bindings
twice with byte and SHA-256 equality, audit all immutable hashes, and run the
repository's P7.3, P7.4, external-validation, schema-mirror, and complete
`tests/python` commands.

The final closeout may claim only deterministic P7.2 raw-to-P7.3 validated
artifact-to-P7.4 replay-report bindings for these four simulator-generated
FreeRTOS traces.  It is report-only for counts, byte sizes, and runtime.  It
must retain `hardware_validation=false`, `proof_parity_eligible=false`,
`proof_parity=not_evaluated`, `proof_correctness=not_evaluated`, and
`diagnosis_correctness=not_evaluated`.  P7.5 and P7.6 are not started.
