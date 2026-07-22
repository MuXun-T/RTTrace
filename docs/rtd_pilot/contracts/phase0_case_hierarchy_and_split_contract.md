# RTD-Pilot Phase 0 Case Hierarchy and Split Contract

## Entities

```text
Scenario Template -> Case -> Capture -> Observation Condition / Mask -> Candidate / Diagnosis
```

| Entity | Definition | Independent statistical unit? |
| --- | --- | --- |
| Scenario Template | The reusable workload/fault/control structure, including entity roles and parameter ranges. | No; it is the split grouping key. |
| Case | One independently instantiated template with immutable Case ID, configuration, seed, target entities, fault/control designation, and intended predicate. | Yes; primary unit. |
| Capture | One independent execution/recording attempt for a Case with a Capture ID and its own Injection Ledger, OAR, CVR, CCM, CIR, Evidence Lineage, and Independent Observer artifacts. | No. |
| Observation Condition | A predeclared collector/capability condition, including natural loss or approved future controlled observation condition. | No. |
| Mask | A deterministic, versioned analysis perturbation of a Capture permitted only by the future protocol. | No. It cannot create a new Case. |
| Candidate / Diagnosis | One deterministic method/baseline output for a specified Case-Capture-condition/mask. | No. Re-runs are technical repeats only. |

## Frozen minimum Case contract

Each fault family requires, at minimum, **four independently parameterized
manifested-positive Cases**, plus **one healthy control Case, one near-miss Case,
and one wrong-entity Case**: 7 independent Cases per family, 21 across F1--F3.
Every Case requires at least three independently started raw Captures, giving a
minimum of 63 raw Captures before counting failures or non-manifested attempts.
This is a floor, not a guarantee of statistical sufficiency. A Capture, Mask,
Observation Condition, duplicate analysis, or model rerun never increases the
independent Case count. Enabled-but-not-manifested attempts are retained
separately and do not count toward the seven; acquisition-invalid Captures are
also retained separately and do not count as valid Cases. A non-manifested
attempt cannot be relabelled healthy, and an invalid Capture cannot be relabelled
negative.

The false-confirmation denominator is all valid fault-negative/control Cases;
the false-refutation denominator is all valid manifested-positive Cases; and
Case-level recall uses manifested-positive Cases only. The Capture aggregation
rule is deferred to the Phase 5 Pilot and must be frozen before holdout access;
Captures must not be treated as independent samples.

## Scenario Template minimum and split manifest

Each family entering formal holdout evaluation must have at least one
development, one validation, and one holdout Scenario Template. The three
templates must differ structurally in at least one of workload structure, task
relationship, holder/waiter/interferer topology, IRQ source/service path,
control-flow structure, injection mechanism, or entity-role composition.
Random seed, task ID, threshold, load value, repetition count, resource/IRQ
renaming, or an approximate parameter variant within one firmware does not make
a new template.

If a fault family lacks structurally independent development, validation, and
holdout templates, that family is limited to within-template parameter
generalization: it may support mechanism/feasibility evidence but not
template-level generalization. The paper Threats to Validity must state this and
holdout must still isolate Case clusters. If every fault family lacks the
required template set, remove the formal template-level generalization claim and
downgrade the study to a single-platform engineering case study.

## Split freeze

| Split | Permitted use | Prohibited use |
| --- | --- | --- |
| development | Implement future contracts; choose candidate rules, parameters, and threshold ranges; diagnose acquisition defects. | Reporting final efficacy or moving a related template to another split. |
| validation | Select among predeclared alternatives and freeze thresholds/statistics after Pilot work. | Repeated tuning after inspecting holdout, use of holdout truth, or splitting Captures/masks as Cases. |
| holdout | One blinded final evaluation under frozen code, parameters, scorer, exclusion policy, and template allocation. | Any tuning, threshold selection, rule selection, Case substitution, or template leakage. |

All Cases descended from one Scenario Template, including approximate variants,
must stay in one split. Captures, observation conditions, and masks inherit that
Case's split. The split manifest is created before acquisition and has immutable
IDs, template membership, intended controls, and version/digest. Its minimum
fields are:

```text
split_manifest_id, scenario_template_id, template_structure_hash, case_ids,
case_role, metric_eligibility, split, allocation_reason, allocation_timestamp,
allocator, manifest_version, manifest_digest
```

`case_role` is exactly one of `manifested_positive`, `healthy`, `near_miss`, or
`wrong_entity`. `metric_eligibility` explicitly lists the applicable members of
`false_confirmation`, `false_refutation`, `precision`, `recall`, and
`abstention`; it is a pre-acquisition declaration, not a result-dependent tag.

## Minimum Case-role allocation

The fixed minimum remains seven Cases per family: four manifested-positive,
one healthy, one near-miss, and one wrong-entity. Without increasing that floor,
the split manifest must allocate each family as follows:

| Split | Minimum Case roles | Rule |
| --- | --- | --- |
| development | at least 1 manifested-positive | May receive the fourth manifested-positive Case. |
| validation | at least 1 manifested-positive | May receive the fourth manifested-positive Case. |
| holdout | at least 1 manifested-positive, 1 healthy, 1 near-miss, 1 wrong-entity | Every listed role is an independent Case from a holdout Scenario Template. |

The two permitted seven-Case minima are therefore `development: 2
manifested-positive; validation: 1 manifested-positive; holdout: 1
manifested-positive + 3 controls`, or the same allocation with development and
validation positive counts reversed. Development and validation need not each
contain every control type at the minimum contract. Their rules and thresholds
may use only their own data and preregistered Pilot data. Holdout control Cases
may never be used for after-the-fact tuning.

All holdout Cases must belong to holdout Scenario Templates. A Template's
controls cannot be holdout while its positives are development or validation,
and the reverse split is equally forbidden. Controls are independent Cases, not
Captures or Masks. If the seven-Case floor cannot simultaneously meet
Template-level split isolation and holdout category completeness, the Case count
must increase; the split contract cannot be broken.

## Holdout metric-identifiability gate

For a fault family, complete holdout false-confirmation rate,
false-refutation rate, confirmed precision, and Case-level recall may be
reported only when holdout contains at least one manifested-positive, one
healthy, one near-miss, and one wrong-entity Case. If any category is absent,
report only submetrics with an actual denominator and explicitly downgrade the
paper statement; never infer the missing denominators from development,
validation, a Capture, or a Mask.

First holdout
access records person, time, commit/config/rule/scorer versions, and purpose.
If that access reveals a protocol error, the affected holdout is contaminated:
create a new independent holdout or downgrade the result to validation-only.

This hierarchy follows the total plan's explicit Case--Capture--Mask order and
its Case-level requirement ([implementation plan](../../research_track/rtos_reliable_diagnosis_pilot/rtos_reliable_diagnosis_implementation_plan.md),
section "Overall Architecture").
