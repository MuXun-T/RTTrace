#include "trace_api.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <new>
#include <string>
#include <thread>
#include <vector>

namespace {

std::atomic<bool> g_track_allocations{false};
std::atomic<uint64_t> g_allocation_count{0};

}  // namespace

void* operator new(std::size_t size) {
  if (g_track_allocations.load(std::memory_order_relaxed)) {
    g_allocation_count.fetch_add(1, std::memory_order_relaxed);
  }
  if (void* ptr = std::malloc(size)) {
    return ptr;
  }
  throw std::bad_alloc();
}

void operator delete(void* ptr) noexcept {
  std::free(ptr);
}

void* operator new[](std::size_t size) {
  if (g_track_allocations.load(std::memory_order_relaxed)) {
    g_allocation_count.fetch_add(1, std::memory_order_relaxed);
  }
  if (void* ptr = std::malloc(size)) {
    return ptr;
  }
  throw std::bad_alloc();
}

void operator delete[](void* ptr) noexcept {
  std::free(ptr);
}

void operator delete(void* ptr, std::size_t) noexcept {
  std::free(ptr);
}

void operator delete[](void* ptr, std::size_t) noexcept {
  std::free(ptr);
}

namespace {

#pragma pack(push, 1)
struct IntegrityPayloadDiskView {
  uint16_t core_id;
  uint32_t count;
  uint16_t reason;
};
#pragma pack(pop)

struct FileRecords {
  trace_global_header_disk_t global{};
  std::vector<trace_segment_meta_disk_t> segment_metas;
  std::vector<uint16_t> event_ids;
  std::vector<uint64_t> seqs;
  std::vector<trace_integrity_payload_t> integrity_payloads;
};

bool ReadFile(const std::string& path, std::vector<uint8_t>* out) {
  std::ifstream file(path, std::ios::binary);
  if (!file.is_open()) {
    return false;
  }
  file.seekg(0, std::ios::end);
  const std::streamsize size = file.tellg();
  file.seekg(0, std::ios::beg);
  out->resize(static_cast<size_t>(size));
  file.read(reinterpret_cast<char*>(out->data()), size);
  return static_cast<bool>(file);
}

bool ParseRecords(const std::vector<uint8_t>& bytes, FileRecords* out) {
  if (bytes.size() < sizeof(trace_global_header_disk_t)) {
    return false;
  }
  size_t pos = 0;
  std::memcpy(&out->global, bytes.data(), sizeof(out->global));
  pos += sizeof(out->global);
  if (out->global.format_ver >= 2u && pos + sizeof(trace_segment_meta_disk_t) <= bytes.size()) {
    trace_segment_meta_disk_t segment_meta{};
    std::memcpy(&segment_meta, bytes.data() + pos, sizeof(segment_meta));
    if (segment_meta.magic == TRACE_SEGMENT_META_MAGIC) {
      out->segment_metas.push_back(segment_meta);
      pos += sizeof(segment_meta);
    }
  }
  while (pos + sizeof(trace_chunk_header_disk_t) <= bytes.size()) {
    trace_chunk_header_disk_t chunk{};
    std::memcpy(&chunk, bytes.data() + pos, sizeof(chunk));
    pos += sizeof(chunk);
    if (chunk.magic != TRACE_CHUNK_MAGIC) {
      return false;
    }
    for (uint32_t i = 0; i < chunk.record_count; ++i) {
      if (pos + sizeof(trace_event_header_disk_t) > bytes.size()) {
        return false;
      }
      trace_event_header_disk_t event{};
      std::memcpy(&event, bytes.data() + pos, sizeof(event));
      pos += sizeof(event);
      if (pos + event.payload_len > bytes.size()) {
        return false;
      }
      if ((event.event_id == TRACE_EVENT_LOSS || event.event_id == TRACE_EVENT_OVERFLOW) &&
          event.payload_len >= sizeof(IntegrityPayloadDiskView)) {
        IntegrityPayloadDiskView payload_disk{};
        std::memcpy(&payload_disk, bytes.data() + pos, sizeof(payload_disk));
        trace_integrity_payload_t payload{};
        payload.core_id = payload_disk.core_id;
        payload.count = payload_disk.count;
        payload.reason = payload_disk.reason;
        out->integrity_payloads.push_back(payload);
      }
      pos += event.payload_len;
      out->event_ids.push_back(event.event_id);
      out->seqs.push_back(event.seq);
    }
  }
  return true;
}

bool ParseRecords(const std::string& path, FileRecords* out) {
  std::vector<uint8_t> bytes;
  if (!ReadFile(path, &bytes)) {
    return false;
  }
  return ParseRecords(bytes, out);
}

bool Check(bool condition, const std::string& label) {
  if (!condition) {
    std::cerr << "FAIL: " << label << "\n";
    return false;
  }
  return true;
}

template <typename Predicate>
bool WaitUntil(Predicate predicate, int timeout_ms = 500) {
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  while (std::chrono::steady_clock::now() < deadline) {
    if (predicate()) {
      return true;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  return predicate();
}

struct ScopedAllocationTracking {
  ScopedAllocationTracking() {
    g_allocation_count.store(0, std::memory_order_relaxed);
    g_track_allocations.store(true, std::memory_order_relaxed);
  }

  ~ScopedAllocationTracking() {
    g_track_allocations.store(false, std::memory_order_relaxed);
  }

  uint64_t count() const {
    return g_allocation_count.load(std::memory_order_relaxed);
  }
};

trace_init_cfg_t DefaultCfg(const std::string& path, uint32_t ring_size) {
  trace_init_cfg_t cfg{};
  cfg.core_count = 2;
  cfg.ring_size = ring_size;
  cfg.run_id = "test";
  cfg.channel_cfg.type = TRACE_CHANNEL_FILE;
  cfg.channel_cfg.output_path = path.c_str();
  cfg.default_sampling.mode = TRACE_SAMPLING_DISABLED;
  return cfg;
}

bool TestBasicCapture() {
  const std::string path = "tests/cpp/basic.trace";
  std::remove(path.c_str());

  trace_init_cfg_t cfg = DefaultCfg(path, 2048);
  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init basic")) {
    return false;
  }
  if (!Check(trace_Enable(handle, 0x3u) == TRACE_STATUS_OK, "enable basic")) {
    return false;
  }

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 100;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 11;
  ready.prio = 5;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record ready");

  trace_sched_decision_payload_t decision{};
  decision.timestamp_ns = 105;
  decision.core_id = 0;
  decision.selected_task_id = 11;
  decision.rq_len = 1;
  decision.reason = TRACE_SWITCH_REASON_DISPATCH;
  Check(trace_RecordSchedDecision(handle, &decision) == TRACE_STATUS_OK, "record sched decision");

  trace_task_switch_payload_t sw{};
  sw.timestamp_ns = 110;
  sw.core_id = 0;
  sw.prev_task_id = TRACE_TASK_ID_IDLE;
  sw.next_task_id = 11;
  sw.reason = TRACE_SWITCH_REASON_DISPATCH;
  Check(trace_RecordTaskSwitch(handle, &sw) == TRACE_STATUS_OK, "record switch");

  trace_irq_payload_t irq{};
  irq.timestamp_ns = 120;
  irq.core_id = 0;
  irq.irq_id = 2;
  irq.nesting_depth = 1;
  irq.entering = 1;
  Check(trace_RecordIRQ(handle, &irq) == TRACE_STATUS_OK, "record irq");

  Check(trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0) == TRACE_STATUS_OK, "flush basic");
  trace_stats_t stats{};
  Check(trace_GetStats(handle, &stats) == TRACE_STATUS_OK, "get stats");
  Check(stats.flushed_records == 4, "flushed count");
  trace_Destroy(handle);

  FileRecords parsed{};
  Check(ParseRecords(path, &parsed), "parse basic trace");
  return Check(parsed.event_ids.size() == 4 &&
                   parsed.event_ids[0] == TRACE_EVENT_TASK_READY &&
                   parsed.event_ids[1] == TRACE_EVENT_SCHED_DECISION &&
                   parsed.event_ids[2] == TRACE_EVENT_CTX_SWITCH &&
                   parsed.event_ids[3] == TRACE_EVENT_IRQ_ENTER,
               "basic event ids");
}

bool TestFilterAndSampling() {
  const std::string path = "tests/cpp/filter.trace";
  std::remove(path.c_str());

  trace_init_cfg_t cfg = DefaultCfg(path, 2048);
  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init filter")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable filter");

  trace_filter_rule_t rule{};
  rule.event_domain_mask = (1ull << TRACE_DOMAIN_TASK);
  Check(trace_SetFilter(handle, &rule) == TRACE_STATUS_OK, "set filter");

  trace_sampling_policy_t sampling{};
  sampling.mode = TRACE_SAMPLING_EVERY_N;
  sampling.threshold = 2;
  sampling.mark_sampled = 1;
  Check(trace_SetSampling(handle, &sampling) == TRACE_STATUS_OK, "set sampling");

  for (int i = 0; i < 4; ++i) {
    trace_task_state_payload_t dispatch{};
    dispatch.timestamp_ns = 200 + i;
    dispatch.core_id = 0;
    dispatch.kind = TRACE_TASK_STATE_DISPATCH;
    dispatch.task_id = 20 + i;
    dispatch.prio = 7;
    dispatch.reason = TRACE_SWITCH_REASON_BLOCK;
    Check(trace_RecordTaskState(handle, &dispatch) == TRACE_STATUS_OK, "dispatch sample");
  }

  trace_irq_payload_t irq{};
  irq.timestamp_ns = 300;
  irq.core_id = 0;
  irq.irq_id = 1;
  irq.nesting_depth = 1;
  irq.entering = 1;
  Check(trace_RecordIRQ(handle, &irq) == TRACE_STATUS_OK, "filtered irq");

  Check(trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0) == TRACE_STATUS_OK, "flush filter");
  trace_Destroy(handle);

  FileRecords parsed{};
  Check(ParseRecords(path, &parsed), "parse filter trace");
  return Check(parsed.event_ids.size() == 2 &&
                   parsed.event_ids[0] == TRACE_EVENT_TASK_DISPATCH &&
                   parsed.event_ids[1] == TRACE_EVENT_TASK_DISPATCH,
               "filter sampling result");
}

bool TestIntegrityBackfill() {
  const std::string path = "tests/cpp/integrity.trace";
  std::remove(path.c_str());

  trace_init_cfg_t cfg = DefaultCfg(path, 128);
  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init integrity")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable integrity");

  uint8_t blob[80];
  std::memset(blob, 0xAB, sizeof(blob));
  for (int i = 0; i < 4; ++i) {
    Check(trace_RecordEvent(handle, TRACE_EVENT_SYNC_CALIB, blob, sizeof(blob)) == TRACE_STATUS_OK,
          "overflow attempt");
  }

  Check(trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0) == TRACE_STATUS_OK, "flush first stage");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 500;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 42;
  ready.prio = 1;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record after overflow");
  Check(trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0) == TRACE_STATUS_OK, "flush second stage");
  trace_Destroy(handle);

  FileRecords parsed{};
  Check(ParseRecords(path, &parsed), "parse integrity trace");

  bool saw_integrity = false;
  for (uint16_t id : parsed.event_ids) {
    if (id == TRACE_EVENT_LOSS || id == TRACE_EVENT_OVERFLOW) {
      saw_integrity = true;
    }
  }
  return Check(saw_integrity, "integrity backfill present");
}

bool TestIntegrityFlushOnDestroy() {
  const std::string path = "tests/cpp/integrity-destroy.trace";
  std::remove(path.c_str());

  trace_init_cfg_t cfg = DefaultCfg(path, 128);
  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init integrity destroy")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable integrity destroy");

  uint8_t blob[80];
  std::memset(blob, 0xCD, sizeof(blob));
  for (int i = 0; i < 4; ++i) {
    Check(trace_RecordEvent(handle, TRACE_EVENT_SYNC_CALIB, blob, sizeof(blob)) == TRACE_STATUS_OK,
          "overflow before destroy");
  }

  Check(trace_Destroy(handle) == TRACE_STATUS_OK, "destroy with pending overflow");

  FileRecords parsed{};
  Check(ParseRecords(path, &parsed), "parse destroy integrity trace");
  bool saw_integrity = false;
  for (uint16_t id : parsed.event_ids) {
    if (id == TRACE_EVENT_LOSS || id == TRACE_EVENT_OVERFLOW) {
      saw_integrity = true;
    }
  }
  return Check(saw_integrity, "destroy flush integrity present");
}

bool TestAutoFlushThreshold() {
  const std::string path = "tests/cpp/auto-flush.trace";
  std::remove(path.c_str());

  trace_init_cfg_t cfg = DefaultCfg(path, 512);
  cfg.flush_policy.auto_flush = 1;
  cfg.flush_policy.flush_threshold_bytes = 1;
  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init auto flush")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable auto flush");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 900;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 9;
  ready.prio = 2;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record auto flush");

  trace_stats_t stats{};
  const bool flushed = WaitUntil([&]() {
    return trace_GetStats(handle, &stats) == TRACE_STATUS_OK && stats.flushed_records == 1;
  });
  const bool ok = Check(flushed, "auto flush triggered");
  trace_Destroy(handle);

  FileRecords parsed{};
  return ok && Check(ParseRecords(path, &parsed), "parse auto flush trace") &&
         Check(parsed.event_ids.size() == 1 && parsed.event_ids[0] == TRACE_EVENT_TASK_READY,
               "auto flush event ids");
}

bool TestSegmentDurationRotation() {
  const std::string path = "tests/cpp/duration-rotate.trace";
  const std::string segment_path = "tests/cpp/duration-rotate_segment1.trace";
  std::remove(path.c_str());
  std::remove(segment_path.c_str());

  trace_init_cfg_t cfg = DefaultCfg(path, 1024);
  cfg.flush_policy.segment_duration_ns = 100;
  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init duration rotate")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable duration rotate");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1000;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 31;
  ready.prio = 3;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record rotate first");
  Check(trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0) == TRACE_STATUS_OK, "flush rotate first");

  ready.timestamp_ns = 1200;
  ready.task_id = 32;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record rotate second");
  Check(trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0) == TRACE_STATUS_OK, "flush rotate second");
  trace_Destroy(handle);

  std::ifstream segment_file(segment_path, std::ios::binary);
  FileRecords first{};
  FileRecords second{};
  return Check(segment_file.good(), "segment rotation created second file") &&
         Check(ParseRecords(path, &first), "parse duration rotate first") &&
         Check(ParseRecords(segment_path, &second), "parse duration rotate second") &&
         Check(first.event_ids.size() == 1 && second.event_ids.size() == 1, "segment rotation split events");
}

struct MemorySink {
  struct RouteBehavior {
    std::string path;
    uint32_t fail_after_writes = 0xFFFFFFFFu;
    uint32_t remaining_failures = 0u;
    uint32_t write_delay_ms = 0u;
    trace_status_t failure_status = TRACE_STATUS_IO_ERROR;
    uint32_t writes = 0u;
  };

  bool open_called = false;
  bool close_called = false;
  std::string path;
  std::string current_path;
  std::vector<uint8_t> bytes;
  std::vector<std::string> opened_paths;
  std::vector<RouteBehavior> route_behaviors;
  std::atomic<uint32_t> write_count{0};
  std::thread::id last_write_thread{};
  uint32_t write_delay_ms = 0;
};

MemorySink::RouteBehavior* FindRouteBehavior(MemorySink* sink, const std::string& path) {
  for (auto& behavior : sink->route_behaviors) {
    if (behavior.path == path) {
      return &behavior;
    }
  }
  return nullptr;
}

trace_status_t MemoryOpen(void* user_ctx, const char* output_path) {
  auto* sink = static_cast<MemorySink*>(user_ctx);
  sink->open_called = true;
  sink->path = output_path == nullptr ? "" : output_path;
  sink->current_path = sink->path;
  sink->opened_paths.push_back(sink->path);
  sink->bytes.clear();
  return TRACE_STATUS_OK;
}

trace_status_t MemoryWrite(void* user_ctx, const void* data, uint32_t size) {
  auto* sink = static_cast<MemorySink*>(user_ctx);
  sink->write_count.fetch_add(1, std::memory_order_relaxed);
  sink->last_write_thread = std::this_thread::get_id();
  uint32_t delay_ms = sink->write_delay_ms;
  MemorySink::RouteBehavior* behavior = FindRouteBehavior(sink, sink->current_path);
  if (behavior != nullptr) {
    ++behavior->writes;
    delay_ms = behavior->write_delay_ms;
  }
  if (delay_ms > 0) {
    std::this_thread::sleep_for(std::chrono::milliseconds(delay_ms));
  }
  if (behavior != nullptr && behavior->writes > behavior->fail_after_writes &&
      behavior->remaining_failures > 0u) {
    if (behavior->remaining_failures != 0xFFFFFFFFu) {
      --behavior->remaining_failures;
    }
    return behavior->failure_status;
  }
  const auto* ptr = static_cast<const uint8_t*>(data);
  sink->bytes.insert(sink->bytes.end(), ptr, ptr + size);
  return TRACE_STATUS_OK;
}

trace_status_t MemoryClose(void* user_ctx) {
  auto* sink = static_cast<MemorySink*>(user_ctx);
  sink->close_called = true;
  return TRACE_STATUS_OK;
}

trace_init_cfg_t DefaultHookCfg(MemorySink* sink,
                                const char* run_id,
                                const char* output_path,
                                uint32_t ring_size = 1024) {
  trace_init_cfg_t cfg{};
  cfg.core_count = 1;
  cfg.ring_size = ring_size;
  cfg.run_id = run_id;
  cfg.channel_cfg.type = TRACE_CHANNEL_SERIAL;
  cfg.channel_cfg.output_path = output_path;
  cfg.default_sampling.mode = TRACE_SAMPLING_DISABLED;
  cfg.port_hooks.output_open = MemoryOpen;
  cfg.port_hooks.output_write = MemoryWrite;
  cfg.port_hooks.output_close = MemoryClose;
  cfg.port_hooks.user_ctx = sink;
  return cfg;
}

bool OpenedPath(const MemorySink& sink, const std::string& path) {
  for (const auto& opened_path : sink.opened_paths) {
    if (opened_path == path) {
      return true;
    }
  }
  return false;
}

bool HasIntegrityReason(const FileRecords& parsed, uint16_t reason) {
  for (const auto& payload : parsed.integrity_payloads) {
    if (payload.reason == reason) {
      return true;
    }
  }
  return false;
}

bool TestPeriodicFlushInterval() {
  MemorySink sink{};
  trace_init_cfg_t cfg = DefaultHookCfg(&sink, "interval", "serial://interval");
  cfg.flush_policy.auto_flush = 1;
  cfg.flush_policy.flush_interval_ms = 20;

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init periodic interval")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable periodic interval");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 930;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 19;
  ready.prio = 2;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record periodic interval");

  trace_stats_t stats{};
  const bool flushed = WaitUntil([&]() {
    return trace_GetStats(handle, &stats) == TRACE_STATUS_OK && stats.flushed_records == 1;
  }, 1000);
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords parsed{};
  return Check(flushed, "periodic flush interval triggered") &&
         Check(destroy_status == TRACE_STATUS_OK, "destroy periodic interval") &&
         Check(ParseRecords(sink.bytes, &parsed), "parse periodic interval trace") &&
         Check(parsed.event_ids.size() == 1 && parsed.event_ids[0] == TRACE_EVENT_TASK_READY,
               "periodic interval event ids");
}

bool TestPeriodicFlushDoesNotSpinWhenIdle() {
  MemorySink sink{};
  trace_init_cfg_t cfg = DefaultHookCfg(&sink, "idle", "serial://idle");
  cfg.flush_policy.auto_flush = 1;
  cfg.flush_policy.flush_interval_ms = 20;

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init periodic idle")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable periodic idle");

  const uint32_t baseline_writes = sink.write_count.load(std::memory_order_relaxed);
  std::this_thread::sleep_for(std::chrono::milliseconds(70));
  trace_stats_t stats{};
  const trace_status_t stats_status = trace_GetStats(handle, &stats);
  const uint32_t writes_after_wait = sink.write_count.load(std::memory_order_relaxed);
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords parsed{};
  return Check(stats_status == TRACE_STATUS_OK, "stats periodic idle") &&
         Check(writes_after_wait == baseline_writes, "periodic idle no extra writes") &&
         Check(stats.flushed_records == 0, "periodic idle no flushed records") &&
         Check(destroy_status == TRACE_STATUS_OK, "destroy periodic idle") &&
         Check(ParseRecords(sink.bytes, &parsed), "parse periodic idle trace") &&
         Check(parsed.event_ids.empty(), "periodic idle no events");
}

bool TestDisableDrainsCommittedRecordsBeforeReset() {
  MemorySink sink{};
  trace_init_cfg_t cfg = DefaultHookCfg(&sink, "disable-reset", "serial://disable-reset");

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init disable reset")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable disable reset");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1200;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 41;
  ready.prio = 3;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record disable reset");

  const trace_status_t disable_status = trace_Disable(handle, 0x1u, 0);
  trace_stats_t stats{};
  const trace_status_t stats_status = trace_GetStats(handle, &stats);
  ready.timestamp_ns = 1201;
  ready.task_id = 42;
  const trace_status_t record_after_disable = trace_RecordTaskState(handle, &ready);
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords parsed{};
  return Check(disable_status == TRACE_STATUS_OK, "disable reset drains") &&
         Check(stats_status == TRACE_STATUS_OK, "stats disable reset") &&
         Check(stats.flushed_records == 1, "disable reset flushed count") &&
         Check(record_after_disable == TRACE_STATUS_NOT_READY, "disable reset stops record") &&
         Check(destroy_status == TRACE_STATUS_OK, "destroy disable reset") &&
         Check(ParseRecords(sink.bytes, &parsed), "parse disable reset trace") &&
         Check(parsed.event_ids.size() == 1 && parsed.event_ids[0] == TRACE_EVENT_TASK_READY,
               "disable reset event ids");
}

bool TestDisableKeepBufferPreservesDrainableData() {
  MemorySink sink{};
  trace_init_cfg_t cfg = DefaultHookCfg(&sink, "disable-keep", "serial://disable-keep");

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init disable keep")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable disable keep");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1300;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 51;
  ready.prio = 4;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record disable keep first");

  const trace_status_t disable_status = trace_Disable(handle, 0x1u, 1);
  trace_stats_t mid_stats{};
  const trace_status_t mid_stats_status = trace_GetStats(handle, &mid_stats);
  const trace_status_t enable_status = trace_Enable(handle, 0x1u);

  ready.timestamp_ns = 1301;
  ready.task_id = 52;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record disable keep second");
  const trace_status_t flush_status = trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0);
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords parsed{};
  return Check(disable_status == TRACE_STATUS_OK, "disable keep drains") &&
         Check(mid_stats_status == TRACE_STATUS_OK, "stats disable keep") &&
         Check(mid_stats.flushed_records == 1, "disable keep flushed first record") &&
         Check(enable_status == TRACE_STATUS_OK, "enable after disable keep") &&
         Check(flush_status == TRACE_STATUS_OK, "flush after disable keep") &&
         Check(destroy_status == TRACE_STATUS_OK, "destroy disable keep") &&
         Check(ParseRecords(sink.bytes, &parsed), "parse disable keep trace") &&
         Check(parsed.event_ids.size() == 2, "disable keep event count") &&
         Check(parsed.seqs.size() == 2 && parsed.seqs[1] == parsed.seqs[0] + 1u,
               "disable keep seq continuity");
}

bool TestIntervalAndThresholdFlushCanCoexist() {
  MemorySink sink{};
  trace_init_cfg_t cfg = DefaultHookCfg(&sink, "coexist", "serial://coexist", 2048);
  cfg.flush_policy.auto_flush = 1;
  cfg.flush_policy.flush_interval_ms = 200;
  cfg.flush_policy.flush_threshold_bytes = 256;

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init coexist")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable coexist");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1400;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 61;
  ready.prio = 5;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record coexist interval");

  trace_stats_t stats{};
  const bool interval_flushed = WaitUntil([&]() {
    return trace_GetStats(handle, &stats) == TRACE_STATUS_OK && stats.flushed_records == 1;
  }, 1000);

  uint8_t blob[96];
  std::memset(blob, 0x5A, sizeof(blob));
  Check(trace_RecordEvent(handle, TRACE_EVENT_SYNC_CALIB, blob, sizeof(blob)) == TRACE_STATUS_OK,
        "record coexist threshold first");
  Check(trace_RecordEvent(handle, TRACE_EVENT_SYNC_CALIB, blob, sizeof(blob)) == TRACE_STATUS_OK,
        "record coexist threshold second");

  const bool threshold_flushed = WaitUntil([&]() {
    return trace_GetStats(handle, &stats) == TRACE_STATUS_OK && stats.flushed_records == 3;
  }, 150);
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords parsed{};
  return Check(interval_flushed, "coexist interval flush") &&
         Check(threshold_flushed, "coexist threshold flush") &&
         Check(destroy_status == TRACE_STATUS_OK, "destroy coexist") &&
         Check(ParseRecords(sink.bytes, &parsed), "parse coexist trace") &&
         Check(parsed.event_ids.size() == 3, "coexist event count");
}

bool TestSegmentHeaderCarriesSequenceLink() {
  const std::string path = "tests/cpp/segment-link.trace";
  const std::string segment_path = "tests/cpp/segment-link_segment1.trace";
  std::remove(path.c_str());
  std::remove(segment_path.c_str());

  trace_init_cfg_t cfg = DefaultCfg(path, 1024);
  cfg.flush_policy.segment_duration_ns = 100;
  cfg.dict_ref.dict_ver = 3;
  cfg.dict_ref.dict_ref_algo = TRACE_DICT_REF_CRC32;
  cfg.dict_ref.dict_ref_checksum = 0xAABBCCDDu;

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init segment link")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable segment link");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1000;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 71;
  ready.prio = 3;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record segment link first");
  Check(trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0) == TRACE_STATUS_OK, "flush segment link first");

  ready.timestamp_ns = 1200;
  ready.task_id = 72;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record segment link second");
  Check(trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0) == TRACE_STATUS_OK, "flush segment link second");
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords first{};
  FileRecords second{};
  return Check(destroy_status == TRACE_STATUS_OK, "destroy segment link") &&
         Check(ParseRecords(path, &first), "parse segment link first") &&
         Check(ParseRecords(segment_path, &second), "parse segment link second") &&
         Check(first.segment_metas.size() == 1, "segment link first meta count") &&
         Check(second.segment_metas.size() == 1, "segment link second meta count") &&
         Check(first.segment_metas[0].segment_seq == 1u, "segment link first seq") &&
         Check(first.segment_metas[0].prev_segment_seq == 0u, "segment link first prev seq") &&
         Check(second.segment_metas[0].segment_seq == 2u, "segment link second seq") &&
         Check(second.segment_metas[0].prev_segment_seq == 1u, "segment link second prev seq") &&
         Check(second.segment_metas[0].dict_ref_checksum == 0xAABBCCDDu, "segment link checksum");
}

bool TestRotateWritesFreshGlobalHeaderPerSegment() {
  const std::string path = "tests/cpp/rotate-header.trace";
  const std::string segment_path = "tests/cpp/rotate-header_segment1.trace";
  std::remove(path.c_str());
  std::remove(segment_path.c_str());

  trace_init_cfg_t cfg = DefaultCfg(path, 1024);
  cfg.dict_ref.dict_ver = 5;
  cfg.dict_ref.dict_ref_algo = TRACE_DICT_REF_CRC32;
  cfg.dict_ref.dict_ref_checksum = 0x01020304u;

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init rotate header")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable rotate header");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1500;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 81;
  ready.prio = 4;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record rotate header first");
  Check(trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0) == TRACE_STATUS_OK, "flush rotate header first");

  ready.timestamp_ns = 1501;
  ready.task_id = 82;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record rotate header second");
  Check(trace_FlushBuffer(handle, TRACE_FLUSH_MODE_FORCE_ROTATE, 0) == TRACE_STATUS_OK,
        "flush rotate header force");
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords first{};
  FileRecords second{};
  return Check(destroy_status == TRACE_STATUS_OK, "destroy rotate header") &&
         Check(ParseRecords(path, &first), "parse rotate header first") &&
         Check(ParseRecords(segment_path, &second), "parse rotate header second") &&
         Check(first.global.format_ver == 2u, "rotate header first format v2") &&
         Check(second.global.format_ver == 2u, "rotate header second format v2") &&
         Check(first.segment_metas.size() == 1, "rotate header first meta count") &&
         Check(second.segment_metas.size() == 1, "rotate header second meta count");
}

bool TestDictionaryVersionChangeForcesRotation() {
  const std::string path = "tests/cpp/dict-rotate.trace";
  const std::string segment_path = "tests/cpp/dict-rotate_segment1.trace";
  std::remove(path.c_str());
  std::remove(segment_path.c_str());

  trace_init_cfg_t cfg = DefaultCfg(path, 1024);
  cfg.dict_ref.dict_ver = 1;
  cfg.dict_ref.dict_ref_algo = TRACE_DICT_REF_CRC32;
  cfg.dict_ref.dict_ref_checksum = 0x11111111u;

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init dict rotate")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable dict rotate");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1700;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 91;
  ready.prio = 5;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record dict rotate first");
  Check(trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0) == TRACE_STATUS_OK, "flush dict rotate first");

  trace_dict_ref_t next_ref{};
  next_ref.dict_ver = 2;
  next_ref.dict_ref_algo = TRACE_DICT_REF_CRC32;
  next_ref.dict_ref_checksum = 0x22222222u;
  next_ref.dict_ref_path = "dict-v2.json";
  Check(trace_SetDictionaryRef(handle, &next_ref) == TRACE_STATUS_OK, "set dict rotate ref");

  ready.timestamp_ns = 1701;
  ready.task_id = 92;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record dict rotate second");
  Check(trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0) == TRACE_STATUS_OK, "flush dict rotate second");
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords first{};
  FileRecords second{};
  return Check(destroy_status == TRACE_STATUS_OK, "destroy dict rotate") &&
         Check(ParseRecords(path, &first), "parse dict rotate first") &&
         Check(ParseRecords(segment_path, &second), "parse dict rotate second") &&
         Check(first.segment_metas.size() == 1, "dict rotate first meta count") &&
         Check(second.segment_metas.size() == 1, "dict rotate second meta count") &&
         Check(first.segment_metas[0].dict_ver == 1u, "dict rotate first ver") &&
         Check(second.segment_metas[0].dict_ver == 2u, "dict rotate second ver") &&
         Check(second.segment_metas[0].dict_ref_checksum == 0x22222222u, "dict rotate second checksum");
}

bool TestOutputHooksForType(trace_channel_type_t channel_type, const std::string& output_path) {
  MemorySink sink{};
  trace_init_cfg_t cfg{};
  cfg.core_count = 1;
  cfg.ring_size = 1024;
  cfg.run_id = "hook";
  cfg.channel_cfg.type = channel_type;
  cfg.channel_cfg.output_path = output_path.c_str();
  cfg.default_sampling.mode = TRACE_SAMPLING_DISABLED;
  cfg.port_hooks.now_ns = nullptr;
  cfg.port_hooks.output_open = MemoryOpen;
  cfg.port_hooks.output_write = MemoryWrite;
  cfg.port_hooks.output_close = MemoryClose;
  cfg.port_hooks.user_ctx = &sink;

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init output hooks")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable output hooks");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 700;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 8;
  ready.prio = 4;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record output hook event");

  Check(trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0) == TRACE_STATUS_OK, "flush output hooks");
  trace_Destroy(handle);

  FileRecords parsed{};
  return Check(sink.open_called, "output hook open called") &&
         Check(sink.close_called, "output hook close called") &&
         Check(sink.path == output_path, "output hook path preserved") &&
         Check(!sink.bytes.empty(), "output hook wrote bytes") &&
         Check(ParseRecords(sink.bytes, &parsed), "parse output hook trace") &&
         Check(parsed.event_ids.size() == 1 && parsed.event_ids[0] == TRACE_EVENT_TASK_READY,
               "output hook event ids");
}

bool TestOutputHooks() {
  return TestOutputHooksForType(TRACE_CHANNEL_FILE, "memory.trace") &&
         TestOutputHooksForType(TRACE_CHANNEL_SERIAL, "serial://mock-uart0") &&
         TestOutputHooksForType(TRACE_CHANNEL_NETWORK, "tcp://127.0.0.1:9000");
}

bool TestChannelFallbackOnPrimaryFailure() {
  MemorySink sink{};
  sink.route_behaviors.push_back({"serial://primary-fail", 2u, 0xFFFFFFFFu, 0u, TRACE_STATUS_IO_ERROR});

  trace_init_cfg_t cfg = DefaultHookCfg(&sink, "fallback", "serial://primary-fail");
  cfg.channel_cfg.target_count = 2;
  cfg.channel_cfg.targets[0] = {TRACE_CHANNEL_SERIAL, "serial://primary-fail", 0, 0};
  cfg.channel_cfg.targets[1] = {TRACE_CHANNEL_SERIAL, "serial://fallback-ok", 0, 0};

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init channel fallback")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable channel fallback");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1800;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 111;
  ready.prio = 4;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record channel fallback");

  const trace_status_t flush_status = trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0);
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords parsed{};
  return Check(flush_status == TRACE_STATUS_OK, "channel fallback flush status") &&
         Check(destroy_status == TRACE_STATUS_OK, "destroy channel fallback") &&
         Check(OpenedPath(sink, "serial://fallback-ok"), "channel fallback opened fallback target") &&
         Check(ParseRecords(sink.bytes, &parsed), "parse channel fallback trace") &&
         Check(parsed.event_ids.size() == 1 && parsed.event_ids[0] == TRACE_EVENT_TASK_READY,
               "channel fallback event ids");
}

bool TestRetryOccursBeforeFallback() {
  MemorySink sink{};
  sink.route_behaviors.push_back({"serial://retry-primary", 0xFFFFFFFFu, 0u, 0u, TRACE_STATUS_IO_ERROR});

  trace_init_cfg_t cfg = DefaultHookCfg(&sink, "retry-fallback", "serial://retry-primary");
  cfg.channel_cfg.target_count = 2;
  cfg.channel_cfg.targets[0] = {TRACE_CHANNEL_SERIAL, "serial://retry-primary", 0, 0};
  cfg.channel_cfg.targets[1] = {TRACE_CHANNEL_SERIAL, "serial://retry-fallback", 0, 0};
  cfg.channel_cfg.retry_limit = 2;

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init retry fallback")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable retry fallback");

  // Start fault injection after init so we only observe retry/fallback during flush.
  sink.route_behaviors[0].writes = 0u;
  sink.route_behaviors[0].fail_after_writes = 0u;
  sink.route_behaviors[0].remaining_failures = 3u;

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1810;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 112;
  ready.prio = 4;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record retry fallback");

  const trace_status_t flush_status = trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0);
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords parsed{};
  return Check(flush_status == TRACE_STATUS_OK, "retry fallback flush status") &&
         Check(destroy_status == TRACE_STATUS_OK, "destroy retry fallback") &&
         Check(sink.route_behaviors[0].writes == 3u, "retry fallback primary attempts") &&
         Check(OpenedPath(sink, "serial://retry-fallback"), "retry fallback opened fallback target") &&
         Check(ParseRecords(sink.bytes, &parsed), "parse retry fallback trace") &&
         Check(parsed.event_ids.size() == 1 && parsed.event_ids[0] == TRACE_EVENT_TASK_READY,
               "retry fallback event ids");
}

bool TestBackpressureEmitsIntegrityReasonIoBackpressure() {
  MemorySink sink{};
  sink.route_behaviors.push_back({"serial://backpressure", 2u, 1u, 0u, TRACE_STATUS_IO_ERROR});

  trace_init_cfg_t cfg = DefaultHookCfg(&sink, "backpressure", "serial://backpressure");

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init backpressure")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable backpressure");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1810;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 121;
  ready.prio = 4;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record backpressure first");

  const trace_status_t first_flush_status = trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0);
  trace_stats_t after_failure{};
  const trace_status_t after_failure_stats_status = trace_GetStats(handle, &after_failure);

  ready.timestamp_ns = 1811;
  ready.task_id = 122;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record backpressure second");
  const trace_status_t second_flush_status = trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0);
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords parsed{};
  const bool parsed_ok = ParseRecords(sink.bytes, &parsed);
  bool saw_ready = false;
  for (uint16_t event_id : parsed.event_ids) {
    if (event_id == TRACE_EVENT_TASK_READY) {
      saw_ready = true;
      break;
    }
  }

  return Check(first_flush_status == TRACE_STATUS_IO_ERROR, "backpressure first flush failed") &&
         Check(after_failure_stats_status == TRACE_STATUS_OK, "backpressure stats after failure") &&
         Check(after_failure.io_backpressure_total == 1u, "backpressure stats count") &&
         Check(second_flush_status == TRACE_STATUS_OK, "backpressure second flush ok") &&
         Check(destroy_status == TRACE_STATUS_OK, "destroy backpressure") &&
         Check(parsed_ok, "parse backpressure trace") &&
         Check(HasIntegrityReason(parsed, TRACE_INTEGRITY_REASON_IO_BACKPRESSURE),
               "backpressure integrity reason present") &&
         Check(saw_ready, "backpressure preserved later ready event");
}

bool TestFallbackPathStillDoesNotBlockRecordPath() {
  MemorySink sink{};
  sink.route_behaviors.push_back({"serial://slow-primary", 2u, 0xFFFFFFFFu, 80u, TRACE_STATUS_IO_ERROR});

  trace_init_cfg_t cfg = DefaultHookCfg(&sink, "fallback-async", "serial://slow-primary");
  cfg.flush_policy.auto_flush = 1;
  cfg.flush_policy.flush_threshold_bytes = 1;
  cfg.channel_cfg.target_count = 2;
  cfg.channel_cfg.targets[0] = {TRACE_CHANNEL_SERIAL, "serial://slow-primary", 0, 0};
  cfg.channel_cfg.targets[1] = {TRACE_CHANNEL_SERIAL, "serial://fast-fallback", 0, 0};

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init fallback async")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable fallback async");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1820;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 131;
  ready.prio = 4;
  ready.reason = TRACE_READY_REASON_CREATE;

  const std::thread::id record_thread = std::this_thread::get_id();
  const auto start = std::chrono::steady_clock::now();
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record fallback async");
  const auto elapsed_ms =
      std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start).count();

  trace_stats_t stats{};
  const bool flushed = WaitUntil([&]() {
    return trace_GetStats(handle, &stats) == TRACE_STATUS_OK && stats.flushed_records == 1;
  }, 3000);
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords parsed{};
  return Check(flushed, "fallback async flushed") &&
         Check(elapsed_ms < 40, "fallback async record path not blocked") &&
         Check(OpenedPath(sink, "serial://fast-fallback"), "fallback async opened fallback target") &&
         Check(sink.last_write_thread != record_thread, "fallback async write on worker thread") &&
         Check(destroy_status == TRACE_STATUS_OK, "destroy fallback async") &&
         Check(ParseRecords(sink.bytes, &parsed), "parse fallback async trace") &&
         Check(parsed.event_ids.size() == 1 && parsed.event_ids[0] == TRACE_EVENT_TASK_READY,
               "fallback async event ids");
}

bool TestFlushStatusReportsFinalChannelFailure() {
  MemorySink sink{};
  sink.route_behaviors.push_back({"serial://fail-primary", 2u, 0xFFFFFFFFu, 0u, TRACE_STATUS_IO_ERROR});
  sink.route_behaviors.push_back({"serial://fail-fallback", 2u, 0xFFFFFFFFu, 0u, TRACE_STATUS_IO_ERROR});

  trace_init_cfg_t cfg = DefaultHookCfg(&sink, "final-failure", "serial://fail-primary");
  cfg.channel_cfg.target_count = 2;
  cfg.channel_cfg.targets[0] = {TRACE_CHANNEL_SERIAL, "serial://fail-primary", 0, 0};
  cfg.channel_cfg.targets[1] = {TRACE_CHANNEL_SERIAL, "serial://fail-fallback", 0, 0};

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init final failure")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable final failure");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1830;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 141;
  ready.prio = 4;
  ready.reason = TRACE_READY_REASON_CREATE;
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record final failure");

  const trace_status_t flush_status = trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0);
  trace_stats_t stats{};
  const trace_status_t stats_status = trace_GetStats(handle, &stats);
  const trace_status_t destroy_status = trace_Destroy(handle);

  return Check(flush_status == TRACE_STATUS_IO_ERROR, "final failure flush status") &&
         Check(stats_status == TRACE_STATUS_OK, "final failure stats status") &&
         Check(stats.io_backpressure_total == 1u, "final failure backpressure stats") &&
         Check(destroy_status == TRACE_STATUS_IO_ERROR, "final failure destroy status");
}

bool TestRecordPathNoHeapAlloc() {
  const std::string path = "tests/cpp/no-heap.trace";
  std::remove(path.c_str());

  trace_init_cfg_t cfg = DefaultCfg(path, 512);
  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init no heap")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable no heap");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1500;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 55;
  ready.prio = 3;
  ready.reason = TRACE_READY_REASON_CREATE;

  uint64_t allocation_count = 0;
  {
    ScopedAllocationTracking scope;
    Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record no heap");
    allocation_count = scope.count();
  }

  Check(trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0) == TRACE_STATUS_OK, "flush no heap");
  const bool ok = Check(allocation_count == 0, "record path no heap alloc");
  trace_Destroy(handle);

  FileRecords parsed{};
  return ok && Check(ParseRecords(path, &parsed), "parse no heap trace") &&
         Check(parsed.event_ids.size() == 1 && parsed.event_ids[0] == TRACE_EVENT_TASK_READY,
               "no heap event ids");
}

bool TestFlushWorkerDrainsWithoutBlockingRecordPath() {
  MemorySink sink{};
  sink.write_delay_ms = 80;

  trace_init_cfg_t cfg{};
  cfg.core_count = 1;
  cfg.ring_size = 512;
  cfg.run_id = "async";
  cfg.channel_cfg.type = TRACE_CHANNEL_SERIAL;
  cfg.channel_cfg.output_path = "serial://slow-uart0";
  cfg.default_sampling.mode = TRACE_SAMPLING_DISABLED;
  cfg.flush_policy.auto_flush = 1;
  cfg.flush_policy.flush_threshold_bytes = 1;
  cfg.port_hooks.output_open = MemoryOpen;
  cfg.port_hooks.output_write = MemoryWrite;
  cfg.port_hooks.output_close = MemoryClose;
  cfg.port_hooks.user_ctx = &sink;

  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init async flush worker")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable async flush worker");

  trace_task_state_payload_t ready{};
  ready.timestamp_ns = 1600;
  ready.core_id = 0;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = 66;
  ready.prio = 4;
  ready.reason = TRACE_READY_REASON_CREATE;

  const std::thread::id record_thread = std::this_thread::get_id();
  const auto start = std::chrono::steady_clock::now();
  Check(trace_RecordTaskState(handle, &ready) == TRACE_STATUS_OK, "record async flush worker");
  const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                              std::chrono::steady_clock::now() - start)
                              .count();

  trace_stats_t stats{};
  const bool flushed = WaitUntil([&]() {
    return trace_GetStats(handle, &stats) == TRACE_STATUS_OK && stats.flushed_records == 1;
  }, 3000);
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords parsed{};
  return Check(flushed, "async flush completed") &&
         Check(elapsed_ms < 40, "record path not blocked by slow flush") &&
         Check(sink.write_count.load(std::memory_order_relaxed) > 0, "flush worker wrote output") &&
         Check(sink.last_write_thread != record_thread, "flush executed on worker thread") &&
         Check(destroy_status == TRACE_STATUS_OK, "destroy async flush worker") &&
         Check(ParseRecords(sink.bytes, &parsed), "parse async flush output") &&
         Check(parsed.event_ids.size() == 1 && parsed.event_ids[0] == TRACE_EVENT_TASK_READY,
               "async flush event ids");
}

bool TestStatsSnapshotConcurrentRecordAndFlush() {
  const std::string path = "tests/cpp/stats-concurrent.trace";
  std::remove(path.c_str());

  trace_init_cfg_t cfg = DefaultCfg(path, 16384);
  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init stats concurrent")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable stats concurrent");

  std::atomic<bool> recorder_done{false};
  std::atomic<bool> flush_ok{true};
  std::atomic<bool> record_ok{true};
  std::atomic<bool> snapshot_ok{true};

  std::thread recorder([&]() {
    for (uint32_t i = 0; i < 200; ++i) {
      trace_task_state_payload_t ready{};
      ready.timestamp_ns = 2000 + i;
      ready.core_id = 0;
      ready.kind = TRACE_TASK_STATE_READY;
      ready.task_id = 100 + i;
      ready.prio = 5;
      ready.reason = TRACE_READY_REASON_CREATE;
      if (trace_RecordTaskState(handle, &ready) != TRACE_STATUS_OK) {
        record_ok.store(false, std::memory_order_relaxed);
        break;
      }
    }
    recorder_done.store(true, std::memory_order_release);
  });

  std::thread flusher([&]() {
    while (!recorder_done.load(std::memory_order_acquire)) {
      if (trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0) != TRACE_STATUS_OK) {
        flush_ok.store(false, std::memory_order_relaxed);
        break;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
  });

  while (!recorder_done.load(std::memory_order_acquire)) {
    trace_stats_t stats{};
    if (trace_GetStats(handle, &stats) != TRACE_STATUS_OK ||
        stats.written_records < stats.flushed_records ||
        stats.written_bytes < stats.flushed_bytes) {
      snapshot_ok.store(false, std::memory_order_relaxed);
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }

  recorder.join();
  flusher.join();

  trace_stats_t final_stats{};
  const trace_status_t flush_status = trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0);
  const trace_status_t stats_status = trace_GetStats(handle, &final_stats);
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords parsed{};
  return Check(record_ok.load(std::memory_order_relaxed), "concurrent record ok") &&
         Check(flush_ok.load(std::memory_order_relaxed), "concurrent flush ok") &&
         Check(snapshot_ok.load(std::memory_order_relaxed), "stats snapshot monotonic") &&
         Check(flush_status == TRACE_STATUS_OK, "final concurrent flush") &&
         Check(stats_status == TRACE_STATUS_OK, "final concurrent stats") &&
         Check(final_stats.lost_total == 0, "concurrent lost count") &&
         Check(final_stats.overflow_total == 0, "concurrent overflow count") &&
         Check(final_stats.written_records == 200, "concurrent written records") &&
         Check(final_stats.flushed_records == 200, "concurrent flushed records") &&
         Check(destroy_status == TRACE_STATUS_OK, "destroy concurrent stats") &&
         Check(ParseRecords(path, &parsed), "parse concurrent stats trace") &&
         Check(parsed.event_ids.size() == 200, "concurrent parsed count");
}

bool TestSameCoreConcurrentProducersPreserveSeqOrder() {
  const std::string path = "tests/cpp/same-core-concurrent.trace";
  std::remove(path.c_str());

  trace_init_cfg_t cfg = DefaultCfg(path, 16384);
  cfg.core_count = 1;
  trace_handle_t* handle = nullptr;
  if (!Check(trace_Init(&cfg, &handle) == TRACE_STATUS_OK, "init same core concurrent")) {
    return false;
  }
  Check(trace_Enable(handle, 0x1u) == TRACE_STATUS_OK, "enable same core concurrent");

  auto producer = [&](uint32_t base_task_id, uint64_t base_ts) {
    for (uint32_t i = 0; i < 100; ++i) {
      trace_task_state_payload_t ready{};
      ready.timestamp_ns = base_ts + i;
      ready.core_id = 0;
      ready.kind = TRACE_TASK_STATE_READY;
      ready.task_id = base_task_id + i;
      ready.prio = 6;
      ready.reason = TRACE_READY_REASON_CREATE;
      if (trace_RecordTaskState(handle, &ready) != TRACE_STATUS_OK) {
        return false;
      }
    }
    return true;
  };

  bool producer_a_ok = true;
  bool producer_b_ok = true;
  std::thread producer_a([&]() { producer_a_ok = producer(5000, 4000); });
  std::thread producer_b([&]() { producer_b_ok = producer(6000, 5000); });
  producer_a.join();
  producer_b.join();

  const trace_status_t flush_status = trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0);
  const trace_status_t destroy_status = trace_Destroy(handle);

  FileRecords parsed{};
  const bool parsed_ok = ParseRecords(path, &parsed);
  bool monotonic = true;
  for (size_t i = 1; i < parsed.seqs.size(); ++i) {
    if (parsed.seqs[i] != parsed.seqs[i - 1] + 1u) {
      monotonic = false;
      break;
    }
  }

  return Check(producer_a_ok, "producer a same core concurrent") &&
         Check(producer_b_ok, "producer b same core concurrent") &&
         Check(flush_status == TRACE_STATUS_OK, "flush same core concurrent") &&
         Check(destroy_status == TRACE_STATUS_OK, "destroy same core concurrent") &&
         Check(parsed_ok, "parse same core concurrent trace") &&
         Check(parsed.event_ids.size() == 200, "same core concurrent parsed count") &&
         Check(monotonic, "same core concurrent seq monotonic");
}

}  // namespace

int main() {
  const bool ok = TestBasicCapture() && TestFilterAndSampling() && TestIntegrityBackfill() &&
                  TestIntegrityFlushOnDestroy() && TestAutoFlushThreshold() &&
                  TestPeriodicFlushInterval() &&
                  TestPeriodicFlushDoesNotSpinWhenIdle() &&
                  TestDisableDrainsCommittedRecordsBeforeReset() &&
                  TestDisableKeepBufferPreservesDrainableData() &&
                  TestIntervalAndThresholdFlushCanCoexist() &&
                  TestSegmentHeaderCarriesSequenceLink() &&
                  TestRotateWritesFreshGlobalHeaderPerSegment() &&
                  TestDictionaryVersionChangeForcesRotation() &&
                  TestSegmentDurationRotation() && TestOutputHooks() &&
                  TestChannelFallbackOnPrimaryFailure() &&
                  TestRetryOccursBeforeFallback() &&
                  TestBackpressureEmitsIntegrityReasonIoBackpressure() &&
                  TestFallbackPathStillDoesNotBlockRecordPath() &&
                  TestFlushStatusReportsFinalChannelFailure() &&
                  TestRecordPathNoHeapAlloc() &&
                  TestFlushWorkerDrainsWithoutBlockingRecordPath() &&
                  TestStatsSnapshotConcurrentRecordAndFlush() &&
                  TestSameCoreConcurrentProducersPreserveSeqOrder();
  if (!ok) {
    return 1;
  }
  std::cout << "collector tests passed\n";
  return 0;
}
