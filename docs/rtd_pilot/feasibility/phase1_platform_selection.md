# Phase 1 selected platform

Status: `SELECTED`. The project owner completed `SELF_REVIEW` and approved the
exact H3 configuration under `OWNER_APPROVAL`. The binding decision is
`phase1_platform_selection_decision.md`.

Selected H3 platform: STM32F103ZET6 + FreeRTOS from the locally audited
STM32CubeF1 tree. The source root is
`/home/zzq/embedded/sdk/STM32CubeF1/Middlewares/Third_Party/FreeRTOS`, Cube
commit `d12e75247d5bcedc734f829b394517ab4c2726e3` (CubeF1 `v1.8.7`, recorded
in `sdk/STM32CubeF1_VERSION.txt`), using
`Source/portable/GCC/ARM_CM3/{port.c,portmacro.h}` and `heap_4.c`. This
directly supports Cortex-M3, GCC, tasks, mutexes, scheduler tick, and IRQ
priority controls.

Backup: repo-owned `RTOS/uC-OS3-develop`, Apache-2.0, ARMv7-M GNU port at `Ports/ARM-Cortex-M/ARMv7-M/GNU`. Its target collector hook scaffold exists at `collector/hook/ucos3`, but no target backend is claimed by this phase. It is not selected because FreeRTOS is already present in the audited board SDK and has the smaller initial integration surface.

Bare metal is a calibration/control baseline only. It is not an RTOS platform and cannot establish task/mutex conclusions.

The exact H3 configuration has retained Capture A/B Observer evidence, a
hash-bound collector UART validator with capacity, sequence-gap, and overflow
evidence, and all eleven mandatory H3 scorecard items marked `PASS`. The data
route is `OWNER_CONTROLLED_RESEARCH_ACCESS` with
`data_release_rights = PASS`; it does not require automatic public release.
The eight-channel smoke map and H3 marker-capability evidence establish only
the listed Phase 1 target-side semantics, not a Phase 2 Case, injection, or
manifestation claim.

This selection is a Phase 1 feasibility freeze input. It does not claim a
recorder backend, parser integration, diagnostics, CCM, Ledger, lineage,
injection truth, a formal dataset, or a paper experiment.
