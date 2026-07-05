# uC/OS-III Trace Adapter

This directory contains the repo-owned glue layer for wiring `uC/OS-III`
kernel trace points into the frozen `trace_*` collector API used by the
rest of this repository.

It is intentionally not part of the default host build.

## Integration Steps

1. Add this directory to the target include path ahead of any third-party
   trace recorder package so `os_trace_events.h` resolves to this adapter.
2. Compile these files into the target project:
   - `trace_ucos3_adapter.c`
   - `os_app_hooks.c`
3. Enable the relevant uC/OS-III switches:
   - `OS_CFG_TRACE_EN = 1`
   - `OS_CFG_DBG_EN = 1`
   - `OS_CFG_APP_HOOKS_EN = 1`
4. After `trace_Init()` and `trace_Enable()`, call:
   - `trace_ucos3_attach(handle);`
   - `App_OS_SetAllHooks();`
5. Wrap board ISRs with:
   - `trace_ucos3_irq_enter(<irq_id>);`
   - `trace_ucos3_irq_exit(<irq_id>);`

## Event Mapping

- `OS_TRACE_TASK_CREATE` -> `TASK_READY(reason=create)`
- `OS_TRACE_TASK_READY` -> `TASK_WAKEUP` for resource-release paths
- `OS_TRACE_TASK_DLY` -> `TASK_BLOCK(reason=delay)`
- `OS_TRACE_TASK_SUSPEND` -> `TASK_BLOCK(reason=suspend)`
- `OS_TRACE_TASK_RESUME` -> `TASK_WAKEUP(wake_src=task_resume)`
- `OS_TRACE_MUTEX/SEM/Q/FLAG/TASK_*_PEND_ENTER` -> `SYNC_TRY`
- `OS_TRACE_MUTEX/SEM/Q/FLAG/TASK_*_PEND_BLOCK` -> `TASK_BLOCK`
- `OS_TRACE_MUTEX/SEM/Q/FLAG/TASK_*_POST` -> primes a resource-release wakeup
- `App_OS_TaskSwHook` -> `TASK_DISPATCH + SCHED_DECISION + CTX_SWITCH`
- `OS_TRACE_TASK_SWITCHED_IN` -> same-task `SCHED_DECISION`; duplicate port-side switched-in callbacks are ignored
- `OS_TRACE_TASK_DEL` / task return -> `TASK_EXIT`
- BSP wrappers -> `IRQ_ENTER / IRQ_EXIT`

## Notes

- The adapter is single-core by default and reports `core_id = 0`.
- uC/OS-III trace ID fields are used as the outward-facing task/object IDs.
  When a field is still zero, the adapter seeds it from the object address as a
  fallback. Real products can replace that seeding policy with application IDs.
- Resource-release wake hints stay live only for the enclosing `*_POST` call.
  This prevents a successful post with no waiting tasks from contaminating the
  next unrelated `TASK_READY`.
- Delay and timeout wakeups are not emitted as standalone `TASK_WAKEUP` events
  by this portable adapter because upstream trace hooks do not expose the
  original ready cause precisely enough without deeper kernel changes.
- This adapter assumes a target-side `trace_*` backend is available. The host
  `collector/core/trace_collector.cpp` remains the reference backend for local
  simulation and format verification.
