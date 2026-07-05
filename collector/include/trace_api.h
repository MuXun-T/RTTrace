#ifndef TRACE_API_H_
#define TRACE_API_H_

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define TRACE_FORMAT_MAGIC 0x54524345u
#define TRACE_CHUNK_MAGIC 0x43484b31u
#define TRACE_SEGMENT_META_MAGIC 0x53474d32u
#define TRACE_FORMAT_VERSION 2u
#define TRACE_HEADER_VERSION 2u
#define TRACE_DICT_VERSION 1u
#define TRACE_TASK_ID_IDLE 0u

#define TRACE_DOMAIN_TASK 0x1u
#define TRACE_DOMAIN_SYNC 0x2u
#define TRACE_DOMAIN_IRQ 0x3u
#define TRACE_DOMAIN_INTEGRITY 0x4u

#define TRACE_EVENT_ID(domain, type) (uint16_t)((((domain) & 0x0Fu) << 12) | ((type) & 0x0FFFu))

enum {
  TRACE_EVENT_TASK_READY = TRACE_EVENT_ID(TRACE_DOMAIN_TASK, 0x001u),
  TRACE_EVENT_TASK_BLOCK = TRACE_EVENT_ID(TRACE_DOMAIN_TASK, 0x002u),
  TRACE_EVENT_TASK_WAKEUP = TRACE_EVENT_ID(TRACE_DOMAIN_TASK, 0x003u),
  TRACE_EVENT_TASK_DISPATCH = TRACE_EVENT_ID(TRACE_DOMAIN_TASK, 0x004u),
  TRACE_EVENT_TASK_EXIT = TRACE_EVENT_ID(TRACE_DOMAIN_TASK, 0x005u),
  TRACE_EVENT_CTX_SWITCH = TRACE_EVENT_ID(TRACE_DOMAIN_TASK, 0x006u),
  TRACE_EVENT_SCHED_DECISION = TRACE_EVENT_ID(TRACE_DOMAIN_TASK, 0x007u),
  TRACE_EVENT_SYNC_TRY = TRACE_EVENT_ID(TRACE_DOMAIN_SYNC, 0x001u),
  TRACE_EVENT_SYNC_LOCK = TRACE_EVENT_ID(TRACE_DOMAIN_SYNC, 0x002u),
  TRACE_EVENT_SYNC_UNLOCK = TRACE_EVENT_ID(TRACE_DOMAIN_SYNC, 0x003u),
  TRACE_EVENT_IRQ_ENTER = TRACE_EVENT_ID(TRACE_DOMAIN_IRQ, 0x001u),
  TRACE_EVENT_IRQ_EXIT = TRACE_EVENT_ID(TRACE_DOMAIN_IRQ, 0x002u),
  TRACE_EVENT_LOSS = TRACE_EVENT_ID(TRACE_DOMAIN_INTEGRITY, 0x001u),
  TRACE_EVENT_OVERFLOW = TRACE_EVENT_ID(TRACE_DOMAIN_INTEGRITY, 0x002u),
  TRACE_EVENT_SYNC_CALIB = TRACE_EVENT_ID(TRACE_DOMAIN_INTEGRITY, 0x003u),
  TRACE_EVENT_TS_CALIB = TRACE_EVENT_ID(TRACE_DOMAIN_INTEGRITY, 0x004u)
};

typedef enum trace_status {
  TRACE_STATUS_OK = 0,
  TRACE_STATUS_INVALID_ARG = 1,
  TRACE_STATUS_NOT_READY = 2,
  TRACE_STATUS_NO_SPACE = 3,
  TRACE_STATUS_IO_ERROR = 4,
  TRACE_STATUS_UNSUPPORTED = 5,
  TRACE_STATUS_INTERNAL_ERROR = 6
} trace_status_t;

typedef enum trace_channel_type {
  TRACE_CHANNEL_FILE = 0,
  TRACE_CHANNEL_SERIAL = 1,
  TRACE_CHANNEL_NETWORK = 2
} trace_channel_type_t;

typedef enum trace_dict_ref_algo {
  TRACE_DICT_REF_NONE = 0,
  TRACE_DICT_REF_CRC32 = 1
} trace_dict_ref_algo_t;

typedef enum trace_flush_mode {
  TRACE_FLUSH_MODE_SYNC = 0,
  TRACE_FLUSH_MODE_FORCE_ROTATE = 1
} trace_flush_mode_t;

typedef enum trace_sampling_mode {
  TRACE_SAMPLING_DISABLED = 0,
  TRACE_SAMPLING_EVERY_N = 1,
  TRACE_SAMPLING_RATIO = 2
} trace_sampling_mode_t;

typedef enum trace_task_state_kind {
  TRACE_TASK_STATE_READY = 0,
  TRACE_TASK_STATE_BLOCK = 1,
  TRACE_TASK_STATE_WAKEUP = 2,
  TRACE_TASK_STATE_DISPATCH = 3,
  TRACE_TASK_STATE_EXIT = 4
} trace_task_state_kind_t;

typedef enum trace_sync_action {
  TRACE_SYNC_ACTION_TRY = 0,
  TRACE_SYNC_ACTION_LOCK = 1,
  TRACE_SYNC_ACTION_UNLOCK = 2
} trace_sync_action_t;

typedef enum trace_object_type {
  TRACE_OBJECT_TYPE_MUTEX = 1,
  TRACE_OBJECT_TYPE_SEM = 2,
  TRACE_OBJECT_TYPE_QUEUE = 3,
  TRACE_OBJECT_TYPE_FLAG = 4,
  TRACE_OBJECT_TYPE_TASK_SEM = 5,
  TRACE_OBJECT_TYPE_TASK_MSG_Q = 6
} trace_object_type_t;

typedef enum trace_ready_reason {
  TRACE_READY_REASON_CREATE = 1,
  TRACE_READY_REASON_RESUME = 2,
  TRACE_READY_REASON_PREEMPT_RETURN = 3,
  TRACE_READY_REASON_IRQ_RETURN = 4
} trace_ready_reason_t;

typedef enum trace_block_reason {
  TRACE_BLOCK_REASON_DELAY = 1,
  TRACE_BLOCK_REASON_SUSPEND = 2,
  TRACE_BLOCK_REASON_MUTEX_WAIT = 3,
  TRACE_BLOCK_REASON_SEM_WAIT = 4,
  TRACE_BLOCK_REASON_QUEUE_WAIT = 5,
  TRACE_BLOCK_REASON_FLAG_WAIT = 6,
  TRACE_BLOCK_REASON_TASK_SEM_WAIT = 7,
  TRACE_BLOCK_REASON_TASK_MSG_Q_WAIT = 8
} trace_block_reason_t;

typedef enum trace_wake_source {
  TRACE_WAKE_SOURCE_RESOURCE_RELEASE = 1,
  TRACE_WAKE_SOURCE_TIMER = 2,
  TRACE_WAKE_SOURCE_TASK_RESUME = 3,
  TRACE_WAKE_SOURCE_IRQ = 4
} trace_wake_source_t;

typedef enum trace_switch_reason {
  TRACE_SWITCH_REASON_DISPATCH = 1,
  TRACE_SWITCH_REASON_PREEMPT = 2,
  TRACE_SWITCH_REASON_BLOCK = 3,
  TRACE_SWITCH_REASON_WAKEUP = 4,
  TRACE_SWITCH_REASON_IRQ_RETURN = 5,
  TRACE_SWITCH_REASON_EXIT = 6
} trace_switch_reason_t;

typedef enum trace_integrity_reason {
  TRACE_INTEGRITY_REASON_SEQ_GAP = 1,
  TRACE_INTEGRITY_REASON_BUFFER_OVERFLOW = 2,
  TRACE_INTEGRITY_REASON_IO_BACKPRESSURE = 3,
  TRACE_INTEGRITY_REASON_MEDIA_CORRUPTION = 4
} trace_integrity_reason_t;

typedef uint64_t (*trace_now_ns_cb_t)(void* user_ctx);
typedef trace_status_t (*trace_output_open_cb_t)(void* user_ctx, const char* output_path);
typedef trace_status_t (*trace_output_write_cb_t)(void* user_ctx, const void* data, uint32_t size);
typedef trace_status_t (*trace_output_close_cb_t)(void* user_ctx);

typedef struct trace_port_hooks {
  trace_now_ns_cb_t now_ns;
  trace_output_open_cb_t output_open;
  trace_output_write_cb_t output_write;
  trace_output_close_cb_t output_close;
  void* user_ctx;
} trace_port_hooks_t;

typedef struct trace_channel_target_cfg {
  trace_channel_type_t type;
  const char* output_path;
  uint32_t rotate_segment_bytes;
  uint32_t rotate_segment_records;
} trace_channel_target_cfg_t;

typedef struct trace_channel_cfg {
  trace_channel_type_t type;
  const char* output_path;
  uint32_t rotate_segment_bytes;
  uint32_t rotate_segment_records;
  uint32_t target_count;
  trace_channel_target_cfg_t targets[3];
  uint32_t retry_limit;
  uint32_t retry_backoff_ms;
} trace_channel_cfg_t;

typedef struct trace_flush_policy {
  uint32_t flush_threshold_bytes;
  uint32_t flush_interval_ms;
  uint32_t segment_size_limit;
  uint64_t segment_duration_ns;
  uint8_t auto_flush;
} trace_flush_policy_t;

typedef struct trace_filter_rule {
  uint64_t event_domain_mask;
  uint64_t core_mask;
  const uint32_t* task_ids;
  uint32_t task_count;
  const uint64_t* object_ids;
  uint32_t object_count;
  uint8_t drop_unknown;
  uint8_t drop_integrity;
} trace_filter_rule_t;

typedef struct trace_sampling_policy {
  trace_sampling_mode_t mode;
  uint32_t ratio_numerator;
  uint32_t ratio_denominator;
  uint64_t window_ns;
  uint64_t threshold;
  uint8_t mark_sampled;
} trace_sampling_policy_t;

typedef struct trace_dict_ref {
  uint16_t dict_ver;
  trace_dict_ref_algo_t dict_ref_algo;
  uint32_t dict_ref_checksum;
  const char* dict_ref_path;
} trace_dict_ref_t;

typedef struct trace_init_cfg {
  uint16_t core_count;
  uint32_t ring_size;
  const char* run_id;
  trace_channel_cfg_t channel_cfg;
  trace_filter_rule_t default_filter;
  trace_sampling_policy_t default_sampling;
  trace_flush_policy_t flush_policy;
  trace_dict_ref_t dict_ref;
  trace_port_hooks_t port_hooks;
} trace_init_cfg_t;

typedef struct trace_record_hdr {
  uint16_t ver;
  uint16_t flags;
  uint16_t core_id;
  uint16_t event_id;
  uint64_t seq;
  uint64_t timestamp;
  uint32_t payload_len;
} trace_record_hdr_t;

typedef struct trace_stats {
  uint64_t lost_total;
  uint64_t overflow_total;
  uint64_t io_backpressure_total;
  uint64_t written_records;
  uint64_t flushed_records;
  uint64_t written_bytes;
  uint64_t flushed_bytes;
  uint64_t flush_latency_ns;
} trace_stats_t;

typedef struct trace_task_state_payload {
  uint64_t timestamp_ns;
  uint16_t core_id;
  trace_task_state_kind_t kind;
  uint32_t task_id;
  int16_t prio;
  uint16_t core_hint;
  uint64_t wait_obj_id;
  uint16_t reason;
  uint32_t owner_task_id;
  uint16_t wake_src;
  uint64_t obj_id;
  int32_t exit_code;
} trace_task_state_payload_t;

typedef struct trace_task_switch_payload {
  uint64_t timestamp_ns;
  uint16_t core_id;
  uint32_t prev_task_id;
  uint32_t next_task_id;
  uint16_t reason;
} trace_task_switch_payload_t;

typedef struct trace_sched_decision_payload {
  uint64_t timestamp_ns;
  uint16_t core_id;
  uint32_t selected_task_id;
  uint16_t rq_len;
  uint16_t reason;
} trace_sched_decision_payload_t;

typedef struct trace_irq_payload {
  uint64_t timestamp_ns;
  uint16_t core_id;
  uint16_t irq_id;
  uint8_t nesting_depth;
  uint8_t entering;
} trace_irq_payload_t;

typedef struct trace_sync_payload {
  uint64_t timestamp_ns;
  uint16_t core_id;
  uint32_t task_id;
  uint64_t obj_id;
  uint16_t obj_type;
  trace_sync_action_t action;
  uint64_t timeout_ns;
} trace_sync_payload_t;

typedef struct trace_integrity_payload {
  uint16_t core_id;
  uint32_t count;
  uint16_t reason;
} trace_integrity_payload_t;

typedef struct trace_handle trace_handle_t;

#pragma pack(push, 1)
typedef struct trace_global_header_disk {
  uint32_t magic;
  uint16_t endian;
  uint16_t time_unit;
  uint16_t format_ver;
  uint16_t dict_ver;
  uint16_t header_ver;
  uint16_t core_count;
  char clock_source[16];
  char producer_ver[32];
  char run_id[32];
} trace_global_header_disk_t;

typedef struct trace_segment_meta_disk {
  uint32_t magic;
  uint16_t header_ver;
  uint16_t meta_size;
  uint32_t segment_seq;
  uint32_t prev_segment_seq;
  uint16_t dict_ver;
  uint16_t dict_ref_algo;
  uint32_t dict_ref_checksum;
} trace_segment_meta_disk_t;

typedef struct trace_chunk_header_disk {
  uint32_t magic;
  uint16_t header_ver;
  uint16_t core_id;
  uint32_t record_count;
  uint32_t payload_bytes;
  uint64_t chunk_start_ts;
  uint64_t chunk_end_ts;
  uint64_t seq_begin;
  uint64_t seq_end;
  uint32_t dict_ver;
  uint32_t chunk_crc;
} trace_chunk_header_disk_t;

typedef struct trace_event_header_disk {
  uint16_t ver;
  uint16_t flags;
  uint16_t core_id;
  uint16_t event_id;
  uint64_t seq;
  uint64_t timestamp;
  uint32_t payload_len;
} trace_event_header_disk_t;
#pragma pack(pop)

trace_status_t trace_Init(const trace_init_cfg_t* cfg, trace_handle_t** out_handle);
trace_status_t trace_Destroy(trace_handle_t* handle);
trace_status_t trace_Enable(trace_handle_t* handle, uint64_t core_mask);
trace_status_t trace_Disable(trace_handle_t* handle, uint64_t core_mask, uint8_t keep_buffer);
trace_status_t trace_RecordEvent(trace_handle_t* handle, uint16_t event_id, const void* payload, uint32_t payload_len);
trace_status_t trace_RecordTaskSwitch(trace_handle_t* handle, const trace_task_switch_payload_t* payload);
trace_status_t trace_RecordSchedDecision(trace_handle_t* handle, const trace_sched_decision_payload_t* payload);
trace_status_t trace_RecordTaskState(trace_handle_t* handle, const trace_task_state_payload_t* payload);
trace_status_t trace_RecordIRQ(trace_handle_t* handle, const trace_irq_payload_t* payload);
trace_status_t trace_RecordSync(trace_handle_t* handle, const trace_sync_payload_t* payload);
trace_status_t trace_SetFilter(trace_handle_t* handle, const trace_filter_rule_t* rule);
trace_status_t trace_SetSampling(trace_handle_t* handle, const trace_sampling_policy_t* policy);
trace_status_t trace_SetDictionaryRef(trace_handle_t* handle, const trace_dict_ref_t* dict_ref);
trace_status_t trace_FlushBuffer(trace_handle_t* handle, trace_flush_mode_t mode, uint32_t deadline_ms);
trace_status_t trace_GetStats(trace_handle_t* handle, trace_stats_t* out_stats);

#ifdef __cplusplus
}
#endif

#endif  // TRACE_API_H_
