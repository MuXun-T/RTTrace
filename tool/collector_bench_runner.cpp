#include "trace_api.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;

struct RouteBehavior {
  std::string path;
  uint32_t fail_after_writes = std::numeric_limits<uint32_t>::max();
  uint32_t remaining_failures = 0;
  uint32_t write_delay_ms = 0;
  trace_status_t failure_status = TRACE_STATUS_IO_ERROR;
  uint32_t writes = 0;
};

struct BenchSink {
  std::mutex mu;
  std::string current_path;
  std::vector<RouteBehavior> routes;
  uint64_t total_bytes = 0;
  uint64_t write_calls = 0;
};

RouteBehavior* FindRoute(BenchSink* sink, const std::string& path) {
  for (auto& route : sink->routes) {
    if (route.path == path) {
      return &route;
    }
  }
  return nullptr;
}

trace_status_t SinkOpen(void* user_ctx, const char* output_path) {
  auto* sink = static_cast<BenchSink*>(user_ctx);
  std::lock_guard<std::mutex> guard(sink->mu);
  sink->current_path = output_path == nullptr ? "" : output_path;
  return TRACE_STATUS_OK;
}

trace_status_t SinkWrite(void* user_ctx, const void* data, uint32_t size) {
  (void)data;
  auto* sink = static_cast<BenchSink*>(user_ctx);
  uint32_t delay_ms = 0;
  trace_status_t failure_status = TRACE_STATUS_OK;
  bool should_fail = false;
  {
    std::lock_guard<std::mutex> guard(sink->mu);
    sink->write_calls += 1;
    RouteBehavior* route = FindRoute(sink, sink->current_path);
    if (route != nullptr) {
      route->writes += 1;
      delay_ms = route->write_delay_ms;
      if (route->writes > route->fail_after_writes && route->remaining_failures > 0) {
        should_fail = true;
        failure_status = route->failure_status;
        if (route->remaining_failures != std::numeric_limits<uint32_t>::max()) {
          route->remaining_failures -= 1;
        }
      }
    }
  }
  if (delay_ms > 0) {
    std::this_thread::sleep_for(std::chrono::milliseconds(delay_ms));
  }
  if (should_fail) {
    return failure_status;
  }
  std::lock_guard<std::mutex> guard(sink->mu);
  sink->total_bytes += size;
  return TRACE_STATUS_OK;
}

trace_status_t SinkClose(void* user_ctx) {
  auto* sink = static_cast<BenchSink*>(user_ctx);
  std::lock_guard<std::mutex> guard(sink->mu);
  sink->current_path.clear();
  return TRACE_STATUS_OK;
}

struct ScenarioResult {
  std::string scenario;
  std::string status = "ok";
  std::string message;
  uint64_t events_total = 0;
  double duration_sec = 0.0;
  double events_per_sec = 0.0;
  uint64_t p50_record_latency_ns = 0;
  uint64_t p95_record_latency_ns = 0;
  uint64_t p99_record_latency_ns = 0;
  uint64_t flush_latency_ns = 0;
  trace_status_t flush_status = TRACE_STATUS_OK;
  trace_status_t destroy_status = TRACE_STATUS_OK;
  trace_stats_t stats{};
};

uint64_t Percentile(const std::vector<uint64_t>& values, double ratio) {
  if (values.empty()) {
    return 0;
  }
  const size_t index = static_cast<size_t>(ratio * static_cast<double>(values.size() - 1));
  return values[index];
}

void FinalizeLatencies(std::vector<uint64_t>* latencies, ScenarioResult* result) {
  std::sort(latencies->begin(), latencies->end());
  result->p50_record_latency_ns = Percentile(*latencies, 0.50);
  result->p95_record_latency_ns = Percentile(*latencies, 0.95);
  result->p99_record_latency_ns = Percentile(*latencies, 0.99);
}

trace_task_state_payload_t MakeReady(uint16_t core_id, uint32_t task_id, uint64_t timestamp_ns) {
  trace_task_state_payload_t ready{};
  ready.timestamp_ns = timestamp_ns;
  ready.core_id = core_id;
  ready.kind = TRACE_TASK_STATE_READY;
  ready.task_id = task_id;
  ready.prio = 5;
  ready.reason = TRACE_READY_REASON_CREATE;
  return ready;
}

trace_init_cfg_t BaseCfg(BenchSink* sink, const char* run_id, uint16_t core_count, uint32_t ring_size) {
  trace_init_cfg_t cfg{};
  cfg.core_count = core_count;
  cfg.ring_size = ring_size;
  cfg.run_id = run_id;
  cfg.channel_cfg.type = TRACE_CHANNEL_SERIAL;
  cfg.channel_cfg.output_path = "serial://bench";
  cfg.default_sampling.mode = TRACE_SAMPLING_DISABLED;
  cfg.port_hooks.output_open = SinkOpen;
  cfg.port_hooks.output_write = SinkWrite;
  cfg.port_hooks.output_close = SinkClose;
  cfg.port_hooks.user_ctx = sink;
  return cfg;
}

bool Ensure(trace_status_t status, const char* message, ScenarioResult* result) {
  if (status == TRACE_STATUS_OK) {
    return true;
  }
  result->status = "error";
  result->message = message;
  return false;
}

ScenarioResult RunSingleCore(uint32_t events) {
  ScenarioResult result{};
  result.scenario = "single_core";
  BenchSink sink{};
  trace_init_cfg_t cfg = BaseCfg(&sink, "collector-bench-single", 1, 1u << 20);
  trace_handle_t* handle = nullptr;
  if (!Ensure(trace_Init(&cfg, &handle), "trace_Init failed", &result)) {
    return result;
  }
  if (!Ensure(trace_Enable(handle, 0x1u), "trace_Enable failed", &result)) {
    trace_Destroy(handle);
    return result;
  }

  std::vector<uint64_t> latencies;
  latencies.reserve(events);
  const auto bench_begin = Clock::now();
  for (uint32_t index = 0; index < events; ++index) {
    trace_task_state_payload_t ready = MakeReady(0, 1000 + index, 1000 + index);
    const auto start = Clock::now();
    const trace_status_t status = trace_RecordTaskState(handle, &ready);
    const auto end = Clock::now();
    if (status != TRACE_STATUS_OK) {
      result.status = "error";
      result.message = "trace_RecordTaskState failed";
      break;
    }
    latencies.push_back(static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count()));
  }
  const auto bench_end = Clock::now();
  const auto flush_begin = Clock::now();
  result.flush_status = trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0);
  const auto flush_end = Clock::now();
  trace_GetStats(handle, &result.stats);
  result.destroy_status = trace_Destroy(handle);

  result.events_total = events;
  result.duration_sec = std::chrono::duration<double>(bench_end - bench_begin).count();
  result.events_per_sec = result.duration_sec > 0.0 ? static_cast<double>(events) / result.duration_sec : 0.0;
  result.flush_latency_ns =
      static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(flush_end - flush_begin).count());
  FinalizeLatencies(&latencies, &result);
  if (result.flush_status != TRACE_STATUS_OK || result.destroy_status != TRACE_STATUS_OK) {
    result.status = "error";
    result.message = "flush or destroy failed";
  }
  return result;
}

ScenarioResult RunMultiCore(uint32_t events_per_core, uint16_t core_count) {
  ScenarioResult result{};
  result.scenario = "multi_core";
  BenchSink sink{};
  trace_init_cfg_t cfg = BaseCfg(&sink, "collector-bench-multi", core_count, 1u << 20);
  trace_handle_t* handle = nullptr;
  if (!Ensure(trace_Init(&cfg, &handle), "trace_Init failed", &result)) {
    return result;
  }
  const uint64_t enable_mask = core_count >= 64 ? 0u : ((1ull << core_count) - 1u);
  if (!Ensure(trace_Enable(handle, enable_mask), "trace_Enable failed", &result)) {
    trace_Destroy(handle);
    return result;
  }

  std::vector<std::vector<uint64_t>> latency_rows(core_count);
  std::vector<std::thread> producers;
  const auto bench_begin = Clock::now();
  for (uint16_t core_id = 0; core_id < core_count; ++core_id) {
    latency_rows[core_id].reserve(events_per_core);
    producers.emplace_back([&, core_id]() {
      for (uint32_t index = 0; index < events_per_core; ++index) {
        trace_task_state_payload_t ready =
            MakeReady(core_id, 2000 + core_id * events_per_core + index, 2000 + index);
        const auto start = Clock::now();
        const trace_status_t status = trace_RecordTaskState(handle, &ready);
        const auto end = Clock::now();
        if (status != TRACE_STATUS_OK) {
          result.status = "error";
          result.message = "trace_RecordTaskState failed";
          return;
        }
        latency_rows[core_id].push_back(static_cast<uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count()));
      }
    });
  }
  for (auto& producer : producers) {
    producer.join();
  }
  const auto bench_end = Clock::now();

  const auto flush_begin = Clock::now();
  result.flush_status = trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0);
  const auto flush_end = Clock::now();
  trace_GetStats(handle, &result.stats);
  result.destroy_status = trace_Destroy(handle);

  std::vector<uint64_t> latencies;
  for (const auto& row : latency_rows) {
    latencies.insert(latencies.end(), row.begin(), row.end());
  }
  result.events_total = static_cast<uint64_t>(events_per_core) * core_count;
  result.duration_sec = std::chrono::duration<double>(bench_end - bench_begin).count();
  result.events_per_sec = result.duration_sec > 0.0 ? static_cast<double>(result.events_total) / result.duration_sec : 0.0;
  result.flush_latency_ns =
      static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(flush_end - flush_begin).count());
  FinalizeLatencies(&latencies, &result);
  if (result.flush_status != TRACE_STATUS_OK || result.destroy_status != TRACE_STATUS_OK) {
    result.status = "error";
    result.message = "flush or destroy failed";
  }
  return result;
}

ScenarioResult RunSlowFallback(uint32_t events) {
  ScenarioResult result{};
  result.scenario = "slow_fallback";
  BenchSink sink{};
  sink.routes.push_back({"serial://primary-slow", 2u, std::numeric_limits<uint32_t>::max(), 50u, TRACE_STATUS_IO_ERROR});
  trace_init_cfg_t cfg = BaseCfg(&sink, "collector-bench-fallback", 1, 1u << 18);
  cfg.channel_cfg.target_count = 2;
  cfg.channel_cfg.targets[0] = {TRACE_CHANNEL_SERIAL, "serial://primary-slow", 0, 0};
  cfg.channel_cfg.targets[1] = {TRACE_CHANNEL_SERIAL, "serial://fallback-fast", 0, 0};

  trace_handle_t* handle = nullptr;
  if (!Ensure(trace_Init(&cfg, &handle), "trace_Init failed", &result)) {
    return result;
  }
  if (!Ensure(trace_Enable(handle, 0x1u), "trace_Enable failed", &result)) {
    trace_Destroy(handle);
    return result;
  }

  std::vector<uint64_t> latencies;
  latencies.reserve(events);
  const auto bench_begin = Clock::now();
  for (uint32_t index = 0; index < events; ++index) {
    trace_task_state_payload_t ready = MakeReady(0, 3000 + index, 3000 + index);
    const auto start = Clock::now();
    const trace_status_t status = trace_RecordTaskState(handle, &ready);
    const auto end = Clock::now();
    if (status != TRACE_STATUS_OK) {
      result.status = "error";
      result.message = "trace_RecordTaskState failed";
      break;
    }
    latencies.push_back(static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count()));
  }
  const auto bench_end = Clock::now();

  const auto flush_begin = Clock::now();
  result.flush_status = trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0);
  const auto flush_end = Clock::now();
  trace_GetStats(handle, &result.stats);
  result.destroy_status = trace_Destroy(handle);

  result.events_total = events;
  result.duration_sec = std::chrono::duration<double>(bench_end - bench_begin).count();
  result.events_per_sec = result.duration_sec > 0.0 ? static_cast<double>(events) / result.duration_sec : 0.0;
  result.flush_latency_ns =
      static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(flush_end - flush_begin).count());
  FinalizeLatencies(&latencies, &result);
  if (result.flush_status != TRACE_STATUS_OK || result.destroy_status != TRACE_STATUS_OK) {
    result.status = "error";
    result.message = "flush or destroy failed";
  }
  return result;
}

ScenarioResult RunSoak(uint16_t core_count, double duration_sec) {
  ScenarioResult result{};
  result.scenario = "soak";
  BenchSink sink{};
  trace_init_cfg_t cfg = BaseCfg(&sink, "collector-bench-soak", core_count, 1u << 22);
  cfg.flush_policy.auto_flush = 1;
  cfg.flush_policy.flush_threshold_bytes = 1024;
  cfg.flush_policy.flush_interval_ms = 1;

  trace_handle_t* handle = nullptr;
  if (!Ensure(trace_Init(&cfg, &handle), "trace_Init failed", &result)) {
    return result;
  }
  const uint64_t enable_mask = core_count >= 64 ? 0u : ((1ull << core_count) - 1u);
  if (!Ensure(trace_Enable(handle, enable_mask), "trace_Enable failed", &result)) {
    trace_Destroy(handle);
    return result;
  }

  std::atomic<bool> stop{false};
  std::atomic<uint64_t> events_total{0};
  std::vector<std::vector<uint64_t>> latency_rows(core_count);
  std::vector<std::thread> producers;
  const auto bench_begin = Clock::now();
  for (uint16_t core_id = 0; core_id < core_count; ++core_id) {
    producers.emplace_back([&, core_id]() {
      uint32_t index = 0;
      while (!stop.load(std::memory_order_acquire)) {
        trace_task_state_payload_t ready =
            MakeReady(core_id, 4000 + core_id * 100000 + index, 4000 + index);
        const auto start = Clock::now();
        const trace_status_t status = trace_RecordTaskState(handle, &ready);
        const auto end = Clock::now();
        if (status != TRACE_STATUS_OK) {
          result.status = "error";
          result.message = "trace_RecordTaskState failed";
          stop.store(true, std::memory_order_release);
          return;
        }
        latency_rows[core_id].push_back(static_cast<uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count()));
        events_total.fetch_add(1u, std::memory_order_relaxed);
        ++index;
      }
    });
  }

  std::this_thread::sleep_for(std::chrono::duration<double>(duration_sec));
  stop.store(true, std::memory_order_release);
  for (auto& producer : producers) {
    producer.join();
  }
  const auto bench_end = Clock::now();

  const auto flush_begin = Clock::now();
  result.flush_status = trace_FlushBuffer(handle, TRACE_FLUSH_MODE_SYNC, 0);
  const auto flush_end = Clock::now();
  trace_GetStats(handle, &result.stats);
  result.destroy_status = trace_Destroy(handle);

  std::vector<uint64_t> latencies;
  for (const auto& row : latency_rows) {
    latencies.insert(latencies.end(), row.begin(), row.end());
  }
  result.events_total = events_total.load(std::memory_order_relaxed);
  result.duration_sec = std::chrono::duration<double>(bench_end - bench_begin).count();
  result.events_per_sec =
      result.duration_sec > 0.0 ? static_cast<double>(result.events_total) / result.duration_sec : 0.0;
  result.flush_latency_ns =
      static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(flush_end - flush_begin).count());
  FinalizeLatencies(&latencies, &result);
  if (result.flush_status != TRACE_STATUS_OK || result.destroy_status != TRACE_STATUS_OK) {
    result.status = "error";
    result.message = "flush or destroy failed";
  }
  return result;
}

std::string Escape(const std::string& value) {
  std::string escaped;
  escaped.reserve(value.size());
  for (char ch : value) {
    if (ch == '\\' || ch == '"') {
      escaped.push_back('\\');
    }
    escaped.push_back(ch);
  }
  return escaped;
}

void PrintResult(const ScenarioResult& result) {
  std::cout << "{";
  std::cout << "\"scenario\":\"" << Escape(result.scenario) << "\",";
  std::cout << "\"status\":\"" << Escape(result.status) << "\",";
  std::cout << "\"message\":\"" << Escape(result.message) << "\",";
  std::cout << "\"events_total\":" << result.events_total << ",";
  std::cout << "\"duration_sec\":" << result.duration_sec << ",";
  std::cout << "\"events_per_sec\":" << result.events_per_sec << ",";
  std::cout << "\"record_latency_ns\":{";
  std::cout << "\"p50\":" << result.p50_record_latency_ns << ",";
  std::cout << "\"p95\":" << result.p95_record_latency_ns << ",";
  std::cout << "\"p99\":" << result.p99_record_latency_ns << "},";
  std::cout << "\"flush_latency_ns\":" << result.flush_latency_ns << ",";
  std::cout << "\"flush_status\":" << static_cast<int>(result.flush_status) << ",";
  std::cout << "\"destroy_status\":" << static_cast<int>(result.destroy_status) << ",";
  std::cout << "\"stats\":{";
  std::cout << "\"lost_total\":" << result.stats.lost_total << ",";
  std::cout << "\"overflow_total\":" << result.stats.overflow_total << ",";
  std::cout << "\"io_backpressure_total\":" << result.stats.io_backpressure_total << ",";
  std::cout << "\"written_records\":" << result.stats.written_records << ",";
  std::cout << "\"flushed_records\":" << result.stats.flushed_records << ",";
  std::cout << "\"written_bytes\":" << result.stats.written_bytes << ",";
  std::cout << "\"flushed_bytes\":" << result.stats.flushed_bytes << ",";
  std::cout << "\"flush_latency_ns\":" << result.stats.flush_latency_ns << "}";
  std::cout << "}\n";
}

std::string ArgValue(int argc, char** argv, const std::string& flag, const std::string& fallback = "") {
  for (int index = 1; index + 1 < argc; ++index) {
    if (argv[index] == flag) {
      return argv[index + 1];
    }
  }
  return fallback;
}

}  // namespace

int main(int argc, char** argv) {
  const std::string scenario = ArgValue(argc, argv, "--scenario", "single_core");
  const uint32_t events = static_cast<uint32_t>(std::stoul(ArgValue(argc, argv, "--events", "20000")));
  const uint16_t cores = static_cast<uint16_t>(std::stoul(ArgValue(argc, argv, "--cores", "4")));
  const double duration_sec = std::stod(ArgValue(argc, argv, "--duration-sec", "5"));

  ScenarioResult result;
  if (scenario == "single_core") {
    result = RunSingleCore(events);
  } else if (scenario == "multi_core") {
    result = RunMultiCore(events, cores);
  } else if (scenario == "slow_fallback") {
    result = RunSlowFallback(events);
  } else if (scenario == "soak") {
    result = RunSoak(cores, duration_sec);
  } else {
    result.scenario = scenario;
    result.status = "error";
    result.message = "unknown scenario";
  }
  PrintResult(result);
  return result.status == "ok" ? 0 : 1;
}
