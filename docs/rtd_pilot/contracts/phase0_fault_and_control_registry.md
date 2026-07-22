# RTD-Pilot Phase 0 Fault and Control Registry

Status: frozen paper protocol; no injection, firmware, trace, or threshold value
is created by this document. `T_F1`, `T_F2`, and `T_F3` are named parameters,
not measured numbers. Their owner is the Phase 5 Pilot lead; values may be
chosen using development Cases only, recorded with rationale and version, and
locked before any holdout access.

## Family inventory contract

For each of F1, F2, and F3, the formal evaluation inventory must contain at
least four independently parameterized **manifested-positive** Cases, one
healthy control, one near-miss, and one wrong-entity control (at least seven
independent Cases per family; 21 total). Each Case has at least three
independently started Captures (at least 63 raw Captures total). The controls do
not consume positive slots. Enabled-but-not-manifested attempts and
acquisition-invalid Captures are retained with their status and excluded from
the corresponding valid Case denominator; neither may be relabelled healthy or
negative. Each family entering holdout also needs structurally distinct
development, validation, and holdout Scenario Templates. A family lacking that
set is limited to within-template parameter generalization with Case-cluster-
isolated holdout; if every family lacks it, formal template-level generalization
is removed and the study is downgraded to a single-platform engineering case study.

Every future scored Case binds an Injection Ledger, Observer Adjudication Record
(OAR), Capture Validity Record (CVR), Capture Capability Manifest (CCM), Capture
Integrity Record (CIR), Evidence Lineage, Diagnostic Relevance Audit, Diagnosis
Verdict, and Evaluation Result. Ledger establishes configuration and identity;
OAR establishes manifestation truth; CVR establishes technical Capture validity;
CCM establishes configured capability; CIR establishes actual integrity; lineage
establishes provenance; and the diagnoser alone assesses fault-specific
relevance and writes the verdict.

## F1: Bounded Task Interference

| Contract item | Registration |
| --- | --- |
| fault_family | `F1_bounded_task_interference` |
| allowed_claim | A specified target task was READY, did not execute within `T_F1`, and a same-core higher-priority task occupied CPU in that finite interval. |
| forbidden_claim | Permanent starvation, scheduler unique root cause, or behavior outside the declared target/core/interval. |
| entities | target task, interfering task(s), core 0, finite ready-but-not-running interval. |
| parameters | target/interferer IDs, priority ordering, workload seed, injection mode, intended interval, `T_F1`. |
| severity_threshold_owner | Phase 5 Pilot lead; development split only; freeze before holdout. |
| observer_manifestation_predicate | Observer sees Case boundary and the declared interference-enable interval; independent timing shows target milestone absent during the declared interval while the interfering workload milestone is present. |
| required_trace_events | `TASK_READY`, `TASK_DISPATCH`/`CTX_SWITCH`, task identity/priority, Case boundary; relevant `LOSS`/`OVERFLOW` where present. |
| healthy_control | Same workload and target, injection disabled; no F1 predicate. |
| near_miss_control | Interference duration/load is intentionally below the frozen F1 manifestation criterion. |
| wrong_entity_control | Same pattern targets a registered non-target task or a different priority relation. |
| enabled_but_not_manifested | Ledger says enabled but Observer predicate fails: retain as non-manifested, exclude from fault-positive denominator, never relabel positive. |
| acquisition_invalid | Missing Case/enable/end marker, unknown alignment bound, corrupt Observer record, or Capture cannot identify required channels/boundaries. |
| OOD conditions | multicore, unregistered priority policy, task identity ambiguity, deadline/queue semantics needed, interval beyond bound, or unregistered RTOS/board/config. |

## F2: Mutex Contention / Long Hold

| Contract item | Registration |
| --- | --- |
| fault_family | `F2_mutex_contention_long_hold` |
| allowed_claim | A named waiter, mutex, and holder have observed wait/hold overlap exceeding `T_F2`. Priority inversion is a pattern label only. |
| forbidden_claim | Full priority-inheritance behavior, unique causal cause, queue/semaphore behavior, or general deadlock claim. |
| entities | waiter task, holder task, one mutex, core 0, lock/unlock and wait boundaries. |
| parameters | waiter/holder/mutex IDs, priority relation, hold mode/duration, workload seed, `T_F2`. |
| severity_threshold_owner | Phase 5 Pilot lead; development split only; freeze before holdout. |
| observer_manifestation_predicate | Independent record shows injection enable, holder critical-section boundary, waiter milestone, and their overlap above the declared criterion. |
| required_trace_events | `SYNC_TRY`, `SYNC_LOCK`, `SYNC_UNLOCK`, `TASK_BLOCK` with mutex wait, `TASK_WAKEUP`, dispatch/context events, integrity events. |
| healthy_control | Same mutex workload with normal hold, no long overlap. |
| near_miss_control | Registered hold/wait overlap below frozen threshold. |
| wrong_entity_control | A different mutex, holder, or waiter is affected while the scored trio remains healthy. |
| enabled_but_not_manifested | Enable recorded but independent overlap predicate absent: retained as non-manifested, not fault-positive truth. |
| acquisition_invalid | Required open/close Observer milestones absent, mutex identity ambiguous, alignment fails, or raw/Observer intervals disagree beyond the stated bound. |
| OOD conditions | queue/sem behavior, unknown mutex implementation, multiple-core ownership, unregistered PI semantics, recursive mutex semantics, or unmatched identity. |

## F3: IRQ Interference

| Contract item | Registration |
| --- | --- |
| fault_family | `F3_irq_interference` |
| allowed_claim | A long ISR or high-frequency IRQ overlaps a specified ready-but-not-running interval and exceeds `T_F3` under the registered predicate. |
| forbidden_claim | IRQ as the unique causal root cause, arbitrary interrupt behavior, or multicore causality. |
| entities | IRQ ID, core 0, target task, ISR entry/exit, ready-but-not-running interval. |
| parameters | IRQ source/rate/service mode, target task, intended interval, workload seed, `T_F3`. |
| severity_threshold_owner | Phase 5 Pilot lead; development split only; freeze before holdout. |
| observer_manifestation_predicate | Independent GPIO/timer record contains IRQ entry/exit or validated surrogate plus target/Case milestones; computed overlap meets declared criterion. |
| required_trace_events | `IRQ_ENTER`, `IRQ_EXIT`, target `TASK_READY`, `TASK_DISPATCH`/`CTX_SWITCH`, integrity events. |
| healthy_control | Same workload with registered IRQ injection disabled. |
| near_miss_control | IRQ rate/service time below the frozen manifestation criterion. |
| wrong_entity_control | A different registered IRQ or non-target task is affected. |
| enabled_but_not_manifested | Ledger enable without Observer interval predicate; retain as non-manifested. |
| acquisition_invalid | No trustworthy IRQ boundaries, Observer channel ambiguity, alignment failure, or loss of mandatory Capture channel. |
| OOD conditions | nested/unidentified IRQ beyond protocol, DMA-only surrogate without validation, multicore routing, unregistered ISR path, or unrelated RTOS/board. |

Controls are first-class Cases, not post-hoc negative examples. The Case and
split rules are in [the hierarchy contract](phase0_case_hierarchy_and_split_contract.md).
The required independent marker semantics for each family are frozen in the
[Observer boundary](phase0_observer_truth_boundary.md); they must be validated
through an independent channel in Phase 1 and are not merely trace events.
The event requirements are grounded in the current dictionary's task,
synchronization, IRQ and integrity definitions
([event dictionary](../../../spec/assets/dictionary.json)); the uC/OS hook is only
a single-core scaffold and requires a target backend
([hook README](../../../collector/hook/ucos3/README.md)).
