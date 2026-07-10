# Phase 6 P6.3 Replay Validation

## Scope

P6.3 validates replay preservation for the eight P6.2 synthetic fixtures only.  They are not hardware traces.  A result is report-only synthetic metadata preservation, not real RTOS generality, root-cause correctness, anomaly-detection quality, or proof parity.

## Inputs And Deterministic Diagnoses

The input is the frozen P6.2 suite and its small companion artifacts.  The full diagnosis is produced only when the candidate records satisfy the fixed case predicate: the case kind and its candidate diagnostic marker.  It contains the fixed root-cause identifier and kind, the fixed affected entity, required declared evidence references, the declared closure mode, and the matching deterministic signal identifiers.

The bounded representation contains only the case identity, selected named baseline and candidate signal records, the applicable tiny companion signal record when required, declared evidence references, closure mode, and a SHA-256 checksum of that selected record set.  Full input still reads the complete small companion artifacts.  Reopen reads only the selected representation and applies the same fixed predicate.  It does not reconstruct a raw trace or use an advisor, retrieval, or language model.

## Equivalence And Status

The equivalence scope is `metadata_only`.  Comparison requires retention of case identity, the root-cause identifier and kind, affected entity identity and any non-empty associated identifier, required evidence-reference set, closure mode, and deterministic signal identifiers.

- `pass`: all declared metadata is retained, closure is not `reference_only`, and deterministic proof-fact drift is zero.
- `reference_only`: comparison completed with all metadata retained and zero drift, but closure is `reference_only`.
- `fail`: the bounded representation is invalid, a required field is not retained, or proof-fact drift is non-zero.
- `not_evaluated`: required suite metadata or a companion artifact is absent, or the case predicate cannot produce a diagnosis.

`reference_only` and `not_evaluated` always set `replay_pass` to false.  The P6.2 cases all use `reference_only` closure, so they are intentionally not replay passes.

For the frozen P6.2 synthetic suite, all eight evaluated cases are `reference_only`: no case has `replay_pass=true`, and `pass_count` is zero.  This is metadata preservation within the declared scope, not full evidence-package replay equivalence.

## Evidence And Proof Boundary

Evidence retention is the fraction of required declared references retained after reopen.  Closure retention is an exact comparison of the declared closure mode.  P6.2 supplies no deterministic proof facts; its zero drift count means only that no supplied facts differ, not that a proof artifact was reproduced.  When callers supply deterministic facts for comparison, any difference fails closed.  This module neither changes nor writes the existing proof-hash or proof-digest paths.

## Exclusions

P6.3 excludes raw-trace reconstruction, diagnosis ranking or top-k evaluation, benchmark reporting, advisor-assisted variants, human feedback, real LLM calls, real hardware, and all P6.4 and later work.
