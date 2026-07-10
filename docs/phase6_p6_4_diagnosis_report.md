# Phase 6 P6.4 Diagnosis Report

## Scope

P6.4 converts the frozen P6.3 synthetic replay results into a deterministic JSON report. The report is still synthetic-only, metadata-only, and report-only. It does not prove proof correctness, proof parity, evidence-package replay equivalence, full trace reconstruction, real RTOS correctness, or generalization.

P6.5 and later work, including advisor comparison, LLM explanation comparison, top-k root-cause benchmarking, and human feedback, is outside this freeze.

## Inputs

- Source suite: the frozen P6.2 synthetic diagnosis suite.
- Replay results: P6.3 `synthetic_replay` case rows over that suite.
- Generated time: explicit caller input, or `suite.generated_at` when the caller leaves it unset. The builder never reads current time.

## Output Contract

The report schema version is `rtos-diagnosis-report-v1`. The top-level payload contains:

- `schema_version`, `report_id`, `generated_at`
- `source_suite`
- `source_replay_mode`
- `scope`
- `summary`
- `case_coverage`
- `preservation_metrics`
- `replay_status_metrics`
- `integrity_metrics`
- `claim_boundary`
- `limitations`
- `cases`
- `notes`

Per-case rows keep only bounded metrics and booleans. They intentionally omit raw `full_diagnosis`, `replay_diagnosis`, `source_records`, and proof facts.

## Metric Semantics

- `reference_only` counts as evaluated and participates in evidence-retention and preservation metrics.
- `reference_only` never counts as `replay_pass`, even when metadata retention is complete, `evidence_retention_ratio` is `1.0`, and `proof_drift_count` is `0`.
- Empty expected evidence refs have ratio `1.0` only for evaluated rows. `not_evaluated` rows keep the P6.3 missing-data ratio `0.0`.
- `all_required_cases_evaluated` only means no case stayed `not_evaluated`.
- `all_replay_passed` is stricter: every case must be an actual replay `pass`, with no `reference_only`, no `not_evaluated`, no proof drift, and no integrity errors.
- `preservation_complete` reports complete metadata preservation independently from replay pass semantics.
- `fail_closed` is reserved for fail-closed outcomes such as `fail`, `not_evaluated`, deterministic drift, or detected representation tamper. A normal `reference_only` row is not a replay pass, but it is also not counted as fail-closed.

## Integrity Rules

The builder fails closed and raises `ValueError` when:

- replay counts do not sum to `cases_total`
- `replay_pass` appears on `reference_only` or `not_evaluated`
- status flags disagree with each other
- case-kind coverage fields disagree with the required and observed case-kind sets
- claimable counts increase beyond the source suite
- forbidden output fields appear
- raw replay internals appear in the report
- canonical JSON exceeds roughly 64 KiB
- inputs are mutated during report construction

For a valid report, `forbidden_field_count` and `input_mutation_count` remain `0`.

## Determinism

- Cases are ordered by `case_id`.
- Unordered string lists are sorted and deduplicated.
- Canonical JSON uses `sort_keys=True`, `ensure_ascii=True`, `indent=2`, and a trailing newline.

## Current Frozen P6.2/P6.3 Baseline

The approved baseline remains:

- `cases_total=8`
- `replay_pass_count=0`
- `replay_fail_count=0`
- `reference_only_count=8`
- `not_evaluated_count=0`
- `proof_drift_count=0`
- `fail_closed_count=0`
- `preservation_complete_count=8`
- `evidence_retention_ratio=1.0`
- `all_required_cases_evaluated=true`
- `all_replay_passed=false`
- `synthetic_case_count=8`
- `real_hardware_case_count=0`
- `claimable_count=0`
- `report_only_count=8`

That baseline is a preservation report over synthetic metadata, not a replay-pass benchmark.
