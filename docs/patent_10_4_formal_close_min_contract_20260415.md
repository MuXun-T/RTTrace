# Patent 10.4 Formal Close Minimum Contract (2026-04-15)

## 1. Purpose and Boundary

This document freezes the minimum evidence contract for patent-oriented section `10.4` formal close.

Scope:

1. Applies only to patent `10.4-A/B/C` formal close adjudication.
2. Does not modify project-level historical facts.
3. Does not allow replacing formal evidence with prevalidation or perf-only artifacts.

Normative references:

1. `doc/详细设计说明书_专利导向_基于依赖侧车索引的有界证据闭包导出_v1.0.md` (`10.4`)
2. `realization/docs/patent_final_validation_entry_20260415.md`
3. `realization/docs/patent_scope_bridge_20260415.md`
4. `realization/docs/patent_external_blocker_register_20260415.md`

## 2. Common Contract for A/B/C

Each formal evidence record MUST contain:

1. `contract_version` (fixed, machine-readable)
2. `proof_group` (`A_control_plane_first` or `B_budget_pre_freeze` or `C_degraded_audit`)
3. `platform` (`linux` or `windows`)
4. `run_scope` (must be `patent_10_4_formal`)
5. `input_contract`:
   - `input_class=real_external_dense_1gb`
   - `trace_sha256`
   - `trace_size_bytes` (`>= 1073741824`)
   - `formal_1gb_verified=true`
6. `artifact_refs` (raw report path, package path, command, timestamp, environment summary)
7. `verdict` (`pass` or `fail`)

Global reject rules:

1. If `input_class` is not `real_external_dense_1gb`, result is supporting evidence only.
2. If `acceptance_scope=perf_only` and no `10.4` proof fields are present, result is supporting evidence only.
3. If either Linux or Windows formal evidence is missing, `10.4` remains `partial`.

## 3. Group A Contract (Control Plane First)

Required metrics:

1. `scan_count`
2. `seek_count`
3. `window_span_total`
4. `sidecar_lookup_count`
5. `sidecar_bytes`
6. `proof_digest` (traceable)

Required comparison fields:

1. `baseline_path_kind` (`legacy` or `clipped`)
2. `baseline_scan_or_seek`
3. `evidence_scan_or_seek`
4. `reduction_verdict`

Pass boundary:

1. Must show no fallback to full global rescan.
2. Must show auditable reduction against baseline path.

## 4. Group B Contract (Budget Pre-freeze)

Required metrics:

1. `frontier_halt_reason`
2. `truncated_frontier_count`
3. `projected_next_events`
4. `projected_next_bytes`
5. per-round `round_budget` and `round_read`
6. `reject_round_has_read`
7. `frontier_snapshot` or `frontier_refs`

Pass boundary:

1. Reject rounds must not perform new trace reads (`reject_round_has_read=false`).
2. Bounded package must link back to frozen frontier evidence.

## 5. Group C Contract (Degraded but Auditable)

Required scenarios:

1. `SIDECAR_MISMATCH`
2. `CYCLE_EXPANSION` (or equivalent cycle expansion scenario)
3. `CORRUPT_SEGMENT`
4. `TRACE_IO_GUARD`

Per-scenario required fields:

1. `blocker_artifact.code`
2. `minimal_legal_package_valid=true`
3. `proof_consumer_mode`
4. `proof_digest` and `frontier_snapshot`
5. `package_contract_validation_passed`

Pass boundary:

1. Degraded path still writes a minimal legal package and passes contract checks.
2. Replay/compare degraded behaviors remain machine-readable and auditable.

## 6. Platform / Input / Soak Dimensions

Platform:

1. Linux and Windows must both submit A/B/C summaries using the same `contract_version`.
2. Field set must be identical across platforms.

Input:

1. `small_correctness`, `mid_flow`, `external_dense_1gb` can be archived together.
2. Only `external_dense_1gb` can close formal `10.4`.

Soak:

1. Long-duration soak evidence must bind to the same `10.4` chain.
2. Mandatory fields: duration, error observations, command, environment summary.
3. If soak evidence is missing or not linked to `10.4` chain, `10.4` remains `partial`.

## 7. Allowed Outcomes

1. `closed`: all mandatory A/B/C + Linux/Windows + formal 1GB + soak conditions satisfied.
2. `partial`: repository proof chain exists but one or more formal conditions are missing.
3. `blocked_external`: missing conditions require external execution and cannot be forged in-repo.
