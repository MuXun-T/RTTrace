# RTD-Pilot Phase 2 Capture Contracts

Status: implemented contract surface only. No file in this directory is a real
Case, Capture, raw trace, or hardware claim.

## Authority and Boundaries

`rtd_injection_ledger` retains capture identity, configuration identity, and
injection administration only. Its only links to adjudicated material are
`observer_adjudication_ref`/digest and
`capture_validity_record_ref`/digest. It rejects manifestation, observer
conclusion, Capture-validity, invalid-capture, raw-trace, verdict, diagnosis,
and Agent-output fields. A Ledger is not a technical-validity record.

`rtd_observer_record` is the Observer Adjudication Record (OAR), retained under
the existing filename for the Phase 2 schema surface. OAR is the sole authority
for `observer_status`, `manifestation_status`, and
`manifestation_interval`. Its manifestation states are `manifested`,
`not_manifested`, and `not_assessable`.

`rtd_capture_validity_record` is the Capture Validity Record (CVR). CVR is the
sole authority for `capture_validity_status` and `invalid_reason`; its states
are `valid`, `invalid`, and `pending_adjudication`. CVR references OAR, CCM,
CIR, and the raw-artifact inventory but cannot write manifestation truth.

`rtd_capture_capability_manifest` (CCM) records configured capability only:
enabled event types, filters, sampling, buffer and flush configuration,
timestamp/payload/dictionary/mapping/collector versions, and configured trace
boundaries. Actual gaps, LOSS, OVERFLOW, watermark, truncation and corruption
are rejected from CCM.

`rtd_capture_integrity_record` (CIR) records actual acquisition degradation:
sequence state/gaps, LOSS, OVERFLOW, watermark, truncation, corruption,
alignment/mapping/observer-channel degradation, affected intervals/entities,
natural overflow and integrity reason codes. It rejects fault manifestation,
control labels, observer predicate outcomes, relevance, verdicts and Agent
output.

OAR, CVR, CCM, and CIR have separate writers and references. No object can
replace another: CCM cannot report acquired integrity, CIR cannot report
manifestation or verdicts, OAR cannot report Capture validity, and CVR cannot
report manifestation or diagnosis.

## Objects and Binding

All records use `schema_version = rtd-phase2-v1.0` and a SHA-256
`record_digest` over canonical ASCII JSON excluding that field. The structural
bundle validator retains all record versions and resolves the unique current
version as the unreferenced leaf of each complete `prior_*_ref` chain. It then
checks the Ledger OAR/CVR bindings, Ledger/CCM configuration, CCM/CIR binding,
CVR references, and raw-artifact identity/hash for the resolved current
versions.

Every `prior_*_ref` is required, including the explicit `null` root value.
`validate_case_definition_collection()`, `validate_snapshot_collection()`, and
`validate_seal_collection()` apply the same full-chain/sole-leaf rule outside a
Capture bundle. Case versions are partitioned by `case_id`, snapshot corrections
by unchanged `config_hash`, and seal corrections by `session_id`; a changed
configuration hash is a new snapshot lineage, not a correction of the old one.

Structural validation deliberately accepts `valid`, `invalid`, and
`pending_adjudication` captures. `validate_scoring_eligibility()` and
`tool/rtd_validate_capture_bundle.py --scoring-eligible` are separate checks:
they require a CVR of `valid`, a complete CIR and OAR, and an available raw
artifact. Invalid or pending captures remain retained but cannot enter scoring.

The collector snapshot adapter produces only
`rtd_collector_config_snapshot`. It includes source metadata digest and adapter
version but has no Capture identity. A caller supplies Capture identity only
when assembling a CCM. The adapter rejects runtime results and truth fields.

## Case Identity Contract

Phase 2 freezes identifier and binding rules only; it creates no real Case.
`scenario_template_id`, `case_id`, and `capture_id` must respectively match
`scenario:<token>`, `case:<token>`, and `capture:<token>`, where `<token>`
starts with ASCII alphanumeric text and then uses only alphanumeric text,
period, underscore, or hyphen. A Case definition must set `example_only: true`;
`false` is rejected until Phase 5.

A Case ID is unique and cannot be reused after any identity-defining change:
scenario template, fault family/variant or control role, injection definition,
version or parameters, target entity binding, workload seed, manifestation
predicate ID/version, or configuration hash. A correction may retain its Case
ID only when all of those fields are unchanged and its `prior_case_definition_ref`
forms one unbranched version chain.

Each Capture binds to exactly one Case ID across Ledger, OAR, and CVR history.
One Case may bind several independent Capture IDs. Ledger must match its Case's
template, fault/control, injection, seed, and configuration fields; OAR must
match its manifestation predicate. A Capture bound to an unknown Case, another
template, a different identity field, or multiple Case IDs is rejected. Phase 5
alone may instantiate real F1/F2/F3 Cases and controls.

## Ledger Workflow

`tool/rtd_append_ledger.py` is the compliant writer. It serializes writers,
assigns a monotonic append sequence and previous-record digest, canonicalizes
the row, and fsyncs it. `tool/rtd_seal_ledger.py` writes a new seal using
exclusive create. The seal binds the canonical JSONL bytes, row count, first
and last sequence, and last record digest. `tool/rtd_validate_ledger.py` is
read-only and rejects changed, inserted, reordered or noncanonical records.

The workflow detects silent changes made outside the compliant writer when the
retained seal is intact. A privileged actor able to replace both ledger and
seal is outside the cryptographic assurance of local filesystem hashes; owner
controlled immutable retention is required in deployment.

There is no implicit legacy write path. A future compatibility reader may only
derive read-only legacy presentation values from OAR/CVR; it must never write
those values into Ledger.

## Deferred Scope

This contract does not implement Phase 3 lineage, UntrustedWindow, parser or
rebuild changes, CaptureEpisode/CaseEpisodeBundle, a diagnoser, CAPE-RT,
Agent/LLM behavior, database/UI services, hardware collection, or formal fault
Cases.
