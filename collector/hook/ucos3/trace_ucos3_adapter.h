#ifndef TRACE_UCOS3_ADAPTER_H_
#define TRACE_UCOS3_ADAPTER_H_

#include "trace_api.h"

#include <os.h>

#ifdef __cplusplus
extern "C" {
#endif

void trace_ucos3_attach(trace_handle_t* handle);
void trace_ucos3_detach(void);
trace_handle_t* trace_ucos3_handle(void);

void trace_ucos3_irq_enter(uint16_t irq_id);
void trace_ucos3_irq_exit(uint16_t irq_id);

void trace_ucos3_clear_ready_source(void);
void trace_ucos3_finish_ready_source(void);
void trace_ucos3_note_switch_reason(uint16_t reason);
void trace_ucos3_note_ready_source(uint16_t wake_src, uint64_t obj_id);

void trace_ucos3_on_task_create(OS_TCB* p_tcb);
void trace_ucos3_on_task_delete(OS_TCB* p_tcb);
void trace_ucos3_on_task_return(OS_TCB* p_tcb);
void trace_ucos3_on_task_ready_event(OS_TCB* p_tcb);
void trace_ucos3_on_task_delay(OS_TICK dly_ticks);
void trace_ucos3_on_task_suspend(OS_TCB* p_tcb);
void trace_ucos3_on_task_resume(OS_TCB* p_tcb);
void trace_ucos3_on_task_switch(OS_TCB* prev_tcb, OS_TCB* next_tcb);
void trace_ucos3_on_sched_decision(OS_TCB* selected_tcb, uint16_t reason);

void trace_ucos3_on_sync_try(OS_TCB* p_tcb, uint64_t obj_id, uint16_t obj_type, uint64_t timeout_ns);
void trace_ucos3_on_sync_lock(OS_TCB* p_tcb, uint64_t obj_id, uint16_t obj_type);
void trace_ucos3_on_sync_unlock(OS_TCB* p_tcb, uint64_t obj_id, uint16_t obj_type);
void trace_ucos3_on_task_block(OS_TCB* p_tcb, uint16_t reason, uint64_t obj_id, uint32_t owner_task_id);

uint32_t trace_ucos3_task_id(OS_TCB* p_tcb);
uint64_t trace_ucos3_mutex_id(OS_MUTEX* p_mutex);
uint64_t trace_ucos3_sem_id(OS_SEM* p_sem);
uint64_t trace_ucos3_queue_id(OS_Q* p_q);
uint64_t trace_ucos3_msgq_id(OS_MSG_Q* p_msg_q);
uint64_t trace_ucos3_flag_id(OS_FLAG_GRP* p_grp);
uint64_t trace_ucos3_task_sem_id(OS_TCB* p_tcb);
uint64_t trace_ucos3_task_msgq_id(OS_TCB* p_tcb);
uint64_t trace_ucos3_pending_obj_id(const OS_TCB* p_tcb);
uint16_t trace_ucos3_pending_obj_type(const OS_TCB* p_tcb);
uint32_t trace_ucos3_mutex_owner_task_id(OS_MUTEX* p_mutex);

#ifdef __cplusplus
}
#endif

#endif  // TRACE_UCOS3_ADAPTER_H_
