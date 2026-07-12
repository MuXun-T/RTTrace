# P7.3 package reopen closeout

P7.3 is an offline package-open phase, not replay. Its subphases are P7.3.0
`80a4785`, P7.3.1 `7ca32d0`, P7.3.2 `505999f`, P7.3.3 `12c2dd1`, P7.3.4
`760a2ea`, P7.3.5 `2ab59e3`, and P7.3.6 `df1cfb2`; P7.3.7 records the final
freeze.

The schemas/models define manifest, source, artifact, reference, result and
reason contracts. The directory reader is read-only and rejects unsafe paths,
links, special files and oversized input. The validator loads the P7.2 source
inventory exactly once per validation into a frozen descriptor context, then
uses that same descriptor set for provenance, source before/after snapshots,
and validation. Package snapshots include root identity and deterministic entry
summaries plus manifest and each declared artifact. Mutation counts are unique
descriptor-entry counts: frozen `(source_id, artifact_id)` entries for sources,
and root, manifest and declared artifact entries for packages.

Acquisition provenance is anchored to `acquired`, `acquisition_blocked`, or
`external_reference_only`; status rewriting, local artifacts for reference-only
sources, and hardware assertions are invalid. Result coverage is
`not_attempted`, `opened`, `opened_reference_only`, `blocked`, `invalid`, and
`unsupported`. CLI reports have fixed exit codes and retain
`replay_evaluated=false` and `hardware_validation=false`.

Focused tests cover four FreeRTOS artifacts, reference-only/blocked contracts,
malformed and adversarial inputs, path/link restrictions, descriptor reuse,
provenance rewrites, source/package mutation and canonical CLI output. The
full Python regression is run before freezing. P6 diagnosis report hash and
P7.2 source content remain unchanged. Remaining gap: semantic replay is out of
scope and requires separate P7.4 planning.
