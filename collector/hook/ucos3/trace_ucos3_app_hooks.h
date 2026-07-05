#ifndef TRACE_UCOS3_APP_HOOKS_H_
#define TRACE_UCOS3_APP_HOOKS_H_

#include <os.h>

#ifdef __cplusplus
extern "C" {
#endif

void Trace_uCOS3_SetAllHooks(void);
void Trace_uCOS3_ClearAllHooks(void);

void Trace_uCOS3_AppTaskCreateHook(OS_TCB* p_tcb);
void Trace_uCOS3_AppTaskDelHook(OS_TCB* p_tcb);
void Trace_uCOS3_AppTaskReturnHook(OS_TCB* p_tcb);
void Trace_uCOS3_AppTaskSwHook(void);

#ifdef __cplusplus
}
#endif

#endif  // TRACE_UCOS3_APP_HOOKS_H_
