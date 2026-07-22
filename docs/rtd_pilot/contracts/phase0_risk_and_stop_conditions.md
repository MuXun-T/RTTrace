# RTD-Pilot Phase 0 Risk and Stop Conditions

All owners and deadlines below are future protocol roles/dates to be named in
Phase 1. `Stop` means permanently stop the diagnosis-paper route or downgrade
only to an explicitly labelled engineering/provenance report; it does not mean
fabricate substitute data.

| Risk / permanent condition | Early signal | Mitigation | Owner | Decision deadline | Stop/downgrade result |
| --- | --- | --- | --- | --- | --- |
| No real board | No inventory-verified flashable candidate | Score approved backup candidate | Phase 1 platform lead | Before Phase 1 execution | Stop C3 and diagnosis-paper route. |
| No independent Observer | No isolated GPIO/timer/analyzer design | Redesign only with independent clock/channel | Observer lead | Before hardware Pilot | Stop. |
| No immutable Ledger | No append-only accountable capture administration design | Define isolated Ledger service and review access roles | Data steward | Before any hardware dataset | Stop. |
| OAR/CVR authority separation fails | Ledger or capture service contains manifestation/validity conclusions, or OAR/CVR lacks its independent adjudicator | Separate Ledger identity, OAR manifestation truth, and CVR technical validity; retain references only | Data steward | Before any hardware dataset | Stop C1/C2/C3 route. |
| CCM/CIR cannot be recorded separately | Capability or actual integrity facts unavailable, conflated, or writable by decoder/parser | Reselect collector/platform or redesign isolated Manifest assembler | Collector lead | Before Case acquisition | Stop C1/C2 route. |
| Complete lineage cannot be implemented | Derived object lacks source/open/close/boundary/objective intersections or rebuild pre-judges relevance | Restrict output to non-decisive audit or redesign future rebuild | Analysis lead | Before Phase 6 | Stop C1/C2 route. |
| Only P6/P7 available | No real Case/Observer inventory | Use P6/P7 only for tests/format/regression | Publication lead | Before Phase 5 | Stop. |
| Case minimum cannot be met | Registry lacks 4 manifested-positive + healthy + near-miss + wrong-entity Cases per family, or 3 independent Captures per Case | Retain all invalid/non-manifested records; acquire valid independent Cases | Experiment lead | Before formal evaluation | Stop formal claim. |
| Three independent templates unavailable | A family lacks development/validation/holdout structural templates | Downgrade that family to within-template parameter generalization and Case-cluster-isolated holdout | Statistician | Before holdout | That family cannot support template-level generalization; if all families fail, remove formal template-level generalization and downgrade to a single-platform engineering case study. |
| Data/license unresolved | Rights matrix has unknown/prohibited rows or exact selected configuration lacks H3 recheck | Obtain written clearance or controlled-access route | Release owner | Before H3, collection, and submission | Stop/downgrade. |
| Improvement requires ignoring relevant gaps | Diagnostic audit omits a relevance-required window or converts objective lineage facts into relevance claims | Retain all objective intersections; revise relevance rule only under development protocol | Safety lead | Before holdout | Stop C2 claim. |
| Final dependence on Agent packaging | Verdict/label/result changes when Agent is removed | Remove Agent and re-evaluate deterministic core | Analysis lead | Before formal evaluation | Stop/downgrade; Agent cannot be core contribution. |
| Observer/trace cannot align | Error bound absent or marker conflicts | Add independent epoch/redundancy; invalidate affected Captures | Observer lead | Before scoring | Stop affected dataset; stop route if systemic. |
| Fault not stably manifested | Enabled attempts repeatedly fail predicate | Tune on development only, retain non-manifested attempts | Experiment lead | Pilot closeout | Stop that family or route if minimum Cases fails. |
| Collector perturbs behavior | Clean/collector-on Observer behavior diverges | Measure overhead; redesign recorder/platform | Collector lead | Pilot closeout | Stop/reselect collector. |
| Holdout leakage | Related template/Capture/mask crosses split or early access occurs | New independent holdout; log contamination | Statistician | Immediately on discovery | Downgrade to validation if no replacement. |

The permanent stops follow the total plan's list, including no hardware,
Observer, Ledger, lineage, sufficient Cases, legal data, relevant-gap safety, or
Agent independence ([implementation plan](../../research_track/rtos_reliable_diagnosis_pilot/rtos_reliable_diagnosis_implementation_plan.md)).
