#define MICRIUM_SOURCE
#include <os.h>

#include "trace_ucos3_adapter.h"

typedef struct trace_ucos3_ready_source {
  CPU_BOOLEAN valid;
  CPU_BOOLEAN consumed;
  CPU_INT16U wake_src;
  CPU_INT64U obj_id;
} trace_ucos3_ready_source_t;

static trace_handle_t* g_trace_ucos3_handle = (trace_handle_t*)0;
static CPU_INT08U g_trace_ucos3_irq_depth = 0u;
static CPU_INT16U g_trace_ucos3_switch_reason = TRACE_SWITCH_REASON_DISPATCH;
static trace_ucos3_ready_source_t g_trace_ucos3_ready_source = {0u, 0u, 0u, 0u};

static CPU_BOOLEAN trace_ucos3_attached(void) {
  return g_trace_ucos3_handle != (trace_handle_t*)0;
}

static CPU_INT16U trace_ucos3_core_id(void) {
  return 0u;
}

static CPU_INT64U trace_ucos3_now_ns(void) {
#if (OS_CFG_TS_EN > 0u)
  return (CPU_INT64U)OS_TS_GET();
#else
  return 0u;
#endif
}

void trace_ucos3_clear_ready_source(void) {
  g_trace_ucos3_ready_source.valid = 0u;
  g_trace_ucos3_ready_source.consumed = 0u;
  g_trace_ucos3_ready_source.wake_src = 0u;
  g_trace_ucos3_ready_source.obj_id = 0u;
}

void trace_ucos3_finish_ready_source(void) {
  CPU_BOOLEAN consumed;

  consumed = g_trace_ucos3_ready_source.consumed;
  trace_ucos3_clear_ready_source();

  if ((g_trace_ucos3_switch_reason == TRACE_SWITCH_REASON_WAKEUP) &&
      ((OSIntNestingCtr == 0u) || (consumed == 0u))) {
    g_trace_ucos3_switch_reason = TRACE_SWITCH_REASON_DISPATCH;
  }
}

static void trace_ucos3_emit_task_state(trace_task_state_kind_t kind,
                                        CPU_INT32U task_id,
                                        CPU_INT16S prio,
                                        CPU_INT16U reason,
                                        CPU_INT64U obj_id,
                                        CPU_INT32U owner_task_id,
                                        CPU_INT16U wake_src,
                                        CPU_INT32S exit_code) {
  trace_task_state_payload_t payload;

  if (!trace_ucos3_attached()) {
    return;
  }

  payload.timestamp_ns = trace_ucos3_now_ns();
  payload.core_id = trace_ucos3_core_id();
  payload.kind = kind;
  payload.task_id = task_id;
  payload.prio = prio;
  payload.core_hint = trace_ucos3_core_id();
  payload.wait_obj_id = obj_id;
  payload.reason = reason;
  payload.owner_task_id = owner_task_id;
  payload.wake_src = wake_src;
  payload.obj_id = obj_id;
  payload.exit_code = exit_code;
  (void)trace_RecordTaskState(g_trace_ucos3_handle, &payload);
}

static void trace_ucos3_emit_sync(trace_sync_action_t action,
                                  CPU_INT32U task_id,
                                  CPU_INT64U obj_id,
                                  CPU_INT16U obj_type,
                                  CPU_INT64U timeout_ns) {
  trace_sync_payload_t payload;

  if (!trace_ucos3_attached()) {
    return;
  }

  payload.timestamp_ns = trace_ucos3_now_ns();
  payload.core_id = trace_ucos3_core_id();
  payload.task_id = task_id;
  payload.obj_id = obj_id;
  payload.obj_type = obj_type;
  payload.action = action;
  payload.timeout_ns = timeout_ns;
  (void)trace_RecordSync(g_trace_ucos3_handle, &payload);
}

static CPU_INT16U trace_ucos3_ready_queue_len(void) {
  CPU_INT16U ready = 0u;
  OS_PRIO prio;
  OS_TCB* p_tcb;

  for (prio = 0u; prio < (OS_CFG_PRIO_MAX - 1u); ++prio) {
    p_tcb = OSRdyList[prio].HeadPtr;
    while (p_tcb != (OS_TCB*)0) {
      if (p_tcb != &OSIdleTaskTCB) {
        ready++;
      }
      p_tcb = p_tcb->NextPtr;
    }
  }

  return ready;
}

static void trace_ucos3_emit_sched_decision_full(CPU_INT32U selected_task_id, CPU_INT16U reason) {
  trace_sched_decision_payload_t payload;

  if (!trace_ucos3_attached()) {
    return;
  }

  payload.timestamp_ns = trace_ucos3_now_ns();
  payload.core_id = trace_ucos3_core_id();
  payload.selected_task_id = selected_task_id;
  payload.rq_len = trace_ucos3_ready_queue_len();
  payload.reason = reason;
  (void)trace_RecordSchedDecision(g_trace_ucos3_handle, &payload);
}

static void trace_ucos3_emit_ctx_switch(CPU_INT32U prev_task_id, CPU_INT32U next_task_id, CPU_INT16U reason) {
  trace_task_switch_payload_t payload;

  if (!trace_ucos3_attached()) {
    return;
  }

  payload.timestamp_ns = trace_ucos3_now_ns();
  payload.core_id = trace_ucos3_core_id();
  payload.prev_task_id = prev_task_id;
  payload.next_task_id = next_task_id;
  payload.reason = reason;
  (void)trace_RecordTaskSwitch(g_trace_ucos3_handle, &payload);
}

void trace_ucos3_attach(trace_handle_t* handle) {
  g_trace_ucos3_handle = handle;
  g_trace_ucos3_irq_depth = 0u;
  g_trace_ucos3_switch_reason = TRACE_SWITCH_REASON_DISPATCH;
  trace_ucos3_clear_ready_source();
}

void trace_ucos3_detach(void) {
  g_trace_ucos3_handle = (trace_handle_t*)0;
  g_trace_ucos3_irq_depth = 0u;
  g_trace_ucos3_switch_reason = TRACE_SWITCH_REASON_DISPATCH;
  trace_ucos3_clear_ready_source();
}

trace_handle_t* trace_ucos3_handle(void) {
  return g_trace_ucos3_handle;
}

void trace_ucos3_note_switch_reason(uint16_t reason) {
  g_trace_ucos3_switch_reason = reason;
}

void trace_ucos3_note_ready_source(uint16_t wake_src, uint64_t obj_id) {
  g_trace_ucos3_ready_source.valid = 1u;
  g_trace_ucos3_ready_source.consumed = 0u;
  g_trace_ucos3_ready_source.wake_src = wake_src;
  g_trace_ucos3_ready_source.obj_id = obj_id;
}

uint32_t trace_ucos3_task_id(OS_TCB* p_tcb) {
  if ((p_tcb == (OS_TCB*)0) || (p_tcb == &OSIdleTaskTCB)) {
    return TRACE_TASK_ID_IDLE;
  }
#if (OS_CFG_TRACE_EN > 0u)
  if (p_tcb->TaskID == (CPU_ADDR)0u) {
    p_tcb->TaskID = (CPU_ADDR)p_tcb;
  }
  return (uint32_t)p_tcb->TaskID;
#else
  return (uint32_t)(CPU_ADDR)p_tcb;
#endif
}

uint64_t trace_ucos3_mutex_id(OS_MUTEX* p_mutex) {
  if (p_mutex == (OS_MUTEX*)0) {
    return 0u;
  }
#if (OS_CFG_TRACE_EN > 0u)
  if (p_mutex->MutexID == (CPU_ADDR)0u) {
    p_mutex->MutexID = (CPU_ADDR)p_mutex;
  }
  return (uint64_t)p_mutex->MutexID;
#else
  return (uint64_t)(CPU_ADDR)p_mutex;
#endif
}

uint64_t trace_ucos3_sem_id(OS_SEM* p_sem) {
  if (p_sem == (OS_SEM*)0) {
    return 0u;
  }
#if (OS_CFG_TRACE_EN > 0u)
  if (p_sem->SemID == (CPU_ADDR)0u) {
    p_sem->SemID = (CPU_ADDR)p_sem;
  }
  return (uint64_t)p_sem->SemID;
#else
  return (uint64_t)(CPU_ADDR)p_sem;
#endif
}

uint64_t trace_ucos3_queue_id(OS_Q* p_q) {
  if (p_q == (OS_Q*)0) {
    return 0u;
  }
#if (OS_CFG_TRACE_EN > 0u)
  if (p_q->MsgQ.MsgQID == (CPU_ADDR)0u) {
    p_q->MsgQ.MsgQID = (CPU_ADDR)p_q;
  }
  return (uint64_t)p_q->MsgQ.MsgQID;
#else
  return (uint64_t)(CPU_ADDR)p_q;
#endif
}

uint64_t trace_ucos3_msgq_id(OS_MSG_Q* p_msg_q) {
  if (p_msg_q == (OS_MSG_Q*)0) {
    return 0u;
  }
#if (OS_CFG_TRACE_EN > 0u)
  if (p_msg_q->MsgQID == (CPU_ADDR)0u) {
    p_msg_q->MsgQID = (CPU_ADDR)p_msg_q;
  }
  return (uint64_t)p_msg_q->MsgQID;
#else
  return (uint64_t)(CPU_ADDR)p_msg_q;
#endif
}

uint64_t trace_ucos3_flag_id(OS_FLAG_GRP* p_grp) {
  if (p_grp == (OS_FLAG_GRP*)0) {
    return 0u;
  }
#if (OS_CFG_TRACE_EN > 0u)
  if (p_grp->FlagID == (CPU_ADDR)0u) {
    p_grp->FlagID = (CPU_ADDR)p_grp;
  }
  return (uint64_t)p_grp->FlagID;
#else
  return (uint64_t)(CPU_ADDR)p_grp;
#endif
}

uint64_t trace_ucos3_task_sem_id(OS_TCB* p_tcb) {
  if (p_tcb == (OS_TCB*)0) {
    return 0u;
  }
#if (OS_CFG_TRACE_EN > 0u)
  if (p_tcb->SemID == (CPU_ADDR)0u) {
    p_tcb->SemID = (CPU_ADDR)&p_tcb->SemCtr;
  }
  return (uint64_t)p_tcb->SemID;
#else
  return (uint64_t)(CPU_ADDR)&p_tcb->SemCtr;
#endif
}

uint64_t trace_ucos3_task_msgq_id(OS_TCB* p_tcb) {
#if (OS_CFG_TASK_Q_EN > 0u)
  if (p_tcb == (OS_TCB*)0) {
    return 0u;
  }
  return trace_ucos3_msgq_id(&p_tcb->MsgQ);
#else
  (void)p_tcb;
  return 0u;
#endif
}

uint64_t trace_ucos3_pending_obj_id(const OS_TCB* p_tcb) {
  if (p_tcb == (const OS_TCB*)0) {
    return 0u;
  }

  switch (p_tcb->PendOn) {
    case OS_TASK_PEND_ON_MUTEX:
      return trace_ucos3_mutex_id((OS_MUTEX*)p_tcb->PendObjPtr);
    case OS_TASK_PEND_ON_SEM:
      return trace_ucos3_sem_id((OS_SEM*)p_tcb->PendObjPtr);
    case OS_TASK_PEND_ON_Q:
      return trace_ucos3_queue_id((OS_Q*)p_tcb->PendObjPtr);
    case OS_TASK_PEND_ON_FLAG:
      return trace_ucos3_flag_id((OS_FLAG_GRP*)p_tcb->PendObjPtr);
    case OS_TASK_PEND_ON_TASK_SEM:
      return trace_ucos3_task_sem_id((OS_TCB*)p_tcb);
    case OS_TASK_PEND_ON_TASK_Q:
#if (OS_CFG_TASK_Q_EN > 0u)
      return trace_ucos3_task_msgq_id((OS_TCB*)p_tcb);
#else
      return 0u;
#endif
    default:
      return 0u;
  }
}

uint16_t trace_ucos3_pending_obj_type(const OS_TCB* p_tcb) {
  if (p_tcb == (const OS_TCB*)0) {
    return 0u;
  }

  switch (p_tcb->PendOn) {
    case OS_TASK_PEND_ON_MUTEX:
      return TRACE_OBJECT_TYPE_MUTEX;
    case OS_TASK_PEND_ON_SEM:
      return TRACE_OBJECT_TYPE_SEM;
    case OS_TASK_PEND_ON_Q:
      return TRACE_OBJECT_TYPE_QUEUE;
    case OS_TASK_PEND_ON_FLAG:
      return TRACE_OBJECT_TYPE_FLAG;
    case OS_TASK_PEND_ON_TASK_SEM:
      return TRACE_OBJECT_TYPE_TASK_SEM;
    case OS_TASK_PEND_ON_TASK_Q:
#if (OS_CFG_TASK_Q_EN > 0u)
      return TRACE_OBJECT_TYPE_TASK_MSG_Q;
#else
      return 0u;
#endif
    default:
      return 0u;
  }
}

uint32_t trace_ucos3_mutex_owner_task_id(OS_MUTEX* p_mutex) {
  if ((p_mutex == (OS_MUTEX*)0) || (p_mutex->OwnerTCBPtr == (OS_TCB*)0)) {
    return TRACE_TASK_ID_IDLE;
  }
  return trace_ucos3_task_id(p_mutex->OwnerTCBPtr);
}

void trace_ucos3_on_task_create(OS_TCB* p_tcb) {
  trace_ucos3_emit_task_state(
      TRACE_TASK_STATE_READY,
      trace_ucos3_task_id(p_tcb),
      (CPU_INT16S)p_tcb->Prio,
      TRACE_READY_REASON_CREATE,
      0u,
      TRACE_TASK_ID_IDLE,
      0u,
      0);
}

void trace_ucos3_on_task_delete(OS_TCB* p_tcb) {
  if (p_tcb == OSTCBCurPtr) {
    trace_ucos3_note_switch_reason(TRACE_SWITCH_REASON_EXIT);
  }
  trace_ucos3_emit_task_state(
      TRACE_TASK_STATE_EXIT,
      trace_ucos3_task_id(p_tcb),
      (CPU_INT16S)p_tcb->Prio,
      0u,
      0u,
      TRACE_TASK_ID_IDLE,
      0u,
      0);
}

void trace_ucos3_on_task_return(OS_TCB* p_tcb) {
  trace_ucos3_on_task_delete(p_tcb);
}

void trace_ucos3_on_task_ready_event(OS_TCB* p_tcb) {
  if ((p_tcb == (OS_TCB*)0) || (p_tcb == &OSIdleTaskTCB)) {
    return;
  }

  if (g_trace_ucos3_ready_source.valid > 0u) {
    g_trace_ucos3_ready_source.consumed = 1u;
    trace_ucos3_emit_task_state(
        TRACE_TASK_STATE_WAKEUP,
        trace_ucos3_task_id(p_tcb),
        (CPU_INT16S)p_tcb->Prio,
        0u,
        g_trace_ucos3_ready_source.obj_id,
        TRACE_TASK_ID_IDLE,
        g_trace_ucos3_ready_source.wake_src,
        0);
  }
}

void trace_ucos3_on_task_delay(OS_TICK dly_ticks) {
  (void)dly_ticks;
  trace_ucos3_note_switch_reason(TRACE_SWITCH_REASON_BLOCK);
  trace_ucos3_emit_task_state(
      TRACE_TASK_STATE_BLOCK,
      trace_ucos3_task_id(OSTCBCurPtr),
      (CPU_INT16S)OSTCBCurPtr->Prio,
      TRACE_BLOCK_REASON_DELAY,
      0u,
      TRACE_TASK_ID_IDLE,
      0u,
      0);
}

void trace_ucos3_on_task_suspend(OS_TCB* p_tcb) {
  if (p_tcb == OSTCBCurPtr) {
    trace_ucos3_note_switch_reason(TRACE_SWITCH_REASON_BLOCK);
  }
  trace_ucos3_emit_task_state(
      TRACE_TASK_STATE_BLOCK,
      trace_ucos3_task_id(p_tcb),
      (CPU_INT16S)p_tcb->Prio,
      TRACE_BLOCK_REASON_SUSPEND,
      0u,
      TRACE_TASK_ID_IDLE,
      0u,
      0);
}

void trace_ucos3_on_task_resume(OS_TCB* p_tcb) {
  trace_ucos3_note_switch_reason(TRACE_SWITCH_REASON_WAKEUP);
  trace_ucos3_emit_task_state(
      TRACE_TASK_STATE_WAKEUP,
      trace_ucos3_task_id(p_tcb),
      (CPU_INT16S)p_tcb->Prio,
      0u,
      0u,
      TRACE_TASK_ID_IDLE,
      TRACE_WAKE_SOURCE_TASK_RESUME,
      0);
}

void trace_ucos3_on_task_switch(OS_TCB* prev_tcb, OS_TCB* next_tcb) {
  CPU_INT32U prev_task_id;
  CPU_INT32U next_task_id;
  CPU_INT16U reason;

  if (next_tcb == (OS_TCB*)0) {
    return;
  }

  reason = g_trace_ucos3_switch_reason;
  prev_task_id = trace_ucos3_task_id(prev_tcb);
  next_task_id = trace_ucos3_task_id(next_tcb);

  trace_ucos3_emit_task_state(
      TRACE_TASK_STATE_DISPATCH,
      next_task_id,
      (CPU_INT16S)next_tcb->Prio,
      reason,
      0u,
      TRACE_TASK_ID_IDLE,
      0u,
      0);
  trace_ucos3_emit_sched_decision_full(next_task_id, reason);
  trace_ucos3_emit_ctx_switch(prev_task_id, next_task_id, reason);
  g_trace_ucos3_switch_reason = TRACE_SWITCH_REASON_DISPATCH;
}

void trace_ucos3_on_sched_decision(OS_TCB* selected_tcb, uint16_t reason) {
  if ((selected_tcb != (OS_TCB*)0) && (selected_tcb != OSTCBCurPtr)) {
    return;
  }

  if ((reason == TRACE_SWITCH_REASON_IRQ_RETURN) &&
      (g_trace_ucos3_switch_reason != TRACE_SWITCH_REASON_DISPATCH)) {
    reason = g_trace_ucos3_switch_reason;
  }

  trace_ucos3_emit_sched_decision_full(trace_ucos3_task_id(selected_tcb), reason);
  g_trace_ucos3_switch_reason = TRACE_SWITCH_REASON_DISPATCH;
}

void trace_ucos3_on_sync_try(OS_TCB* p_tcb, uint64_t obj_id, uint16_t obj_type, uint64_t timeout_ns) {
  trace_ucos3_emit_sync(
      TRACE_SYNC_ACTION_TRY,
      trace_ucos3_task_id(p_tcb),
      obj_id,
      obj_type,
      timeout_ns);
}

void trace_ucos3_on_sync_lock(OS_TCB* p_tcb, uint64_t obj_id, uint16_t obj_type) {
  trace_ucos3_emit_sync(
      TRACE_SYNC_ACTION_LOCK,
      trace_ucos3_task_id(p_tcb),
      obj_id,
      obj_type,
      0u);
}

void trace_ucos3_on_sync_unlock(OS_TCB* p_tcb, uint64_t obj_id, uint16_t obj_type) {
  trace_ucos3_emit_sync(
      TRACE_SYNC_ACTION_UNLOCK,
      trace_ucos3_task_id(p_tcb),
      obj_id,
      obj_type,
      0u);
}

void trace_ucos3_on_task_block(OS_TCB* p_tcb, uint16_t reason, uint64_t obj_id, uint32_t owner_task_id) {
  if (p_tcb == OSTCBCurPtr) {
    trace_ucos3_note_switch_reason(TRACE_SWITCH_REASON_BLOCK);
  }
  trace_ucos3_emit_task_state(
      TRACE_TASK_STATE_BLOCK,
      trace_ucos3_task_id(p_tcb),
      (CPU_INT16S)p_tcb->Prio,
      reason,
      obj_id,
      owner_task_id,
      0u,
      0);
}

void trace_ucos3_irq_enter(uint16_t irq_id) {
  trace_irq_payload_t payload;

  if (!trace_ucos3_attached()) {
    return;
  }

  g_trace_ucos3_irq_depth++;
  payload.timestamp_ns = trace_ucos3_now_ns();
  payload.core_id = trace_ucos3_core_id();
  payload.irq_id = irq_id;
  payload.nesting_depth = g_trace_ucos3_irq_depth;
  payload.entering = 1u;
  (void)trace_RecordIRQ(g_trace_ucos3_handle, &payload);
}

void trace_ucos3_irq_exit(uint16_t irq_id) {
  trace_irq_payload_t payload;

  if (!trace_ucos3_attached()) {
    return;
  }

  payload.timestamp_ns = trace_ucos3_now_ns();
  payload.core_id = trace_ucos3_core_id();
  payload.irq_id = irq_id;
  payload.nesting_depth = (g_trace_ucos3_irq_depth == 0u) ? 1u : g_trace_ucos3_irq_depth;
  payload.entering = 0u;
  (void)trace_RecordIRQ(g_trace_ucos3_handle, &payload);

  if (g_trace_ucos3_irq_depth > 0u) {
    g_trace_ucos3_irq_depth--;
  }
  if ((g_trace_ucos3_irq_depth == 0u) &&
      (g_trace_ucos3_switch_reason == TRACE_SWITCH_REASON_DISPATCH)) {
    trace_ucos3_note_switch_reason(TRACE_SWITCH_REASON_IRQ_RETURN);
  }
}
