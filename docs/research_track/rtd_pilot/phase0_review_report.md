# RTD-Pilot Phase 0 Review Report

This report preserves the initial review trail, records the external repair
findings, and records a new read-only re-review. No reviewer ran hardware,
created data, edited code/Schema/tests, or treated hashes, replay, Agent output,
P6, or P7 as truth evidence.

## Initial Review

The initial two read-only reviews reported `blocking=0`, `major=0`, and two
minor observations each. Reviewer 1 accepted the stated Case/Capture/Mask
hierarchy and required a Phase 5 pre-holdout statistical freeze. Reviewer 2
accepted the conditional platform protocol and required Phase 1 marker-pin and
selected-platform license checks. Those conclusions were superseded for repair
because external review identified ambiguity and object-boundary conflicts not
closed by the initial report.

```text
Initial Reviewer 1: blocking=0, major=0, minor=2
recommendation=ACCEPT PHASE 0 CONTRACT FREEZE; require Phase 5 pre-holdout statistical freeze

Initial Reviewer 2: blocking=0, major=0, minor=2
recommendation=ACCEPT PHASE 0 CONTRACT FREEZE; authorize Phase 1 planning only, not hardware execution
```

## First External Repair Findings: 6 major + 3 minor

1. **Major 1, Case denominator:** four Cases per family could be read as
   including controls, leaving too few manifested positives.
2. **Major 2, split executability:** template isolation existed but no minimum
   number of structurally independent templates supported development,
   validation, and holdout.
3. **Major 3, write permission:** decoder-derived integrity fields conflicted
   with the claim that parser/decoder could not write CCM.
4. **Major 4, object boundary:** CCM mixed configuration capability with
   Capture-specific integrity evidence.
5. **Major 5, conditional fields:** unconditional Ledger fields would force
   fabricated values for healthy, non-manifested, missing, or invalid states.
6. **Major 6, responsibility:** lineage called intersecting windows relevant,
   conflating objective provenance with diagnostic relevance.

The three minor findings were incomplete F1/F2/F3 marker semantics, circular
hardware scorecard entry criteria, and invalid Markdown links to the total plan
and repository artifacts.

## First Repair Actions

| Finding | Revised documents | Closed contract and evidence |
| --- | --- | --- |
| Major 1 | hierarchy, registry, RQ/estimands, claim boundary, risks, closeout | Each family has 4 independently parameterized manifested-positive + healthy + near-miss + wrong-entity Cases = 7; F1--F3 total 21; each Case has 3 independent Captures = 63 minimum. Invalid/non-manifested attempts do not count or become negatives. |
| Major 2 | hierarchy, registry, RQ/estimands, risks, closeout | Formal holdout needs development/validation/holdout templates with structural differences. Failure triggers within-template-only claims, Case-cluster holdout, and Threats to Validity; all-family failure stops formal generalization. A split manifest has ID, structure hash, cases, allocation, and digest fields. |
| Major 3 | Ledger/CCM/CIR/lineage, Observer, claim boundary, closeout | Raw capability writers, immutable integrity evidence producers, an independent deterministic canonical Manifest assembler, and read-only consumers are separated. Decoder emits only `decoder_integrity_report`. |
| Major 4 | Ledger/CCM/CIR/lineage, claim boundary, RQ/estimands, data/license, risks, closeout | CCM is static configured capability; CIR is Capture-specific observed integrity. Every derived object and diagnosis binds both references. |
| Major 5 | Ledger/CCM/CIR/lineage, registry, Observer, data/license, closeout | Status fields and conditional-required rules cover enable epoch, manifestation interval, Observer/raw hashes, and invalid reasons; legacy boolean manifestation is derived only. |
| Major 6 | Ledger/CCM/CIR/lineage, claim boundary, RQ/estimands, risks, closeout | Lineage retains all/boundary intersections and integrity issues as facts. Diagnoser audit alone stores relevant/irrelevant/unresolved IDs plus rule/version/reason. |
| Minor 1 | Observer, registry, hardware scorecard | Exact semantic marker sets are frozen for F1/F2/F3; independent channel or validated surrogate is mandatory in Phase 1. |
| Minor 2 | hardware scorecard, closeout | H1 planning (`score >= 1`), H2 separately authorized bench testing, and H3 exact-configuration selection (`score == 2`) remove the circular gate. |
| Minor 3 | all 11 Phase 0 Markdown documents | Relative links resolve to the real total-plan path `../rtos_reliable_diagnosis_pilot/rtos_reliable_diagnosis_implementation_plan.md` from this report/closeout and the corresponding `../../research_track/...` path from contracts; obsolete line-number URLs were removed. |

## First Independent Re-Review

### Reviewer 1: paper, Case, and statistical contract

Scope: manifested-positive/control separation, Case/Capture/Mask hierarchy,
template split and downgrade, denominators, non-manifested/invalid handling,
C1--C3 measurability, and the universal-abstain safeguard.

Findings: the minimum inventory explicitly reserves four positive Cases per
family and controls cannot occupy those slots. The three-template contract and
within-template downgrade make split claims executable. Denominators are
Case-level and use valid control or manifested-positive populations as required;
Capture aggregation remains a pre-holdout freeze, not sample multiplication.
The gate requires safety and eligible-decision coverage before any abstention
comparison, so universal `UNKNOWN` or `OOD` cannot establish C2.

```text
blocking=0
major=0
minor=0
recommendation=ACCEPT DOCUMENT/PROTOCOL FREEZE; AUTHORIZE PHASE 1 PLANNING ONLY
```

### Reviewer 2: Observer, permission, and data-object contract

Scope: CCM/CIR split, decoder/assembler authority, Ledger conditional fields,
healthy/non-manifested/invalid states, lineage/relevance separation, F1--F3
markers, H1/H2/H3 gates, selected-platform licensing, and Markdown links.

Findings: CCM and CIR have separate required field sets and roles. Decoder
evidence is immutable input to, not a write into, the canonical Manifest;
assembler and read-only consumer responsibilities no longer conflict. Status
and conditional rules avoid fabricated values. Lineage contains only objective
intersections, while diagnoser audit supplies fault-specific relevance. Marker
semantics, hardware staging, exact-platform license rechecks, and relative links
are explicit.

```text
blocking=0
major=0
minor=0
recommendation=ACCEPT DOCUMENT/PROTOCOL FREEZE; AUTHORIZE PHASE 1 PLANNING ONLY
```

## Second External Repair Findings: 3 major + 2 minor

The second external audit found that the first repair still had three contract
gaps and two terminology/claim-boundary gaps. No code, Schema, test, data,
hardware, or experiment finding was in scope.

1. **Final Major 1: Object-level writer permissions.** The prior four-layer
   table labelled parser, rebuild, diagnoser, evaluator, Agent, and export as
   overall read-only consumers while the same contract required some of them to
   write their own outputs. It also lacked authority-register rows for raw Trace
   and raw collection configuration, and initially exposed evaluator to source
   objects outside its sealed evaluation allowlist.
2. **Final Major 2: Ledger/OAR/CVR authority separation.** Injection Ledger
   mixed configuration identity with Observer status/manifestation and Capture
   validity fields although those conclusions require independent adjudication.
3. **Final Major 3: Case-role allocation across development/validation/holdout.**
   The fixed seven-Case family floor did not state where positives and controls
   belong, so complete holdout safety and performance denominators were not
   guaranteed.
4. **Final Minor 1: RQ4 CCM counters terminology.** RQ4 called operational
   counters "CCM counters" even though overflow, continuity, observed
   watermark, truncation, and corruption are CIR/collector/decoder evidence.
5. **Final Minor 2: C3 per-family template downgrade wording.** C3 said only
   "no three independent templates", which could imply that one family failure
   removes every family claim or that all-family failure retains formal
   template-level generalization.

## Second Repair Actions

| Finding | Revised documents | Concrete closed contract | Static verification | Status |
| --- | --- | --- | --- | --- |
| Final Major 1 | Ledger/CCM/CIR/lineage drafts; Observer boundary; claim boundary; closeout | The matrix now grants parser/decoder `decoder_integrity_report` and parsed events, assembler versioned CCM/CIR, rebuild derived objects/Evidence Lineage, diagnoser Diagnostic Relevance Audit/Diagnosis Verdict, evaluator Evaluation Result, and export only presentation artifacts. The authority register gives every listed object one authoritative writer, sources, versioning, readers, and forbidden writers, including raw Trace and configuration. Evaluator reads only sealed OAR/Case truth, frozen diagnosis, split, and scorer; Agent cannot write any governed object. | Matrix/register scan confirms required writers, `authoritative writer`, `decoder_integrity_report`, and Diagnostic Relevance Audit; forbidden whole-component read-only wording and evaluator source-object access are absent. | CLOSED |
| Final Major 2 | Ledger/CCM/CIR/lineage drafts; Observer boundary; fault/control registry; claim boundary; data/license checklist; risk/stop conditions; closeout | Ledger is configuration/identity plus OAR/CVR references only, written by isolated capture administration. OAR alone carries manifestation status/interval and is written by the independent Observer adjudicator. CVR alone carries technical Capture validity and is written by the independent capture quality adjudicator. The capture administration service cannot write either adjudication. | Required OAR/CVR fields, writers, reference graph, and Ledger forbidden fields are explicit; the prohibited Ledger-as-manifestation assertion is absent. | CLOSED |
| Final Major 3 | Case hierarchy/split; RQ/estimands; claim boundary; fault/control registry; risk/stop conditions; closeout | Per family remains four manifested-positive plus healthy, near-miss, wrong-entity: seven Cases. Development and validation each receive at least one positive; holdout receives one positive and all three controls. The fourth positive is preallocated to development or validation. Templates do not cross splits; controls are independent Cases, not Captures/Masks. | Split manifest includes `case_role` and `metric_eligibility`; hierarchy asserts the four holdout roles and metric-identifiability gate. | CLOSED |
| Final Minor 1 | RQ/estimands; Ledger/CCM/CIR/lineage drafts; review report | RQ4 now names CCM configuration, CIR and collector-counter records, clean/collector-on measurements, buffer/loss data, and reproducible environment records. CCM explicitly excludes operational counter/integrity observations. | Full Phase 0 scan finds no live `CCM counters` wording; the historical review description preserves it only as the named old error. | CLOSED |
| Final Minor 2 | Claim boundary; case hierarchy/split; RQ/estimands; fault/control registry; risk/stop conditions; closeout | A deficient family is limited to within-template parameter generalization. If every family lacks structurally independent development/validation/holdout templates, formal template-level generalization is removed and the study becomes a single-platform engineering case study. | Cross-document scan finds both the per-family downgrade and all-family stop wording in the binding contracts and closeout. | CLOSED |

## Final Independent Re-Review

### Reviewer 1: permissions, truth, and object boundaries

Scope: parser/assembler/rebuild/diagnoser/evaluator/Agent permissions; every
object's authoritative writer; Ledger/OAR/CVR separation; capture-service
limits; and self-evaluation cycles.

Findings: every object in the authority register, including raw Trace and raw
collection configuration, has one writer contract. Parser/decoder writes only
its parsed-event and decoder-integrity outputs; assembler writes CCM/CIR;
rebuild writes derived objects and Evidence Lineage; diagnoser writes relevance
and four-state verdict; evaluator writes only scoring output. Ledger has only
configuration/identity and OAR/CVR references. OAR is the sole manifestation
truth record, CVR is the sole technical-validity record, and Agent has no
governed-object write permission. No writer evaluates its own correctness.

```text
blocking=0
major=0
minor=0
recommendation=ACCEPT DOCUMENT/PROTOCOL FREEZE; AUTHORIZE PHASE 1 PLANNING ONLY
```

### Reviewer 2: Case, split, metrics, and paper boundary

Scope: Case/Capture inventory, Case-role allocation, Template isolation,
holdout denominators, C3 downgrade behavior, and RQ4 terminology.

Findings: every family remains at four manifested-positive and three control
Cases, seven Cases per family, at least three Captures per Case, and 21/63
minimum totals. Development and validation each have a positive; holdout has a
positive plus healthy, near-miss, and wrong-entity Cases. The Template cannot
cross split, and controls cannot be substituted by Captures or Masks. The four
complete holdout metrics require all four roles. The per-family and all-family
template downgrade is explicit, and RQ4 does not call counters CCM fields.

```text
blocking=0
major=0
minor=0
recommendation=ACCEPT DOCUMENT/PROTOCOL FREEZE; AUTHORIZE PHASE 1 PLANNING ONLY
```

## Final Gate

All first-round six-major/three-minor repairs and final three-major/two-minor
repairs are closed at the Phase 0 documentation-contract level. Both final
independent reviewers report `blocking=0` and `major=0`. This is not
implementation evidence: no platform is selected, H2/H3 have not passed, and
no hardware, Schema, Ledger, OAR, CVR, CCM, CIR, Evidence Lineage, Trace, data,
or experiment exists.

```text
PHASE 0 READY TO FREEZE
```
