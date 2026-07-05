#ifndef OS_TRACE_EVENTS_H
#define OS_TRACE_EVENTS_H

#include "trace_ucos3_adapter.h"

#if (OS_CFG_TICK_EN > 0u)
#define TRACE_UCOS3_TICKS_TO_NS(timeout_ticks) \
  ((uint64_t)(timeout_ticks) * 1000000000ull / (uint64_t)OSCfg_TickRate_Hz)
#else
#define TRACE_UCOS3_TICKS_TO_NS(timeout_ticks) ((uint64_t)(timeout_ticks))
#endif

/*
 * This file is included by uC/OS-III's os_trace.h when OS_CFG_TRACE_EN=1.
 * The mapping intentionally favors scheduler-meaningful events over
 * third-party recorder compatibility.
 */

#define OS_TRACE_TASK_CREATE(p_tcb) \
  do { \
    trace_ucos3_on_task_create((p_tcb)); \
  } while (0)

#define OS_TRACE_TASK_DEL(p_tcb) \
  do { \
    trace_ucos3_on_task_delete((p_tcb)); \
  } while (0)

#define OS_TRACE_TASK_READY(p_tcb) \
  do { \
    trace_ucos3_on_task_ready_event((p_tcb)); \
  } while (0)

#define OS_TRACE_TASK_SWITCHED_IN(p_tcb) \
  do { \
    trace_ucos3_on_sched_decision((p_tcb), TRACE_SWITCH_REASON_IRQ_RETURN); \
  } while (0)

#define OS_TRACE_TASK_DLY(dly_ticks) \
  do { \
    trace_ucos3_on_task_delay((dly_ticks)); \
  } while (0)

#define OS_TRACE_TASK_SUSPEND(p_tcb) \
  do { \
    trace_ucos3_on_task_suspend((p_tcb)); \
  } while (0)

#define OS_TRACE_TASK_RESUME(p_tcb) \
  do { \
    trace_ucos3_on_task_resume((p_tcb)); \
  } while (0)

#define OS_TRACE_TASK_PREEMPT(p_tcb) \
  do { \
    (void)(p_tcb); \
    trace_ucos3_note_switch_reason(TRACE_SWITCH_REASON_PREEMPT); \
  } while (0)

#define OS_TRACE_TASK_MSG_Q_CREATE(p_msg_q, p_name) \
  do { \
    (void)(p_name); \
    (void)trace_ucos3_msgq_id((p_msg_q)); \
  } while (0)

#define OS_TRACE_TASK_MSG_Q_POST(p_msg_q) \
  do { \
    trace_ucos3_note_ready_source(TRACE_WAKE_SOURCE_RESOURCE_RELEASE, trace_ucos3_msgq_id((p_msg_q))); \
    trace_ucos3_note_switch_reason(TRACE_SWITCH_REASON_WAKEUP); \
  } while (0)

#define OS_TRACE_TASK_MSG_Q_POST_EXIT(RetVal) \
  do { \
    (void)(RetVal); \
    trace_ucos3_finish_ready_source(); \
  } while (0)

#define OS_TRACE_TASK_MSG_Q_PEND_ENTER(p_msg_q, timeout, opt, p_msg_size, p_ts) \
  do { \
    (void)(opt); \
    (void)(p_msg_size); \
    (void)(p_ts); \
    trace_ucos3_on_sync_try( \
        OSTCBCurPtr, \
        trace_ucos3_msgq_id((p_msg_q)), \
        TRACE_OBJECT_TYPE_TASK_MSG_Q, \
        TRACE_UCOS3_TICKS_TO_NS(timeout)); \
  } while (0)

#define OS_TRACE_TASK_MSG_Q_PEND_BLOCK(p_msg_q) \
  do { \
    trace_ucos3_on_task_block( \
        OSTCBCurPtr, \
        TRACE_BLOCK_REASON_TASK_MSG_Q_WAIT, \
        trace_ucos3_msgq_id((p_msg_q)), \
        TRACE_TASK_ID_IDLE); \
  } while (0)

#define OS_TRACE_TASK_SEM_CREATE(p_tcb, p_name) \
  do { \
    (void)(p_name); \
    (void)trace_ucos3_task_sem_id((p_tcb)); \
  } while (0)

#define OS_TRACE_TASK_SEM_POST(p_tcb) \
  do { \
    trace_ucos3_note_ready_source(TRACE_WAKE_SOURCE_RESOURCE_RELEASE, trace_ucos3_task_sem_id((p_tcb))); \
    trace_ucos3_note_switch_reason(TRACE_SWITCH_REASON_WAKEUP); \
  } while (0)

#define OS_TRACE_TASK_SEM_POST_EXIT(RetVal) \
  do { \
    (void)(RetVal); \
    trace_ucos3_finish_ready_source(); \
  } while (0)

#define OS_TRACE_TASK_SEM_PEND_ENTER(p_tcb, timeout, opt, p_ts) \
  do { \
    (void)(opt); \
    (void)(p_ts); \
    trace_ucos3_on_sync_try( \
        (p_tcb), \
        trace_ucos3_task_sem_id((p_tcb)), \
        TRACE_OBJECT_TYPE_TASK_SEM, \
        TRACE_UCOS3_TICKS_TO_NS(timeout)); \
  } while (0)

#define OS_TRACE_TASK_SEM_PEND(p_tcb) \
  do { \
    trace_ucos3_on_sync_lock((p_tcb), trace_ucos3_task_sem_id((p_tcb)), TRACE_OBJECT_TYPE_TASK_SEM); \
  } while (0)

#define OS_TRACE_TASK_SEM_PEND_BLOCK(p_tcb) \
  do { \
    trace_ucos3_on_task_block( \
        (p_tcb), \
        TRACE_BLOCK_REASON_TASK_SEM_WAIT, \
        trace_ucos3_task_sem_id((p_tcb)), \
        TRACE_TASK_ID_IDLE); \
  } while (0)

#define OS_TRACE_MUTEX_CREATE(p_mutex, p_name) \
  do { \
    (void)(p_name); \
    (void)trace_ucos3_mutex_id((p_mutex)); \
  } while (0)

#define OS_TRACE_MUTEX_POST(p_mutex) \
  do { \
    trace_ucos3_on_sync_unlock(OSTCBCurPtr, trace_ucos3_mutex_id((p_mutex)), TRACE_OBJECT_TYPE_MUTEX); \
    trace_ucos3_note_ready_source(TRACE_WAKE_SOURCE_RESOURCE_RELEASE, trace_ucos3_mutex_id((p_mutex))); \
    trace_ucos3_note_switch_reason(TRACE_SWITCH_REASON_WAKEUP); \
  } while (0)

#define OS_TRACE_MUTEX_POST_EXIT(RetVal) \
  do { \
    (void)(RetVal); \
    trace_ucos3_finish_ready_source(); \
  } while (0)

#define OS_TRACE_MUTEX_PEND(p_mutex) \
  do { \
    trace_ucos3_on_sync_lock(OSTCBCurPtr, trace_ucos3_mutex_id((p_mutex)), TRACE_OBJECT_TYPE_MUTEX); \
  } while (0)

#define OS_TRACE_MUTEX_PEND_ENTER(p_mutex, timeout, opt, p_ts) \
  do { \
    (void)(opt); \
    (void)(p_ts); \
    trace_ucos3_on_sync_try( \
        OSTCBCurPtr, \
        trace_ucos3_mutex_id((p_mutex)), \
        TRACE_OBJECT_TYPE_MUTEX, \
        TRACE_UCOS3_TICKS_TO_NS(timeout)); \
  } while (0)

#define OS_TRACE_MUTEX_PEND_BLOCK(p_mutex) \
  do { \
    trace_ucos3_on_task_block( \
        OSTCBCurPtr, \
        TRACE_BLOCK_REASON_MUTEX_WAIT, \
        trace_ucos3_mutex_id((p_mutex)), \
        trace_ucos3_mutex_owner_task_id((p_mutex))); \
  } while (0)

#define OS_TRACE_SEM_CREATE(p_sem, p_name) \
  do { \
    (void)(p_name); \
    (void)trace_ucos3_sem_id((p_sem)); \
  } while (0)

#define OS_TRACE_SEM_POST(p_sem) \
  do { \
    trace_ucos3_on_sync_unlock(OSTCBCurPtr, trace_ucos3_sem_id((p_sem)), TRACE_OBJECT_TYPE_SEM); \
    trace_ucos3_note_ready_source(TRACE_WAKE_SOURCE_RESOURCE_RELEASE, trace_ucos3_sem_id((p_sem))); \
    trace_ucos3_note_switch_reason(TRACE_SWITCH_REASON_WAKEUP); \
  } while (0)

#define OS_TRACE_SEM_POST_EXIT(RetVal) \
  do { \
    (void)(RetVal); \
    trace_ucos3_finish_ready_source(); \
  } while (0)

#define OS_TRACE_SEM_PEND(p_sem) \
  do { \
    trace_ucos3_on_sync_lock(OSTCBCurPtr, trace_ucos3_sem_id((p_sem)), TRACE_OBJECT_TYPE_SEM); \
  } while (0)

#define OS_TRACE_SEM_PEND_ENTER(p_sem, timeout, opt, p_ts) \
  do { \
    (void)(opt); \
    (void)(p_ts); \
    trace_ucos3_on_sync_try( \
        OSTCBCurPtr, \
        trace_ucos3_sem_id((p_sem)), \
        TRACE_OBJECT_TYPE_SEM, \
        TRACE_UCOS3_TICKS_TO_NS(timeout)); \
  } while (0)

#define OS_TRACE_SEM_PEND_BLOCK(p_sem) \
  do { \
    trace_ucos3_on_task_block( \
        OSTCBCurPtr, \
        TRACE_BLOCK_REASON_SEM_WAIT, \
        trace_ucos3_sem_id((p_sem)), \
        TRACE_TASK_ID_IDLE); \
  } while (0)

#define OS_TRACE_Q_CREATE(p_q, p_name) \
  do { \
    (void)(p_name); \
    (void)trace_ucos3_queue_id((p_q)); \
  } while (0)

#define OS_TRACE_Q_POST(p_q) \
  do { \
    trace_ucos3_note_ready_source(TRACE_WAKE_SOURCE_RESOURCE_RELEASE, trace_ucos3_queue_id((p_q))); \
    trace_ucos3_note_switch_reason(TRACE_SWITCH_REASON_WAKEUP); \
  } while (0)

#define OS_TRACE_Q_POST_EXIT(RetVal) \
  do { \
    (void)(RetVal); \
    trace_ucos3_finish_ready_source(); \
  } while (0)

#define OS_TRACE_Q_PEND_ENTER(p_q, timeout, opt, p_msg_size, p_ts) \
  do { \
    (void)(opt); \
    (void)(p_msg_size); \
    (void)(p_ts); \
    trace_ucos3_on_sync_try( \
        OSTCBCurPtr, \
        trace_ucos3_queue_id((p_q)), \
        TRACE_OBJECT_TYPE_QUEUE, \
        TRACE_UCOS3_TICKS_TO_NS(timeout)); \
  } while (0)

#define OS_TRACE_Q_PEND_BLOCK(p_q) \
  do { \
    trace_ucos3_on_task_block( \
        OSTCBCurPtr, \
        TRACE_BLOCK_REASON_QUEUE_WAIT, \
        trace_ucos3_queue_id((p_q)), \
        TRACE_TASK_ID_IDLE); \
  } while (0)

#define OS_TRACE_FLAG_CREATE(p_grp, p_name) \
  do { \
    (void)(p_name); \
    (void)trace_ucos3_flag_id((p_grp)); \
  } while (0)

#define OS_TRACE_FLAG_POST(p_grp) \
  do { \
    trace_ucos3_note_ready_source(TRACE_WAKE_SOURCE_RESOURCE_RELEASE, trace_ucos3_flag_id((p_grp))); \
    trace_ucos3_note_switch_reason(TRACE_SWITCH_REASON_WAKEUP); \
  } while (0)

#define OS_TRACE_FLAG_POST_EXIT(RetVal) \
  do { \
    (void)(RetVal); \
    trace_ucos3_finish_ready_source(); \
  } while (0)

#define OS_TRACE_FLAG_PEND_ENTER(p_grp, flags, timeout, opt, p_ts) \
  do { \
    (void)(flags); \
    (void)(opt); \
    (void)(p_ts); \
    trace_ucos3_on_sync_try( \
        OSTCBCurPtr, \
        trace_ucos3_flag_id((p_grp)), \
        TRACE_OBJECT_TYPE_FLAG, \
        TRACE_UCOS3_TICKS_TO_NS(timeout)); \
  } while (0)

#define OS_TRACE_FLAG_PEND_BLOCK(p_grp) \
  do { \
    trace_ucos3_on_task_block( \
        OSTCBCurPtr, \
        TRACE_BLOCK_REASON_FLAG_WAIT, \
        trace_ucos3_flag_id((p_grp)), \
        TRACE_TASK_ID_IDLE); \
  } while (0)

#endif  // OS_TRACE_EVENTS_H
