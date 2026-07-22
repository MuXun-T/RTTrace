# RTD-Pilot Phase 0 Hardware Feasibility Scorecard

This is a selection method for Phase 1, not a platform selection. No current
asset inventory verifies that any board is available. The uC/OS-III adapter is
only a non-default, single-core scaffold and assumes a target-side backend
([hook README](../../../collector/hook/ucos3/README.md)).

Future feasibility evidence must support separate Capture Capability Manifest
(CCM) configuration facts and Capture Integrity Record (CIR) evidence facts;
neither object exists or is implemented in Phase 0.

## Candidates and scoring

Score each candidate 0 (absent/unverified), 1 (documented but unproven), 2
(demonstrated in the exact candidate configuration). Candidate rows are:

1. current uC/OS-III approach;
2. FreeRTOS single-core Cortex-M alternative; and
3. each other single-core board actually available to the laboratory.

| Dimension | Class | Pass condition |
| --- | --- | --- |
| physical_availability | mandatory pass | Board, cables, power, and ownership/access are inventory-verified. |
| license | mandatory pass | RTOS, BSP, tools, firmware and planned data-release rights are known and compatible. |
| toolchain_reproducibility | mandatory pass | Exact supported build path and version can be documented without an unapproved dependency. |
| GPIO | mandatory pass | Separate observable pins exist for Case, enable, and IRQ markers. |
| hardware_timer | mandatory pass | An independent timer/counter can support epoch/alignment measurement. |
| UART/transport | mandatory pass | Trace transport can be isolated from required Observer channels. |
| SWD/debug | desirable | Debug/recovery access exists without becoming the truth source. |
| priority_control | mandatory pass | Workload can establish registered task priorities. |
| mutex_support | mandatory pass | One identifiable mutex supports F2 without unregistered semantics. |
| controllable_IRQ | mandatory pass | F3 can be enabled/disabled and independently marked. |
| current_hook_reuse | desirable | Existing uC/OS hook mapping can be reused after semantic verification. |
| buffer_pressure | desirable | Capacity/high-watermark state can be measured. |
| natural_overflow | desirable | Natural overflow can be detected and attributed; it is not required to force loss. |
| observer_isolation | mandatory pass | Observer receives independent signals/clock and cannot read diagnosis output. |
| data_release_rights | mandatory pass | Raw/Observer/derived data release or an approved controlled-access route is legally feasible. |
| estimated_engineering_risk | desirable | Risks, owner, and fallback can be stated without assuming completion. |

Disqualifying conditions: no verified board; no independent Observer path; no
legal toolchain/data route; inability to identify task/mutex/IRQ entities;
multicore-only operation; no independent GPIO/timer epoch; or a collector that
cannot report filter/buffer/overflow state. The gates are deliberately staged:

### Gate H1: Phase 1 Planning Entry

Every mandatory item must have documentary or inventory evidence with `score >=
1`. H1 permits only device-list collection, document/license review, and test
card design; it does not permit hardware connection or testing.

### Gate H2: Limited Bench-Test Authorization

H2 requires physical board/cable/tool availability, no obvious basic-license
blocker, reviewed safety/test card, and a paper-feasible independent Observer
channel. A separate user authorization is required before any limited bench
test. The platform is not selected at H2.

### Gate H3: Platform Selection Freeze

Every mandatory item must score `2` from the exact candidate configuration. H3
also requires a verified marker pin map, clean toolchain build, Observer
isolation, constructible F1/F2/F3 minimum workloads, and a reviewed data-release
route. Only H3 permits formal platform selection and later integration. A
candidate that fails a mandatory H3 item is rejected or replaced by a backup;
there is no implicit execution authorization.

## Phase 1 test card

| Item | Required evidence before any implementation/hardware execution |
| --- | --- |
| candidate identity | Board revision, RTOS/version, BSP, firmware baseline, core count, owner/access status. |
| build feasibility | Toolchain/version/license and reproducible clean build instructions. |
| marker feasibility | Pin map and independently validated semantics for F1/F2/F3 markers; no pin conflict. Phase 0 freezes semantics only, not final pins. |
| observer feasibility | Analyzer/timer/serial design, independent clock, expected alignment measurement method. |
| task/mutex/IRQ feasibility | Paper designs for F1/F2/F3 and their controls; no queue/deadline/multicore dependency. |
| collector feasibility | Planned enabled events, filters, capacity, watermark, transport, overflow/sequence measurement. |
| rights feasibility | RTOS/BSP/tool/data/board-vendor notices and release route. |
| decision | `PASS`, `FAIL`, or `DEFER`; evidence references, risks, owner, deadline. |

The current collector supports filters, buffering, overflow snapshots and flush
operations ([API](../../../collector/include/trace_api.h)); its host implementation
rejects filtered events and maintains filter history
([collector](../../../collector/core/trace_collector.cpp)). These are candidates
for a future CCM, not evidence that a board passes this scorecard.
