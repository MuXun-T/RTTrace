#include "trace_freertos_hooks.h"

static const rtd_p4_freertos_hook_sink_t* g_sink;

void rtd_p4_freertos_set_hook_sink(const rtd_p4_freertos_hook_sink_t* sink) { g_sink = sink; }
void rtd_p4_freertos_on_task_switch(uint32_t previous_task, uint32_t next_task, uint16_t reason, uint64_t timestamp) { if (g_sink && g_sink->task_switch) g_sink->task_switch(previous_task, next_task, reason, timestamp); }
void rtd_p4_freertos_on_task_state(uint32_t task, uint16_t state, uint64_t object, uint16_t reason, uint64_t timestamp) { if (g_sink && g_sink->task_state) g_sink->task_state(task, state, object, reason, timestamp); }
void rtd_p4_freertos_on_mutex(uint32_t task, uint64_t object, uint16_t action, uint64_t timestamp) { if (g_sink && g_sink->mutex) g_sink->mutex(task, object, action, timestamp); }
void rtd_p4_freertos_on_irq(uint16_t irq, uint8_t entering, uint8_t nesting, uint64_t timestamp) { if (g_sink && g_sink->irq) g_sink->irq(irq, entering, nesting, timestamp); }
void rtd_p4_freertos_on_shared_epoch(const char* epoch_id, uint64_t timestamp) { if (g_sink && g_sink->shared_epoch) g_sink->shared_epoch(epoch_id, timestamp); }
void rtd_p4_freertos_on_counter_report(void) { if (g_sink && g_sink->counter_report) g_sink->counter_report(); }
