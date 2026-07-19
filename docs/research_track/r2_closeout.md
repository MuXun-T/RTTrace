# R2 Closeout

## Decision And Status

R2 final decision: `REFRAME`.

This is not a final claim freeze and does not enter R1B. It returns any
continuation to R1A for a new registry and then a complete new R2 review.

Implementation Authorization: R2 Documentation Only

Prior-Art Search: Authorized and completed for this bounded review

Baseline Implementation: Not Authorized and not performed

Experiment Execution: Not Started

Final Claim Freeze: Forbidden

Automatic Commit: Disabled

## Basis

C1 has close operational and theoretical lines: P2 combines online dependence
tracking, bounded retained history, selective dependency retention, and
on-demand slicing; P3 covers causal backward/forward slicing; P4 is an
embedded-debug slicing lead. The R1A vector-budget/frontier/package details
were not shown to be a material research difference.

C2 has established dependency/package capture and reproducible reopen in P5,
with P6/P7 supporting the package line. The remaining fail-closed
identity/checksum distinction is too narrow and insufficiently searched to
retain unchanged. C3 has no verified material literature delta and is cut.
No B2 passes the same-contract quantitative gate.

The main agent intervened after the first adverse results and selected
supplementary verification plus conclusion narrowing. It directed that partial
distinctions must not support `PROCEED`; a required R1A problem/claim/mechanism
change must produce `REFRAME` and no new R1A draft here. This closeout follows
that direction.

## Counts And Closest Work

Screened ledger: 15 records: `verified=2`, `partially verified=7`,
`unverified=2`, `excluded=4`. Closest C1 work is Nagarajan et al. P2; closest
C2 work is Rampin et al. P5; no C3 candidate had same-contract full-method
verification. Perera et al. P3 and Lee et al. P4 are adverse C1 support.

## Files And Boundary Audit

Allowed additions:

1. `docs/research_track/r2_execution_plan.md`
2. `docs/research_track/r2_verified_prior_art_review.md`
3. `docs/research_track/r2_novelty_matrix.md`
4. `docs/research_track/r2_b2_candidate_registry.md`
5. `docs/research_track/r2_closeout.md`

No code, schema, fixture, test, P6/P7/R0/R1A document, Skill, cache, or
experiment artifact was modified. Full-text inspection downloads were under
`/tmp/r2_prior_art`, outside the repository. No baseline implementation,
experiment, benchmark, hardware collection, R1B work, commit, push, or tag
occurred.

## Current Regression And Final Reviews

The following are current R2 checks, not replacements for Phase 7 historical
evidence or provenance:

```text
PYTHONPATH=. python3 -m pytest tests/python/test_phase7_closeout_models.py tests/python/test_phase7_closeout_runner.py tests/python/test_phase7_closeout_cli.py tests/python/test_phase7_closeout_audit.py -q -ra
```

Result: `22 passed in 0.17s`.

```text
PYTHONPATH=. python3 -m pytest tests/python -q -ra
```

Result: `843 passed, 1195 subtests passed in 172.76s (0:02:52)`.

### Reviewer A: Literature And Novelty Review

Result: approved for the `REFRAME` handoff. The review includes adverse C1
slicing and C2 package/reopen work, distinguishes verified from partial
records, records failed forward-chain and rate-limit limitations, and does not
turn unverified absences into novelty. The nearest-work selection and B2
downgrade support the conclusion that C1/C2 cannot continue unchanged and C3
is cut.

`blocking=0`, `major=0`, `minor=1`: publisher/API access prevented further
full-text verification for several partial records. The decision remains
conservative and does not rely on those records as its sole basis.

### Reviewer B: Boundary And Reproducibility Review

Result: approved for the `REFRAME` handoff. Only the five allowlisted R2
documents were added; no code, test, schema, fixture, P6/P7/R0/R1A, Skill,
cache, or experiment artifact changed. B2 classifications remain assessment
only, no baseline or experiment ran, and R1B was not entered. The current
focused and full regressions pass.

`blocking=0`, `major=0`, `minor=1`: requested subagent model and reasoning
configuration could not be set or runtime-attested by the available interface.

Final review gate: `blocking=0`, `major=0`, `minor=2`. The two minor findings
are distinct and explicitly bounded; neither permits a `PROCEED` decision.

## Required Next Gate

R1B is not permitted from this result. A continuation must write a new R1A
registry with a narrower falsifiable problem, remove C3 unless separately
justified, state the B2 downgrade, and repeat adverse-inclusive R2. `ABANDON`
remains appropriate if no meaningful non-engineering contribution can be
registered.

## Module Audit: Closeout

| item | sources_checked | files_changed | verification_result | boundary_status | open_issue |
| --- | --- | --- | --- | --- | --- |
| R2 decision and handoff | R2 plan, review, matrix, B2 registry, frozen R1A/master gate | `r2_closeout.md` | Only permissible result from evidence: `REFRAME` | Documentation-only; R1B not entered; no implementation/experiment/version-control mutation | New R1A registry and new R2 required before any later gate |
