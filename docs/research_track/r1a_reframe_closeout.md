# R1A-REFRAME Provisional Closeout

## Status

This is a candidate/provisional documentation closeout for the R2 `REFRAME` handoff. It records a new R1A registry only. It is not a new R2, R1B entry, research result, experiment, implementation, or claim freeze.

Implementation Authorization: R1A-REFRAME Documentation Only

Prior-Art Search: Not Started for Reframed Registry

Experiment Execution: Not Started

Final Claim Freeze: Forbidden

Phase 7 Historical Mutation: Forbidden

Automatic Commit: Disabled

## R2 Handoff Record

The prior R2 decision is `REFRAME`. Former C1 generic budgeted dependency selection, on-demand slicing, and limited-history mechanisms are engineering background only. Former C2 package/dependency capture and reopen are not a candidate contribution. Former C3 index break-even is removed from candidate contributions and is report-only engineering context. No quantitative-eligible B2 is available. The reframed registry requires a complete new R2 before any R1B decision.

## Registered Candidate Direction

The candidate/provisional direction is **Fail-Closed Validation of Identity-Bound Evidence Packages for RTOS Trace Analysis** / **面向 RTOS Trace 分析的身份绑定证据包失效关闭验证**.

Its narrow question is whether, within a predeclared package contract and attack model, source/artifact identity, checksums, required-dependency validation, and state constraints can reduce or prevent modeled invalid evidence packages from being silently accepted. This is not a claim about all attacks, semantic correctness, diagnosis correctness, replay correctness, proof correctness, generic RTOS behavior, or a literature result.

## Implementation Item Record

| Item | File | Documentation check record | Boundary status | Open issue |
| --- | --- | --- | --- | --- |
| 1 | `docs/research_track/r1a_reframe_execution_plan.md` | Created; implementation agent ran `git status --short`, `git diff --name-only`, and `git diff --check`; the latter two produced no tracked diff output. | New allowlisted R1A-REFRAME document only; no frozen or code path changed. | None. |
| 2 | `docs/research_track/r1a_reframed_provisional_claim_registry.md` | Created; implementation agent ran the same three Git checks; no tracked diff output from the latter two. Registry inspection verified provisional claims, refuters, indicators, anti-claims, bounded threat model, truth/output boundary, and planning-only taxonomy. | New allowlisted document only; no search, B2, R1B, benchmark, experiment, hardware, or code activity. | None. |
| 3 | `docs/research_track/r1a_reframe_closeout.md` | Created in this item; immediate Git checks are required after creation and are reported to the parent agent outside this document. | New allowlisted document only. | Parent regression and dual review remain pending. |

`git status --short` lists new untracked documentation paths, including pre-existing user-owned master-plan/R2 records. `git diff --name-only` and `git diff --check` do not display these untracked files; their no-output result does not reclassify or modify any pre-existing user-owned path.

## Candidate Boundaries Recorded

The registry contains one provisional primary candidate and two provisional supporting candidates, each with a support condition, refuting/cut result, and candidate measurable indicators. It records that checksum agreement is not semantic correctness; contract acceptance is not diagnosis correctness; fail-closed behavior is not complete security; a mutation suite is not arbitrary-attack safety; deterministic rejection is not proof correctness; RTOS context alone is not a material distinction; internal B1 isolation is not prior-art superiority; and Agent/LLM is absent from truth and proof paths.

The preliminary threat model is limited to missing artifact, partial package, stale sidecar/index/manifest, source/package mismatch, artifact substitution, checksum mismatch, identity mismatch, and missing required dependency. TOCTOU/post-validation change is only a pending new-R2 candidate. Arbitrary malicious kernels, cryptographic breaking, and full semantic correctness are non-targets. The output vocabulary is provisional `contract-accepted` or auditable `non-success` with a reason code and has no asserted mapping to frozen Phase 7 package-open or replay states.

## New R2 Requirement

The registry records, but does not execute, a new R2 taxonomy spanning artifact/package integrity; provenance validation; identity-bound or content-addressed artifacts; fail-closed validation; stale/partial input rejection; research package validation; digital evidence bag/chain of custody; tamper-evident logging; secure trace/log integrity; in-toto, SLSA, and artifact attestation; workflow/reproducibility package consistency; and dependency graph consistency. Every category includes the question whether an existing cross-domain mechanism directly substitutes for the declared contract.

No source collection, formal search, citation conclusion, B2 selection, benchmark, experiment, implementation, or R1B activity occurred in this pass.

## Parent Regression and Review Handoff

The parent agent must independently run repository checks after this documentation pass. The canonical focused command, as recorded in `docs/research_track/r1a_execution_plan.md` and `docs/research_track/r2_closeout.md`, is:

```text
PYTHONPATH=. python3 -m pytest tests/python/test_phase7_closeout_models.py tests/python/test_phase7_closeout_runner.py tests/python/test_phase7_closeout_cli.py tests/python/test_phase7_closeout_audit.py -q -ra
```

The canonical full command from the same sources is:

```text
PYTHONPATH=. python3 -m pytest tests/python -q -ra
```

Those checks are pending parent execution and are repository checks only; they must not alter, replace, or reinterpret Phase 7 historical evidence. Parent review must also verify the three-file allowlist, frozen-history protection, no early R2/R1B/experiment activity, and candidate/reframing boundaries.

## Historical and Version-Control Boundary

No frozen R1A, R2, Phase 6, or Phase 7 file was modified by this implementation pass. No code, schema, fixture, test, artifact, manifest, data, benchmark, or hardware asset was modified. No commit, push, or tag was created.

## Requested Agent Configuration Record

The requested implementation-agent configuration is `model=gpt-5.6-terra` with `reasoning_effort=high`. The collaboration interface exposes no model-setting or runtime-attestation field. This records the request and interface limitation only; it does not attest the runtime model.
