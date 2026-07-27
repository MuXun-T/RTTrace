# RTD-Pilot Phase 0 Post-Freeze Consistency Record

Status: controlled post-freeze supplement. This record confirms Phase 0; it
does not reopen, replace, or amend the eleven frozen Phase 0 contracts.

## Scope and anchors

This reconciliation compares the frozen Phase 0 contract at commit
`00b599fa62b5704a146e348df86cf56df0d0a94f` with the subsequently frozen RTD
core implementation plan at commit `abee59ffe9aecf7bdd62427abc0de988c2935b0f`
([implementation plan](../rtos_reliable_diagnosis_pilot/rtos_reliable_diagnosis_implementation_plan.md)).

The implementation plan newly names the three-technology graduation system,
`RTDDiagnosisInputBundle`, `RTDDiagnosticReport`, `EvidenceExportSeed`, and the
CAPE-RT bridge. These are future construction/interface names. They do not
create a Phase 0 Schema, record instance, hardware activity, data collection,
experiment, or implementation authorization.

The authoritative Phase 0 boundaries remain the [claim boundary](../../rtd_pilot/contracts/phase0_claim_boundary.md),
[Ledger/OAR/CVR/CCM/CIR/lineage contract](../../rtd_pilot/contracts/phase0_ledger_ccm_lineage_drafts.md),
[case/split contract](../../rtd_pilot/contracts/phase0_case_hierarchy_and_split_contract.md),
[Observer boundary](../../rtd_pilot/contracts/phase0_observer_truth_boundary.md),
and [closeout](phase0_closeout.md). In a field-level conflict, those frozen
contracts control this reconciliation record.

## Coverage and reconciliation

| New or clarified plan item | Frozen Phase 0 coverage | Controlled interpretation | Reopen required? |
| --- | --- | --- | --- |
| CCM configured capability and CIR actual Capture integrity | Covered. | CCM/CIR remain separate, versioned objects. Operational loss, overflow, continuity, truncation, corruption, and observed watermark remain CIR/collector/decoder facts, never CCM fields. | No. |
| Runtime diagnosis must not read independent truth | Covered and reaffirmed. | The future `RTDDiagnosisInputBundle` may contain parsed/rebuilt evidence, CCM, CIR, Evidence Lineage, semantic profile, and frozen rule/threshold versions. It must not contain the Injection Ledger, OAR, CVR, manifestation status/interval, Observer labels, or any other independent-truth result. | No. |
| Plan's legacy monolithic Ledger field sketch | Not field-level compatible without this record. | The sketch is interpreted through the frozen split: configuration/identity and `injection_enable_epoch` belong to Injection Ledger; `observer_hash`, manifestation status/interval, and any `fault_manifested` projection belong to OAR; `raw_trace_hash`, invalidity status, and `invalid_reason` belong to CVR. Ledger links OAR/CVR by reference only. No physical duplication permits capture administration to adjudicate truth or validity. | No; this record supplies the required consistency interpretation. |
| `RTDDiagnosticReport` | Partly covered by frozen Diagnosis Verdict and Diagnostic Relevance Audit, but the interface name is new. | It is a later deterministic diagnoser/export interface. Its verdict remains exactly `SUPPORTED`, `REFUTED`, `UNKNOWN`, or `OOD`, derives only from permitted diagnostic inputs, and is read-only to CAPE-RT and evidence export. It does not replace OAR, CVR, Evaluation Result, or the four-state contract. | No. |
| `EvidenceExportSeed` and bounded-closure integration | Covered at the truth boundary; the interface name is new. | It is a future downstream reference/seed only. Sidecar, bounded closure, proof digest, package, replay, and reopen status stay outside the truth and verdict paths and cannot write Ledger, OAR, CVR, CCM, CIR, lineage, relevance, verdict, or evaluation. | No. |
| CAPEFeatureBundle, CaptureEpisode/CaseEpisodeBundle, CAPE-RT bridge, optional LLM planner | Not a Phase 0 object, and deliberately deferred. | These are post-Phase-0, one-way consumers of frozen diagnostic outputs. They cannot create truth, rewrite RTD, substitute for independent Cases/Captures/controls, change the 7-Case family floor or split, or authorize collection. Any model training, active planning, Agent work, or new data protocol needs its own later authorization and plan. | No. |
| Single-core F1/F2/F3 scope, four verdicts, independent Observer, H1/H2/H3 | Covered. | These boundaries retain their exact frozen meaning. In particular, H1 permits planning only; H2 still requires separate user authorization before bench testing; H3 remains the only platform-selection gate. | No. |

## Preserved invariants

This record preserves, without modification:

- Injection Ledger configuration/identity only; OAR independent manifestation
  truth; CVR technical Capture validity; CCM configured capability; CIR actual
  integrity; Evidence Lineage objective provenance; diagnoser relevance and
  four-state verdict; evaluator accuracy assessment.
- Four manifested-positive plus healthy, near-miss, and wrong-entity Cases per
  family; 7 Cases per family, 21 total, 3 Captures per Case, and 63 minimum raw
  Captures.
- Template-level split isolation, holdout Case-role completeness, C3 downgrade
  rules, and the existing Agent prohibition.
- No selected platform, no H2/H3 passage, no hardware connection, no firmware,
  no Schema, no record instances, no Trace/data collection, and no experiment.

## Reconfirmation result

The plan additions are either already covered by frozen Phase 0 boundaries or
are now explicitly constrained as later, one-way interface work. The legacy
monolithic Ledger wording has a single controlled interpretation through the
frozen Ledger/OAR/CVR separation. No Phase 0 gate, claim, Case count, split,
hardware requirement, or truth boundary is reopened.

```text
PHASE 0 REMAINS FROZEN
PHASE 1 PLANNING ONLY REMAINS AUTHORIZED
```
