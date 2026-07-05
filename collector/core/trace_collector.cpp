#include "trace_api.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>

namespace {

constexpr uint16_t kEndianLittle = 1;
constexpr uint16_t kTimeUnitNanoseconds = 1;
constexpr uint16_t kFlagSampled = 1u << 0;
constexpr uint16_t kFlagSynthetic = 1u << 1;
constexpr uint16_t kFlagIrqContext = 1u << 2;
constexpr uint32_t kRingPrefixFlagPadding = 1u << 0;

uint64_t NowNs() {
  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(now).count());
}

uint64_t NowNsFromHooks(const trace_port_hooks_t* hooks) {
  if (hooks != nullptr && hooks->now_ns != nullptr) {
    return hooks->now_ns(hooks->user_ctx);
  }
  return NowNs();
}

uint32_t Crc32(const uint8_t* data, size_t size) {
  uint32_t crc = 0xFFFFFFFFu;
  for (size_t i = 0; i < size; ++i) {
    crc ^= static_cast<uint32_t>(data[i]);
    for (int bit = 0; bit < 8; ++bit) {
      const uint32_t mask = 0u - (crc & 1u);
      crc = (crc >> 1u) ^ (0xEDB88320u & mask);
    }
  }
  return ~crc;
}

size_t AlignUp(size_t value, size_t alignment) {
  const size_t mask = alignment - 1u;
  return (value + mask) & ~mask;
}

template <typename T>
std::vector<uint8_t> ToBytes(const T& value) {
  std::vector<uint8_t> bytes(sizeof(T));
  std::memcpy(bytes.data(), &value, sizeof(T));
  return bytes;
}

uint16_t EventDomain(uint16_t event_id) {
  return static_cast<uint16_t>((event_id >> 12u) & 0x0Fu);
}

bool IsKnownEvent(uint16_t event_id) {
  switch (event_id) {
    case TRACE_EVENT_TASK_READY:
    case TRACE_EVENT_TASK_BLOCK:
    case TRACE_EVENT_TASK_WAKEUP:
    case TRACE_EVENT_TASK_DISPATCH:
    case TRACE_EVENT_TASK_EXIT:
    case TRACE_EVENT_CTX_SWITCH:
    case TRACE_EVENT_SCHED_DECISION:
    case TRACE_EVENT_SYNC_TRY:
    case TRACE_EVENT_SYNC_LOCK:
    case TRACE_EVENT_SYNC_UNLOCK:
    case TRACE_EVENT_IRQ_ENTER:
    case TRACE_EVENT_IRQ_EXIT:
    case TRACE_EVENT_LOSS:
    case TRACE_EVENT_OVERFLOW:
    case TRACE_EVENT_SYNC_CALIB:
    case TRACE_EVENT_TS_CALIB:
      return true;
    default:
      return false;
  }
}

#pragma pack(push, 1)
struct TaskReadyPayloadDisk {
  uint32_t task_id;
  int16_t prio;
  uint16_t core_hint;
  uint16_t reason;
};

struct TaskBlockPayloadDisk {
  uint32_t task_id;
  uint64_t wait_obj_id;
  uint16_t reason;
  uint32_t owner_task_id;
};

struct TaskWakeupPayloadDisk {
  uint32_t task_id;
  uint16_t wake_src;
  uint64_t obj_id;
};

struct TaskDispatchPayloadDisk {
  uint32_t task_id;
  uint16_t core_id;
  int16_t prio;
  uint16_t reason;
};

struct TaskExitPayloadDisk {
  uint32_t task_id;
  int32_t exit_code;
};

struct TaskSwitchPayloadDisk {
  uint16_t core_id;
  uint32_t prev_task_id;
  uint32_t next_task_id;
  uint16_t reason;
};

struct SchedDecisionPayloadDisk {
  uint16_t core_id;
  uint32_t selected_task_id;
  uint16_t rq_len;
  uint16_t reason;
};

struct SyncPayloadDisk {
  uint32_t task_id;
  uint64_t obj_id;
  uint16_t obj_type;
  uint64_t timeout_ns;
};

struct IrqPayloadDisk {
  uint16_t irq_id;
  uint16_t core_id;
  uint8_t nesting_depth;
};

struct IntegrityPayloadDisk {
  uint16_t core_id;
  uint32_t count;
  uint16_t reason;
};
#pragma pack(pop)

struct alignas(8) RingRecordPrefix {
  uint32_t committed_size;
  uint32_t flags;
};

static_assert(alignof(RingRecordPrefix) == 8, "ring prefix alignment drifted");

constexpr size_t kMaxChannelTargets = 3;

struct NormalizedChannelConfig {
  std::array<trace_channel_target_cfg_t, kMaxChannelTargets> targets{};
  uint32_t target_count = 0;
  uint32_t retry_limit = 0;
  uint32_t retry_backoff_ms = 0;
};

NormalizedChannelConfig NormalizeChannelConfig(const trace_channel_cfg_t& cfg) {
  NormalizedChannelConfig normalized{};
  normalized.retry_limit = cfg.retry_limit;
  normalized.retry_backoff_ms = cfg.retry_backoff_ms;
  if (cfg.target_count == 0u) {
    normalized.target_count = 1u;
    normalized.targets[0].type = cfg.type;
    normalized.targets[0].output_path = cfg.output_path;
    normalized.targets[0].rotate_segment_bytes = cfg.rotate_segment_bytes;
    normalized.targets[0].rotate_segment_records = cfg.rotate_segment_records;
    return normalized;
  }

  normalized.target_count = std::min<uint32_t>(cfg.target_count, kMaxChannelTargets);
  for (uint32_t i = 0; i < normalized.target_count; ++i) {
    normalized.targets[i] = cfg.targets[i];
  }
  return normalized;
}

struct TraceChannelTarget {
  TraceChannelTarget(const trace_channel_target_cfg_t& cfg,
                     const trace_port_hooks_t& hooks,
                     uint64_t segment_duration_ns)
      : cfg_(cfg),
        hooks_(hooks),
        base_path_(cfg.output_path == nullptr
                       ? (hooks.output_write == nullptr
                              ? ""
                              : (cfg.type == TRACE_CHANNEL_SERIAL
                                     ? "serial://trace"
                                     : (cfg.type == TRACE_CHANNEL_NETWORK
                                            ? "network://trace"
                                            : "trace.trace")))
                       : cfg.output_path),
        rotate_segment_bytes_(cfg.rotate_segment_bytes),
        rotate_segment_records_(cfg.rotate_segment_records),
        rotate_segment_duration_ns_(segment_duration_ns) {}

  trace_status_t Open() {
    if (cfg_.type != TRACE_CHANNEL_FILE && hooks_.output_write == nullptr) {
      return TRACE_STATUS_UNSUPPORTED;
    }
    if (hooks_.output_write == nullptr && base_path_.empty()) {
      return TRACE_STATUS_UNSUPPORTED;
    }
    return OpenSegment(/*force_new=*/true);
  }

  void SeedSegmentSeq(uint32_t segment_seq) {
    segment_seq_ = segment_seq;
    current_bytes_ = 0;
    current_records_ = 0;
    segment_start_ts_.reset();
    last_written_segment_seq_ = segment_seq;
  }

  uint32_t CurrentSegmentSeq() const {
    return segment_seq_;
  }

  uint32_t LastWrittenSegmentSeq() const {
    return last_written_segment_seq_;
  }

  trace_status_t WriteChunk(const trace_global_header_disk_t& global,
                            const trace_dict_ref_t& dict_ref,
                            uint16_t core_id,
                            const std::vector<std::vector<uint8_t>>& records,
                            trace_flush_mode_t mode) {
    if (!IsReady()) {
      return TRACE_STATUS_NOT_READY;
    }
    uint32_t payload_bytes = 0;
    uint64_t start_ts = 0;
    uint64_t end_ts = 0;
    uint64_t seq_begin = 0;
    uint64_t seq_end = 0;
    std::vector<uint8_t> blob;
    for (size_t i = 0; i < records.size(); ++i) {
      payload_bytes += static_cast<uint32_t>(records[i].size());
      blob.insert(blob.end(), records[i].begin(), records[i].end());
      trace_event_header_disk_t header{};
      std::memcpy(&header, records[i].data(), sizeof(header));
      if (i == 0) {
        start_ts = header.timestamp;
        seq_begin = header.seq;
      }
      end_ts = header.timestamp;
      seq_end = header.seq;
    }

    const size_t expected_bytes = sizeof(trace_chunk_header_disk_t) + blob.size();
    const bool rotate_by_duration =
        rotate_segment_duration_ns_ > 0 && segment_start_ts_.has_value() &&
        end_ts > *segment_start_ts_ &&
        (end_ts - *segment_start_ts_) > rotate_segment_duration_ns_;
    if (mode == TRACE_FLUSH_MODE_FORCE_ROTATE ||
        (rotate_segment_bytes_ > 0 && current_bytes_ + expected_bytes > rotate_segment_bytes_) ||
        (rotate_segment_records_ > 0 && current_records_ + records.size() > rotate_segment_records_) ||
        rotate_by_duration) {
      const trace_status_t status = OpenSegment(/*force_new=*/true);
      if (status != TRACE_STATUS_OK) {
        return status;
      }
      const trace_status_t global_status = WriteGlobal(global, dict_ref);
      if (global_status != TRACE_STATUS_OK) {
        return global_status;
      }
    }

    trace_chunk_header_disk_t chunk{};
    chunk.magic = TRACE_CHUNK_MAGIC;
    chunk.header_ver = TRACE_HEADER_VERSION;
    chunk.core_id = core_id;
    chunk.record_count = static_cast<uint32_t>(records.size());
    chunk.payload_bytes = payload_bytes;
    chunk.chunk_start_ts = start_ts;
    chunk.chunk_end_ts = end_ts;
    chunk.seq_begin = seq_begin;
    chunk.seq_end = seq_end;
    chunk.dict_ver = global.dict_ver;
    chunk.chunk_crc = Crc32(blob.data(), blob.size());

    const trace_status_t header_status = WriteBytes(&chunk, sizeof(chunk));
    if (header_status != TRACE_STATUS_OK) {
      return header_status;
    }
    if (!blob.empty()) {
      const trace_status_t payload_status = WriteBytes(blob.data(), blob.size());
      if (payload_status != TRACE_STATUS_OK) {
        return payload_status;
      }
    }
    if (!segment_start_ts_.has_value() && !records.empty()) {
      segment_start_ts_ = start_ts;
    }
    current_bytes_ += sizeof(chunk) + blob.size();
    current_records_ += records.size();
    return TRACE_STATUS_OK;
  }

  trace_status_t WriteGlobal(const trace_global_header_disk_t& global, const trace_dict_ref_t& dict_ref) {
    if (!IsReady()) {
      return TRACE_STATUS_NOT_READY;
    }
    const trace_status_t status = WriteBytes(&global, sizeof(global));
    if (status != TRACE_STATUS_OK) {
      return status;
    }
    current_bytes_ += sizeof(global);
    if (global.format_ver >= 2u) {
      const trace_segment_meta_disk_t segment_meta = BuildSegmentMeta(global, dict_ref);
      const trace_status_t meta_status = WriteBytes(&segment_meta, sizeof(segment_meta));
      if (meta_status != TRACE_STATUS_OK) {
        return meta_status;
      }
      current_bytes_ += sizeof(segment_meta);
    }
    last_written_segment_seq_ = segment_seq_;
    return TRACE_STATUS_OK;
  }

  void Close() {
    if (!open_) {
      return;
    }
    if (hooks_.output_write != nullptr) {
      if (hooks_.output_close != nullptr) {
        hooks_.output_close(hooks_.user_ctx);
      }
      open_ = false;
      return;
    }
    if (file_.is_open()) {
      file_.flush();
      file_.close();
    }
    open_ = false;
  }

 private:
  trace_segment_meta_disk_t BuildSegmentMeta(const trace_global_header_disk_t& global,
                                             const trace_dict_ref_t& dict_ref) const {
    trace_segment_meta_disk_t meta{};
    meta.magic = TRACE_SEGMENT_META_MAGIC;
    meta.header_ver = global.header_ver;
    meta.meta_size = static_cast<uint16_t>(sizeof(meta));
    meta.segment_seq = segment_seq_;
    meta.prev_segment_seq = segment_seq_ > 1u ? segment_seq_ - 1u : 0u;
    meta.dict_ver = dict_ref.dict_ver == 0u ? global.dict_ver : dict_ref.dict_ver;
    meta.dict_ref_algo = static_cast<uint16_t>(dict_ref.dict_ref_algo);
    meta.dict_ref_checksum = dict_ref.dict_ref_checksum;
    return meta;
  }

  bool IsReady() const {
    if (hooks_.output_write != nullptr) {
      return open_;
    }
    return open_ && file_.is_open();
  }

  trace_status_t WriteBytes(const void* data, size_t size) {
    if (hooks_.output_write != nullptr) {
      return hooks_.output_write(hooks_.user_ctx, data, static_cast<uint32_t>(size));
    }
    file_.write(reinterpret_cast<const char*>(data), static_cast<std::streamsize>(size));
    return file_ ? TRACE_STATUS_OK : TRACE_STATUS_IO_ERROR;
  }

  trace_status_t OpenSegment(bool force_new) {
    if (!force_new && IsReady()) {
      return TRACE_STATUS_OK;
    }
    Close();

    std::filesystem::path base;
    if (!(hooks_.output_write != nullptr &&
          (cfg_.type == TRACE_CHANNEL_SERIAL || cfg_.type == TRACE_CHANNEL_NETWORK))) {
      base = std::filesystem::path(base_path_);
    }
    if (!base.empty() && base.has_parent_path()) {
      std::error_code ec;
      std::filesystem::create_directories(base.parent_path(), ec);
    }
    std::filesystem::path current = base;
    if (!base.empty() && segment_seq_ > 0) {
      const std::string stem = base.stem().string();
      const std::string ext = base.extension().string();
      current = base.parent_path() /
                std::filesystem::path(stem + "_segment" + std::to_string(segment_seq_) + ext);
    }
    if (hooks_.output_write != nullptr) {
      if (hooks_.output_open != nullptr) {
        const std::string open_target = current.empty() ? base_path_ : current.string();
        const trace_status_t status = hooks_.output_open(hooks_.user_ctx, open_target.c_str());
        if (status != TRACE_STATUS_OK) {
          return status;
        }
      }
      ++segment_seq_;
      current_bytes_ = 0;
      current_records_ = 0;
      segment_start_ts_.reset();
      open_ = true;
      return TRACE_STATUS_OK;
    }
    file_.open(current, std::ios::binary | std::ios::trunc);
    if (!file_.is_open()) {
      return TRACE_STATUS_IO_ERROR;
    }
    ++segment_seq_;
    current_bytes_ = 0;
    current_records_ = 0;
    segment_start_ts_.reset();
    open_ = true;
    return TRACE_STATUS_OK;
  }

  trace_channel_target_cfg_t cfg_{};
  trace_port_hooks_t hooks_{};
  std::string base_path_;
  uint32_t rotate_segment_bytes_ = 0;
  uint32_t rotate_segment_records_ = 0;
  uint64_t rotate_segment_duration_ns_ = 0;
  std::ofstream file_;
  bool open_ = false;
  uint32_t segment_seq_ = 0;
  uint32_t last_written_segment_seq_ = 0;
  size_t current_bytes_ = 0;
  size_t current_records_ = 0;
  std::optional<uint64_t> segment_start_ts_;
};

struct ChannelMux {
  explicit ChannelMux(const trace_init_cfg_t& cfg)
      : config_(NormalizeChannelConfig(cfg.channel_cfg)),
        retry_limit_(cfg.channel_cfg.retry_limit),
        retry_backoff_ms_(cfg.channel_cfg.retry_backoff_ms) {
    targets_.reserve(config_.target_count);
    for (uint32_t i = 0; i < config_.target_count; ++i) {
      trace_channel_target_cfg_t target_cfg = config_.targets[i];
      if (target_cfg.rotate_segment_bytes == 0u) {
        target_cfg.rotate_segment_bytes = cfg.flush_policy.segment_size_limit;
      }
      targets_.emplace_back(target_cfg, cfg.port_hooks, cfg.flush_policy.segment_duration_ns);
    }
  }

  trace_status_t Open() {
    if (targets_.empty()) {
      return TRACE_STATUS_INVALID_ARG;
    }
    trace_status_t last_status = TRACE_STATUS_UNSUPPORTED;
    for (size_t idx = 0; idx < targets_.size(); ++idx) {
      targets_[idx].SeedSegmentSeq(0u);
      const trace_status_t status = targets_[idx].Open();
      if (status == TRACE_STATUS_OK) {
        active_target_index_ = idx;
        return TRACE_STATUS_OK;
      }
      last_status = status;
    }
    return last_status;
  }

  trace_status_t WriteGlobal(const trace_global_header_disk_t& global, const trace_dict_ref_t& dict_ref) {
    if (targets_.empty() || active_target_index_ >= targets_.size()) {
      return TRACE_STATUS_NOT_READY;
    }

    trace_status_t last_status = TRACE_STATUS_IO_ERROR;
    const uint32_t seed_segment_seq = targets_[active_target_index_].LastWrittenSegmentSeq();
    for (size_t idx = active_target_index_; idx < targets_.size(); ++idx) {
      TraceChannelTarget& target = targets_[idx];
      if (idx != active_target_index_) {
        target.SeedSegmentSeq(seed_segment_seq);
        const trace_status_t open_status = target.Open();
        if (open_status != TRACE_STATUS_OK) {
          last_status = open_status;
          continue;
        }
      }
      const trace_status_t status = WriteWithRetry([&]() { return target.WriteGlobal(global, dict_ref); });
      if (status == TRACE_STATUS_OK) {
        active_target_index_ = idx;
        return TRACE_STATUS_OK;
      }
      last_status = status;
    }
    return last_status;
  }

  trace_status_t WriteChunk(const trace_global_header_disk_t& global,
                            const trace_dict_ref_t& dict_ref,
                            uint16_t core_id,
                            const std::vector<std::vector<uint8_t>>& records,
                            trace_flush_mode_t mode) {
    if (targets_.empty() || active_target_index_ >= targets_.size()) {
      return TRACE_STATUS_NOT_READY;
    }

    trace_status_t last_status = TRACE_STATUS_IO_ERROR;
    const uint32_t seed_segment_seq = targets_[active_target_index_].LastWrittenSegmentSeq();
    for (size_t idx = active_target_index_; idx < targets_.size(); ++idx) {
      TraceChannelTarget& target = targets_[idx];
      trace_flush_mode_t target_mode = mode;
      if (idx != active_target_index_) {
        target.SeedSegmentSeq(seed_segment_seq);
        const trace_status_t open_status = target.Open();
        if (open_status != TRACE_STATUS_OK) {
          last_status = open_status;
          continue;
        }
        const trace_status_t global_status = WriteWithRetry([&]() { return target.WriteGlobal(global, dict_ref); });
        if (global_status != TRACE_STATUS_OK) {
          last_status = global_status;
          continue;
        }
        target_mode = TRACE_FLUSH_MODE_SYNC;
      }

      const trace_status_t status = WriteWithRetry(
          [&]() { return target.WriteChunk(global, dict_ref, core_id, records, target_mode); });
      if (status == TRACE_STATUS_OK) {
        active_target_index_ = idx;
        return TRACE_STATUS_OK;
      }
      last_status = status;
    }
    return last_status;
  }

  void Close() {
    for (auto& target : targets_) {
      target.Close();
    }
  }

 private:
  template <typename WriterFn>
  trace_status_t WriteWithRetry(WriterFn&& writer) {
    trace_status_t status = TRACE_STATUS_OK;
    for (uint32_t attempt = 0; attempt <= retry_limit_; ++attempt) {
      status = writer();
      if (status == TRACE_STATUS_OK) {
        return status;
      }
      if (attempt < retry_limit_ && retry_backoff_ms_ > 0u) {
        std::this_thread::sleep_for(std::chrono::milliseconds(retry_backoff_ms_));
      }
    }
    return status;
  }

  NormalizedChannelConfig config_{};
  std::vector<TraceChannelTarget> targets_;
  size_t active_target_index_ = 0;
  uint32_t retry_limit_ = 0;
  uint32_t retry_backoff_ms_ = 0;
};

struct FilterState {
  trace_filter_rule_t raw{};
  std::unordered_set<uint32_t> task_ids;
  std::unordered_set<uint64_t> object_ids;
};

struct SamplingState {
  trace_sampling_policy_t raw{};
};

struct CpuCounters {
  std::atomic<uint64_t> lost_total{0};
  std::atomic<uint64_t> overflow_total{0};
  std::atomic<uint64_t> io_backpressure_total{0};
  std::atomic<uint64_t> written_records{0};
  std::atomic<uint64_t> written_bytes{0};
};

struct CpuState {
  explicit CpuState(uint32_t size) : buffer(size, 0u) {}

  void ResetBuffer() {
    reserve_idx.store(0, std::memory_order_relaxed);
    flush_idx.store(0, std::memory_order_relaxed);
    pending_loss.store(0, std::memory_order_relaxed);
    pending_overflow.store(0, std::memory_order_relaxed);
    pending_io_backpressure.store(0, std::memory_order_relaxed);
    std::memset(buffer.data(), 0, buffer.size());
  }

  std::vector<uint8_t> buffer;
  std::atomic<uint64_t> reserve_idx{0};
  std::atomic<uint64_t> flush_idx{0};
  std::atomic<uint64_t> seq_gen{0};
  std::atomic<uint64_t> sample_counter{0};
  std::atomic<uint32_t> pending_loss{0};
  std::atomic<uint32_t> pending_overflow{0};
  std::atomic<uint32_t> pending_io_backpressure{0};
  std::atomic<bool> enabled{false};
  std::atomic<uint32_t> active_recorders{0};
  std::atomic_flag record_gate = ATOMIC_FLAG_INIT;
  CpuCounters counters;
};

struct TraceHandle {
  explicit TraceHandle(const trace_init_cfg_t& cfg)
      : cfg(cfg),
        channel(cfg),
        cpus() {
    cpus.reserve(cfg.core_count);
    for (uint16_t i = 0; i < cfg.core_count; ++i) {
      cpus.emplace_back(std::make_unique<CpuState>(cfg.ring_size));
    }
  }

  trace_init_cfg_t cfg{};
  trace_global_header_disk_t global{};
  ChannelMux channel;
  std::vector<std::unique_ptr<CpuState>> cpus;
  std::mutex control_mu;
  trace_dict_ref_t dict_ref{};
  std::string dict_ref_path;
  std::vector<std::unique_ptr<FilterState>> filter_history;
  std::vector<std::unique_ptr<SamplingState>> sampling_history;
  std::atomic<const FilterState*> filter{nullptr};
  std::atomic<const SamplingState*> sampling{nullptr};
  std::thread flush_worker;
  std::mutex flush_wait_mu;
  std::mutex flush_exec_mu;
  std::condition_variable flush_cv;
  std::atomic<uint64_t> flush_requested{0};
  std::atomic<uint64_t> flush_completed{0};
  std::atomic<bool> force_rotate_requested{false};
  std::atomic<bool> shutdown{false};
  std::atomic<bool> accepting_records{true};
  std::atomic<trace_status_t> flush_status{TRACE_STATUS_OK};
  std::atomic<uint64_t> flushed_records{0};
  std::atomic<uint64_t> flushed_bytes{0};
  std::atomic<uint64_t> flush_latency_ns{0};
};

struct RecordPathGuard {
  CpuState* cpu = nullptr;
  bool engaged = false;

  ~RecordPathGuard() {
    if (!engaged || cpu == nullptr) {
      return;
    }
    cpu->record_gate.clear(std::memory_order_release);
    cpu->active_recorders.fetch_sub(1u, std::memory_order_acq_rel);
  }
};

struct RingReservation {
  uint64_t base_pos = 0;
  uint64_t record_pos = 0;
  uint32_t padding = 0;
  uint32_t record_size = 0;
  uint32_t total_reserved = 0;
};

FilterState BuildFilterState(const trace_filter_rule_t& rule) {
  FilterState state;
  state.raw = rule;
  for (uint32_t i = 0; i < rule.task_count; ++i) {
    state.task_ids.insert(rule.task_ids[i]);
  }
  for (uint32_t i = 0; i < rule.object_count; ++i) {
    state.object_ids.insert(rule.object_ids[i]);
  }
  return state;
}

SamplingState BuildSamplingState(const trace_sampling_policy_t& policy) {
  SamplingState state;
  state.raw = policy;
  return state;
}

trace_dict_ref_t NormalizeDictionaryRef(const trace_dict_ref_t* dict_ref) {
  trace_dict_ref_t normalized{};
  normalized.dict_ver = TRACE_DICT_VERSION;
  normalized.dict_ref_algo = TRACE_DICT_REF_NONE;
  normalized.dict_ref_checksum = 0u;
  normalized.dict_ref_path = nullptr;
  if (dict_ref == nullptr) {
    return normalized;
  }
  normalized = *dict_ref;
  if (normalized.dict_ver == 0u) {
    normalized.dict_ver = TRACE_DICT_VERSION;
  }
  return normalized;
}

std::optional<trace_global_header_disk_t> BuildGlobalHeader(const trace_init_cfg_t& cfg) {
  trace_global_header_disk_t header{};
  header.magic = TRACE_FORMAT_MAGIC;
  header.endian = kEndianLittle;
  header.time_unit = kTimeUnitNanoseconds;
  header.format_ver = TRACE_FORMAT_VERSION;
  header.dict_ver = NormalizeDictionaryRef(&cfg.dict_ref).dict_ver;
  header.header_ver = TRACE_HEADER_VERSION;
  header.core_count = cfg.core_count;
  std::snprintf(header.clock_source, sizeof(header.clock_source), "%s", "steady_clock");
  std::snprintf(header.producer_ver, sizeof(header.producer_ver), "%s", "collector-mvp-1");
  std::snprintf(header.run_id, sizeof(header.run_id), "%s", cfg.run_id == nullptr ? "run" : cfg.run_id);
  return header;
}

void ApplyDictionaryRef(TraceHandle* handle, const trace_dict_ref_t& dict_ref) {
  handle->dict_ref = dict_ref;
  handle->dict_ref_path = dict_ref.dict_ref_path == nullptr ? "" : dict_ref.dict_ref_path;
  handle->dict_ref.dict_ref_path =
      handle->dict_ref_path.empty() ? nullptr : handle->dict_ref_path.c_str();
  handle->global.dict_ver = handle->dict_ref.dict_ver;
}

const FilterState& CurrentFilter(const TraceHandle* handle) {
  return *handle->filter.load(std::memory_order_acquire);
}

const SamplingState& CurrentSampling(const TraceHandle* handle) {
  return *handle->sampling.load(std::memory_order_acquire);
}

bool CoreSelected(uint64_t core_mask, uint16_t core_id) {
  return core_mask == 0u || ((core_mask >> core_id) & 0x1u) != 0u;
}

RingRecordPrefix* PrefixAt(CpuState* cpu, uint64_t absolute_pos) {
  return reinterpret_cast<RingRecordPrefix*>(cpu->buffer.data() +
                                             static_cast<size_t>(absolute_pos % cpu->buffer.size()));
}

uint32_t LoadCommittedSize(const RingRecordPrefix* prefix) {
  return __atomic_load_n(&prefix->committed_size, __ATOMIC_ACQUIRE);
}

void StoreCommittedSize(RingRecordPrefix* prefix, uint32_t value) {
  __atomic_store_n(&prefix->committed_size, value, __ATOMIC_RELEASE);
}

void ResetCommittedSize(RingRecordPrefix* prefix) {
  __atomic_store_n(&prefix->committed_size, 0u, __ATOMIC_RELAXED);
}

bool AcceptByFilter(const FilterState& filter,
                    uint16_t event_id,
                    uint16_t core_id,
                    std::optional<uint32_t> task_id,
                    std::optional<uint64_t> obj_id) {
  if (filter.raw.core_mask != 0u && ((filter.raw.core_mask >> core_id) & 0x1u) == 0u) {
    return false;
  }
  const uint16_t domain = EventDomain(event_id);
  if (filter.raw.event_domain_mask != 0u &&
      ((filter.raw.event_domain_mask >> domain) & 0x1u) == 0u) {
    return false;
  }
  if (filter.raw.drop_unknown && !IsKnownEvent(event_id)) {
    return false;
  }
  if (filter.raw.drop_integrity &&
      (event_id == TRACE_EVENT_LOSS || event_id == TRACE_EVENT_OVERFLOW)) {
    return false;
  }
  if (task_id.has_value() && !filter.task_ids.empty() &&
      filter.task_ids.find(*task_id) == filter.task_ids.end()) {
    return false;
  }
  if (obj_id.has_value() && !filter.object_ids.empty() &&
      filter.object_ids.find(*obj_id) == filter.object_ids.end()) {
    return false;
  }
  return true;
}

bool AcceptBySampling(CpuState* cpu, const SamplingState& sampling, uint16_t* flags) {
  if (sampling.raw.mode == TRACE_SAMPLING_DISABLED) {
    return true;
  }
  const uint64_t counter = cpu->sample_counter.fetch_add(1, std::memory_order_relaxed) + 1u;
  bool accepted = true;
  switch (sampling.raw.mode) {
    case TRACE_SAMPLING_EVERY_N:
      if (sampling.raw.threshold == 0u) {
        accepted = true;
      } else {
        accepted = ((counter - 1u) % sampling.raw.threshold) == 0u;
      }
      break;
    case TRACE_SAMPLING_RATIO:
      if (sampling.raw.ratio_denominator == 0u) {
        accepted = false;
      } else {
        const uint64_t slot = (counter - 1u) % sampling.raw.ratio_denominator;
        accepted = slot < sampling.raw.ratio_numerator;
      }
      break;
    default:
      accepted = true;
      break;
  }
  if (accepted && sampling.raw.mark_sampled) {
    *flags |= kFlagSampled;
  }
  return accepted;
}

uint64_t BufferedBytes(const CpuState* cpu) {
  return cpu->reserve_idx.load(std::memory_order_acquire) -
         cpu->flush_idx.load(std::memory_order_acquire);
}

bool TryReserveRecord(CpuState* cpu, uint32_t payload_len, RingReservation* out) {
  const size_t capacity = cpu->buffer.size();
  const size_t record_size =
      AlignUp(sizeof(RingRecordPrefix) + sizeof(trace_event_header_disk_t) + payload_len,
              alignof(RingRecordPrefix));
  if (record_size > capacity) {
    return false;
  }

  for (;;) {
    const uint64_t flush_idx = cpu->flush_idx.load(std::memory_order_acquire);
    uint64_t reserve_idx = cpu->reserve_idx.load(std::memory_order_relaxed);
    const size_t offset = static_cast<size_t>(reserve_idx % capacity);
    const size_t remaining = capacity - offset;
    uint32_t padding = 0;
    if (remaining < sizeof(RingRecordPrefix) || offset + record_size > capacity) {
      padding = static_cast<uint32_t>(remaining);
    }
    const uint64_t needed = static_cast<uint64_t>(padding) + static_cast<uint64_t>(record_size);
    if (reserve_idx - flush_idx + needed > capacity) {
      return false;
    }
    if (cpu->reserve_idx.compare_exchange_weak(
            reserve_idx,
            reserve_idx + needed,
            std::memory_order_acq_rel,
            std::memory_order_relaxed)) {
      out->base_pos = reserve_idx;
      out->record_pos = reserve_idx + padding;
      out->padding = padding;
      out->record_size = static_cast<uint32_t>(record_size);
      out->total_reserved = static_cast<uint32_t>(needed);
      return true;
    }
  }
}

void PublishPaddingIfNeeded(CpuState* cpu, const RingReservation& reservation) {
  if (reservation.padding < sizeof(RingRecordPrefix)) {
    return;
  }
  RingRecordPrefix* prefix = PrefixAt(cpu, reservation.base_pos);
  ResetCommittedSize(prefix);
  prefix->flags = kRingPrefixFlagPadding;
  StoreCommittedSize(prefix, reservation.padding);
}

void CommitReservedRecord(CpuState* cpu,
                          const RingReservation& reservation,
                          const trace_event_header_disk_t& header,
                          const void* payload,
                          uint32_t payload_len) {
  PublishPaddingIfNeeded(cpu, reservation);

  const size_t offset = static_cast<size_t>(reservation.record_pos % cpu->buffer.size());
  RingRecordPrefix* prefix = PrefixAt(cpu, reservation.record_pos);
  ResetCommittedSize(prefix);
  prefix->flags = 0u;

  uint8_t* cursor = cpu->buffer.data() + offset + sizeof(RingRecordPrefix);
  std::memcpy(cursor, &header, sizeof(header));
  cursor += sizeof(header);
  if (payload_len > 0u) {
    std::memcpy(cursor, payload, payload_len);
    cursor += payload_len;
  }
  const size_t committed_payload = sizeof(RingRecordPrefix) + sizeof(header) + payload_len;
  const size_t padding_bytes = reservation.record_size - committed_payload;
  if (padding_bytes > 0u) {
    std::memset(cursor, 0, padding_bytes);
  }
  StoreCommittedSize(prefix, reservation.record_size);
}

std::vector<uint8_t> BuildOwnedRecordBlob(uint16_t event_id,
                                          uint16_t core_id,
                                          uint64_t seq,
                                          uint64_t timestamp_ns,
                                          uint16_t flags,
                                          const void* payload,
                                          uint32_t payload_len) {
  trace_event_header_disk_t header{};
  header.ver = TRACE_HEADER_VERSION;
  header.flags = flags;
  header.core_id = core_id;
  header.event_id = event_id;
  header.seq = seq;
  header.timestamp = timestamp_ns;
  header.payload_len = payload_len;

  std::vector<uint8_t> blob(sizeof(header) + payload_len);
  std::memcpy(blob.data(), &header, sizeof(header));
  if (payload_len > 0u) {
    std::memcpy(blob.data() + sizeof(header), payload, payload_len);
  }
  return blob;
}

void RecordDrop(CpuState* cpu) {
  cpu->counters.lost_total.fetch_add(1, std::memory_order_relaxed);
  cpu->counters.overflow_total.fetch_add(1, std::memory_order_relaxed);
  cpu->pending_loss.fetch_add(1, std::memory_order_relaxed);
  cpu->pending_overflow.fetch_add(1, std::memory_order_relaxed);
}

void NoteWrittenRecord(CpuState* cpu, uint32_t bytes) {
  cpu->counters.written_records.fetch_add(1, std::memory_order_relaxed);
  cpu->counters.written_bytes.fetch_add(bytes, std::memory_order_relaxed);
}

bool TryAppendPreparedRecord(CpuState* cpu,
                             uint16_t event_id,
                             uint16_t core_id,
                             uint64_t timestamp_ns,
                             const void* payload,
                             uint32_t payload_len,
                             uint16_t flags) {
  RingReservation reservation{};
  if (!TryReserveRecord(cpu, payload_len, &reservation)) {
    return false;
  }

  trace_event_header_disk_t header{};
  header.ver = TRACE_HEADER_VERSION;
  header.flags = flags;
  header.core_id = core_id;
  header.event_id = event_id;
  header.seq = cpu->seq_gen.fetch_add(1, std::memory_order_relaxed) + 1u;
  header.timestamp = timestamp_ns;
  header.payload_len = payload_len;
  CommitReservedRecord(cpu, reservation, header, payload, payload_len);
  NoteWrittenRecord(cpu, static_cast<uint32_t>(sizeof(header) + payload_len));
  return true;
}

uint64_t RequestFlushAsync(TraceHandle* handle, trace_flush_mode_t mode) {
  if (mode == TRACE_FLUSH_MODE_FORCE_ROTATE) {
    handle->force_rotate_requested.store(true, std::memory_order_release);
  }
  const uint64_t request_id =
      handle->flush_requested.fetch_add(1, std::memory_order_acq_rel) + 1u;
  handle->flush_cv.notify_one();
  return request_id;
}

bool TryClaimPending(std::atomic<uint32_t>* pending, uint32_t* count) {
  uint32_t expected = pending->load(std::memory_order_acquire);
  while (expected > 0u) {
    if (pending->compare_exchange_weak(
            expected, 0u, std::memory_order_acq_rel, std::memory_order_acquire)) {
      *count = expected;
      return true;
    }
  }
  return false;
}

bool TryEmitPendingIntegrityRecord(CpuState* cpu,
                                   uint16_t core_id,
                                   uint64_t timestamp_ns,
                                   std::atomic<uint32_t>* pending,
                                   uint16_t event_id,
                                   uint16_t reason) {
  uint32_t count = 0;
  if (!TryClaimPending(pending, &count)) {
    return true;
  }
  IntegrityPayloadDisk integrity{core_id, count, reason};
  if (TryAppendPreparedRecord(cpu,
                              event_id,
                              core_id,
                              timestamp_ns,
                              &integrity,
                              sizeof(integrity),
                              kFlagSynthetic)) {
    return true;
  }
  pending->fetch_add(count, std::memory_order_relaxed);
  return false;
}

void TryEmitPendingIntegrity(TraceHandle* handle, CpuState* cpu, uint16_t core_id, uint64_t timestamp_ns) {
  const bool loss_ok = TryEmitPendingIntegrityRecord(cpu,
                                                     core_id,
                                                     timestamp_ns,
                                                     &cpu->pending_loss,
                                                     TRACE_EVENT_LOSS,
                                                     TRACE_INTEGRITY_REASON_SEQ_GAP);
  const bool overflow_ok =
      TryEmitPendingIntegrityRecord(cpu,
                                    core_id,
                                    timestamp_ns,
                                    &cpu->pending_overflow,
                                    TRACE_EVENT_OVERFLOW,
                                    TRACE_INTEGRITY_REASON_BUFFER_OVERFLOW);
  const bool io_backpressure_ok =
      TryEmitPendingIntegrityRecord(cpu,
                                    core_id,
                                    timestamp_ns,
                                    &cpu->pending_io_backpressure,
                                    TRACE_EVENT_OVERFLOW,
                                    TRACE_INTEGRITY_REASON_IO_BACKPRESSURE);
  if ((!loss_ok || !overflow_ok || !io_backpressure_ok) && handle->cfg.flush_policy.auto_flush) {
    RequestFlushAsync(handle, TRACE_FLUSH_MODE_SYNC);
  }
}

bool EnterRecordPath(TraceHandle* handle, CpuState* cpu, RecordPathGuard* guard) {
  if (!handle->accepting_records.load(std::memory_order_acquire)) {
    return false;
  }
  cpu->active_recorders.fetch_add(1u, std::memory_order_acq_rel);
  if (!handle->accepting_records.load(std::memory_order_acquire)) {
    cpu->active_recorders.fetch_sub(1u, std::memory_order_acq_rel);
    return false;
  }
  while (cpu->record_gate.test_and_set(std::memory_order_acquire)) {
    std::this_thread::yield();
  }
  guard->cpu = cpu;
  guard->engaged = true;
  return true;
}

void WaitForCpuQuiescent(CpuState* cpu) {
  while (cpu->active_recorders.load(std::memory_order_acquire) > 0u) {
    std::this_thread::yield();
  }
}

void WaitForAllProducers(TraceHandle* handle) {
  for (const auto& cpu_ptr : handle->cpus) {
    WaitForCpuQuiescent(cpu_ptr.get());
  }
}

trace_status_t RecordInternal(TraceHandle* handle,
                              uint16_t event_id,
                              uint16_t core_id,
                              uint64_t timestamp_ns,
                              const void* payload,
                              uint32_t payload_len,
                              std::optional<uint32_t> task_id,
                              std::optional<uint64_t> obj_id,
                              uint16_t flags) {
  if (handle == nullptr || core_id >= handle->cpus.size()) {
    return TRACE_STATUS_INVALID_ARG;
  }
  CpuState* cpu = handle->cpus[core_id].get();
  RecordPathGuard guard;
  if (!EnterRecordPath(handle, cpu, &guard)) {
    return TRACE_STATUS_NOT_READY;
  }
  if (!cpu->enabled.load(std::memory_order_acquire)) {
    return TRACE_STATUS_NOT_READY;
  }

  const FilterState& filter = CurrentFilter(handle);
  if (!AcceptByFilter(filter, event_id, core_id, task_id, obj_id)) {
    return TRACE_STATUS_OK;
  }

  if (event_id != TRACE_EVENT_LOSS && event_id != TRACE_EVENT_OVERFLOW) {
    const SamplingState& sampling = CurrentSampling(handle);
    if (!AcceptBySampling(cpu, sampling, &flags)) {
      return TRACE_STATUS_OK;
    }
  }

  TryEmitPendingIntegrity(handle, cpu, core_id, timestamp_ns);
  if (!TryAppendPreparedRecord(cpu, event_id, core_id, timestamp_ns, payload, payload_len, flags)) {
    RecordDrop(cpu);
    if (handle->cfg.flush_policy.auto_flush) {
      RequestFlushAsync(handle, TRACE_FLUSH_MODE_SYNC);
    }
    return TRACE_STATUS_OK;
  }

  if (handle->cfg.flush_policy.auto_flush &&
      handle->cfg.flush_policy.flush_threshold_bytes > 0 &&
      BufferedBytes(cpu) >= handle->cfg.flush_policy.flush_threshold_bytes) {
    RequestFlushAsync(handle, TRACE_FLUSH_MODE_SYNC);
  }
  return TRACE_STATUS_OK;
}

std::vector<std::vector<uint8_t>> DrainReadyRecords(CpuState* cpu) {
  std::vector<std::vector<uint8_t>> records;
  const size_t capacity = cpu->buffer.size();

  for (;;) {
    const uint64_t flush_idx = cpu->flush_idx.load(std::memory_order_relaxed);
    const uint64_t reserve_idx = cpu->reserve_idx.load(std::memory_order_acquire);
    if (flush_idx >= reserve_idx) {
      break;
    }
    const size_t offset = static_cast<size_t>(flush_idx % capacity);
    const size_t remaining = capacity - offset;
    if (remaining < sizeof(RingRecordPrefix)) {
      cpu->flush_idx.store(flush_idx + remaining, std::memory_order_release);
      continue;
    }

    RingRecordPrefix* prefix = PrefixAt(cpu, flush_idx);
    const uint32_t committed = LoadCommittedSize(prefix);
    if (committed == 0u) {
      break;
    }
    if (committed < sizeof(RingRecordPrefix) || committed > remaining) {
      break;
    }
    const uint32_t flags = prefix->flags;
    if ((flags & kRingPrefixFlagPadding) != 0u) {
      prefix->flags = 0u;
      ResetCommittedSize(prefix);
      cpu->flush_idx.store(flush_idx + committed, std::memory_order_release);
      continue;
    }

    trace_event_header_disk_t header{};
    std::memcpy(&header,
                cpu->buffer.data() + offset + sizeof(RingRecordPrefix),
                sizeof(header));
    const size_t blob_size = sizeof(header) + header.payload_len;
    std::vector<uint8_t> blob(blob_size);
    std::memcpy(blob.data(),
                cpu->buffer.data() + offset + sizeof(RingRecordPrefix),
                blob_size);
    prefix->flags = 0u;
    ResetCommittedSize(prefix);
    cpu->flush_idx.store(flush_idx + committed, std::memory_order_release);
    records.push_back(std::move(blob));
  }
  return records;
}

void AppendSyntheticIntegrityRecord(std::vector<std::vector<uint8_t>>* records,
                                    CpuState* cpu,
                                    uint16_t core_id,
                                    uint64_t timestamp_ns,
                                    uint32_t count,
                                    uint16_t reason,
                                    uint16_t event_id) {
  if (count == 0u) {
    return;
  }
  IntegrityPayloadDisk payload{core_id, count, reason};
  const uint64_t seq = cpu->seq_gen.fetch_add(1, std::memory_order_relaxed) + 1u;
  records->push_back(BuildOwnedRecordBlob(event_id,
                                          core_id,
                                          seq,
                                          timestamp_ns,
                                          kFlagSynthetic,
                                          &payload,
                                          sizeof(payload)));
  NoteWrittenRecord(cpu,
                    static_cast<uint32_t>(sizeof(trace_event_header_disk_t) + sizeof(payload)));
}

void CollectPendingIntegrityRecords(CpuState* cpu,
                                    uint16_t core_id,
                                    uint64_t timestamp_ns,
                                    std::vector<std::vector<uint8_t>>* records) {
  const uint32_t pending_loss = cpu->pending_loss.exchange(0u, std::memory_order_acq_rel);
  const uint32_t pending_overflow =
      cpu->pending_overflow.exchange(0u, std::memory_order_acq_rel);
  const uint32_t pending_io_backpressure =
      cpu->pending_io_backpressure.exchange(0u, std::memory_order_acq_rel);
  AppendSyntheticIntegrityRecord(records,
                                 cpu,
                                 core_id,
                                 timestamp_ns,
                                 pending_loss,
                                 TRACE_INTEGRITY_REASON_SEQ_GAP,
                                 TRACE_EVENT_LOSS);
  AppendSyntheticIntegrityRecord(records,
                                 cpu,
                                 core_id,
                                 timestamp_ns,
                                 pending_overflow,
                                 TRACE_INTEGRITY_REASON_BUFFER_OVERFLOW,
                                 TRACE_EVENT_OVERFLOW);
  AppendSyntheticIntegrityRecord(records,
                                 cpu,
                                 core_id,
                                 timestamp_ns,
                                 pending_io_backpressure,
                                 TRACE_INTEGRITY_REASON_IO_BACKPRESSURE,
                                 TRACE_EVENT_OVERFLOW);
}

bool HasFlushableData(const TraceHandle* handle, uint64_t core_mask) {
  for (uint16_t core_id = 0; core_id < handle->cfg.core_count; ++core_id) {
    if (!CoreSelected(core_mask, core_id)) {
      continue;
    }
    const CpuState* cpu = handle->cpus[core_id].get();
    if (cpu->reserve_idx.load(std::memory_order_acquire) >
        cpu->flush_idx.load(std::memory_order_acquire)) {
      return true;
    }
    if (cpu->pending_loss.load(std::memory_order_acquire) > 0u ||
        cpu->pending_overflow.load(std::memory_order_acquire) > 0u ||
        cpu->pending_io_backpressure.load(std::memory_order_acquire) > 0u) {
      return true;
    }
  }
  return false;
}

trace_status_t PerformFlushMasked(TraceHandle* handle, trace_flush_mode_t mode, uint64_t core_mask) {
  const uint64_t flush_begin = NowNsFromHooks(&handle->cfg.port_hooks);
  bool rotate_pending = mode == TRACE_FLUSH_MODE_FORCE_ROTATE;
  trace_global_header_disk_t global = handle->global;
  trace_dict_ref_t dict_ref = handle->dict_ref;
  {
    std::lock_guard<std::mutex> guard(handle->control_mu);
    global = handle->global;
    dict_ref = handle->dict_ref;
  }

  for (uint16_t core_id = 0; core_id < handle->cfg.core_count; ++core_id) {
    if (!CoreSelected(core_mask, core_id)) {
      continue;
    }
    CpuState* cpu = handle->cpus[core_id].get();
    auto records = DrainReadyRecords(cpu);
    if (cpu->pending_loss.load(std::memory_order_acquire) > 0u ||
        cpu->pending_overflow.load(std::memory_order_acquire) > 0u ||
        cpu->pending_io_backpressure.load(std::memory_order_acquire) > 0u) {
      CollectPendingIntegrityRecords(cpu, core_id, flush_begin, &records);
    }
    if (records.empty()) {
      continue;
    }

    const trace_flush_mode_t write_mode =
        rotate_pending ? TRACE_FLUSH_MODE_FORCE_ROTATE : TRACE_FLUSH_MODE_SYNC;
    rotate_pending = false;
    const trace_status_t status = handle->channel.WriteChunk(global, dict_ref, core_id, records, write_mode);
    if (status != TRACE_STATUS_OK) {
      cpu->pending_io_backpressure.fetch_add(static_cast<uint32_t>(records.size()),
                                             std::memory_order_relaxed);
      cpu->counters.io_backpressure_total.fetch_add(records.size(), std::memory_order_relaxed);
      return status;
    }

    handle->flushed_records.fetch_add(records.size(), std::memory_order_relaxed);
    uint64_t flushed_bytes = 0;
    for (const auto& record : records) {
      flushed_bytes += record.size();
    }
    handle->flushed_bytes.fetch_add(flushed_bytes, std::memory_order_relaxed);
  }

  handle->flush_latency_ns.store(NowNsFromHooks(&handle->cfg.port_hooks) - flush_begin,
                                 std::memory_order_relaxed);
  return TRACE_STATUS_OK;
}

void AdvanceFlushCompleted(TraceHandle* handle, uint64_t target) {
  uint64_t current = handle->flush_completed.load(std::memory_order_acquire);
  while (current < target &&
         !handle->flush_completed.compare_exchange_weak(
             current, target, std::memory_order_release, std::memory_order_acquire)) {
  }
}

void FlushWorkerMain(trace_handle_t* opaque_handle) {
  auto* handle = reinterpret_cast<TraceHandle*>(opaque_handle);
  std::unique_lock<std::mutex> lock(handle->flush_wait_mu);
  const bool timed_flush_enabled = handle->cfg.flush_policy.flush_interval_ms > 0u;
  const auto flush_interval = std::chrono::milliseconds(handle->cfg.flush_policy.flush_interval_ms);
  for (;;) {
    if (timed_flush_enabled) {
      const auto deadline = std::chrono::steady_clock::now() + flush_interval;
      handle->flush_cv.wait_until(lock, deadline, [&]() {
        return handle->shutdown.load(std::memory_order_acquire) ||
               handle->flush_completed.load(std::memory_order_acquire) <
                   handle->flush_requested.load(std::memory_order_acquire);
      });
    } else {
      handle->flush_cv.wait(lock, [&]() {
        return handle->shutdown.load(std::memory_order_acquire) ||
               handle->flush_completed.load(std::memory_order_acquire) <
                   handle->flush_requested.load(std::memory_order_acquire);
      });
    }
    const bool shutting_down = handle->shutdown.load(std::memory_order_acquire);
    const uint64_t requested = handle->flush_requested.load(std::memory_order_acquire);
    const uint64_t completed = handle->flush_completed.load(std::memory_order_acquire);
    const bool has_request = completed < requested;
    const bool timed_flush_due =
        timed_flush_enabled && !has_request && HasFlushableData(handle, 0u);
    if (shutting_down && !has_request) {
      break;
    }
    if (!has_request && !timed_flush_due) {
      continue;
    }

    const bool rotate =
        has_request && handle->force_rotate_requested.exchange(false, std::memory_order_acq_rel);
    lock.unlock();
    trace_status_t status = TRACE_STATUS_OK;
    {
      std::lock_guard<std::mutex> flush_guard(handle->flush_exec_mu);
      status = PerformFlushMasked(handle,
                                  rotate ? TRACE_FLUSH_MODE_FORCE_ROTATE : TRACE_FLUSH_MODE_SYNC,
                                  0u);
    }
    if (status != TRACE_STATUS_OK) {
      handle->flush_status.store(status, std::memory_order_release);
    }
    if (has_request) {
      AdvanceFlushCompleted(handle, requested);
      handle->flush_cv.notify_all();
    }
    lock.lock();
  }
}

trace_status_t WaitForFlush(trace_handle_t* opaque_handle, uint64_t request_id, uint32_t deadline_ms) {
  auto* handle = reinterpret_cast<TraceHandle*>(opaque_handle);
  std::unique_lock<std::mutex> lock(handle->flush_wait_mu);
  const auto completed = [&]() {
    return handle->flush_completed.load(std::memory_order_acquire) >= request_id;
  };
  if (deadline_ms == 0u) {
    handle->flush_cv.wait(lock, completed);
  } else {
    const auto timeout = std::chrono::milliseconds(deadline_ms);
    if (!handle->flush_cv.wait_for(lock, timeout, completed)) {
      return TRACE_STATUS_IO_ERROR;
    }
  }
  return handle->flush_status.load(std::memory_order_acquire);
}

}  // namespace

struct trace_handle : public TraceHandle {
  explicit trace_handle(const trace_init_cfg_t& cfg) : TraceHandle(cfg) {}
};

extern "C" {

trace_status_t trace_Init(const trace_init_cfg_t* cfg, trace_handle_t** out_handle) {
  if (cfg == nullptr || out_handle == nullptr || cfg->core_count == 0u || cfg->ring_size == 0u ||
      cfg->channel_cfg.target_count > kMaxChannelTargets) {
    return TRACE_STATUS_INVALID_ARG;
  }
  auto header = BuildGlobalHeader(*cfg);
  if (!header.has_value()) {
    return TRACE_STATUS_INVALID_ARG;
  }

  auto handle = std::make_unique<trace_handle>(*cfg);
  handle->global = *header;
  ApplyDictionaryRef(handle.get(), NormalizeDictionaryRef(&cfg->dict_ref));
  handle->filter_history.emplace_back(std::make_unique<FilterState>(BuildFilterState(cfg->default_filter)));
  handle->sampling_history.emplace_back(
      std::make_unique<SamplingState>(BuildSamplingState(cfg->default_sampling)));
  handle->filter.store(handle->filter_history.back().get(), std::memory_order_release);
  handle->sampling.store(handle->sampling_history.back().get(), std::memory_order_release);

  const trace_status_t open_status = handle->channel.Open();
  if (open_status != TRACE_STATUS_OK) {
    return open_status;
  }
  const trace_status_t write_status = handle->channel.WriteGlobal(handle->global, handle->dict_ref);
  if (write_status != TRACE_STATUS_OK) {
    handle->channel.Close();
    return write_status;
  }

  try {
    handle->flush_worker = std::thread(FlushWorkerMain, reinterpret_cast<trace_handle_t*>(handle.get()));
  } catch (...) {
    handle->channel.Close();
    return TRACE_STATUS_INTERNAL_ERROR;
  }

  *out_handle = handle.release();
  return TRACE_STATUS_OK;
}

trace_status_t trace_Destroy(trace_handle_t* handle) {
  if (handle == nullptr) {
    return TRACE_STATUS_INVALID_ARG;
  }

  handle->accepting_records.store(false, std::memory_order_release);
  for (const auto& cpu_ptr : handle->cpus) {
    cpu_ptr->enabled.store(false, std::memory_order_release);
  }
  WaitForAllProducers(handle);
  handle->flush_status.store(TRACE_STATUS_OK, std::memory_order_release);
  const uint64_t request_id = RequestFlushAsync(handle, TRACE_FLUSH_MODE_SYNC);
  const trace_status_t flush_status = WaitForFlush(handle, request_id, 0u);
  handle->shutdown.store(true, std::memory_order_release);
  handle->flush_cv.notify_all();
  if (handle->flush_worker.joinable()) {
    handle->flush_worker.join();
  }
  handle->channel.Close();
  delete handle;
  return flush_status;
}

trace_status_t trace_Enable(trace_handle_t* handle, uint64_t core_mask) {
  if (handle == nullptr) {
    return TRACE_STATUS_INVALID_ARG;
  }
  for (uint16_t i = 0; i < handle->cfg.core_count; ++i) {
    if (CoreSelected(core_mask, i)) {
      handle->cpus[i]->enabled.store(true, std::memory_order_release);
    }
  }
  return TRACE_STATUS_OK;
}

trace_status_t trace_Disable(trace_handle_t* handle, uint64_t core_mask, uint8_t keep_buffer) {
  if (handle == nullptr) {
    return TRACE_STATUS_INVALID_ARG;
  }
  for (uint16_t i = 0; i < handle->cfg.core_count; ++i) {
    if (CoreSelected(core_mask, i)) {
      handle->cpus[i]->enabled.store(false, std::memory_order_release);
    }
  }
  for (uint16_t i = 0; i < handle->cfg.core_count; ++i) {
    if (CoreSelected(core_mask, i)) {
      WaitForCpuQuiescent(handle->cpus[i].get());
    }
  }

  trace_status_t flush_status = TRACE_STATUS_OK;
  {
    std::lock_guard<std::mutex> flush_guard(handle->flush_exec_mu);
    flush_status = PerformFlushMasked(handle, TRACE_FLUSH_MODE_SYNC, core_mask);
  }
  if (flush_status != TRACE_STATUS_OK) {
    return flush_status;
  }

  if (!keep_buffer) {
    for (uint16_t i = 0; i < handle->cfg.core_count; ++i) {
      if (CoreSelected(core_mask, i)) {
        handle->cpus[i]->ResetBuffer();
      }
    }
  }
  return flush_status;
}

trace_status_t trace_RecordEvent(trace_handle_t* handle,
                                 uint16_t event_id,
                                 const void* payload,
                                 uint32_t payload_len) {
  if (handle == nullptr || (payload == nullptr && payload_len > 0u)) {
    return TRACE_STATUS_INVALID_ARG;
  }
  return RecordInternal(handle,
                        event_id,
                        0u,
                        NowNsFromHooks(&handle->cfg.port_hooks),
                        payload,
                        payload_len,
                        std::nullopt,
                        std::nullopt,
                        0u);
}

trace_status_t trace_RecordTaskSwitch(trace_handle_t* handle, const trace_task_switch_payload_t* payload) {
  if (handle == nullptr || payload == nullptr) {
    return TRACE_STATUS_INVALID_ARG;
  }
  const TaskSwitchPayloadDisk disk{
      payload->core_id,
      payload->prev_task_id,
      payload->next_task_id,
      payload->reason,
  };
  const uint64_t ts =
      payload->timestamp_ns == 0u ? NowNsFromHooks(&handle->cfg.port_hooks) : payload->timestamp_ns;
  return RecordInternal(handle,
                        TRACE_EVENT_CTX_SWITCH,
                        payload->core_id,
                        ts,
                        &disk,
                        sizeof(disk),
                        payload->next_task_id,
                        std::nullopt,
                        0u);
}

trace_status_t trace_RecordSchedDecision(trace_handle_t* handle, const trace_sched_decision_payload_t* payload) {
  if (handle == nullptr || payload == nullptr) {
    return TRACE_STATUS_INVALID_ARG;
  }
  const SchedDecisionPayloadDisk disk{
      payload->core_id,
      payload->selected_task_id,
      payload->rq_len,
      payload->reason,
  };
  const uint64_t ts =
      payload->timestamp_ns == 0u ? NowNsFromHooks(&handle->cfg.port_hooks) : payload->timestamp_ns;
  const std::optional<uint32_t> task_id =
      payload->selected_task_id == TRACE_TASK_ID_IDLE ? std::nullopt
                                                      : std::optional<uint32_t>(payload->selected_task_id);
  return RecordInternal(handle,
                        TRACE_EVENT_SCHED_DECISION,
                        payload->core_id,
                        ts,
                        &disk,
                        sizeof(disk),
                        task_id,
                        std::nullopt,
                        0u);
}

trace_status_t trace_RecordTaskState(trace_handle_t* handle, const trace_task_state_payload_t* payload) {
  if (handle == nullptr || payload == nullptr) {
    return TRACE_STATUS_INVALID_ARG;
  }
  const uint64_t ts =
      payload->timestamp_ns == 0u ? NowNsFromHooks(&handle->cfg.port_hooks) : payload->timestamp_ns;
  switch (payload->kind) {
    case TRACE_TASK_STATE_READY: {
      const TaskReadyPayloadDisk disk{payload->task_id, payload->prio, payload->core_hint, payload->reason};
      return RecordInternal(handle,
                            TRACE_EVENT_TASK_READY,
                            payload->core_id,
                            ts,
                            &disk,
                            sizeof(disk),
                            payload->task_id,
                            std::nullopt,
                            0u);
    }
    case TRACE_TASK_STATE_BLOCK: {
      const TaskBlockPayloadDisk disk{
          payload->task_id,
          payload->wait_obj_id,
          payload->reason,
          payload->owner_task_id,
      };
      return RecordInternal(handle,
                            TRACE_EVENT_TASK_BLOCK,
                            payload->core_id,
                            ts,
                            &disk,
                            sizeof(disk),
                            payload->task_id,
                            payload->wait_obj_id,
                            0u);
    }
    case TRACE_TASK_STATE_WAKEUP: {
      const TaskWakeupPayloadDisk disk{payload->task_id, payload->wake_src, payload->obj_id};
      return RecordInternal(handle,
                            TRACE_EVENT_TASK_WAKEUP,
                            payload->core_id,
                            ts,
                            &disk,
                            sizeof(disk),
                            payload->task_id,
                            payload->obj_id,
                            0u);
    }
    case TRACE_TASK_STATE_DISPATCH: {
      const TaskDispatchPayloadDisk disk{
          payload->task_id,
          payload->core_id,
          payload->prio,
          payload->reason,
      };
      return RecordInternal(handle,
                            TRACE_EVENT_TASK_DISPATCH,
                            payload->core_id,
                            ts,
                            &disk,
                            sizeof(disk),
                            payload->task_id,
                            std::nullopt,
                            0u);
    }
    case TRACE_TASK_STATE_EXIT: {
      const TaskExitPayloadDisk disk{payload->task_id, payload->exit_code};
      return RecordInternal(handle,
                            TRACE_EVENT_TASK_EXIT,
                            payload->core_id,
                            ts,
                            &disk,
                            sizeof(disk),
                            payload->task_id,
                            std::nullopt,
                            0u);
    }
    default:
      return TRACE_STATUS_INVALID_ARG;
  }
}

trace_status_t trace_RecordIRQ(trace_handle_t* handle, const trace_irq_payload_t* payload) {
  if (handle == nullptr || payload == nullptr) {
    return TRACE_STATUS_INVALID_ARG;
  }
  const IrqPayloadDisk disk{payload->irq_id, payload->core_id, payload->nesting_depth};
  const uint64_t ts =
      payload->timestamp_ns == 0u ? NowNsFromHooks(&handle->cfg.port_hooks) : payload->timestamp_ns;
  return RecordInternal(handle,
                        payload->entering ? TRACE_EVENT_IRQ_ENTER : TRACE_EVENT_IRQ_EXIT,
                        payload->core_id,
                        ts,
                        &disk,
                        sizeof(disk),
                        std::nullopt,
                        std::nullopt,
                        kFlagIrqContext);
}

trace_status_t trace_RecordSync(trace_handle_t* handle, const trace_sync_payload_t* payload) {
  if (handle == nullptr || payload == nullptr) {
    return TRACE_STATUS_INVALID_ARG;
  }
  uint16_t event_id = TRACE_EVENT_SYNC_TRY;
  switch (payload->action) {
    case TRACE_SYNC_ACTION_TRY:
      event_id = TRACE_EVENT_SYNC_TRY;
      break;
    case TRACE_SYNC_ACTION_LOCK:
      event_id = TRACE_EVENT_SYNC_LOCK;
      break;
    case TRACE_SYNC_ACTION_UNLOCK:
      event_id = TRACE_EVENT_SYNC_UNLOCK;
      break;
    default:
      return TRACE_STATUS_INVALID_ARG;
  }
  const SyncPayloadDisk disk{payload->task_id, payload->obj_id, payload->obj_type, payload->timeout_ns};
  const uint64_t ts =
      payload->timestamp_ns == 0u ? NowNsFromHooks(&handle->cfg.port_hooks) : payload->timestamp_ns;
  return RecordInternal(handle,
                        event_id,
                        payload->core_id,
                        ts,
                        &disk,
                        sizeof(disk),
                        payload->task_id,
                        payload->obj_id,
                        0u);
}

trace_status_t trace_SetFilter(trace_handle_t* handle, const trace_filter_rule_t* rule) {
  if (handle == nullptr || rule == nullptr) {
    return TRACE_STATUS_INVALID_ARG;
  }
  std::lock_guard<std::mutex> guard(handle->control_mu);
  handle->filter_history.emplace_back(std::make_unique<FilterState>(BuildFilterState(*rule)));
  handle->filter.store(handle->filter_history.back().get(), std::memory_order_release);
  return TRACE_STATUS_OK;
}

trace_status_t trace_SetSampling(trace_handle_t* handle, const trace_sampling_policy_t* policy) {
  if (handle == nullptr || policy == nullptr) {
    return TRACE_STATUS_INVALID_ARG;
  }
  std::lock_guard<std::mutex> guard(handle->control_mu);
  handle->sampling_history.emplace_back(std::make_unique<SamplingState>(BuildSamplingState(*policy)));
  handle->sampling.store(handle->sampling_history.back().get(), std::memory_order_release);
  return TRACE_STATUS_OK;
}

trace_status_t trace_SetDictionaryRef(trace_handle_t* handle, const trace_dict_ref_t* dict_ref) {
  if (handle == nullptr || dict_ref == nullptr) {
    return TRACE_STATUS_INVALID_ARG;
  }
  const trace_dict_ref_t normalized = NormalizeDictionaryRef(dict_ref);
  bool rotate_requested = false;
  {
    std::lock_guard<std::mutex> guard(handle->control_mu);
    rotate_requested = normalized.dict_ver != handle->dict_ref.dict_ver ||
                       normalized.dict_ref_checksum != handle->dict_ref.dict_ref_checksum;
    ApplyDictionaryRef(handle, normalized);
  }
  if (rotate_requested) {
    handle->force_rotate_requested.store(true, std::memory_order_release);
  }
  return TRACE_STATUS_OK;
}

trace_status_t trace_FlushBuffer(trace_handle_t* handle, trace_flush_mode_t mode, uint32_t deadline_ms) {
  if (handle == nullptr) {
    return TRACE_STATUS_INVALID_ARG;
  }
  handle->flush_status.store(TRACE_STATUS_OK, std::memory_order_release);
  const uint64_t request_id = RequestFlushAsync(handle, mode);
  return WaitForFlush(handle, request_id, deadline_ms);
}

trace_status_t trace_GetStats(trace_handle_t* handle, trace_stats_t* out_stats) {
  if (handle == nullptr || out_stats == nullptr) {
    return TRACE_STATUS_INVALID_ARG;
  }

  trace_stats_t snapshot{};
  for (const auto& cpu_ptr : handle->cpus) {
    const CpuState* cpu = cpu_ptr.get();
    snapshot.lost_total += cpu->counters.lost_total.load(std::memory_order_relaxed);
    snapshot.overflow_total += cpu->counters.overflow_total.load(std::memory_order_relaxed);
    snapshot.io_backpressure_total +=
        cpu->counters.io_backpressure_total.load(std::memory_order_relaxed);
    snapshot.written_records += cpu->counters.written_records.load(std::memory_order_relaxed);
    snapshot.written_bytes += cpu->counters.written_bytes.load(std::memory_order_relaxed);
  }
  snapshot.flushed_records = handle->flushed_records.load(std::memory_order_relaxed);
  snapshot.flushed_bytes = handle->flushed_bytes.load(std::memory_order_relaxed);
  snapshot.flush_latency_ns = handle->flush_latency_ns.load(std::memory_order_relaxed);
  *out_stats = snapshot;
  return TRACE_STATUS_OK;
}

}  // extern "C"
