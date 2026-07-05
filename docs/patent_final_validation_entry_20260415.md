# Patent Final Validation Entry (2026-04-15)

## 1. Authoritative Gate Entry

This document is the only authoritative gate entry for evaluating whether the project satisfies the patent-oriented detailed design closure scope.

Authoritative scope:

1. Detailed design closure for patent subsystem (`P1-P4`) and section `10.4` proof-oriented acceptance.
2. Gap classification and gate decisions for `G2/G3/G4/G5`.

Authoritative inputs:

1. `/mnt/hgfs/share-document/patent/realization/docs/serial_iterations/group01_worker01_review.md`
2. `/mnt/hgfs/share-document/patent/realization/docs/serial_iterations/group01_worker02_plan.md`
3. `/mnt/hgfs/share-document/patent/doc/详细设计说明书_专利导向_基于依赖侧车索引的有界证据闭包导出_v1.0.md`
4. `/mnt/hgfs/share-document/patent/realization/docs/serial_iterations/group05_worker01_review.md`
5. `/mnt/hgfs/share-document/patent/realization/docs/serial_iterations/group05_worker02_plan.md`
6. `/mnt/hgfs/share-document/patent/realization/docs/serial_iterations/group05_worker03_execution.md`
7. `/mnt/hgfs/share-document/patent/realization/docs/patent_10_4_formal_close_min_contract_20260415.md`
8. `/mnt/hgfs/share-document/patent/realization/docs/patent_scope_bridge_20260415.md`
9. `/mnt/hgfs/share-document/patent/realization/docs/patent_external_blocker_register_20260415.md`
10. `/home/zzq/patent/realization/docs/evidence_proof_archive_20260418/formal_10_4/A_control_plane_first/formal_summary_linux.json`
11. `/home/zzq/patent/realization/docs/evidence_proof_archive_20260418/formal_10_4/B_budget_pre_freeze/formal_summary_linux.json`
12. `/home/zzq/patent/realization/docs/evidence_proof_archive_20260418/formal_10_4/C_degraded_audit/formal_summary_linux.json`
13. `/home/zzq/patent/realization/docs/evidence_proof_archive_20260421/formal_10_4/WP06_24h_linux/formal_summary_linux.json`
14. `/home/zzq/patent/realization/docs/evidence_proof_archive_20260421/formal_10_4/WP06_24h_linux/soak_report_linux.json`
15. `/home/zzq/patent/realization/docs/evidence_proof_archive_20260421/formal_10_4/WP06_24h_linux/wp07_linux_readiness.json`
16. `/home/zzq/patent/realization/docs/evidence_proof_archive_20260418/formal_10_4/parity/formal_parity_report.json`
17. `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/windows_final_readiness_report.linux_recheck.json`
18. `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/formal_parity_report.linux_recheck.json`
19. `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/package_sweep_linux_recheck.json`
20. `/home/zzq/patent/windows-3/wp05_windows_artifact_audit_20260429.md`
21. `/home/zzq/patent/windows-3/windows_formal_10_4_delivery_checksums_wp05_pass_20260429.txt`
22. `/home/zzq/patent/Windows/soak/windows_wp06_soak_scope_note.json`

## 2. Scope Boundary and Conflict Resolution

Conflict identified:

1. `/mnt/hgfs/share-document/patent/realization/docs/final_validation_status_20260314.json:51-166` marks several project-level external items as `closed`.
2. Patent-specific documents previously distinguished checked-in Linux formal evidence from the cross-platform blocker; as of the Windows-3 recheck, that blocker is closed:
   - `/home/zzq/patent/realization/docs/patent_external_blocker_register_20260415.md`
   - `/home/zzq/patent/realization/docs/专利代理沟通口径_20260415.md`
   - `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/formal_parity_report.linux_recheck.json`

Resolution rule (to avoid dual fact sources):

1. `final_validation_status_20260314.json` is treated as project-level/general validation status reference.
2. Patent closure decisions for this execution line are decided only by this entry and `/mnt/hgfs/share-document/patent/realization/docs/patent_final_validation_index_20260415.json`.
3. When these sources disagree, this entry takes precedence for patent detailed-design closure gates.
4. The normative bridge for cross-document interpretation is `/mnt/hgfs/share-document/patent/realization/docs/patent_scope_bridge_20260415.md`.

## 3. Closure Status Snapshot

### 3.1 Closed Core Implementation Items

1. Evidence export core path is implemented (`export_Evidence`):
   `/mnt/hgfs/share-document/patent/realization/desktop/services.py:3698-3720`.
2. `proof_digest / frontier_snapshot / blocker_artifact` write path is implemented:
   `/mnt/hgfs/share-document/patent/realization/desktop/evidence_export.py:943-1055`.
3. Repro consumer path exposes `proof_digest / proof_verification / consumer_mode / result_validity`:
   `/mnt/hgfs/share-document/patent/realization/desktop/repro_evidence.py:599-611`.
4. Regression coverage exists for Mode-B success matrix and object-edge expansion:
   `/mnt/hgfs/share-document/patent/realization/tests/python/test_desktop.py:3824-3895`.
5. `G4-W3` closed the minimum GUI patent surface without changing proof semantics or `patent-job-v1`:
   - evidence export entry and async submit:
     `/mnt/hgfs/share-document/patent/realization/desktop/app/gui.py:1063-1111`,
     `/mnt/hgfs/share-document/patent/realization/desktop/app/gui.py:3153-3276`
   - structured proof digest + closure presentation:
     `/mnt/hgfs/share-document/patent/realization/desktop/app/gui.py:1124-1147`,
     `/mnt/hgfs/share-document/patent/realization/desktop/app/gui.py:1615-1720`
   - offscreen GUI regression for patent primary flow:
     `/mnt/hgfs/share-document/patent/realization/tests/python/test_desktop_runtime.py:264-309`
   - execution record:
     `/mnt/hgfs/share-document/patent/realization/docs/serial_iterations/group04_worker03_execution.md`

### 3.2 Formal Evidence Closure

1. Linux-side patent `10.4` formal evidence is imported and auditable:
   - `/home/zzq/patent/realization/docs/evidence_proof_archive_20260418/formal_10_4/A_control_plane_first/formal_summary_linux.json`
   - `/home/zzq/patent/realization/docs/evidence_proof_archive_20260418/formal_10_4/B_budget_pre_freeze/formal_summary_linux.json`
   - `/home/zzq/patent/realization/docs/evidence_proof_archive_20260418/formal_10_4/C_degraded_audit/formal_summary_linux.json`
   - `/home/zzq/patent/realization/docs/evidence_proof_archive_20260421/formal_10_4/WP06_24h_linux/soak_report_linux.json`
   - `/home/zzq/patent/realization/docs/evidence_proof_archive_20260421/formal_10_4/WP06_24h_linux/wp07_linux_readiness.json`
2. Windows-side patent `10.4` formal evidence is imported through the Windows-3 audited root:
   - `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/A_control_plane_first/formal_summary_windows.json`
   - `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/B_budget_pre_freeze/formal_summary_windows.json`
   - `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/C_degraded_audit/formal_summary_windows.json`
   - `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/windows_final_readiness_report.linux_recheck.json`
   - `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/package_sweep_linux_recheck.json`
3. Patent-scope Linux/Windows parity is closed:
   - `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/formal_parity_report.linux_recheck.json`
   - status: `pass`
   - `ready_for_gate=true`
   - A/B/C mandatory field sets match Linux checked-in summaries
   - A/B/C `metric_diff_count=0`
4. The Windows-3 delivery tarball and checksum are generated from the same audited root:
   - `/home/zzq/patent/windows-3/formal_10_4_wp05_pass.tar.gz`
   - `/home/zzq/patent/windows-3/windows_formal_10_4_delivery_checksums_wp05_pass_20260429.txt`
   - SHA-256: `7096f090155d807e825ec9457f526905178cf21c999ebe55fb6e1c9919299f8d`
5. WP-06 soak closure policy for this gate accepts the Linux `24h` soak as the current formal long-duration evidence:
   - `/home/zzq/patent/realization/docs/evidence_proof_archive_20260421/formal_10_4/WP06_24h_linux/soak_report_linux.json`
   - duration: `86439.248334` seconds
   - iterations: `39`
   - errors: `0`
   - Windows-side soak is not a remaining blocker under the current platform policy, as recorded in `/home/zzq/patent/Windows/soak/windows_wp06_soak_scope_note.json`.
6. The closed blocker record is registered in:
   `/mnt/hgfs/share-document/patent/realization/docs/patent_external_blocker_register_20260415.md`.
7. Runtime artifact freshness mismatch noted in earlier `G4` context is now reconciled:
   - `/mnt/hgfs/share-document/patent/realization/docs/desktop_runtime_stage5_linux_venv.json` records `tests_run=12`
   - `/mnt/hgfs/share-document/patent/realization/docs/desktop_runtime_windows.json` records `tests_run=12`
   - no freshness blocker is kept in the current patent gate narrative.

## 4. Detailed Design Item Gates

Gate mapping summary (full machine-readable index in JSON):

1. `1.2` scope-to-test mapping: `closed`, target group `regression_only`.
2. `3.5` dedicated lanes: `closed`, keep regression.
3. `3.5` standard `JOB-*` state contract: `closed`, keep regression.
4. `10.4-A/B/C` proof closure: `closed`, Linux and Windows formal evidence imported; Windows-3 patent-scope parity passed with `ready_for_gate=true`.
5. `11.2` sidecar generation/validation lane productization: `closed`, keep regression.
6. `11.3` closure engine and three-state finalize: `closed`, keep regression.
7. `11.4` windowed read/package/repro consumption: `closed`, keep regression.
8. GUI minimum patent surface: `closed`, keep regression.
9. `10.4` formal-close contract governance docs: `closed`, target group `closed_regression_only`.

## 5. G2/G3 Completion Record and G4 Input Contract

`G2` completed lane and entry productization without changing proof semantics:

1. `sidecar_build` and `evidence_query` are first-class job kinds and service entry points.
2. `export_evidence` remains backward compatible while the CLI now executes it through the job submit path.
3. `sidecar-build` and `evidence-query` CLI entries provide minimal lane-level product surfaces.
4. GUI received only the evidence-query job-manager wiring required for later `G4` work.
5. G2 execution evidence is recorded in `/mnt/hgfs/share-document/patent/realization/docs/serial_iterations/group02_worker03_execution.md`.

`G3` must only address the standard `JOB-*` status contract and governance:

1. Preserve the G2 job kinds and service entry points.
2. Add structured patent-specific state fields or mapping without breaking generic `created/queued/running/succeeded/failed` behavior.
3. Do not claim GUI closure or formal close in `G3`.

`G3` completed the state-contract closure with minimal governance additions:

1. `BackgroundJobManager` now seeds and updates `patent-job-v1` payload fields for `sidecar_build / export_evidence / evidence_query_*`.
2. `export_evidence` progress substages are bridged to structured `JOB-*` states without breaking existing progress payloads.
3. Failure, timeout, cancel, budget rejection, finalized mode and halt reason are queryable from job payload.
4. `cancel` is exposed as a cooperative contract only; no unsafe thread interruption is claimed.
5. G3 execution evidence is recorded in `/mnt/hgfs/share-document/patent/realization/docs/serial_iterations/group03_worker03_execution.md`.

`G4-W1` should now focus only on GUI patent minimum surface:

1. Reuse existing `sidecar_build / export_evidence / evidence_query_*` job kinds and `patent-job-v1` payload contract.
2. Do not reopen `B03_job_contract_state_machine`.
3. Do not claim `10.4` formal close in `G4`.

`G4` completed the GUI minimum patent surface closure:

1. GUI now provides a first-class `Evidence` export entry and reuses the existing async job path.
2. GUI now exposes structured `Proof Digest` fields and explicit `Closure Mode` / `Halt Reason` labels.
3. GUI evidence export summary keys align with CLI output: `job_id / package_path / closure_mode / proof_digest`.
4. Mandatory regressions passed:
   - `tests.python.test_desktop_runtime`
   - `tests.python.test_desktop.DesktopServiceTests.test_cli_export_evidence_mode_a_emits_summary_payload`
   - `tests.python.test_desktop.DesktopServiceTests.test_cli_export_evidence_mode_b_accepts_external_sidecar_inputs`
   - `tests.python.test_repro_evidence.ReproEvidenceTests`
5. Execution evidence is recorded in `/mnt/hgfs/share-document/patent/realization/docs/serial_iterations/group04_worker03_execution.md`.

## 6. Gate Decision

1. `G1` is considered completed after this entry and its index are generated and validated.
2. `G2` is considered completed after lane productization, CLI entry, tests, and docs are validated.
3. `G3` is considered completed after job state contract, governance fields, tests, and docs are validated.
4. `G4` is considered completed after GUI minimum patent surface, mandatory regressions, and gate docs are validated.
5. `G5-W1/W2/W3` are considered completed for repository-local closeout deliverables.
6. Patent `10.4-A/B/C` are `closed`: Linux-side `A/B/C` formal summaries, Windows-side `A/B/C` formal summaries, Windows-3 Linux recheck readiness, package sweep, and patent-scope parity are all present and auditable.
7. `global_patent_formal_close` is `closed` for the frozen `patent_10_4_formal_close_min_contract_20260415` scope.
8. `B01_formal_close_external_validation` is closed because:
   - `B01-A external_dense_1gb_formal` is satisfied by Linux and Windows formal `real_external_dense_1gb` inputs with matching SHA-256.
   - `B01-B cross_platform_parity_formal` is satisfied by `/home/zzq/patent/windows-3/formal_10_4_wp05_pass/formal_parity_report.linux_recheck.json`.
   - `B01-C long_duration_soak_formal` is satisfied by the Linux `24h` soak accepted as the current WP-06 closure basis.
9. Current hard blockers: none for patent `10.4` global formal close under the current platform and soak policy.
