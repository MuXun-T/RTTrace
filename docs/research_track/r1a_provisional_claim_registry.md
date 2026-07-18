# R1A Provisional Claim Registry

## Status

All entries are provisional R1A search inputs. They are not research results,
literature findings, or a frozen paper position.

Implementation Authorization: R1A Documentation Only

Prior-Art Search: Not Started

Experiment Execution: Not Started

Final Claim Freeze: Forbidden

Phase 7 Historical Mutation: Forbidden

Automatic Commit: Disabled

## Candidate Problem

**Candidate problem.** Under a declared contract for reproducible RTOS trace
analysis, can bounded and auditable evidence closure retain predeclared
required evidence and a predeclared result while lowering a declared net-cost
ledger relative to behavioral controls, and reject incomplete or mismatched
packages with explicit non-success states?

The problem is bounded to declared package bytes, event/dependency records,
identity/checksum declarations, vector budget, and output status. It does not
presume preservation, lower cost, rejection behavior, a material literature
difference, or generality outside the contract.

## Preliminary Truth Boundary and Output Contract

The preliminary boundary is
`B=(bytes, events, depth, window, artifacts, host-IO)`. A candidate evaluation
must record immutable seed inputs, rule family, total order and tie break,
required-dependency priority, budget-before-read policy, selected artifacts,
and the dependency frontier. It covers only the raw/package information and
host-I/O behavior named by that record.

The preliminary output is `exact`, `bounded`, or `degraded`, plus selected
artifacts, missing or truncated frontier, identity/checksum/dependency/closure
status, and canonical reason codes. A required missing item or required
identity/checksum/dependency mismatch is non-success. `exact` is a contract
status, not an assertion about semantic meaning.

Excluded from this boundary are soundness, completeness, minimality, semantic
or proof claims, diagnosis correctness, generic RTOS behavior, hardware
effects, literature superiority, and factual improvement from an Agent or LLM.
Advisor/LLM material remains appendix-only and cannot supply truth, labels,
selection, validation status, or output-contract fields.

## Candidate Records

### C1

| Field | Provisional registration |
| --- | --- |
| `candidate_id` / status | `C1` / `provisional primary candidate` |
| Problem | Full scan/export may have a larger net-cost ledger, while fixed clipping may omit remote required dependencies under a declared boundary. |
| Method | Freeze seed, rules, order/tie break, required-dependency priority, and vector budget before reads; perform budget-before-read frontier selection with local sidecar support. |
| Insight | Local selection may preserve a predeclared required evidence/result set only under its declared contract and only with an explicit missing/truncated frontier. |
| Candidate support condition | Independently frozen labels and predeclared required evidence/results are retained under the same contract, with repeatable declared net cost relative to B0/B1. |
| Candidate refutation condition | Any required evidence/result loss, no repeatable net-cost benefit, or R2 finding of no material delta removes or narrows C1. |
| Required experimental unit | D/C units establish preservation against independent labels; P units measure closure/read/serialize/reopen/validate cost. Processing repeats cannot substitute for D cases. |
| Current engineering evidence | Frozen planning and Phase 7 materials describe declared seeds/rules/order, budget-before-read, frontier, sidecar/workset, and deterministic contract outputs. This is engineering context only. |
| Missing evidence | Independent labels, predeclared net-cost ledger, D/C coverage, holdout governance, repeatability evidence, and R2-verified literature delta. |
| R2 categories | RTOS trace analysis; dynamic/backward/causal slicing; selective capture/export/query; dependency closure; reproducible debugging/replay. |
| Forbidden interpretation | No assertion that a selected package preserves any undeclared evidence, has lower cost in general, provides diagnosis accuracy, or exceeds prior work. |
| B0/B1/B2 boundary | B0 is full raw scan and predeclared fixed clipping; B1 is internal variants; B2 is `TBD/R2 only`. |
| Cut trigger | Delete or narrow C1 on any required loss, no repeatable net benefit, or absent material R2 distinction. |

### C2

| Field | Provisional registration |
| --- | --- |
| `candidate_id` / status | `C2` / `provisional support candidate` |
| Problem | A package may appear usable despite an identity, checksum, dependency, or closure mismatch unless the contract produces a canonical non-success. |
| Method | Validate identity/checksum/dependency declarations and require canonical reason-coded non-success for required mismatch or absence. |
| Insight | The value under review is auditable rejection behavior for modeled invalid packages, not semantic validity of a package. |
| Candidate support condition | A mutation suite, reason-code ledger, valid controls, valid/invalid denominators, and a controlled fail-open comparator show modeled invalid inputs are not accepted as complete. |
| Candidate refutation condition | Any silent invalid acceptance or completion status for a required invalid condition removes C2. |
| Required experimental unit | D/C mutation conditions are clustered by case/capture; mutation variants do not create new independent diagnosis cases. |
| Current engineering evidence | Frozen records describe identity/checksum/dependency validation, layered statuses, and canonical non-success reason codes. This is engineering context only. |
| Missing evidence | Registered mutation corpus, variant-generation hashes, valid controls, reason-code consistency observation, denominator accounting, and a controlled fail-open comparator. |
| R2 categories | Integrity/identity/fail-closed validation; evidence/package dependency closure; reproducible debugging/replay; RTOS trace package handling. |
| Forbidden interpretation | No assertion of proof, diagnosis, general security, or unmodeled-input behavior. |
| B0/B1/B2 boundary | B1 may be a controlled validation variant; B0 and B2 do not replace the required valid/invalid control design. B2 remains `TBD/R2 only`. |
| Cut trigger | Delete or narrow C2 on silent invalid acceptance/completion, absent controls, or an R2 result that removes the material bounded distinction. |

### C3

| Field | Provisional registration |
| --- | --- |
| `candidate_id` / status | `C3` / `provisional conditional support candidate` |
| Problem | An index can shift rather than reduce cost once build, storage, cold/warm state, and reuse are counted. |
| Method | Compare validated indexed access with stream-sidecar access under a predeclared total-cost and reuse-amortization ledger. |
| Insight | Index benefit, if any, is scale- and reuse-dependent rather than assumed from query latency alone. |
| Candidate support condition | At a declared trace/query/reuse condition, total build/storage/cold/warm/reuse cost has a repeatable break-even without recall loss or RSS dominance. |
| Candidate refutation condition | No break-even, recall loss, RSS dominance, or an R2 result with no material delta removes C3. |
| Required experimental unit | Trace x query workload x P processing run; trace/case grouping remains visible in any later analysis. |
| Current engineering evidence | Frozen planning distinguishes sidecar/index access and records a scale-dependent cost question. This is engineering context only. |
| Missing evidence | Predeclared workload/reuse model, build and storage measurements, cold/warm measurements, recall checks, RSS observations, and R2 literature delta. |
| R2 categories | Indexing/selective I/O; selective capture/export/query; trace query; reproducible trace analysis. |
| Forbidden interpretation | No assertion that indexing is generally faster, more economical, more complete, or a necessary paper component. |
| B0/B1/B2 boundary | Stream-sidecar is the primary comparator; B1 may isolate index behavior; B2 remains `TBD/R2 only`. |
| Cut trigger | Cut C3 rather than pad the main line if no declared break-even, recall loss, RSS dominance, or no material R2 distinction occurs. |

## Anti-Claim

Engineering contracts, deterministic output, replay records, identity/checksum
validation, and internal variants do not establish semantic validity, diagnosis
correctness, literature novelty, or superiority. They are current engineering
evidence only and require independently governed evidence plus R2 review before
any later paper decision.

## Baseline Boundary and Comparability Gates

| Class | Provisional planning boundary |
| --- | --- |
| B0 | Full raw scan and fixed clipping selected before results; behavioral controls only. |
| B1 | Internal variants for mechanism isolation; never a literature comparator. |
| B2 | `TBD/R2 only`; no source, title, tool, or implementation is selected in R1A. |

A later B2 quantitative branch requires the master plan's same-contract fields
exactly: same raw SHA-256; case/workload; preprocessing; truth boundary; output
contract; environment identity; metrics; split; failure policy.
Source/version/license and mechanism/parameter mapping are later reproduction
records and do not replace those gates. Failure of any gate is capability-only
or not-comparable, not a reason to replace B2 with a weaker self-made baseline.

## R2 Search Taxonomy

| Taxonomy | Exact and synonym/mechanism terms | Required R2 inspection |
| --- | --- | --- |
| RTOS/embedded traces | RTOS trace, embedded trace, scheduling trace, event trace, trace diagnosis | Problem and trace representation. |
| Slicing | dynamic slicing, backward slicing, causal slicing, dependency slice, event dependency | Seed, traversal, order, and preservation boundary. |
| Selective access | selective tracing, selective capture, selective export, trace reduction, trace query, budgeted retrieval | Pre-read selection, omitted evidence, and budget semantics. |
| Closure/provenance | evidence closure, dependency closure, provenance package, artifact bundle | Required/optional closure and package status. |
| Replay/debugging | deterministic replay, reproducible debugging, replay package | Identity, repeatability, output status, and scope. |
| Index/I-O | sidecar index, trace index, indexed access, stream scan, selective I/O | Build/storage/cold/warm/reuse and recall. |
| Integrity rejection | checksum validation, identity validation, invalid package, canonical reason code, fail closed | Mutation model, acceptance behavior, and comparator. |

R2 must use exact-term, synonym, mechanism, problem, backward-citation, and
forward-citation queries. This registry contains no search output and no
preselected B2 work.

## Candidate Cut List and Decisions

| Candidate | Cut or reframe condition |
| --- | --- |
| C1 | Required evidence/result is not retained; declared net-cost benefit is not repeatable; R2 finds no material bounded difference. |
| C2 | Required invalid input reaches silent acceptance/completion; mutation/control design is not feasible; R2 removes the bounded difference. |
| C3 | No break-even, recall loss, RSS dominance, or no material R2 difference. |
| Whole problem | Independent labels, P/C/D separation, or a bounded output contract cannot be specified. |

`PROCEED` is available only when R2 verifies a material bounded distinction and
the retained candidate has a feasible refutation/support path. `REFRAME`
returns a narrowed candidate to a new R1A registry followed by a fresh R2.
`ABANDON` ends this candidate line when no material distinction or responsible
test path remains. These are future R2 decisions, not R1A outcomes.

## Current and Missing Evidence Summary

| Area | Current engineering context | Missing before any later evidence decision |
| --- | --- | --- |
| C1 | Contract mechanisms and deterministic package outputs are documented. | Independent labels, case/capture/processing protocol, B0/B1 net-cost observation, and R2 delta. |
| C2 | Layered identity/checksum/dependency rejection mechanisms are documented. | Mutation corpus, controls, denominators, reason-code observations, and R2 delta. |
| C3 | Sidecar/index is documented as a conditional mechanism. | Workload, break-even ledger, recall/RSS observations, and R2 delta. |
| All | R0 preserves Phase 7 provenance uncertainty without changing frozen history. | R2 verification and every later authorized benchmark, experiment, and review gate. |

## Forbidden Claims

The following phrases are prohibited as assertions in this provisional
registry: `novelty established`, `final contribution`, `final claim frozen`,
`first method`, `SOTA`, `diagnosis correctness proven`, `proof correctness`,
and `general RTOS validation`. They are listed only to prohibit them. This
registry cannot supply any of those conclusions.
