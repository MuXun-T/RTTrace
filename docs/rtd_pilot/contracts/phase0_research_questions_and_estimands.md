# RTD-Pilot Phase 0 Research Questions and Estimands

## Research questions

| RQ | Frozen question | Required future data |
| --- | --- | --- |
| RQ1 | Does CCM distinguish configured observational capability from an unobserved channel, and does CIR distinguish complete from degraded acquisition? | CCM, CIR, planned observation conditions, and independent truth for each scored Case. |
| RQ2 | Does evidence lineage completely record source/boundary/integrity facts without deciding diagnostic relevance? | Raw event IDs, boundary IDs, objective intersecting windows, lineage records, and Observer interval evidence. |
| RQ3 | Against rule-only, current `_trusted`, and global gap-overlap, does capability + integrity + lineage let the diagnoser assign fault-specific relevance and reduce false confirmation/refutation, then unnecessary `UNKNOWN` under a safety gate? | Same-input, frozen-parameter outputs, diagnostic relevance audit, and blinded Case-level truth. |
| RQ4 | Are recorder, memory, transport, and analysis costs acceptable for the declared one-core RTOS scope? | CCM configuration, CIR and collector-counter records, clean/collector-on measurements, buffer/loss data, and reproducible environment records. |

## Statistical unit and estimands

The primary statistical unit is a **Case**. A Case-level outcome aggregates its
predeclared Captures and observation conditions through the frozen scorer; no
Capture, Mask, model run, replay, or repeated processing is an independent
Case. Definitions and split rules are binding in
[the hierarchy contract](phase0_case_hierarchy_and_split_contract.md).

The minimum formal inventory is fixed at four independently parameterized
manifested-positive Cases plus one healthy, one near-miss, and one wrong-entity
Case per family (7 per family, 21 total), with at least three independent raw
Captures per Case (63 minimum). Invalid Captures and enabled-but-not-manifested
attempts are retained but excluded from valid denominators; Capture aggregation
is frozen after the Phase 5 Pilot and before holdout. False-confirmation uses
valid fault-negative/control Cases, false-refutation uses valid manifested-positive
Cases, and Case-level recall uses manifested-positive Cases only. Captures are
never independent samples. Each family entering template-level formal holdout
has one structurally independent development, validation, and holdout Scenario
Template; a family lacking that set is limited to within-template parameter
generalization with Case-cluster-isolated holdout. If every family lacks the
required template set, the formal template-level generalization claim is removed
and the study is downgraded to a single-platform engineering case study.

| Estimand | Numerator / denominator | Scope |
| --- | --- | --- |
| false-confirmation rate | fault-negative/control Cases output `SUPPORTED` for the registered fault / all valid fault-negative/control Cases | primary safety |
| false-refutation rate | valid manifested-positive Cases output `REFUTED` / all valid manifested-positive Cases | primary safety |
| missed-relevant-gap rate | valid Cases whose truth-relevant missing channel/boundary is not represented as blocking/unknown/OOD by the method / all Cases with that registered relevant gap | primary safety |
| confirmed precision | truth-positive `SUPPORTED` Cases / all `SUPPORTED` Cases | practical |
| Case-level recall | truth-positive Cases correctly `SUPPORTED` under the registered observation condition / valid manifested truth-positive Cases | practical |
| appropriate abstention | Cases where `UNKNOWN` or `OOD` agrees with an unavailable required channel, incomplete lineage, invalid capability, or registered OOD condition / Cases where abstention is required by protocol | practical |
| unnecessary UNKNOWN | truth-admissible Cases output `UNKNOWN` although required capability, lineage, and decisive evidence are complete / eligible Cases | practical, evaluated only after safety gates |
| eligible-decision coverage | eligible Cases with a non-`UNKNOWN` and non-`OOD` verdict / all eligible Cases | anti-universal-abstain gate |
| lineage completeness | scored decisive derivations with every required source/open/close/boundary reference, objective intersecting-window facts, and status / all scored decisive derivations | C1 process measure |
| relevance audit quality | diagnostic audit records with `relevant_untrusted_window_ids`, `irrelevant_untrusted_window_ids`, `unresolved_window_ids`, rule/version, and reason consistent with frozen fault rule / all audited derivations | RQ3 process measure |
| loss-attribution accuracy | registered loss/overflow/filter/sampling condition correctly attributed or explicitly marked unknown / valid injected and natural loss conditions | C1 process measure |

`OOD` is neither a false confirmation nor a false refutation; it is reported
separately. Invalid Captures are retained and reported, but excluded from a
diagnostic denominator only by an exclusion rule frozen before scoring.
Complete per-family holdout false-confirmation, false-refutation, confirmed
precision, and Case-level recall require the four holdout Case roles defined in
[the hierarchy contract](phase0_case_hierarchy_and_split_contract.md); otherwise
only submetrics with actual denominators may be reported with an explicit
downgrade.
Lineage completeness is not diagnostic correctness: RQ2 tests objective source,
boundary, and integrity recording; RQ3 tests diagnoser relevance for a specific
fault rule and entity binding. The same window may be relevant to one diagnosis
and irrelevant or unresolved for another.

## Gate order and deferred numeric choices

1. First test that false-confirmation does not worsen against each named
   baseline on the frozen comparison set.
2. Then test that false-refutation and missed-relevant-gap rate meet their
   frozen safety conditions.
3. Then require frozen eligible-decision coverage; a universal `UNKNOWN` or
   `OOD` output cannot establish C2 even if it avoids false conclusions.
4. Only then compare unnecessary `UNKNOWN`, precision, recall, and overhead.
5. A method may not improve confirmation or abstention merely by ignoring a
   relevant gap; that is a stop condition.

No alpha, tolerance, effect size, sample-size/power value, or final severity
threshold is invented here. The Phase 5 Pilot statistician must freeze all of
them, the Case aggregation rule, confidence-interval method, valid-Case rule,
and multiplicity handling after development/validation Pilot results but before
the first holdout access. The holdout access log must record the freeze digest.

The current baseline uses a coarse `_trusted` flag and alert support levels;
those are comparison baselines, not the future four-state truth semantics
([implementation plan](../../research_track/rtos_reliable_diagnosis_pilot/rtos_reliable_diagnosis_implementation_plan.md)).
