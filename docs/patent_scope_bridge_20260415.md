# Patent Scope Bridge (2026-04-15)

## 1. Purpose

This bridge defines how to interpret project-level and patent-level documents without producing dual conclusions in the same repository.

## 2. Priority Rule

When conclusions conflict, adjudication priority is:

1. `realization/docs/patent_final_validation_entry_20260415.md`
2. `realization/docs/patent_final_validation_index_20260415.json`
3. `realization/docs/patent_10_4_formal_close_min_contract_20260415.md`
4. `realization/docs/patent_external_blocker_register_20260415.md`
5. Project-level historical docs (`final_validation_status_20260314.json`, `final_acceptance_readiness_20260314.md`, `external_validation_checklist_20260314.md`)

## 3. Scope Mapping

Project-level docs are valid for:

1. General readiness summary
2. Historical external execution snapshots
3. Non-patent release communication

Project-level docs are not sufficient for:

1. Patent `10.4` formal close adjudication
2. External dense `1GB` proof closure under patent scope
3. Patent-scope parity and long-duration soak closure

Patent-level docs are required for:

1. `10.4-A/B/C` closure status
2. External blocker state for patent closure
3. Final patent communication boundaries

## 4. Conflict Resolution Examples

If project-level status and patent gate status disagree:

1. Keep project-level historical fact unchanged.
2. Keep patent gate result as authoritative for patent closure.
3. Record the difference in blocker register, not by rewriting historical facts.
4. Current adjudicated patent gate result is `closed` for `10.4` under `patent_10_4_formal_close_min_contract_20260415`.

If old execution log contains stale runtime count and runtime artifacts are updated:

1. Keep the historical record in its original execution file.
2. Update patent gate current-state narrative to the latest checked-in artifact.
3. Validate with `tests.python.test_desktop_tooling.DesktopToolingTests.test_checked_in_runtime_artifacts_match_current_suite_size`.

## 5. Normative Statement for External Communication

Approved one-line scope statement:

`Patent 10.4 global formal close is closed under the frozen contract, based on Linux/Windows external dense 1GB A/B/C evidence, Windows-3 parity pass, and Linux 24h soak accepted as the current WP-06 closure basis.`
