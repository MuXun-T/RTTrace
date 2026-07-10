# Phase 6 P6.6 Human Feedback Contract

P6.6 provides a privacy-preserving, synthetic-only feedback contract and a
deterministic descriptive aggregation pipeline for P6.4 reports and P6.5
review overlays. It does not collect or analyse real participant data.

## Data Boundary

Only records marked `data_source=synthetic`, `synthetic=true`,
`real_participant=false`, and `pipeline_validation_only=true` are accepted.
`consented_manual` and all other data sources fail closed in this release.
No direct identifier, demographic attribute, free text, exact personal time,
secret, path, action, tool request, proof field, or diagnosis truth field is
permitted. Records use fixed enumerations and five integer ratings (1--5).

## Consent And Retention

Synthetic records declare consent as not applicable. A manual record would
require an explicit, granted, versioned consent contract and non-repository
storage, but is rejected by the P6.6 pipeline pending separate approval.
Deletion is represented only as a future governance capability
(`deletion_supported`); P6.6 stores no participant record or identifier.

## Aggregation And Claims

The aggregator verifies canonical P6.4/P6.5 source hashes, never mutates its
inputs, rejects duplicate response IDs, and emits count/mean/min/max metrics
only. Empty rating sets have a `null` mean, minimum, and maximum. It performs
no inferential statistics, ranking, causal inference, correctness evaluation,
or human-effect assertion.

P6.6 therefore completes the privacy-preserving human-feedback contract,
synthetic feedback pipeline, and descriptive aggregation validation only. It
does not establish human factors, usability improvement, explanation quality,
diagnosis correctness, replay correctness, or proof correctness.
