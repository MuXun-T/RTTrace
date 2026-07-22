# RTD-Pilot Phase 0 Claim Boundary

Status: frozen conceptual contract. It authorizes neither implementation nor experiments.

## Title and scope

Primary title: **基于采集能力、采集完整性与证据血缘的 RTOS 轨迹缺失感知诊断方法**

Alternative title: **面向不完整观测的 RTOS 轨迹可靠诊断系统设计与实现**

The future paper is restricted to one named RTOS, one named board and firmware
configuration, one core, finite traces, task/mutex/IRQ objects, bounded fault
patterns, and the verdicts `SUPPORTED`, `REFUTED`, `UNKNOWN`, and `OOD`. Its
truth evidence must come from real hardware and an independent Observer.

It excludes queue backlog, complete priority-inheritance semantics, deadline
semantics, multicore causality, arbitrary RTOSes, universal root cause,
arbitrary loss, Agent-selected diagnoses, completion/possible-world reasoning,
and new formal-verification theory. F1 never proves permanent starvation or a
unique scheduler cause; F3 never proves IRQ as the unique causal cause; F2 may
call priority inversion a pattern only.

## Binding inherited boundaries

Bounded closure, identity packages, WARDS, completion quotient, VEC-RT and
OOV-RT remain negative decisions. Agent/LLM/RAG/Gate, manifest/checksum/proof
digest/package replay, and P6/P7 labels are outside the truth path. P6/P7 may
only be used later for unit, format, and regression work. The first version is
single-core and task/mutex/IRQ-only.

Repository basis: the total plan fixes this positioning and the exclusions
([implementation plan](../../research_track/rtos_reliable_diagnosis_pilot/rtos_reliable_diagnosis_implementation_plan.md),
sections "Executive Summary", "Inherited Negative Boundaries", and "Claim Boundary");
the earlier routes mark WARDS `NO-GO`, completion `ABANDON`, and VEC/OOV
`REFRAME` ([same plan](../../research_track/rtos_reliable_diagnosis_pilot/rtos_reliable_diagnosis_implementation_plan.md)).
P6 has zero real-hardware Cases ([P6 report](../../phase6_p6_4_diagnosis_report.md));
P7 explicitly prohibits hardware-validation claims ([P7 plan](../../phase7_p7_2_licensed_external_trace_plan.md)).

## Contribution contract

| ID | Claim | Required implementation | Required experiment | Required baseline | Required evidence | Downgrade condition |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | Capture Capability, Capture Integrity, and complete evidence lineage make every task/mutex/IRQ derivation auditable. | Versioned CCM/CIR binding; complete source/open/close/boundary lineage; deterministic validator. | Hardware Captures with planned filter/sampling/loss/boundary conditions, including invalid captures retained. | Current `_trusted`; no-lineage rebuild; global gap-overlap. | CCM, CIR, raw trace, lineage, observer alignment record and immutable Ledger references join for every scored result. | Any missing capability/integrity fact or incomplete lineage for a claimed decisive interval: restrict to `UNKNOWN`/`OOD`, or remove C1 efficacy claim. |
| C2 | A deterministic missing-aware diagnoser conservatively emits four verdicts and reduces unsafe conclusions. | Capability/integrity/lineage admission checks; witnesses/counter-witnesses; deterministic rules and audit report. | Blinded, real-hardware Case-level comparison across registered faults and controls. | Rule-only, current `_trusted`, global gap-overlap, all with identical input and frozen parameters. | Observer truth, Case-level scorer, retained gaps/masks, false-confirmation/refutation audit, and frozen eligible-decision coverage. | A relevant missed gap produces false confirmation; universal `UNKNOWN`/`OOD` prevents decision coverage; Agent/gate affects a verdict; or fair input parity fails. |
| C3 | In the stated one-RTOS, one-board scope, reliability benefit and overhead are measured on real RTOS Cases. | Recorder/transport measurement hooks and reproducible analysis path, after separate authorization. | Per family: 4 independently parameterized manifested-positive Cases + healthy, near-miss, wrong-entity controls = at least 7 Cases; at least 3 Captures per Case. Thus at least 21 Cases/63 raw Captures across F1--F3, excluding invalid and non-manifested attempts. | Same workload with collector disabled plus C2 baselines. | Independent Observer, Injection Ledger, OAR, CVR, CCM, CIR, raw and derived data rights, Case-level results and retained failures. | No board/Observer/Ledger/licence; fewer than the minimum Cases; P6/P7 substituted; or results improve only by ignoring relevant gaps. A fault family lacking structurally independent development, validation, and holdout templates is limited to within-template parameter generalization. If every fault family lacks the required template set, remove the formal template-level generalization claim and downgrade the study to a single-platform engineering case study. |

No contribution currently has implementation or experimental evidence. These are
future obligations, not current claims. The current dictionary already names
task/sync/IRQ/integrity events, including `LOSS`/`OVERFLOW`
([event dictionary](../../../spec/assets/dictionary.json)), but
that is only an implementation starting point, not C1 evidence.

## Claim-to-evidence rule

Every future paper sentence must identify C1, C2, or C3; a frozen Case scope;
an evidence class; and a permissible conclusion. The Injection Ledger proves
configured injection and identity; OAR proves independent manifestation truth;
CVR proves technical Capture validity; CCM proves configured capability; CIR
proves per-Capture integrity; Evidence Lineage proves objective derivation
provenance; the diagnoser writes the Diagnostic Relevance Audit and Diagnosis
Verdict; and the evaluator writes the Evaluation Result. A hash or replay
establishes file identity only. No component can substitute for another, and an
Agent writes none of these objects or verdicts.
