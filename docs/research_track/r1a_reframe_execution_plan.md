# R1A-REFRAME Provisional Registration Execution Plan

## Status and Scope

This is a candidate/provisional R1A-REFRAME planning record. It is a
documentation-only handoff from the completed R2 decision `REFRAME`, not a
prior-art result, implementation record, experiment, or R1B entry.

Implementation Authorization: R1A-REFRAME Documentation Only

Prior-Art Search: Not Started for Reframed Registry

Experiment Execution: Not Started

Final Claim Freeze: Forbidden

Phase 7 Historical Mutation: Forbidden

Automatic Commit: Disabled

## R2 Handoff and Candidate Direction

R2 requires `REFRAME`: generic budgeted dependency selection, on-demand
slicing, and bounded-history mechanisms in former C1 have strong adjacent
work. Former C2 package/dependency capture and reopen are occupied. Former C3
index break-even is removed from candidate research contributions and is only
an engineering/report-only observation. R2 recorded no quantitative-eligible
B2. A complete new R2 is required before any R1B decision.

Candidate title: **Fail-Closed Validation of Identity-Bound Evidence Packages
for RTOS Trace Analysis**

中文候选标题：**面向 RTOS Trace 分析的身份绑定证据包失效关闭验证**

| PMI element | Candidate/provisional registration |
| --- | --- |
| Problem | A multi-artifact RTOS trace evidence input can be partial, stale, corrupt, or mismatched yet be silently accepted. |
| Method | Under a predeclared package contract and attack model, bind and validate source/artifact identity, checksums, required dependencies, and validation-state constraints. |
| Insight | The candidate question is whether this combination reduces or prevents silent acceptance for modeled invalid packages, rather than whether package capture, reopen, slicing, or indexing is generally useful. |

## Candidate Claim Schema

Every candidate/provisional claim states its contract, modeled attack class,
support condition, refuting/cut result, and candidate measurable indicators. A
later R2 may delete, narrow, or reject every record.

| Candidate | Role | Support condition | Refuting/cut result | Candidate measurable indicators |
| --- | --- | --- | --- | --- |
| PC-1 | primary | Each declared modeled invalid package yields a non-success rather than silent contract acceptance; valid controls remain separately recorded. | A modeled invalid package is silently contract-accepted, a required field is undeclared, or new R2 finds direct substituting prior work that removes the material distinction. | modeled-invalid silent-accept count/rate, valid-control non-success count/rate, attack-class coverage, reason-code determinism |
| SC-1 | supporting | Each modeled violation maps to a deterministic, auditable non-success status and reason code. | Identical declared inputs produce incompatible status/reason output, or a modeled violation has no auditable non-success. | status/reason repeat agreement, unmapped violation count, reason-code coverage |
| SC-2 | supporting | The combined contract detects more modeled cross-artifact mismatch classes than existence-only, checksum-only, or manifest-only weak validations. | A weak validation detects the same modeled class set, the combined contract misses a declared class, or comparison exceeds B1 mechanism isolation. | modeled mismatch-class coverage, per-variant detection table, valid-control non-success rate |

Weak validation variants are candidate B1 mechanism isolation only. They are
not a B2, literature comparison, or superiority evidence. No
quantitative-eligible B2 is currently registered.

## Preliminary Contract, Truth Boundary, and Output Vocabulary

The preliminary package contract must predeclare the accepted artifact set,
immutable bytes or content references, artifact identifiers, source-to-artifact
identity projections, checksums, required-dependency declarations, validation
state/reason taxonomy, and modeled attack classes. Identity must not be
inferred from paths or names. A digest is an identity projection only when the
contract says so; a digest/checksum is not interchangeable with raw bytes,
semantic meaning, or provenance by default.

The candidate/provisional truth boundary covers only declared contract fields
and modeled invalid-package classes. It excludes undeclared inputs, semantic
correctness, diagnosis correctness, proof correctness, arbitrary attacker
behavior, general RTOS behavior, and outcomes outside the contract. Agent/LLM
material is excluded from truth, proof, acceptance, validation-state, and
reason-code paths.

The preliminary/provisional output vocabulary may contain
`contract-accepted` and deterministic auditable `non-success` statuses with
reason codes. It does not map to, replace, claim implementation of, or infer
the frozen Phase 7 package-open or replay states. Contract acceptance means
only declared validations succeeded; it does not establish diagnosis, semantic,
or replay correctness.

## Preliminary Threat Model

The candidate attack model is limited to these declared input conditions:

| Modeled condition | Candidate validation question |
| --- | --- |
| missing artifact | Is a required declared artifact absent? |
| partial package | Is the required artifact/dependency set incomplete? |
| stale sidecar, index, or manifest | Does it bind to the current declared identity projection? |
| source/package mismatch | Does the package bind to the declared source identity projection? |
| artifact substitution | Does substitution violate a required identity or checksum field? |
| checksum mismatch | Does a declared checksum disagree with the declared bytes/content reference? |
| identity mismatch | Do declared source/artifact identity projections violate the contract relation? |
| missing required dependency | Is a declared required dependency absent or invalid? |
| TOCTOU or post-validation change | Candidate for the new R2 only; no mechanism or coverage is asserted here. |

Non-targets are arbitrary malicious-kernel behavior, cryptographic breaking,
and full semantic correctness. The candidate does not claim complete security,
complete detection, or protection outside the declared attack model.

## New R2 Taxonomy: Planning Only

No search, source collection, citation conclusion, or prior-art decision is
performed here. The next R2 must inspect each category with exact, synonym,
mechanism, problem, and citation-chain queries, including the direct
substitution question.

| Category | Required cross-domain direct-substitution question |
| --- | --- |
| artifact/package integrity | Can an integrity mechanism directly satisfy the declared RTOS evidence-package contract? |
| provenance validation | Can a provenance validator directly bind required identity projections and dependencies? |
| identity-bound/content-addressed artifacts | Can content-addressed artifacts directly substitute for the identity relation? |
| fail-closed validation | Can an existing validator directly provide the modeled rejection semantics? |
| stale/partial input rejection | Can an existing mechanism directly cover declared mismatch classes? |
| research package validation | Can package tooling directly validate the contract without a material new mechanism? |
| digital evidence bag/chain of custody | Can digital-forensics custody mechanisms directly substitute for the contract and vocabulary? |
| tamper-evident logging | Can tamper-evident logging directly handle cross-artifact acceptance rather than only record changes? |
| secure trace/log integrity | Can secure trace/log methods directly cover the RTOS multi-artifact threat model? |
| in-toto, SLSA, artifact attestation | Can attestation frameworks directly enforce declared dependency and state constraints? |
| workflow/reproducibility package consistency | Can workflow consistency mechanisms directly reject stated stale or mismatched evidence inputs? |
| dependency graph consistency | Can graph-consistency mechanisms directly validate the required-dependency relation? |

The next R2 must include adverse cross-domain mechanisms and decide whether
any is directly substitutable. This taxonomy is not evidence that no such work
exists.

## File-Level Implementation and Controls

Only the following new files are authorized:

1. `docs/research_track/r1a_reframe_execution_plan.md`
2. `docs/research_track/r1a_reframed_provisional_claim_registry.md`
3. `docs/research_track/r1a_reframe_closeout.md`

| Step | Output | Immediate validation | Stop condition | Rollback boundary |
| --- | --- | --- | --- | --- |
| 1 | This plan | `git status --short`; `git diff --name-only`; `git diff --check`; scope/wording inspection | Unapproved path, search required, or candidate remains former C1 | Remove only this new plan before later outputs exist |
| 2 | Provisional registry | Same Git checks; inspect status, refuters, anti-claims, threat model, truth boundary, and taxonomy | Unfalsifiable claim, acceptance-as-diagnosis wording, frozen/code change requirement, or threat-model expansion | Remove only registry; retain plan for parent review |
| 3 | Closeout | Same Git checks; inspect item reports, fixed statuses, history boundary, and pending parent-regression fields | Records new R2/R1B/experiment as performed, asserts a frozen research position, or reports a test not run | Remove only closeout; retain earlier documents |

After each item, report `item`, `files_changed`, `validation`,
`boundary_status`, and `open_issue` to the parent. A test failure, unapproved
file, frozen-file/code modification requirement, formal-search requirement,
material former-C1 similarity, unfalsifiable claim, unbounded threat model, or
acceptance-as-diagnosis statement requires an immediate pause.

## Regression Source and Boundary

The parent alone runs repository regressions after this documentation pass. The
canonical focused command is recorded in `docs/research_track/r1a_execution_plan.md`
and `docs/research_track/r2_closeout.md`:

```text
PYTHONPATH=. python3 -m pytest tests/python/test_phase7_closeout_models.py tests/python/test_phase7_closeout_runner.py tests/python/test_phase7_closeout_cli.py tests/python/test_phase7_closeout_audit.py -q -ra
```

The canonical full command is recorded in the same sources:

```text
PYTHONPATH=. python3 -m pytest tests/python -q -ra
```

Any current execution checks repository integrity only. It does not alter,
replace, or reinterpret frozen Phase 7 historical evidence, manifests, or
recorded results.

## Risk Register

| Risk | Preventive control and stop action |
| --- | --- |
| Checksum/manifest stacking is presented as a research mechanism | Require a falsifiable contract question and new R2 cross-domain review; cut if directly substitutable. |
| RTOS setting is presented as a new mechanism | State RTOS only as the candidate input context; stop on context-only differentiation. |
| Cross-domain adjacent work is omitted | Require the taxonomy, including attestation, provenance, forensics, secure logging, and reproducibility systems. |
| Fail-closed is written as complete security | Limit language to modeled invalid packages and contract conditions; stop on universal security language. |
| Attack model becomes unbounded | Keep only listed classes/non-targets; stop before adding a class. |
| Mutation-suite performance is generalized | Treat any future mutation suite as modeled-class evidence only. |
| Contract acceptance is written as diagnosis correctness | Maintain output/truth boundaries; stop on acceptance-to-diagnosis inference. |
| Former closure narrative returns | Former C1 is engineering background only; stop on budgeted selection, slicing, or limited history as a claim. |
| Package/reopen becomes a contribution | Former C2 capture/reopen is background only; stop on its use as a contribution. |
| C3 returns as a contribution | Index break-even is report-only; stop on an index claim, metric, or comparator as a contribution. |
| R1B, experiment, or implementation starts early | Documentation allowlist; stop on code, test, data, B2, benchmark, hardware, or R1B activity. |
| Agent/LLM enters a proof path | Exclude Agent/LLM from truth, proof, acceptance, validation-state, and reason-code paths. |

## Requested Agent Configuration Record

The requested implementation-agent configuration is `model=gpt-5.6-terra`
with `reasoning_effort=high`. The collaboration interface exposes no
model-setting or runtime-attestation field. This records the request and
interface limitation only; it does not attest the runtime model.
