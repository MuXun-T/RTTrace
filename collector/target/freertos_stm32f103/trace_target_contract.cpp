#include "trace_target_contract.hpp"

#include <algorithm>
#include <cstring>
#include <limits>

namespace rtd::p4::freertos_stm32f103 {
TargetCollector::TargetCollector(uint32_t capacity, const char* capture_id)
    : capacity_(std::min<uint32_t>(std::max<uint32_t>(capacity, 1u), kMaxRecords)) {
  if (capture_id != nullptr) std::strncpy(capture_id_.data(), capture_id, capture_id_.size() - 1);
  counters_.buffer_capacity_records = capacity_;
}

uint32_t TargetCollector::Crc32(const uint8_t* data, uint32_t size) const {
  uint32_t crc = 0xFFFFFFFFu;
  for (uint32_t index = 0; index < size; ++index) {
    crc ^= data[index];
    for (int bit = 0; bit < 8; ++bit) { const uint32_t mask = 0u - (crc & 1u); crc = (crc >> 1u) ^ (0xEDB88320u & mask); }
  }
  return ~crc;
}

bool TargetCollector::Append(const Event& event, uint64_t sequence) {
  if (ring_count_ >= capacity_ || event.payload_len > kMaxPayloadBytes) return false;
  ring_[ring_count_] = event;
  sequences_[ring_count_] = sequence;
  ++ring_count_;
  counters_.buffer_high_watermark = std::max(counters_.buffer_high_watermark, ring_count_);
  return true;
}

bool TargetCollector::AppendMarker(uint16_t event_id, uint16_t core_id, uint64_t timestamp, uint32_t delta, uint16_t reason) {
  Event marker{};
  marker.core_id = core_id;
  marker.event_id = event_id;
  marker.timestamp = timestamp;
  // Canonical trace payloads are packed little-endian (HIH = 8 bytes); the
  // public C convenience struct can carry ABI padding and must not be dumped.
  marker.payload_len = 8;
  std::memcpy(marker.payload.data(), &core_id, sizeof(core_id));
  std::memcpy(marker.payload.data() + 2, &delta, sizeof(delta));
  std::memcpy(marker.payload.data() + 6, &reason, sizeof(reason));
  if (!Append(marker, next_sequence_++)) return false;
  ++counters_.integrity_markers;
  return true;
}

bool TargetCollector::AppendPendingMarkers() {
  if (pending_loss_delta_ == 0) return true;
  if (ring_count_ + 2u > capacity_) return false;
  const uint32_t loss_delta = static_cast<uint32_t>(std::min<uint64_t>(pending_loss_delta_, std::numeric_limits<uint32_t>::max()));
  const uint32_t overflow_delta = static_cast<uint32_t>(std::min<uint64_t>(pending_overflow_delta_, std::numeric_limits<uint32_t>::max()));
  // Both bounded appends are pre-reserved; no state/counter is cleared until
  // the pair is present, so a marker pair cannot partially commit.
  if (!AppendMarker(TRACE_EVENT_LOSS, pending_core_id_, pending_timestamp_, loss_delta, TRACE_INTEGRITY_REASON_SEQ_GAP) ||
      !AppendMarker(TRACE_EVENT_OVERFLOW, pending_core_id_, pending_timestamp_, overflow_delta, TRACE_INTEGRITY_REASON_BUFFER_OVERFLOW)) return false;
  emitted_loss_delta_total_ += loss_delta;
  emitted_overflow_delta_total_ += overflow_delta;
  pending_loss_delta_ = pending_overflow_delta_ = 0;
  markers_finalized_ = emitted_loss_delta_total_ == counters_.buffer_overflow_dropped_records &&
      emitted_overflow_delta_total_ == counters_.buffer_overflow_dropped_records;
  return true;
}

bool TargetCollector::Record(const Event& event) {
  const uint64_t source_sequence = next_sequence_++;
  ++counters_.attempted_records;
  if (event.payload_len > kMaxPayloadBytes) {
    ++counters_.dropped_records;
    ++counters_.rejected_payload_records;
    ++counters_.truncations;
    return false;
  }
  if (ring_count_ >= capacity_) {
    ++counters_.dropped_records;
    ++counters_.buffer_overflow_dropped_records;
    ++pending_loss_delta_;
    ++pending_overflow_delta_;
    pending_core_id_ = event.core_id;
    pending_timestamp_ = event.timestamp;
    markers_finalized_ = false;
    return false;
  }
  const uint32_t marker_count = pending_loss_delta_ == 0 ? 0u : 2u;
  if (ring_count_ + marker_count + 1u > capacity_) {
    ++counters_.dropped_records;
    ++counters_.buffer_overflow_dropped_records;
    ++pending_loss_delta_;
    ++pending_overflow_delta_;
    pending_core_id_ = event.core_id;
    pending_timestamp_ = event.timestamp;
    markers_finalized_ = false;
    return false;
  }
  if (!Append(event, source_sequence)) return false;
  ++counters_.accepted_records;
  if (pending_loss_delta_ == 0) return true;
  return AppendPendingMarkers();
}

bool TargetCollector::Write(ByteBuffer* output, const void* data, uint32_t size) const {
  if (output == nullptr || data == nullptr || size > kMaxTraceBytes - output->size) return false;
  std::memcpy(output->bytes.data() + output->size, data, size); output->size += size; return true;
}

bool TargetCollector::FlushCanonicalInto(ByteBuffer* output) {
  CanonicalDrainSnapshot snapshot{};
  if (output == nullptr || !TakeCanonicalDrainSnapshot(false, &snapshot)) return false;
  // Keep the legacy raw-buffer API self-contained: host callers receive a
  // global header with every returned buffer, while FrameForUart removes later
  // duplicates. The board snapshot path uses emit_global directly instead.
  snapshot.emit_global = snapshot.record_count != 0;
  return SerializeCanonicalDrainSnapshot(snapshot, output);
}

bool TargetCollector::TakeCanonicalDrainSnapshot(bool finalize, CanonicalDrainSnapshot* snapshot) {
  if (snapshot == nullptr) return false;
  snapshot->record_count = 0;
  if (finalize && pending_loss_delta_ != 0 && ring_count_ + 2u <= capacity_ && !AppendPendingMarkers()) return false;
  // A board drain can run while the capture gate is off. Treat that bounded
  // empty poll as a successful no-output operation: it must not consume a
  // segment sequence or fabricate a canonical/UART frame.
  if (ring_count_ == 0) return true;
  snapshot->record_count = ring_count_;
  snapshot->segment_seq = segment_seq_++;
  snapshot->emit_global = !uart_global_sent_;
  snapshot->capture_id = capture_id_;
  for (uint32_t index = 0; index < ring_count_; ++index) {
    snapshot->records[index] = ring_[index];
    snapshot->sequences[index] = sequences_[index];
  }
  counters_.flushed_records += ring_count_;
  ring_count_ = 0;
  return true;
}

bool TargetCollector::SerializeCanonicalDrainSnapshot(const CanonicalDrainSnapshot& snapshot, ByteBuffer* output) const {
  if (output == nullptr) return false;
  output->size = 0;
  if (snapshot.record_count == 0) return true;
  trace_global_header_disk_t global{};
  global.magic = TRACE_FORMAT_MAGIC; global.endian = 1; global.time_unit = 1; global.format_ver = TRACE_FORMAT_VERSION;
  global.dict_ver = TRACE_DICT_VERSION; global.header_ver = TRACE_HEADER_VERSION; global.core_count = 1;
  constexpr char kClockSource[] = "dwt_cyccnt";
  constexpr char kProducerVersion[] = "rtd-p4-arm-bounded-v1";
  static_assert(sizeof(kClockSource) <= sizeof(global.clock_source), "clock token must fit including NUL");
  static_assert(sizeof(kProducerVersion) <= sizeof(global.producer_ver), "producer version must fit including NUL");
  std::memcpy(global.clock_source, kClockSource, sizeof(kClockSource));
  std::memcpy(global.producer_ver, kProducerVersion, sizeof(kProducerVersion));
  std::memcpy(global.run_id, snapshot.capture_id.data(), snapshot.capture_id.size() - 1);
  if (snapshot.emit_global && !Write(output, &global, sizeof(global))) return false;
  trace_segment_meta_disk_t segment{};
  segment.magic = TRACE_SEGMENT_META_MAGIC; segment.header_ver = TRACE_HEADER_VERSION; segment.meta_size = sizeof(segment);
  segment.segment_seq = snapshot.segment_seq; segment.prev_segment_seq = snapshot.segment_seq == 0 ? 0 : snapshot.segment_seq - 1;
  segment.dict_ver = TRACE_DICT_VERSION; segment.dict_ref_algo = TRACE_DICT_REF_NONE;
  if (!Write(output, &segment, sizeof(segment))) return false;
  if (output->size + sizeof(trace_chunk_header_disk_t) > kMaxTraceBytes) return false;
  const uint32_t chunk_offset = output->size;
  output->size += sizeof(trace_chunk_header_disk_t);
  const uint32_t payload_offset = output->size;
  for (uint32_t index = 0; index < snapshot.record_count; ++index) {
    trace_event_header_disk_t header{};
    header.ver = TRACE_FORMAT_VERSION;
    header.core_id = snapshot.records[index].core_id; header.event_id = snapshot.records[index].event_id; header.seq = snapshot.sequences[index];
    header.timestamp = snapshot.records[index].timestamp; header.payload_len = snapshot.records[index].payload_len;
    if (!Write(output, &header, sizeof(header)) || !Write(output, snapshot.records[index].payload.data(), snapshot.records[index].payload_len)) return false;
  }
  trace_chunk_header_disk_t chunk{};
  chunk.magic = TRACE_CHUNK_MAGIC;
  chunk.header_ver = TRACE_HEADER_VERSION;
  chunk.core_id = snapshot.records[0].core_id; chunk.record_count = snapshot.record_count; chunk.payload_bytes = output->size - payload_offset;
  chunk.chunk_start_ts = snapshot.records[0].timestamp; chunk.chunk_end_ts = snapshot.records[snapshot.record_count - 1].timestamp;
  chunk.seq_begin = snapshot.sequences[0]; chunk.seq_end = snapshot.sequences[snapshot.record_count - 1];
  chunk.dict_ver = TRACE_DICT_VERSION;
  chunk.chunk_crc = Crc32(output->bytes.data() + payload_offset, chunk.payload_bytes);
  std::memcpy(output->bytes.data() + chunk_offset, &chunk, sizeof(chunk));
  return true;
}

ByteBuffer TargetCollector::FlushCanonical() {
  ByteBuffer output{};
  (void)FlushCanonicalInto(&output);
  return output;
}

bool TargetCollector::FinalizeCanonicalInto(ByteBuffer* output) {
  CanonicalDrainSnapshot snapshot{};
  if (output == nullptr || !TakeCanonicalDrainSnapshot(true, &snapshot)) return false;
  snapshot.emit_global = snapshot.record_count != 0;
  return SerializeCanonicalDrainSnapshot(snapshot, output);
}

ByteBuffer TargetCollector::FinalizeCanonical() {
  ByteBuffer output{};
  (void)FinalizeCanonicalInto(&output);
  return output;
}

bool TargetCollector::FrameForUart(const ByteBuffer& raw, WireBuffer* wire) {
  if (wire == nullptr || raw.size < sizeof(trace_segment_meta_disk_t)) return false;
  uint32_t payload_offset = 0;
  uint32_t payload_size = raw.size;
  uint32_t magic{};
  if (raw.size >= sizeof(trace_global_header_disk_t)) std::memcpy(&magic, raw.bytes.data(), sizeof(magic));
  const bool has_global = magic == TRACE_FORMAT_MAGIC;
  if (!uart_global_sent_ && !has_global) return false;
  if (uart_global_sent_ && has_global) { payload_offset = sizeof(trace_global_header_disk_t); payload_size -= payload_offset; }
  if (payload_size + 12u > kMaxWireBytes) return false;
  const uint8_t version = kUartFrameVersion;
  const uint8_t type = kUartFrameTypeCanonicalTrace;
  uint32_t crc = Crc32(raw.bytes.data() + payload_offset, payload_size); wire->size = 0;
  auto append = [wire](const void* data, uint32_t size) { if (size > kMaxWireBytes-wire->size) return false; std::memcpy(wire->bytes.data()+wire->size,data,size); wire->size+=size; return true; };
  if (!append(&kUartFrameMagic,2) || !append(&version,1) || !append(&type,1) || !append(&payload_size,4) || !append(&crc,4) || !append(raw.bytes.data()+payload_offset,payload_size)) return false;
  uart_global_sent_ = true;
  ++counters_.wire_frames; counters_.wire_bytes += wire->size; return true;
}

bool TargetCollector::FrameCanonicalDrainSnapshotForUart(const ByteBuffer& raw, WireBuffer* wire) const {
  if (wire == nullptr || raw.size < sizeof(trace_segment_meta_disk_t) || raw.size + 12u > kMaxWireBytes) return false;
  const uint8_t version = kUartFrameVersion;
  const uint8_t type = kUartFrameTypeCanonicalTrace;
  const uint32_t payload_size = raw.size;
  const uint32_t crc = Crc32(raw.bytes.data(), payload_size);
  wire->size = 0;
  auto append = [wire](const void* data, uint32_t size) { if (size > kMaxWireBytes-wire->size) return false; std::memcpy(wire->bytes.data()+wire->size,data,size); wire->size+=size; return true; };
  return append(&kUartFrameMagic, 2) && append(&version, 1) && append(&type, 1) &&
      append(&payload_size, 4) && append(&crc, 4) && append(raw.bytes.data(), payload_size);
}

void TargetCollector::CommitCanonicalDrainSnapshot(const CanonicalDrainSnapshot& snapshot, uint32_t wire_bytes) {
  if (snapshot.record_count == 0 || wire_bytes == 0) return;
  if (snapshot.emit_global) uart_global_sent_ = true;
  ++counters_.wire_frames;
  counters_.wire_bytes += wire_bytes;
}
bool TargetCollector::RebuildRawFromUart(const uint8_t* wire, uint32_t wire_size, CanonicalRawBuffer* raw) {
  if (wire == nullptr || raw == nullptr || wire_size == 0) { ++counters_.truncations; return false; }
  raw->size = 0;
  uint32_t offset = 0;
  bool saw_canonical = false;
  std::array<uint8_t, sizeof(trace_global_header_disk_t)> first_global{};
  while (offset < wire_size) {
    if (wire_size - offset < 12u) { ++counters_.truncations; return false; }
    uint16_t magic{}; uint8_t version{}; uint8_t type{}; uint32_t length{}; uint32_t crc{};
    std::memcpy(&magic, wire + offset, 2); std::memcpy(&version, wire + offset + 2, 1); std::memcpy(&type, wire + offset + 3, 1);
    std::memcpy(&length, wire + offset + 4, 4); std::memcpy(&crc, wire + offset + 8, 4); offset += 12;
    if (magic != kUartFrameMagic || version != kUartFrameVersion || length > kMaxTraceBytes || length > wire_size - offset) { ++counters_.truncations; return false; }
    if (type != kUartFrameTypeCanonicalTrace && type != kUartFrameTypeBoot && type != kUartFrameTypeConfig && type != kUartFrameTypeCounter) { ++counters_.truncations; return false; }
    if (Crc32(wire + offset, length) != crc) { ++counters_.crc_failures; return false; }
    if (type == kUartFrameTypeCanonicalTrace) {
      if (!saw_canonical) {
        if (length < sizeof(trace_global_header_disk_t)) { ++counters_.truncations; return false; }
        uint32_t trace_magic{}; std::memcpy(&trace_magic, wire + offset, sizeof(trace_magic));
        if (trace_magic != TRACE_FORMAT_MAGIC) { ++counters_.truncations; return false; }
        std::memcpy(first_global.data(), wire + offset, first_global.size());
        saw_canonical = true;
      } else if (length < sizeof(trace_segment_meta_disk_t)) { ++counters_.truncations; return false; }
      const uint32_t prefix = saw_canonical && raw->size != 0 ? static_cast<uint32_t>(first_global.size()) : 0;
      if (prefix + length > kMaxRebuiltRawBytes - raw->size) { ++counters_.truncations; return false; }
      if (prefix != 0) { std::memcpy(raw->bytes.data() + raw->size, first_global.data(), prefix); raw->size += prefix; }
      std::memcpy(raw->bytes.data() + raw->size, wire + offset, length); raw->size += length;
    }
    offset += length;
  }
  if (!saw_canonical) { ++counters_.truncations; return false; }
  return true;
}
bool TargetCollector::AttestNaturalOverflow(const NaturalOverflowAttestation& a) {
  const auto digest_is_valid = [](const std::array<char, 65>& value) {
    for (size_t index = 0; index < 64; ++index) {
      const char c = value[index];
      if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    }
    return value[64] == '\0';
  };
  counters_.natural_overflow = digest_is_valid(a.source_sha256) && digest_is_valid(a.workload_config_sha256) &&
      a.producer_drain_competition && !a.force_pressure_requested && !a.masking_requested &&
      !a.offline_deletion_requested && !a.synthetic_marker_requested &&
      counters_.buffer_overflow_dropped_records > 0 && counters_.buffer_high_watermark == capacity_ &&
      pending_loss_delta_ == 0 && markers_finalized_ &&
      emitted_loss_delta_total_ == counters_.buffer_overflow_dropped_records &&
      emitted_overflow_delta_total_ == counters_.buffer_overflow_dropped_records;
  return counters_.natural_overflow;
}
void TargetCollector::NoteDecodedRecords(uint64_t decoded_records) { counters_.decoded_records = decoded_records; }
void TargetCollector::NoteWireFrame(uint32_t wire_bytes) { ++counters_.wire_frames; counters_.wire_bytes += wire_bytes; }
void TargetCollector::NoteBackpressure() { ++counters_.backpressure_total; }
void TargetCollector::NoteTruncation() { ++counters_.truncations; }
CounterSnapshot TargetCollector::Counters() const {
  return counters_;
}

size_t TargetCollector::PendingRecords() const { return ring_count_; }

}  // namespace rtd::p4::freertos_stm32f103
