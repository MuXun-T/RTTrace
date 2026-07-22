# RTD-Pilot Phase 0 Closeout

Status: **PHASE 0 READY TO FREEZE**.

This is a documentation/protocol freeze only. It does not select a platform,
pass H2 or H3, authorize hardware execution, connect hardware, write firmware,
implement a Schema, generate an Injection Ledger/OAR/CVR/CCM/CIR, implement
Evidence Lineage, collect a Trace or data, run an experiment, or create a
commit/push/tag.

## Audit Record

| Item | Value |
| --- | --- |
| repository | `realization/` (the parent `patent/` directory is not a Git repository) |
| branch | `main` |
| START_HEAD | `c0a8b33f40fa63d922907cb234eb79b7380e905f` |
| FINAL_HEAD | `c0a8b33f40fa63d922907cb234eb79b7380e905f` |
| modified files | the 11 Phase 0 Markdown documents listed below only |
| intent-to-add files | the same 11 Phase 0 Markdown documents only; no formal staging was performed |
| code/Schema/test changes | none |
| hardware/data/experiment activity | none |
| commits/push/tags | none |

## Delivered Contracts

1. [Claim boundary](../../rtd_pilot/contracts/phase0_claim_boundary.md): C1 is Capture Capability, Capture Integrity, and evidence lineage; C2 is deterministic conservative diagnosis; C3 is Case-level real RTOS evidence.
2. [Fault and control registry](../../rtd_pilot/contracts/phase0_fault_and_control_registry.md): F1/F2/F3 predicates, controls, marker dependencies, and minimum inventory.
3. [RQ and estimands](../../rtd_pilot/contracts/phase0_research_questions_and_estimands.md): Case-level safety denominators, relevance audit, and anti-universal-abstain gate.
4. [Hierarchy and split contract](../../rtd_pilot/contracts/phase0_case_hierarchy_and_split_contract.md): 4 manifested-positive + 3 controls per family, 21 Cases/63 Captures, template split manifest, and downgrade.
5. [Observer boundary](../../rtd_pilot/contracts/phase0_observer_truth_boundary.md): independent truth and F1/F2/F3 marker semantics.
6. [Hardware scorecard](../../rtd_pilot/contracts/phase0_hardware_feasibility_scorecard.md): H1 planning, separately authorized H2 bench testing, and H3 exact-configuration selection.
7. [Ledger/OAR/CVR/CCM/CIR/lineage drafts](../../rtd_pilot/contracts/phase0_ledger_ccm_lineage_drafts.md): object-level writer permissions, Ledger configuration identity, OAR manifestation truth, CVR technical Capture validity, separate CCM/CIR, and objective lineage versus diagnostic relevance.
8. [License/release checklist](../../rtd_pilot/contracts/phase0_data_license_and_release_checklist.md): release route and H3 selected-platform recheck.
9. [Risk and stop conditions](../../rtd_pilot/contracts/phase0_risk_and_stop_conditions.md): Case/template, object-boundary, rights, and relevance stops.
10. [Review report](phase0_review_report.md): preserved initial trail, six-major/three-minor repair record, and two re-reviews.
11. This closeout.

## Frozen Contract Summary

Each fault family requires 4 independently parameterized manifested-positive Cases, 1 healthy, 1 near-miss, and 1 wrong-entity Case: 7 per family and 21 total. Every Case has at least 3 independently started raw Captures: at least 63. Invalid Captures, enabled-but-not-manifested attempts, Masks, observation conditions, repeated analyses, and model runs never increase this Case count.

Formal template-level evaluation requires structurally distinct development,
validation, and holdout Scenario Templates per evaluated family. A family
missing that set is limited to within-template parameter generalization and
Case-cluster-isolated holdout. If every family lacks the required template set,
the formal template-level generalization claim is removed and the study is
downgraded to a single-platform engineering case study.

Per family, development and validation each contain at least one
manifested-positive Case. Holdout contains at least one manifested-positive,
one healthy, one near-miss, and one wrong-entity independent Case. The fourth
manifested-positive Case is preallocated to development or validation. All
Cases from a Scenario Template remain in its one split; no control is replaced
by a Capture or Mask. The four complete holdout metrics (false-confirmation,
false-refutation, confirmed precision, and Case-level recall) are reported only
when those four holdout roles provide their denominators.

Injection Ledger records configured injection and identity only, and is written
by the isolated capture administration service. OAR is written by the
independent Observer adjudicator and is the sole manifestation-truth record.
CVR is written by the independent capture quality adjudicator and is the sole
technical-Capture-validity record. The canonical manifest assembler writes CCM
(configured observational capability) and CIR (actual per-Capture integrity).
Deterministic rebuild writes Evidence Lineage; deterministic diagnoser writes
the Diagnostic Relevance Audit and Diagnosis Verdict; evaluator writes the
Evaluation Result from sealed OAR/Case truth and frozen diagnosis. Agent cannot
write Ledger, OAR, CVR, CCM, CIR, lineage, relevance, verdict, or evaluation.
Verdicts are only `SUPPORTED`, `REFUTED`, `UNKNOWN`, and `OOD`; Agent
confidence/probability has no truth or verdict role.

F1/F2/F3 marker semantics require independent acquisition or validated surrogates. Hardware gates are H1 planning at documentary/inventory `score >= 1`, H2 limited bench testing only after separate authorization, and H3 platform selection only after every mandatory exact-configuration score is `2`, marker map/build/isolation/workloads/release route are verified. License clearance is rechecked for the selected configuration at H3, collection, and submission.

Stop conditions include failure of the Case/template floor, independent Observer, CCM/CIR separation, complete objective lineage, lawful release route, or relevance-safe conservative diagnosis.

## Gate Decision

The initial six external major findings and three minor findings, plus the final
three major findings and two minor findings, are closed in the documents above.
Both final independent reviewers report `blocking=0, major=0` in the
[review report](phase0_review_report.md). Therefore the documentation gate is
satisfied.

```text
AUTHORIZE PHASE 1 PLANNING
PHASE 0 READY TO FREEZE
```

Only Phase 1 planning is authorized. Any hardware execution still requires a separate user authorization and H2; formal platform selection remains blocked until H3.
