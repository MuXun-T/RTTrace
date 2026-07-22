# RTD-Pilot Phase 0 Independent Observer Truth Boundary

Status: paper protocol only. No board, logic analyzer, Observer, or firmware is
connected or created in Phase 0.

## Required future observation plane

The Phase 1 feasibility test must demonstrate a paper design containing:

- Case start/end GPIO markers;
- injection enable/disable GPIO markers;
- IRQ entry/exit GPIO markers;
- an independent logic analyzer;
- independent clock and hardware timer/counter;
- a secondary serial milestone channel where feasible;
- a trace/Observer shared epoch marker;
- measured alignment error, expressed as an upper bound; and
- retained invalid-capture records.

Phase 0 freezes marker semantics, not final pins. The minimum independent marker
sets are:

```text
F1: case_start, case_end, injection_enable, target_ready_or_release,
    target_run_start, target_run_end_or_milestone, interferer_active_start,
    interferer_active_end
F2: case_start, case_end, injection_enable,
    holder_critical_section_enter, holder_critical_section_exit,
    waiter_request_or_wait_start, waiter_resume_or_wait_end
F3: case_start, case_end, injection_enable, irq_enter, irq_exit,
    target_ready_or_release, target_run_start
```

Phase 1 must prove that each marker is collected through an independent channel
(GPIO, timer capture, secondary serial, or a validated surrogate). Any surrogate
must have its semantics explicitly verified. If independent evidence cannot
verify the registered manifestation predicate for a family, that family stops
or is downgraded and cannot support a formal holdout claim.

The logic analyzer/timer/secondary channel must not consume parser, diagnoser,
Agent, Ledger records, or evaluator output. The Observer clock and recording
path are independent of the trace transport sufficiently that a trace gap
cannot itself manufacture a manifestation predicate. Phase 1 must document
pin ownership, electrical limitations, sampling rate, clock source, analyzer
configuration, epoch procedure, alignment estimation, and the uncertainty
bound before Phase 5 collection begins.

## Authority separation

| Plane | May establish | Must not establish |
| --- | --- | --- |
| Injection Ledger | configured injection, enable/disable epochs, firmware/config identity, intended Case, and OAR/CVR references. | Whether the fault actually manifested; Capture validity; diagnosis correctness. |
| Independent Observer service | raw Observer artifacts, measurement report, and physical/scheduler milestones. | OAR conclusion, diagnostic-output correctness, a unique root cause, Agent explanation correctness. |
| Independent Observer adjudicator | OAR: the registered predicate's manifestation status and interval under the frozen predicate. | Ledger configuration, CVR conclusion, diagnosis output, or a unique root cause. |
| Independent capture quality adjudicator | CVR: technical Capture validity from raw-artifact status, Observer status, CCM, and CIR evidence. | Manifestation conclusion or diagnosis correctness. |
| Evaluator | Evaluation Result: whether a frozen diagnosis agrees with sealed OAR/Case truth under the scorer. | Modify Ledger/OAR/CVR or choose truth after seeing output. |
| Parser/diagnoser/Agent | Parser writes parsed events and `decoder_integrity_report`; diagnoser writes Diagnostic Relevance Audit and Diagnosis Verdict; Agent may only make separately authorized optional recommendations/reports. | Truth labels, alignment authority, Ledger/OAR/CVR mutation, CCM/CIR mutation, lineage mutation, verdict mutation by Agent, evaluation, or control selection. |

The Observer service may emit an immutable `observer_boundary_report` and
integrity evidence, but it does not write OAR, CVR, or the canonical CCM/CIR.
The canonical Manifest assembler only merges configuration and integrity facts;
it never reads diagnoser or Agent output. The capture administration service may
link the Ledger to OAR/CVR by reference but cannot write their conclusion fields.

## Invalid-capture rule

A Capture is invalid if a mandatory Case/enable/end marker is absent or
ambiguous, the declared alignment bound cannot be measured, the independent
Observer record is corrupt/incomplete, pins conflict with required markers, or
the applicable family predicate cannot be assessed. It is retained with reason
and excluded only by the frozen valid-Case rule; it never becomes negative
truth. An enabled but non-manifested attempt is valid only when the Observer
can evaluate and reject its predicate.

Observer status must use explicit states (`complete`, `partial`, `corrupt`,
`missing`, `unalignable`) and must never use a placeholder hash to imply an
artifact that does not exist.

The current repository has no independent Observer or hardware Cases
([implementation plan](../../research_track/rtos_reliable_diagnosis_pilot/rtos_reliable_diagnosis_implementation_plan.md));
the plan requires logic-analyzer/timer/secondary-channel records and an error
bound ([same plan](../../research_track/rtos_reliable_diagnosis_pilot/rtos_reliable_diagnosis_implementation_plan.md)).
