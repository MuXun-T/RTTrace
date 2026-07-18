# R1A Provisional Registration Execution Plan

## Status and Scope

This is an R1A planning record. Every claim-like statement in this document is
a provisional candidate for R2 review, not a research result.

Implementation Authorization: R1A Documentation Only

Prior-Art Search: Not Started

Experiment Execution: Not Started

Final Claim Freeze: Forbidden

Phase 7 Historical Mutation: Forbidden

Automatic Commit: Disabled

## Objective, Inputs, and Completion Gate

| Item | R1A plan |
| --- | --- |
| Objective | Register falsifiable search inputs for bounded and auditable evidence closure in reproducible RTOS trace analysis. |
| Inputs | This master plan; R0 errata and closeout; read-only Phase 6/7 boundaries, closeouts, and frozen manifests. |
| Non-goals | Paper conclusion, formal prior-art review, benchmark or data work, experiment execution, implementation work, hardware work, and R1B entry. |
| Completion gate | A provisional registry names a problem, C1/C2/C3 candidates, an anti-claim, explicit refuters, experimental-unit requirements, an R2 taxonomy, and cut criteria. |
| Stop gate | Stop and return to the parent reviewer if a candidate cannot be falsified, requires an unrecorded engineering fact, requires paper searching, or requires any protected-file or code change. |

## Candidate Extraction Method

1. Read the frozen boundary as an engineering-asset inventory, not as a result.
2. Extract only mechanisms already named in the planning baseline: frozen
   seeds/rules/order, budget-before-read, frontier, sidecar/index,
   identity/checksum/dependency validation, and canonical non-success states.
3. Pair each mechanism with one bounded question, required evidence, and a
   result that would remove or narrow the candidate.
4. Record the applicable experimental unit without equating processing repeats
   with capture runs or independent diagnosis cases: P is processing run, C is
   capture run, and D is a configuration-distinct diagnosis case.
5. Assign B0 and B1 only as protocol categories. Keep B2 as `TBD/R2 only`;
   no paper, tool, or implementation is selected in R1A.
6. Move any statement without a named refuter to the candidate cut list or
   retain it as engineering context only.

## Unified Candidate Record

Each registry row must include `candidate_id`, `status=provisional`, problem,
method, insight, support condition, refutation condition, required
experimental unit, current engineering evidence, missing evidence, R2
prior-art categories, forbidden interpretation, B0/B1/B2 boundary, and cut
trigger. A candidate is retained for R2 only while all fields remain explicit.

## Preliminary Output Contract

For declared immutable inputs and a declared vector budget
`B=(bytes, events, depth, window, artifacts, host-IO)`, a candidate closure
output is one of `exact`, `bounded`, or `degraded`. It records the frozen rule
family, total order/tie break, required-dependency priority,
budget-before-read policy, selected artifacts, and missing or truncated
frontier. Required identity, checksum, dependency, or closure mismatch is a
non-success with a canonical reason code. This is a preliminary contract to
test and review, not a semantic or diagnosis result.

The preliminary truth boundary is only the declared bytes, events, depth,
window, artifacts, host-I/O behavior, immutable identity/checksum declarations,
and dependency graph represented by the package. It excludes unrecorded raw
state, semantic truth, labels created by the analyzer, and any scope outside
the declared contract.

## R2 Search Taxonomy

| Category | Synonyms and mechanism terms | Exclusions / checks |
| --- | --- | --- |
| RTOS and embedded trace analysis | RTOS trace, embedded trace, trace diagnosis, event trace, scheduling trace | Exclude generic observability papers without a comparable trace/evidence mechanism. |
| Dynamic, backward, and causal slicing | dynamic slicing, backward slicing, causal slicing, dependency slice, event dependency | Check seed semantics, traversal/order, and preservation boundary. |
| Selective capture, export, and query | selective tracing, trace reduction, selective export, trace query, budgeted retrieval | Check whether selection happens before read and what evidence can be omitted. |
| Evidence/package dependency closure | evidence closure, provenance package, dependency closure, artifact bundle | Check required/optional dependencies and stated completeness behavior. |
| Reproducible debugging and replay | deterministic replay, reproducible debugging, replay package, trace replay | Check identity binding, repeatability, and output/status scope. |
| Indexing and selective I/O | sidecar index, trace index, indexed access, stream scan, selective I/O | Check build/storage/cold/warm/reuse accounting and recall behavior. |
| Integrity and canonical rejection | checksum validation, identity validation, fail closed, invalid package, reason code | Check mutations, acceptance semantics, and controlled comparators. |

Each category must use exact-term, synonym, mechanism, problem, and citation
chain queries in R2. R2 must verify sources and may delete or narrow a
candidate. It must not treat this taxonomy as a search result.

## Baseline Planning Boundary

| Baseline class | R1A use | Boundary |
| --- | --- | --- |
| B0 | Full raw scan and predeclared fixed clipping as behavioral controls. | No outcome is asserted. |
| B1 | Internal mechanism variants for isolation. | Internal comparisons cannot establish a literature delta. |
| B2 | `TBD/R2 only`. | No paper is preselected. Future quantitative eligibility requires the master plan's same-contract fields exactly: same raw SHA-256; case/workload; preprocessing; truth boundary; output contract; environment identity; metrics; split; failure policy. Source/version/license and mechanism/parameter mapping are later reproduction records and do not replace those gates. |

## Candidate Decisions

| Decision | Provisional condition |
| --- | --- |
| `PROCEED` | R2 verifies a material, bounded distinction and each retained candidate has a feasible support/refutation path. This is an R2 output, not an R1A decision. |
| `REFRAME` | R2 finds overlap, an overwide boundary, or a non-comparable problem statement that can be narrowed into a new falsifiable R1A candidate. |
| `ABANDON` | R2 finds no material distinction, or required evidence/refuters cannot be responsibly specified. |

## Engineering Narrative Downgrade

The registry must delete or downgrade narratives that turn a frozen contract
into semantic success, diagnosis accuracy, broad RTOS behavior, a hardware
finding, a literature comparison, or an Agent/LLM contribution. Sidecar/index
is a conditional mechanism, not a standalone research direction. Advisor and
LLM material remains appendix-only, outside the truth, label, proof, selection,
and output-contract paths.

## File-Level Steps, Validation, Stops, and Rollback

| Step | Output | Immediate validation | Stop condition | Rollback boundary |
| --- | --- | --- | --- | --- |
| 1 | `r1a_execution_plan.md` | Git allowlist, Markdown table/header inspection, phase and candidate wording checks. | Any requirement needs code/test/history edit or a formal search. | Remove only this new R1A file before later items exist. |
| 2 | `r1a_provisional_claim_registry.md` | Verify every candidate has support, refutation, unit, R2 categories, exclusions, and cut trigger. | A candidate is unfalsifiable or Final/Candidate separation fails. | Remove only the registry and retain the plan for parent review. |
| 3 | `r1a_closeout.md` | Verify fixed status, per-item report, pending parent regression placeholders, and no completion/freeze assertion. | A closeout would state R2/R1B/search/experiment/development activity. | Remove only the closeout; parent determines next action. |

After each step, run `git status --short`, `git diff --check`, and `git diff
--name-only`; inspect internal references and Markdown tables; check phase
names, Candidate versus Final wording, R1A/R2/R1B boundaries, protected
Phase 6/7 paths, and the three-file allowlist. A failed check or an
unallowlisted file stops the item for parent review.

The parent, not this plan, performs future final regressions. The canonical
focused command is:

```text
PYTHONPATH=. python3 -m pytest tests/python/test_phase7_closeout_models.py tests/python/test_phase7_closeout_runner.py tests/python/test_phase7_closeout_cli.py tests/python/test_phase7_closeout_audit.py -q -ra
```

The canonical full command is:

```text
PYTHONPATH=. python3 -m pytest tests/python -q -ra
```

Any new execution result must be recorded as a current R1A check and must not
replace the historical Phase 7 counts or provenance.

## Risk Register

| Risk | Control and stop action |
| --- | --- |
| Candidate is written as a conclusion | Require `provisional` in each record; stop on finality wording. |
| Claim is frozen before R2 | Keep R2 decision and all B2 selection deferred; stop on freeze language. |
| Internal ablation is presented as novelty | State B1 as internal isolation only; stop on literature-superiority wording. |
| Agent/LLM scope expands | Keep it appendix-only and outside truth/output paths; stop on a primary role. |
| Truth boundary becomes too wide | Enumerate vector budget and exclusions; narrow or cut when an input is not declared. |
| Claim cannot be falsified | Require named support and refutation conditions; cut or request parent review. |
| Conflict with Phase 7 history | Treat Phase 6/7 as read-only; stop rather than reinterpret a historical conclusion. |
| Frozen record is rewritten for paper fit | Add new R1A documents only; stop on any historical-file change. |

## Forbidden Claims

The following phrases are prohibited as assertions in R1A records: `novelty
established`, `final contribution`, `final claim frozen`, `first method`,
`SOTA`, `diagnosis correctness proven`, `proof correctness`, and `general
RTOS validation`. They appear here only as prohibited wording. Any candidate
must instead state its provisional scope, missing evidence, and refuter.
