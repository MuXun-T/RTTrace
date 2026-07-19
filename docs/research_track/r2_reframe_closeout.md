# R2-REFRAME Closeout

## Final Decision

R2-REFRAME decision: `ABANDON`.

The provisional identity-bound, fail-closed RTOS evidence-package registry
cannot advance to R1B. This is not a finding that identity validation is
unimportant or that the frozen implementation is incorrect. It is a prior-art
and contribution-boundary decision: the registered PC-1 mechanism is covered
by established generic artifact policy/attestation/package-validation systems,
and the remaining differences are RTOS application context, local contract
configuration, and output naming.

Implementation Authorization: R2 Documentation Only

Prior-Art Search: Authorized and completed for this bounded registry

Baseline Implementation: Not Authorized and not performed

Experiment Execution: Not Started

Final Claim Freeze: Forbidden

Automatic Commit: Disabled

## Basis And Claims

The nearest adverse work is Torres-Arias et al.'s verified in-toto paper and
specification: hash-bound materials/products, required/forbidden artifact
rules, matching across steps, thresholds, and erroring verification directly
express the generic PC-1 structure. SLSA provenance independently binds
artifact subject digests and dependencies to a policy; BagIt defines manifest
and complete/valid package checks. These sources were inspected at their
official full-paper/RFC/specification level.

PC-1 is cut because no material RTOS-specific problem/representation/mechanism
delta is registered. SC-1 is cut because deterministic reason-coded
non-success is an acceptance-output detail, not a demonstrated new validator.
SC-2 is cut because combining identity/checksum/manifest/dependency checks and
comparing them to weak B1 variants is engineering mechanism isolation, not a
literature contribution. Contract acceptance still says nothing about
diagnosis, semantic, replay, downstream-consumer, proof, or universal-security
correctness.

`REFRAME` is not selected: it would require a concrete newly registered
falsifiable mechanism or constraint with a plausible material delta. This
review found none, and inventing one would exceed the R2 scope. A later,
genuinely different proposal would start at a new R1A registry and complete a
new adverse-inclusive R2; it is not authorized by this closeout.

## Search Record And B2

Taxonomy covered artifact/package integrity, content-addressing/identity,
provenance, fail-closed behavior, stale/partial/mismatch input, manifests and
dependencies, reproducibility packages/workflows, forensic custody, secure or
tamper-evident logging, attestation/in-toto/SLSA, replay-input validation, and
RTOS trace-specific terms. Sources included official USENIX/JOSS/RFC/W3C/SLSA
documents, project repositories, and discovery/metadata services
(ACM/IEEE/Springer/Elsevier venue searches, DBLP, Crossref, OpenAlex, Semantic
Scholar, Google Scholar, arXiv). Citation chaining and its access limitations
are recorded in the verified review.

Screening ledger: `verified=5`, `partially verified=2`, `unverified=0`,
`excluded=6`. The core decision relies only on verified V1 in-toto, V2 BagIt,
and V3 SLSA. The other verified/partial sources broaden scope and prevent
confirmation bias; none is used to turn a missing source into novelty.

B2 registry: `quantitative eligible=0`, `capability-only=1`, `not
comparable=3`. No source was downloaded, built, run, adapted, benchmarked, or
compared. Quantitative B2 is not permitted, and no capability statement is a
ranking or superiority result.

## File And Boundary Audit

This execution added only the following allowlisted R2-Reframe documents:

1. `docs/research_track/r2_reframe_execution_plan.md`
2. `docs/research_track/r2_reframe_verified_prior_art_review.md`
3. `docs/research_track/r2_reframe_novelty_matrix.md`
4. `docs/research_track/r2_reframe_b2_candidate_registry.md`
5. `docs/research_track/r2_reframe_closeout.md`

After each module, `git status --short`, `git diff --name-only`, and `git diff
--check` were run. The checks showed only these untracked allowlisted
documents, and no whitespace errors. No frozen document, code, schema,
fixture, test, P6/P7/R0/R1A/first-R2 document, Skill, cache, or experiment
artifact was modified. No commit, push, or tag was performed.

The requested model configuration was `gpt-5.6-terra` with high reasoning
effort. The collaboration interface did not expose a model-setting or
runtime-attestation field. This limitation is recorded, not represented as
verification of actual runtime configuration.

## Required Parent Checks And Review Gates

The parent must independently perform final source/diff review and run the
canonical regressions before declaring the documentation pass complete:

```text
PYTHONPATH=. python3 -m pytest tests/python/test_phase7_closeout_models.py tests/python/test_phase7_closeout_runner.py tests/python/test_phase7_closeout_cli.py tests/python/test_phase7_closeout_audit.py -q -ra
PYTHONPATH=. python3 -m pytest tests/python -q -ra
```

These commands are repository-integrity regressions only; they cannot validate
prior-art novelty or make a B2 comparable. Reviewer A must recheck cross-domain
coverage, primary-source method claims, confirmation bias, and `ABANDON`.
Reviewer B must recheck citations, B2 classifications, allowlisted files,
regression results, and that R1B was not entered. Completion requires
`blocking=0` and `major=0` from those parent reviews.

## Parent Final Verification And Independent Reviews

The parent independently rechecked the five new documents, the cited primary
source descriptions, the frozen registry boundary, and the Git path boundary.
The canonical focused command completed as `22 passed in 0.17s`:

```text
PYTHONPATH=. python3 -m pytest tests/python/test_phase7_closeout_models.py tests/python/test_phase7_closeout_runner.py tests/python/test_phase7_closeout_cli.py tests/python/test_phase7_closeout_audit.py -q -ra
```

The canonical full command completed as `843 passed, 1195 subtests passed in
180.47s (0:03:00)`:

```text
PYTHONPATH=. python3 -m pytest tests/python -q -ra
```

These regressions are repository-integrity checks only. They do not validate
the abandoned candidate, a B2, or a research claim.

### Reviewer A: Literature And Novelty Review

Result: approved for `ABANDON`. The review retained supply-chain attestation,
digital-package, provenance, reproducibility, and secure-log/custody lines;
it relies on the official in-toto paper/specification, RFC 8493, and SLSA
v1.0 specification for the direct-coverage conclusion. It does not elevate
partial secure-log or TUF leads into method evidence, and it does not infer
novelty from inaccessible or failed searches.

`blocking=0`, `major=0`, `minor=1`: the interface could not set or attest the
requested subagent model and reasoning configuration. This is recorded as a
limitation and does not support any literature conclusion.

### Reviewer B: Boundary And Reproducibility Review

Result: approved for `ABANDON`. Only the five R2-Reframe allowlisted documents
are present as new paths; the reframed R1A registry, R1A reframe closeout, and
first-R2 records remain unchanged. The B2 registry uses only the permitted
three classifications, no candidate was executed, and no R1B activity
occurred. Focused and full repository-integrity regressions passed.

`blocking=0`, `major=0`, `minor=0`.

Final review gate: `blocking=0`, `major=0`, `minor=1`. The remaining minor
limitation neither changes `ABANDON` nor permits R1B.

## Module Audit: Closeout

| item | sources_checked | files_changed | verification_result | boundary_status | open_issue |
| --- | --- | --- | --- | --- | --- |
| R2-REFRAME decision and handoff | Execution plan, verified review, five-layer matrix, B2 registry, frozen provisional registry and master decision rules | `r2_reframe_closeout.md` | Only defensible decision is `ABANDON`; PC-1, SC-1 and SC-2 are cut | Documentation-only; no implementation, experiment, B2 execution, R1B or VCS mutation | Parent regression/double review remain required; no R1B application is allowed from this candidate |
