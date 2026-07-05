#define MICRIUM_SOURCE
#include <os.h>

#include "os_app_hooks.h"
#include "trace_ucos3_adapter.h"

void App_OS_SetAllHooks(void) {
#if (OS_CFG_APP_HOOKS_EN > 0u)
  CPU_SR_ALLOC();

  CPU_CRITICAL_ENTER();
  OS_AppTaskReturnHookPtr = App_OS_TaskReturnHook;
  OS_AppTaskSwHookPtr = App_OS_TaskSwHook;
  CPU_CRITICAL_EXIT();
#endif
}

void App_OS_ClrAllHooks(void) {
#if (OS_CFG_APP_HOOKS_EN > 0u)
  CPU_SR_ALLOC();

  CPU_CRITICAL_ENTER();
  OS_AppTaskReturnHookPtr = (OS_APP_HOOK_TCB)0;
  OS_AppTaskSwHookPtr = (OS_APP_HOOK_VOID)0;
  CPU_CRITICAL_EXIT();
#endif
}

void App_OS_TaskReturnHook(OS_TCB* p_tcb) {
  trace_ucos3_on_task_return(p_tcb);
}

void App_OS_TaskSwHook(void) {
  trace_ucos3_on_task_switch(OSTCBCurPtr, OSTCBHighRdyPtr);
}
