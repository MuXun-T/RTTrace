#ifndef OS_APP_HOOKS_H
#define OS_APP_HOOKS_H

#include <os.h>

#ifdef __cplusplus
extern "C" {
#endif

void App_OS_SetAllHooks(void);
void App_OS_ClrAllHooks(void);
void App_OS_TaskReturnHook(OS_TCB* p_tcb);
void App_OS_TaskSwHook(void);

#ifdef __cplusplus
}
#endif

#endif  // OS_APP_HOOKS_H
