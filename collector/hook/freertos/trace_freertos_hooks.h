#ifndef RTD_P4_FREERTOS_HOOKS_H_
#define RTD_P4_FREERTOS_HOOKS_H_

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Application-owned adapter boundary. It intentionally avoids FreeRTOS
 * internal headers so host compilation checks only the hook contract. */
typedef struct rtd_p4_freertos_hook_sink {
  void (*task_switch)(uint32_t previous_task, uint32_t next_task, uint16_t reason, uint64_t timestamp);
  void (*task_state)(uint32_t task, uint16_t state, uint64_t object, uint16_t reason, uint64_t timestamp);
  void (*mutex)(uint32_t task, uint64_t object, uint16_t action, uint64_t timestamp);
  void (*irq)(uint16_t irq, uint8_t entering, uint8_t nesting, uint64_t timestamp);
  void (*shared_epoch)(const char* epoch_id, uint64_t timestamp);
  void (*counter_report)(void);
} rtd_p4_freertos_hook_sink_t;

void rtd_p4_freertos_set_hook_sink(const rtd_p4_freertos_hook_sink_t* sink);
void rtd_p4_freertos_on_task_switch(uint32_t previous_task, uint32_t next_task, uint16_t reason, uint64_t timestamp);
void rtd_p4_freertos_on_task_state(uint32_t task, uint16_t state, uint64_t object, uint16_t reason, uint64_t timestamp);
void rtd_p4_freertos_on_mutex(uint32_t task, uint64_t object, uint16_t action, uint64_t timestamp);
void rtd_p4_freertos_on_irq(uint16_t irq, uint8_t entering, uint8_t nesting, uint64_t timestamp);
void rtd_p4_freertos_on_shared_epoch(const char* epoch_id, uint64_t timestamp);
void rtd_p4_freertos_on_counter_report(void);

#ifdef __cplusplus
}
#endif

#endif  // RTD_P4_FREERTOS_HOOKS_H_
