# Phase 6 Scope Note

Date: 2026-07-09

## Phase 6 Goal

Phase 6 is scoped to RTOS Diagnosis and Human Feedback without weakening the existing Phase 5 proof, advisor, and evidence boundaries. The long-range objectives are a known-root-cause RTOS regression benchmark, evidence package replay, proof drift checks, diagnosis metrics, advisor-assisted review, and measured human feedback. The current execution window is limited to P6.0 and P6.1, so it does not implement real case generation, evidence package replay, proof drift evaluation, diagnosis metrics, advisor variants, or human feedback collection.

## Current Baseline

- Branch at start: `main`
- Current baseline HEAD at start: `7f57ec5 docs: add phase6 pre-approval development plan`
- Worktree at start: `git status --short` was clean
- Phase 5 frozen anchor: `c3f51f5 phase5 harden verifier-gated advisor sandbox`
- Pre-approval planning commit: `7f57ec5 docs: add phase6 pre-approval development plan`
- Phase 5 remains frozen for proof digest, proof hash, collector, sandbox, and advisor truth-path boundaries.
- No Phase 6-specific diagnosis benchmark runner exists yet.
- No Phase 6-specific human feedback schema or human feedback code exists yet.
- No real Phase 6 evidence package, replay equivalence result, diagnosis benchmark result, or human helpfulness result exists yet.

## Scope Lock

Allowed now:

- P6.0: documentation-only baseline audit and scope lock.
- P6.1: RTOS diagnosis case and suite schemas, mirrored schema assets, a metadata-only stub suite, and schema tests.

P6.2 and later require separate main-agent review and approval before implementation.

## Forbidden Work In This Window

- Do not generate synthetic RTOS traces.
- Do not generate real evidence packages.
- Do not implement replay equivalence.
- Do not implement a diagnosis benchmark runner.
- Do not compare advisor variants.
- Do not implement an LLM explanation-only harness.
- Do not implement human feedback schema or collection.
- Do not modify proof hash or proof digest semantics.
- Do not modify `parser/evidence_models.py`.
- Do not modify `desktop/evidence_export.py` proof logic.
- Do not modify `collector/` or `tests/cpp/`.
- Do not modify existing benchmark, proof digest, or result validity schemas.
- Do not introduce new dependencies.
- Do not let LLM or advisor output become trace truth, root-cause truth, parse facts, align facts, rebuild facts, sidecar facts, index facts, proof facts, or proof hash input.

## Phase Scope

| Phase | Scope | Current Status |
|---|---|---|
| P6.0 | Baseline audit, claim boundary, implementation checklist, risk register. | In this execution window |
| P6.1 | RTOS diagnosis case schema, suite schema, mirrored assets, metadata-only stub suite, schema tests. | In this execution window |
| P6.2 | Synthetic RTOS regression case fixtures. | Future independent task, not approved here |
| P6.3 | Evidence package and proof replay validation. | Future independent task, not approved here |
| P6.4 | Diagnosis metrics and report output. | Future independent task, not approved here |
| P6.5 | Advisor-assisted review variants. | Future independent task, not approved here |
| P6.6 | Human feedback schema and minimal review fixture. | Future independent task, not approved here |
| P6.7 | Final Phase 6 regression and closeout. | Future independent task, not approved here |

## Claim Boundary

### claimable

- Existing archived Phase 5 proof isolation remains claimable only under its already frozen evidence boundary.
- Existing archived focused regressions remain claimable only as historical focused regression evidence, not as a fresh full-regression result.
- P6.0 documentation and P6.1 schema validity may be claimed after their targeted regressions pass.

### report_only

- Top-k root cause is report-only at the beginning of Phase 6. It must not be written as a hard accuracy claim until a reviewed benchmark and metrics definition exist.
- Phase 6 stub case coverage is report-only metadata coverage, not evidence that diagnosis works.
- Synthetic benchmark results, once P6.2 is separately approved and implemented, may only be reported with their synthetic boundary unless real RTOS or hardware traces are added and reviewed.
- Advisor explanations and LLM explanations may be reported as review artifacts, not truth artifacts.

### not_claimable

- Human helpfulness is not claimable until real human feedback data exists and is reviewed.
- Synthetic benchmark metadata cannot be written as a real RTOS generality claim.
- LLM explanation is not root-cause truth and cannot substitute for known-root-cause labels or deterministic evidence.
- P6.2+ approval is not claimable from this window. Each later phase needs separate main-agent review and approval.
- Phase 6 does not currently support a claim that RTTrace has an evaluated RTOS diagnosis benchmark, diagnosis SOTA, or established top-k root cause performance.
