# P7.3/P7.4 Per-Case Evidence Binding Contract

Version: `p7.3-p7.4-case-evidence-binding-v1`.

## Scope

This contract supplies auditable evidence closure for exactly four acquired
P7.2 FreeRTOS raw traces.  It consumes existing P7.3 package-open and P7.4
replay facts without changing their schemas, state machines, validators,
reports, or canonical artifacts.  A binding is evidence of identity continuity;
it is not replay success, proof parity/correctness, diagnosis correctness,
hardware validation, or a P7.5 result.

## Per-Case Manifest Contract

Each record has exactly one case ID and one P7.3 self-contained package:

| case ID | package ID | source ID | artifact ID | artifact logical path | format |
| --- | --- | --- | --- | --- | --- |
| `freertos_btf_1core` | `p7.3.freertos_btf_1core.v1` | `freertos_btf_trace` | `btf_1core` | `example.btf` | `BTF` |
| `freertos_vcd_1core` | `p7.3.freertos_vcd_1core.v1` | `freertos_btf_trace` | `vcd_1core` | `example.vcd` | `VCD` |
| `freertos_btf_4cores` | `p7.3.freertos_btf_4cores.v1` | `freertos_btf_trace` | `btf_4cores` | `example-4cores.btf` | `BTF` |
| `freertos_btf_50k` | `p7.3.freertos_btf_50k.v1` | `freertos_btf_trace` | `btf_50k` | `example-50k.btf` | `BTF` |

Each manifest uses existing P7.3 contract name/version, manifest version,
closed source and artifact descriptors, `sha256`, and
`p7.3-package-open-only-v1`.  It contains the P7.2 repository, commit,
acquisition status, source ID, artifact ID, logical path, type/format,
declared bytes, raw SHA-256, and `hardware_validation=false`, all projected
from the frozen P7.2 inventory and source file.  Existing P7.3 identity
functions calculate manifest, source, artifact, configuration, and package
identities.  No identity is inferred from a name or location.

Because P7.3 validates a self-contained local artifact, a deterministic test
or generator copies exactly that frozen raw file into a temporary package.
The committed fixture is small metadata only.  The actual result comes only
from the existing P7.3 reader plus validator and the existing package-reopen
CLI.  Its unchanged report exposes package identity, open status,
integrity/completeness/provenance counts, and source/package mutation counts;
it does not expose manifest, source, or artifact identity fields.  The later
binding deterministically carries those identities from the validated manifest
and sealed P7.2 inventory while retaining the report's raw-byte SHA-256 and
result.  Hand-authored open results are prohibited.

## Binding v1 Record

The new schema/model is immutable, closed, canonical JSON, and rejects
unknown fields, absolute paths, temporary paths, and unstable values.  Its
required fields are:

```
binding_version
case_id
p7_2_source_id
p7_2_artifact_id
p7_2_raw_trace_sha256
p7_3_manifest_identity
p7_3_source_identity
p7_3_artifact_identity
p7_3_package_identity
p7_3_canonical_report_path
p7_3_canonical_report_sha256
p7_4_replay_report_path
p7_4_replay_report_sha256
p7_4_trace_identity
p7_4_legacy_aggregate_package_identity
p7_4_legacy_package_identity_qualification
hardware_validation
```

`binding_version` is exactly this contract version and
`hardware_validation` is exactly `false`.  Canonical serialization is UTF-8,
ASCII escaped, sorted compact keys, one trailing LF, with no time, random,
host, environment, absolute, or temporary-path values.  Canonical binding
identity is the SHA-256 of the stored canonical bytes and is not embedded in
the record, avoiding a self-reference cycle.

Validation fails closed unless all of the following hold:

1. the P7.2 source and artifact exist in the sealed inventory and the raw
   no-follow file's bytes/SHA-256 equal its record;
2. the P7.3 manifest is valid under unchanged P7.3 rules and its source,
   artifact, and package identity projections equal the binding;
3. the P7.3 canonical report's raw SHA-256 equals the binding and records a
   successful, zero-mutation self-contained open for that package;
4. the named frozen P7.4 report's raw SHA-256 equals the binding, has the same
   case and source context, and its `trace_identity` equals the P7.2 raw
   SHA-256; and
5. the P7.2 raw SHA-256, P7.3 validated artifact SHA-256 projection, and
   P7.4 `trace_identity` are exactly equal.

The P7.3 artifact identity is a structured P7.3 identity; equality of its
embedded SHA-256 with P7.2/P7.4 trace identity is checked by recomputation,
not by equating identity digest formats.

P7.4's existing FreeRTOS reports contain the frozen aggregate P7.3
`package_identity` from `packages/reports/opened.json`.  The binding preserves
that value only as `p7_4_legacy_aggregate_package_identity` and fixes the
qualification text: `legacy aggregate P7.3 package identity; not equal to the
per-case P7.3 package identity`.  Validation requires the value to match the
unchanged P7.4 report and the unchanged aggregate report; it must reject a
claim that it is the per-case package identity.

## Security, Mutation, and Determinism

All input reads use the existing P7.3 no-follow descriptor policy where
available and re-snapshot source/package/raw inputs before and after work.
Source/package/raw mutation counts must be zero.  Validator tests must reject
swapped cases, wrong hashes/identities, same-name different-content files,
stale reports, source replacement, symlinks, and TOCTOU.  Production binding
construction performs no network, shell, subprocess, environment, LLM,
Advisor, or Feedback call and writes no source/raw/P7.3/P7.4/proof input.

Generating the same four reports and bindings twice must yield byte-identical
canonical files and equal SHA-256 values.  Existing `opened.json`, six P7.4
reports, expected fixtures, comparison profiles, P7.2 raw inputs, Phase 6,
P7.0-P7.4 frozen facts, and prohibited paths must retain their hashes.

## Explicit Exclusions and Claims

Zephyr and Zephelin have no acquired raw trace and are excluded.  This
contract creates no fake trace, package, artifact, reference-only binding, or
closure claim for them.

Allowed claim: the four named simulator-generated FreeRTOS raw traces have
deterministic P7.2-to-P7.3-to-P7.4 identity bindings.  Report-only facts are
package/artifact/binding counts, byte sizes, and execution time.  Forbidden
claims include P7.5 work, P7.6 work, proof parity/correctness, diagnosis or
root-cause correctness, generic FreeRTOS/RTOS support, hardware validation,
performance, baseline superiority, or semantic correctness beyond existing
P7.4 claims.
