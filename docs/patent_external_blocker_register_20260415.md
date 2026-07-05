# Patent External Blocker Register (2026-04-15)

## 1. Register Scope

This register tracks blockers that cannot be closed by repository-only edits and require external execution evidence.

## 2. Active Blockers

None for patent `10.4` global formal close under the current platform and soak policy.

## 3. Closed Blockers

### B01_formal_close_external_validation

Status: `closed`

Reason:

1. Linux-side patent `10.4` formal evidence is now checked in for `A/B/C` with real external dense `1GB` input and auditable `pass` verdicts.
2. Linux-side `WP-06` 24h soak evidence is now checked in and explicitly linked to the same `10.4` proof chain.
3. Windows-side patent `10.4` formal evidence is now checked in under `/home/zzq/patent/windows-3/formal_10_4_wp05_pass` for `A/B/C` with the same `real_external_dense_1gb` input SHA-256.
4. Patent-scope Linux/Windows parity now passes with `ready_for_gate=true`.
5. The current Windows soak policy accepts the Linux `24h` soak as the WP-06 closure basis; Windows-side long soak is not a remaining blocker.

Sub-blockers:

1. `B01-A external_dense_1gb_formal`
   - required output: formal run report satisfying `patent_10_4_formal_close_min_contract_20260415.md`
   - current state: satisfied
   - evidence:
     - `/home/zzq/patent/realization/docs/evidence_proof_archive_20260418/formal_10_4/A_control_plane_first/formal_summary_linux.json`
     - `/home/zzq/patent/realization/docs/evidence_proof_archive_20260418/formal_10_4/B_budget_pre_freeze/formal_summary_linux.json`
     - `/home/zzq/patent/realization/docs/evidence_proof_archive_20260418/formal_10_4/C_degraded_audit/formal_summary_linux.json`
     - `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/A_control_plane_first/formal_summary_windows.json`
     - `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/B_budget_pre_freeze/formal_summary_windows.json`
     - `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/C_degraded_audit/formal_summary_windows.json`
2. `B01-B cross_platform_parity_formal`
   - required output: Linux + Windows A/B/C summaries with same contract version and field set
   - current state: satisfied
   - evidence:
     - `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/windows_final_readiness_report.linux_recheck.json`
     - `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/formal_parity_report.linux_recheck.json`
     - `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/package_sweep_linux_recheck.json`
     - `/home/zzq/patent/windows-3/wp05_windows_artifact_audit_20260429.md`
3. `B01-C long_duration_soak_formal`
   - required output: soak report bound to the same `10.4` proof chain
   - current state: satisfied
   - policy: Windows-side long soak is not required for the current gate; Linux `24h` soak is accepted as the WP-06 closure basis.
   - evidence:
     - `/home/zzq/patent/realization/docs/evidence_proof_archive_20260421/formal_10_4/WP06_24h_linux/soak_report_linux.json`
     - `/home/zzq/patent/realization/docs/evidence_proof_archive_20260421/formal_10_4/WP06_24h_linux/formal_summary_linux.json`
     - `/home/zzq/patent/realization/docs/evidence_proof_archive_20260421/formal_10_4/WP06_24h_linux/wp07_linux_readiness.json`
     - `/home/zzq/patent/Windows/soak/windows_wp06_soak_scope_note.json`

## 4. Evidence Boundaries

Supporting-only (cannot close blockers):

1. repo-controlled prevalidation `1GB` artifacts
2. perf-only acceptance reports without full `10.4` proof fields
3. project-level closed statuses without patent-scope mapping

Closable evidence (required):

1. external run artifacts that satisfy all mandatory contract fields
2. explicit `pass/fail` verdicts with auditable command, hash, and environment metadata
3. dual-platform and long-soak records linked to the same proof chain

## 5. Ownership and Next Action

Owner: external validation execution team (outside repository-only scope)

In-repo owner: patent gate maintainer for blocker tracking and narrative consistency

Next action:

1. Keep Linux and Windows imported evidence pinned in patent gate docs and communication boundaries.
2. Preserve the Windows-3 delivery tarball and checksum as the current Windows formal close artifact.
3. Continue normal regression-only maintenance; no external formal blocker remains open for patent `10.4`.

## 6. Closure Criteria

`B01` is marked closed because:

1. `B01-A`, `B01-B`, and `B01-C` are all satisfied with auditable external evidence.
2. Patent gate entry and index both record `10.4-A/B/C` as `closed`.
3. Scope bridge and external communication wording now align with the closed status.
