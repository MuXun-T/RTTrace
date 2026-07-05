#include "trace_ucos3_app_hooks.h"

#include "trace_ucos3_adapter.h"

void Trace_uCOS3_SetAllHooks(void) {
#if OS_CFG_APP_HOOKS_EN > 0u
  CPU_SR_ALLOC();

  CPU_CRITICAL_ENTER();
  OS_AppTaskReturnHookPtr = Trace_uCOS3_AppTaskReturnHook;
  OS_AppTaskSwHookPtr = Trace_uCOS3_AppTaskSwHook;
  CPU_CRITICAL_EXIT();
#endif
}

void Trace_uCOS3_ClearAllHooks(void) {
#if OS_CFG_APP_HOOKS_EN > 0u
  CPU_SR_ALLOC();

  CPU_CRITICAL_ENTER();
  OS_AppTaskReturnHookPtr = (OS_APP_HOOK_TCB)0;
  OS_AppTaskSwHookPtr = (OS_APP_HOOK_VOID)0;
  CPU_CRITICAL_EXIT();
#endif
}

void Trace_uCOS3_AppTaskCreateHook(OS_TCB* p_tcb) {
  (void)p_tcb;
}

void Trace_uCOS3_AppTaskDelHook(OS_TCB* p_tcb) {
  (void)p_tcb;
}

void Trace_uCOS3_AppTaskReturnHook(OS_TCB* p_tcb) {
  trace_ucos3_on_task_return(p_tcb);
}

void Trace_uCOS3_AppTaskSwHook(void) {
  trace_ucos3_on_task_switch();
}
