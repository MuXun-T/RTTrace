# Phase 7 P7.4 Replay Contract

Version: `p7.4-semantic-replay-v1`.

## State And Start

`ReplayState` is a closed, mutually exclusive enum: `not_evaluated`, `reference_only`, `replay_pass`, `replay_fail`.

P7.3 package-open attempt is distinct from P7.4 semantic replay attempt. P7.3 `not_attempted`, `blocked`, `invalid`, and `unsupported` map to `not_evaluated`, retaining the original package result and reasons. P7.3 `opened_reference_only` maps to `reference_only`, with both attempt flags false. Only P7.3 `opened` enters P7.4 preflight.

Preflight checks the opened result, approved source/trace binding and checksum, supported replay/comparison profile versions, expected contract and identity binding, parser/normalizer/replay/comparator versions, required trace, and before snapshots. Before and after the run, P7.4 snapshots the approved raw trace by no-follow descriptor identity, byte count, and SHA-256 as well as source/package snapshots. Missing-after-open is `TRACE_MISSING_AFTER_OPEN`; descriptor/size/hash drift is `TRACE_CHECKSUM_DRIFT`; any nonzero source, package, or raw-trace mutation fails a started replay. A preflight failure is `not_evaluated`. Immediately before parser record zero is supplied, and nowhere else, the engine sets `replay_attempted=true`. It sets `comparison_attempted=true` only at comparator invocation. Any later parse, normalization, invariant, actual-output, comparison, mutation, TOCTOU, or determinism failure is `replay_fail`; it cannot fall back to `not_evaluated` or `reference_only`.

## Reasons

`ReplayReason` is closed. Report reason codes are unique and sorted by this explicit priority, and `primary_reason` is the first code:

1. `SOURCE_MUTATED`, `PACKAGE_MUTATED`, `TRACE_CHECKSUM_DRIFT`, `TRACE_MISSING_AFTER_OPEN`, `SECURITY_BOUNDARY_VIOLATION`
2. `TRACE_SIZE_LIMIT_EXCEEDED`, `LINE_LENGTH_LIMIT_EXCEEDED`, `EVENT_LIMIT_EXCEEDED`, `SIGNAL_LIMIT_EXCEEDED`
3. `PARSE_ERROR`, `UNSUPPORTED_REQUIRED_RECORD`, `TIMESTAMP_REGRESSION`, `EVENT_ORDER_VIOLATION`, `NORMALIZATION_ERROR`
4. `STATE_TRANSITION_VIOLATION`, `REPLAY_INVARIANT_VIOLATION`, `ACTUAL_OUTPUT_MISSING`
5. `EXPECTED_OUTPUT_IDENTITY_MISMATCH`, `COMPARISON_MISMATCH`, `DETERMINISM_MISMATCH`, `INTERNAL_REPLAY_ERROR`
6. `PACKAGE_NOT_ATTEMPTED`, `PACKAGE_BLOCKED`, `PACKAGE_INVALID`, `PACKAGE_UNSUPPORTED`, `DATA_NOT_APPROVED`, `REPLAY_PROFILE_UNSUPPORTED`, `COMPARISON_PROFILE_UNSUPPORTED`, `TRACE_FORMAT_UNSUPPORTED`, `EXPECTED_CONTRACT_UNAVAILABLE`
7. `PACKAGE_OPENED_REFERENCE_ONLY`, `SOURCE_EXTERNAL_REFERENCE_ONLY`, `SOURCE_ACQUISITION_BLOCKED`, `METADATA_ONLY_CONTRACT`

P7.3 reason strings are preserved separately as package-open facts and never converted into P7.4 reason values.

## Event And Replay Scope

Events are immutable closed records with stable serialization, including event/source indices, timestamp and unit, source format, CPU/task identity, event kind, optional state transition/object/numeric fields, priority, and a sorted closed attributes mapping. `source_record_index` is zero-based semantic input order: BTF headers and VCD directives/timestamp-only lines do not count; VCD scalar changes do. Errors separately use one-based physical lines. For this profile `timestamp_unit` is `us`, without numeric scaling. `MAX_EVENTS` applies only to semantic-candidate records. Paths, time-of-run, source creation time, temporary names, host/user data, random IDs, and unknown extras are forbidden.

The minimum engine validates continuous event indexes, valid source indexes, source ordering/timestamps, task lifecycle and deleted-task exclusion, per-CPU running ownership, context-switch consistency, optional IRQ balance, supported object operations, stage event counts, and stable actual serialization. It is not a general RTOS simulator. VCD signal transitions are markers unless a frozen signal mapping says otherwise.

## Expected And Comparison

Expected fixtures are independently manually curated by a `manual_raw_trace_audit` of frozen raw bytes and the format contract, not from any P7.4 production output. They contain `expected_id`, `expected_version`, immutable authoring-evidence ID, `reviewer_roles=["agent_a","agent_e"]`, provenance/commit, trace ID/SHA-256, authoring method, review scope, semantic/exact/ignored/tolerated fields, tolerances and rationale, comparison profile identity, `hardware_validation=false`, and their own canonical identity. The authoring audit may read frozen raw bytes but may not invoke or inspect a P7.4 parser, normalizer, replay engine, comparator, or actual output. A/E approve them before P7.4.5; that commit freezes fixtures and profiles. Expected mutation, trace/profile binding mismatch, self-blessing, post-mismatch tolerance expansion, or ignoring/tolerating core identity/order/state/count fields is rejected.

The default profile is exact deterministic comparison. Mismatches use stable class/field/index ordering and classify missing expected/actual, unexpected actual, value, ordering, count, state-transition, timestamp, identity, invariant, and determinism differences.

## Report, CLI, And Security

The closed replay report requires: `replay_state` (ReplayState), `replay_attempted` (bool), `comparison_attempted` (bool), `primary_reason` (ReplayReason or null), `reason_codes` (ordered unique ReplayReason array), `package_open_result` (P7.3 result string), `replay_profile_id` (string), `comparison_profile_id` (string), `source_identity`, `package_identity`, `trace_identity`, `expected_identity`, and `actual_identity` (SHA-256 string or null), `source_mutation_count`, `package_mutation_count`, and `raw_trace_mutation_count` (nonnegative integers), `llm_invocation_count`, `advisor_invocation_count`, `feedback_invocation_count`, `network_invocation_count`, `shell_invocation_count`, and `subprocess_invocation_count` (zero nonnegative integers), and `hardware_validation=false`. It also carries closed invariant, comparison, event-count, and identity facts. Canonical JSON is UTF-8, ASCII escaped, sorted compact keys, one LF, finite values, and contains no unstable machine or path data. Performance values are report-only and never affect state or truth digest.

All six invocation counts (`llm`, `advisor`, `feedback`, `network`, `shell`, `subprocess`) are zero. The truth path does not use environment configuration, network, shell, subprocess, or source/package/raw/proof writes. Repeated reports must have byte and SHA-256 equality.

CLI exit codes are frozen: `0` replay pass, `2` reference only, `3` not evaluated, `4` replay fail, `64` usage, `70` internal/output error. Multi-case aggregation uses the same state/reason priority and a replay failure never exits zero.

## Boundaries

Allowed claims are limited to deterministic parsing and normalization of the four named simulator-generated FreeRTOS inputs, replay/comparison under these frozen profiles, resulting replay states, repeatable canonical reports, and observed zero mutation/zero truth-path invocation counts. Report-only facts are trace bytes, event counts, elapsed times, mismatch counts, 4-core/50k observations, and simulator pass/fail counts. Forbidden claims include diagnosis or root-cause accuracy, proof parity or proof correctness, semantic output as correctness proof, general FreeRTOS/RTOS support, hardware validation, cross-board generalization, performance/benchmark advantage, baseline superiority, SOTA, human usability, live LLM quality, or automatic-remediation safety. `package opened` is not replay pass; checksum/identity validity is not diagnosis correctness; semantic equality is not proof correctness; replay is not proof parity; simulator generation is not hardware validation. P7.4 reports `hardware_validation=false`, `proof_parity=not_evaluated`, `proof_correctness=not_evaluated`, and `diagnosis_correctness=not_evaluated`. P7.5 is not started.
