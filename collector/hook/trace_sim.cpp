#include "trace_api.h"

#include <cstdlib>
#include <iostream>
#include <string>

namespace {

trace_status_t EmitDemo(trace_handle_t* handle) {
  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1'000;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 1;
  ready.prio = 10;
  ready.core_hint = 0;
  ready.reason = TRACE_READY_REASON_CREATE;
  if (trace_RecordTaskState(handle, &ready) != TRACE_STATUS_OK) {
    return TRACE_STATUS_INTERNAL_ERROR;
  }

  trace_task_state_payload_t dispatch{};
  dispatch.timestamp_ns = 1'100;
  dispatch.core_id = 0;
  dispatch.kind = TRACE_TASK_STATE_DISPATCH;
  dispatch.task_id = 1;
  dispatch.prio = 10;
  dispatch.reason = TRACE_SWITCH_REASON_PREEMPT;
  if (trace_RecordTaskState(handle, &dispatch) != TRACE_STATUS_OK) {
    return TRACE_STATUS_INTERNAL_ERROR;
  }

  trace_sched_decision_payload_t decision{};
  decision.timestamp_ns = 1'110;
  decision.core_id = 0;
  decision.selected_task_id = 1;
  decision.rq_len = 1;
  decision.reason = TRACE_SWITCH_REASON_DISPATCH;
  if (trace_RecordSchedDecision(handle, &decision) != TRACE_STATUS_OK) {
    return TRACE_STATUS_INTERNAL_ERROR;
  }

  trace_task_switch_payload_t sw{};
  sw.timestamp_ns = 1'120;
  sw.core_id = 0;
  sw.prev_task_id = TRACE_TASK_ID_IDLE;
  sw.next_task_id = 1;
  sw.reason = TRACE_SWITCH_REASON_PREEMPT;
  if (trace_RecordTaskSwitch(handle, &sw) != TRACE_STATUS_OK) {
    return TRACE_STATUS_INTERNAL_ERROR;
  }

  trace_sync_payload_t sync_try{};
  sync_try.timestamp_ns = 1'200;
  sync_try.core_id = 0;
  sync_try.task_id = 2;
  sync_try.obj_id = 0xABC;
  sync_try.obj_type = TRACE_OBJECT_TYPE_MUTEX;
  sync_try.action = TRACE_SYNC_ACTION_TRY;
  sync_try.timeout_ns = 50'000;
  if (trace_RecordSync(handle, &sync_try) != TRACE_STATUS_OK) {
    return TRACE_STATUS_INTERNAL_ERROR;
  }

  trace_task_state_payload_t block{};
  block.timestamp_ns = 1'220;
  block.core_id = 0;
  block.kind = TRACE_TASK_STATE_BLOCK;
  block.task_id = 2;
  block.wait_obj_id = 0xABC;
  block.reason = TRACE_BLOCK_REASON_MUTEX_WAIT;
  block.owner_task_id = 1;
  if (trace_RecordTaskState(handle, &block) != TRACE_STATUS_OK) {
    return TRACE_STATUS_INTERNAL_ERROR;
  }

  trace_irq_payload_t irq_enter{};
  irq_enter.timestamp_ns = 1'250;
  irq_enter.core_id = 0;
  irq_enter.irq_id = 7;
  irq_enter.nesting_depth = 1;
  irq_enter.entering = 1;
  if (trace_RecordIRQ(handle, &irq_enter) != TRACE_STATUS_OK) {
    return TRACE_STATUS_INTERNAL_ERROR;
  }

  trace_irq_payload_t irq_exit = irq_enter;
  irq_exit.timestamp_ns = 1'320;
  irq_exit.entering = 0;
  if (trace_RecordIRQ(handle, &irq_exit) != TRACE_STATUS_OK) {
    return TRACE_STATUS_INTERNAL_ERROR;
  }

  trace_sync_payload_t sync_unlock{};
  sync_unlock.timestamp_ns = 1'350;
  sync_unlock.core_id = 0;
  sync_unlock.task_id = 1;
  sync_unlock.obj_id = 0xABC;
  sync_unlock.obj_type = TRACE_OBJECT_TYPE_MUTEX;
  sync_unlock.action = TRACE_SYNC_ACTION_UNLOCK;
  if (trace_RecordSync(handle, &sync_unlock) != TRACE_STATUS_OK) {
    return TRACE_STATUS_INTERNAL_ERROR;
  }

  trace_task_state_payload_t wakeup{};
  wakeup.timestamp_ns = 1'380;
  wakeup.core_id = 1;
  wakeup.kind = TRACE_TASK_STATE_WAKEUP;
  wakeup.task_id = 2;
  wakeup.wake_src = TRACE_WAKE_SOURCE_IRQ;
  wakeup.obj_id = 0xABC;
  if (trace_RecordTaskState(handle, &wakeup) != TRACE_STATUS_OK) {
    return TRACE_STATUS_INTERNAL_ERROR;
  }

  trace_sched_decision_payload_t decision2{};
  decision2.timestamp_ns = 1'390;
  decision2.core_id = 1;
  decision2.selected_task_id = 2;
  decision2.rq_len = 1;
  decision2.reason = TRACE_SWITCH_REASON_WAKEUP;
  if (trace_RecordSchedDecision(handle, &decision2) != TRACE_STATUS_OK) {
    return TRACE_STATUS_INTERNAL_ERROR;
  }

  trace_task_switch_payload_t sw2{};
  sw2.timestamp_ns = 1'400;
  sw2.core_id = 1;
  sw2.prev_task_id = TRACE_TASK_ID_IDLE;
  sw2.next_task_id = 2;
  sw2.reason = TRACE_SWITCH_REASON_DISPATCH;
  if (trace_RecordTaskSwitch(handle, &sw2) != TRACE_STATUS_OK) {
    return TRACE_STATUS_INTERNAL_ERROR;
  }

  return TRACE_STATUS_OK;
}

}  // namespace

int main(int argc, char** argv) {
  std::string output = "trace_sim.trace";
  if (argc > 1 && argv[1] != nullptr) {
    output = argv[1];
  }

  trace_init_cfg_t cfg{};
  cfg.core_count = 2;
  cfg.ring_size = 4096;
  cfg.run_id = "sim";
  cfg.channel_cfg.type = TRACE_CHANNEL_FILE;
  cfg.channel_cfg.output_path = output.c_str();
  cfg.channel_cfg.rotate_segment_bytes = 0;
  cfg.channel_cfg.rotate_segment_records = 0;
  cfg.flush_policy.auto_flush = 0;
  cfg.flush_policy.flush_threshold_bytes = 0;
  cfg.flush_policy.segment_size_limit = 0;
  cfg.default_sampling.mode = TRACE_SAMPLING_DISABLED;

  trace_handle_t* handle = nullptr;
  const trace_status_t init = trace_Init(&cfg, &handle);
  if (init != TRACE_STATUS_OK) {
    std::cerr << "trace_Init failed: " << init << "\n";
    return EXIT_FAILURE;
  }

  if (trace_Enable(handle, 0x3u) != TRACE_STATUS_OK) {
    std::cerr << "trace_Enable failed\n";
    trace_Destroy(handle);
    return EXIT_FAILURE;
  }

  if (EmitDemo(handle) != TRACE_STATUS_OK) {
    std::cerr << "EmitDemo failed\n";
    trace_Destroy(handle);
    return EXIT_FAILURE;
  }

  if (trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0u) != TRACE_STATUS_OK) {
    std::cerr << "trace_FlushBuffer failed\n";
    trace_Destroy(handle);
    return EXIT_FAILURE;
  }

  trace_stats_t stats{};
  trace_GetStats(handle, &stats);
  std::cout << "collector simulation finished\n";
  std::cout << "output=" << output << "\n";
  std::cout << "written_records=" << stats.written_records << "\n";
  std::cout << "flushed_records=" << stats.flushed_records << "\n";
  std::cout << "lost_total=" << stats.lost_total << "\n";
  std::cout << "overflow_total=" << stats.overflow_total << "\n";

  trace_Destroy(handle);
  return EXIT_SUCCESS;
}
