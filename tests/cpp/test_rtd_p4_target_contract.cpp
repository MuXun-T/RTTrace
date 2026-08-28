#include <cassert>
#include <cstddef>
#include <cstring>
#include <type_traits>

#include "trace_api.h"
#include "trace_target_contract.hpp"

using rtd::p4::freertos_stm32f103::ByteBuffer;
using rtd::p4::freertos_stm32f103::CanonicalRawBuffer;
using rtd::p4::freertos_stm32f103::CanonicalDrainSnapshot;
using rtd::p4::freertos_stm32f103::Event;
using rtd::p4::freertos_stm32f103::NaturalOverflowAttestation;
using rtd::p4::freertos_stm32f103::TargetCollector;
using rtd::p4::freertos_stm32f103::WireBuffer;

namespace {
Event MakeEvent(uint16_t event_id, uint64_t timestamp) {
  Event event{};
  event.core_id = 0;
  event.event_id = event_id;
  event.timestamp = timestamp;
  return event;
}

NaturalOverflowAttestation MakeAttestation() {
  NaturalOverflowAttestation attestation{};
  std::memset(attestation.source_sha256.data(), 'a', 64);
  std::memset(attestation.workload_config_sha256.data(), 'b', 64);
  attestation.producer_drain_competition = true;
  return attestation;
}

trace_event_header_disk_t EventHeaderAt(const ByteBuffer& trace, size_t offset) {
  trace_event_header_disk_t header{};
  std::memcpy(&header, trace.bytes.data() + offset, sizeof(header));
  return header;
}
}  // namespace

int main() {
  static_assert(std::is_trivially_copyable<Event>::value, "target event storage must be fixed and POD");
  static_assert(rtd::p4::freertos_stm32f103::kP4BootPayloadBytes == 40u, "fixed BOOT wire ABI");
  static_assert(rtd::p4::freertos_stm32f103::kP4ConfigPayloadBytes == 147u, "fixed CONFIG wire ABI");
  static_assert(rtd::p4::freertos_stm32f103::kP4CounterPayloadBytes == 114u, "fixed COUNTER wire ABI");
  TargetCollector out_api(2, "capture:p4-out-api");
  TargetCollector by_value_api(2, "capture:p4-out-api");
  assert(out_api.Record(MakeEvent(TRACE_EVENT_TASK_READY, 1)));
  assert(by_value_api.Record(MakeEvent(TRACE_EVENT_TASK_READY, 1)));
  ByteBuffer out_api_trace{};
  assert(out_api.FlushCanonicalInto(&out_api_trace));
  const ByteBuffer by_value_trace = by_value_api.FlushCanonical();
  assert(out_api_trace.size == by_value_trace.size);
  assert(std::memcmp(out_api_trace.bytes.data(), by_value_trace.bytes.data(), out_api_trace.size) == 0);
  TargetCollector null_output(2, "capture:p4-null");
  assert(null_output.Record(MakeEvent(TRACE_EVENT_TASK_READY, 2)));
  const auto null_before = null_output.Counters();
  assert(!null_output.FlushCanonicalInto(nullptr));
  assert(null_output.PendingRecords() == 1 && null_output.Counters().flushed_records == null_before.flushed_records);
  ByteBuffer null_flush{};
  assert(null_output.FlushCanonicalInto(&null_flush) && null_flush.size != 0);
  TargetCollector null_finalize(2, "capture:p4-null-finalize");
  assert(null_finalize.Record(MakeEvent(TRACE_EVENT_TASK_READY, 3)));
  assert(!null_finalize.FinalizeCanonicalInto(nullptr));
  assert(null_finalize.PendingRecords() == 1);
  ByteBuffer final_out{};
  assert(null_finalize.FinalizeCanonicalInto(&final_out) && final_out.size != 0);
  // The board path moves a bounded snapshot under a short critical section,
  // then serializes and frames it without touching collector-owned storage.
  TargetCollector snapshot_collector(2, "capture:p4-snapshot");
  assert(snapshot_collector.Record(MakeEvent(TRACE_EVENT_TASK_READY, 4)));
  CanonicalDrainSnapshot snapshot{};
  assert(snapshot_collector.TakeCanonicalDrainSnapshot(false, &snapshot));
  assert(snapshot.record_count == 1 && snapshot.emit_global && snapshot_collector.PendingRecords() == 0);
  ByteBuffer snapshot_raw{};
  WireBuffer snapshot_wire{};
  assert(snapshot_collector.SerializeCanonicalDrainSnapshot(snapshot, &snapshot_raw));
  assert(snapshot_collector.FrameCanonicalDrainSnapshotForUart(snapshot_raw, &snapshot_wire));
  snapshot_collector.CommitCanonicalDrainSnapshot(snapshot, snapshot_wire.size);
  const auto snapshot_counters = snapshot_collector.Counters();
  assert(snapshot_counters.flushed_records == 1 && snapshot_counters.wire_frames == 1 && snapshot_counters.wire_bytes == snapshot_wire.size);
  // Empty board drains are no-output polls. They clear a caller-provided
  // output size but do not consume a segment, mutate counters, or create a
  // UART frame; the first real record must still be segment zero.
  TargetCollector empty(2, "capture:p4-empty");
  ByteBuffer empty_out{};
  std::memset(empty_out.bytes.data(), 0xa5, empty_out.bytes.size());
  empty_out.size = 17;
  const auto empty_before = empty.Counters();
  assert(empty.FlushCanonicalInto(&empty_out));
  assert(empty_out.size == 0 && empty.PendingRecords() == 0);
  const auto empty_after_flush = empty.Counters();
  assert(std::memcmp(&empty_before, &empty_after_flush, sizeof(empty_before)) == 0);
  WireBuffer empty_wire{};
  empty_wire.size = 9;
  assert(!empty.FrameForUart(empty_out, &empty_wire));
  const auto empty_after_frame = empty.Counters();
  assert(empty_wire.size == 9 && std::memcmp(&empty_before, &empty_after_frame, sizeof(empty_before)) == 0);
  assert(empty.FlushCanonical().size == 0);
  empty_out.size = 13;
  assert(empty.FinalizeCanonicalInto(&empty_out) && empty_out.size == 0);
  assert(empty.FinalizeCanonical().size == 0);
  const auto empty_after_finalize = empty.Counters();
  assert(empty.PendingRecords() == 0 && std::memcmp(&empty_before, &empty_after_finalize, sizeof(empty_before)) == 0);
  assert(empty.Record(MakeEvent(TRACE_EVENT_TASK_READY, 4)));
  ByteBuffer first_after_empty{};
  assert(empty.FlushCanonicalInto(&first_after_empty) && first_after_empty.size != 0);
  trace_segment_meta_disk_t empty_first_segment{};
  std::memcpy(&empty_first_segment, first_after_empty.bytes.data() + sizeof(trace_global_header_disk_t), sizeof(empty_first_segment));
  assert(empty_first_segment.magic == TRACE_SEGMENT_META_MAGIC && empty_first_segment.segment_seq == 0);
  TargetCollector collector(4, "capture:p4-bounded");
  Event event = MakeEvent(TRACE_EVENT_TASK_READY, 10);
  event.payload_len = 2;
  event.payload[0] = 1;
  event.payload[1] = 2;
  assert(collector.Record(event));
  assert(collector.Record(MakeEvent(TRACE_EVENT_TASK_DISPATCH, 20)));
  assert(collector.Record(MakeEvent(TRACE_EVENT_TASK_BLOCK, 30)));
  assert(collector.Record(MakeEvent(TRACE_EVENT_TASK_WAKEUP, 40)));
  assert(!collector.Record(MakeEvent(TRACE_EVENT_TASK_EXIT, 50)));  // attempt #5 is a real buffer-overflow drop.

  const ByteBuffer first = collector.FlushCanonical();
  trace_global_header_disk_t global{};
  std::memcpy(&global, first.bytes.data(), sizeof(global));
  assert(global.magic == TRACE_FORMAT_MAGIC && std::strcmp(global.run_id, "capture:p4-bounded") == 0);
  trace_segment_meta_disk_t segment{};
  std::memcpy(&segment, first.bytes.data() + sizeof(global), sizeof(segment));
  assert(segment.magic == TRACE_SEGMENT_META_MAGIC && segment.segment_seq == 0);
  trace_chunk_header_disk_t first_chunk{};
  const size_t first_chunk_offset = sizeof(global) + sizeof(segment);
  std::memcpy(&first_chunk, first.bytes.data() + first_chunk_offset, sizeof(first_chunk));
  assert(first_chunk.magic == TRACE_CHUNK_MAGIC && first_chunk.record_count == 4 && first_chunk.seq_begin == 1 && first_chunk.seq_end == 4);

  // The post-overflow source gets #6, then the two unique monotonic markers
  // get #7/#8; #5 remains the observable source-event gap.
  assert(collector.Record(MakeEvent(TRACE_EVENT_TASK_WAKEUP, 60)));
  assert(collector.Record(MakeEvent(TRACE_EVENT_TASK_READY, 70)));
  assert(!collector.Record(MakeEvent(TRACE_EVENT_TASK_BLOCK, 80)));
  assert(!collector.Record(MakeEvent(TRACE_EVENT_TASK_EXIT, 90)));
  const ByteBuffer second = collector.FlushCanonical();
  trace_segment_meta_disk_t second_segment{};
  trace_global_header_disk_t second_global{};
  std::memcpy(&second_global, second.bytes.data(), sizeof(second_global));
  assert(second_global.magic == TRACE_FORMAT_MAGIC && std::strcmp(second_global.run_id, "capture:p4-bounded") == 0);
  std::memcpy(&second_segment, second.bytes.data() + sizeof(second_global), sizeof(second_segment));
  assert(second_segment.magic == TRACE_SEGMENT_META_MAGIC && second_segment.segment_seq == 1);
  trace_chunk_header_disk_t second_chunk{};
  std::memcpy(&second_chunk, second.bytes.data() + sizeof(second_global) + sizeof(second_segment), sizeof(second_chunk));
  assert(second_chunk.record_count == 4 && second_chunk.seq_begin == 6 && second_chunk.seq_end == 9);
  const size_t records = sizeof(second_global) + sizeof(second_segment) + sizeof(second_chunk);
  const auto source = EventHeaderAt(second, records);
  const auto loss = EventHeaderAt(second, records + sizeof(source) + source.payload_len);
  const auto overflow = EventHeaderAt(second, records + sizeof(source) + source.payload_len + sizeof(loss) + loss.payload_len);
  assert(source.seq == 6 && loss.event_id == TRACE_EVENT_LOSS && loss.seq == 7);
  assert(overflow.event_id == TRACE_EVENT_OVERFLOW && overflow.seq == 8);
  uint32_t loss_delta{};
  uint32_t overflow_delta{};
  std::memcpy(&loss_delta, second.bytes.data() + records + sizeof(source) + source.payload_len + sizeof(loss) + 2, sizeof(loss_delta));
  std::memcpy(&overflow_delta, second.bytes.data() + records + sizeof(source) + source.payload_len + sizeof(loss) + loss.payload_len + sizeof(overflow) + 2, sizeof(overflow_delta));
  assert(loss.payload_len == 8 && overflow.payload_len == 8 && loss_delta == 1 && overflow_delta == 1);  // burst delta, not cumulative.

  assert(collector.Record(MakeEvent(TRACE_EVENT_TASK_WAKEUP, 100)));
  const ByteBuffer third = collector.FlushCanonical();
  trace_chunk_header_disk_t third_chunk{};
  std::memcpy(&third_chunk, third.bytes.data() + sizeof(trace_global_header_disk_t) + sizeof(trace_segment_meta_disk_t), sizeof(third_chunk));
  assert(third_chunk.record_count == 3 && third_chunk.seq_begin == 12 && third_chunk.seq_end == 14);
  const size_t third_records = sizeof(trace_global_header_disk_t) + sizeof(trace_segment_meta_disk_t) + sizeof(third_chunk);
  const auto second_loss = EventHeaderAt(third, third_records + sizeof(trace_event_header_disk_t));
  const auto second_overflow = EventHeaderAt(third, third_records + sizeof(trace_event_header_disk_t) + sizeof(second_loss) + second_loss.payload_len);
  assert(second_loss.event_id == TRACE_EVENT_LOSS && second_loss.seq == 13);
  assert(second_overflow.event_id == TRACE_EVENT_OVERFLOW && second_overflow.seq == 14);
  uint32_t second_loss_delta{};
  std::memcpy(&second_loss_delta, third.bytes.data() + third_records + sizeof(trace_event_header_disk_t) + sizeof(second_loss) + 2, sizeof(second_loss_delta));
  assert(second_loss_delta == 2);  // the second overflow burst had exactly two source drops.

  WireBuffer wire{};
  WireBuffer second_wire{};
  CanonicalRawBuffer rebuilt{};
  assert(collector.FrameForUart(first, &wire));
  assert(collector.FrameForUart(second, &second_wire));
  std::array<uint8_t, rtd::p4::freertos_stm32f103::kMaxWireBytes * 2> wire_stream{};
  std::memcpy(wire_stream.data(), wire.bytes.data(), wire.size);
  std::memcpy(wire_stream.data() + wire.size, second_wire.bytes.data(), second_wire.size);
  const uint32_t wire_stream_size = wire.size + second_wire.size;
  assert(collector.RebuildRawFromUart(wire_stream.data(), wire_stream_size, &rebuilt));
  assert(rebuilt.size == first.size + second.size && std::memcmp(rebuilt.bytes.data(), first.bytes.data(), first.size) == 0);
  assert(std::memcmp(rebuilt.bytes.data() + first.size, second.bytes.data(), second.size) == 0);
  auto corrupt = wire_stream;
  corrupt[wire_stream_size - 1] ^= 1;
  assert(!collector.RebuildRawFromUart(corrupt.data(), wire_stream_size, &rebuilt));  // CRC corruption.
  assert(!collector.RebuildRawFromUart(wire_stream.data(), wire_stream_size - 1, &rebuilt));  // payload truncation.
  corrupt = wire_stream; corrupt[0] = 0;
  assert(!collector.RebuildRawFromUart(corrupt.data(), wire_stream_size, &rebuilt));  // magic corruption.
  corrupt = wire_stream; corrupt[2] = 2;
  assert(!collector.RebuildRawFromUart(corrupt.data(), wire_stream_size, &rebuilt));  // version corruption.
  corrupt = wire_stream; corrupt[3] = 99;
  assert(!collector.RebuildRawFromUart(corrupt.data(), wire_stream_size, &rebuilt));  // unknown type.
  assert(!collector.RebuildRawFromUart(wire_stream.data() + wire.size, second_wire.size, &rebuilt));  // missing global.
  assert(!collector.RebuildRawFromUart(wire_stream.data(), wire_stream_size + 1, &rebuilt));  // trailing garbage.

  Event oversized = MakeEvent(TRACE_EVENT_TASK_READY, 110);
  oversized.payload_len = rtd::p4::freertos_stm32f103::kMaxPayloadBytes + 1;
  assert(!collector.Record(oversized));
  // Oversize is a rejected source attempt, so its consumed #15 remains a raw
  // gap; it must not be folded into the buffer-overflow burst at #10/#11.
  assert(collector.Record(MakeEvent(TRACE_EVENT_TASK_READY, 120)));
  const ByteBuffer fourth = collector.FlushCanonical();
  trace_chunk_header_disk_t fourth_chunk{};
  std::memcpy(&fourth_chunk, fourth.bytes.data() + sizeof(trace_global_header_disk_t) + sizeof(trace_segment_meta_disk_t), sizeof(fourth_chunk));
  assert(fourth_chunk.record_count == 1 && fourth_chunk.seq_begin == 16 && fourth_chunk.seq_end == 16);
  // End-of-capture burst: no later source event is supplied. Finalize first
  // drains the full source ring, then deterministically emits markers alone.
  assert(collector.Record(MakeEvent(TRACE_EVENT_TASK_READY, 130)));
  assert(collector.Record(MakeEvent(TRACE_EVENT_TASK_READY, 140)));
  assert(collector.Record(MakeEvent(TRACE_EVENT_TASK_READY, 150)));
  assert(collector.Record(MakeEvent(TRACE_EVENT_TASK_READY, 160)));
  assert(!collector.Record(MakeEvent(TRACE_EVENT_TASK_READY, 170)));
  assert(!collector.Record(MakeEvent(TRACE_EVENT_TASK_READY, 180)));
  const ByteBuffer final_sources = collector.FinalizeCanonical();
  trace_chunk_header_disk_t final_sources_chunk{};
  std::memcpy(&final_sources_chunk, final_sources.bytes.data() + sizeof(trace_global_header_disk_t) + sizeof(trace_segment_meta_disk_t), sizeof(final_sources_chunk));
  assert(final_sources_chunk.record_count == 4 && final_sources_chunk.seq_begin == 17 && final_sources_chunk.seq_end == 20);
  const ByteBuffer final_markers = collector.FinalizeCanonical();
  trace_chunk_header_disk_t final_markers_chunk{};
  std::memcpy(&final_markers_chunk, final_markers.bytes.data() + sizeof(trace_global_header_disk_t) + sizeof(trace_segment_meta_disk_t), sizeof(final_markers_chunk));
  assert(final_markers_chunk.record_count == 2 && final_markers_chunk.seq_begin == 23 && final_markers_chunk.seq_end == 24);
  const size_t final_marker_records = sizeof(trace_global_header_disk_t) + sizeof(trace_segment_meta_disk_t) + sizeof(final_markers_chunk);
  const auto final_loss = EventHeaderAt(final_markers, final_marker_records);
  const auto final_overflow = EventHeaderAt(final_markers, final_marker_records + sizeof(final_loss) + final_loss.payload_len);
  uint32_t final_loss_delta{};
  uint32_t final_overflow_delta{};
  std::memcpy(&final_loss_delta, final_markers.bytes.data() + final_marker_records + sizeof(final_loss) + 2, sizeof(final_loss_delta));
  std::memcpy(&final_overflow_delta, final_markers.bytes.data() + final_marker_records + sizeof(final_loss) + final_loss.payload_len + sizeof(final_overflow) + 2, sizeof(final_overflow_delta));
  assert(final_loss.event_id == TRACE_EVENT_LOSS && final_overflow.event_id == TRACE_EVENT_OVERFLOW);
  assert(final_loss_delta == 2 && final_overflow_delta == 2);
  // Raw emitted sequence is strictly increasing. Its gaps are exactly the
  // rejected source attempts: #5, #10/#11, oversize #15, then #21/#22.
  const uint64_t raw_sequences[] = {1, 2, 3, 4, 6, 7, 8, 9, 12, 13, 14, 16, 17, 18, 19, 20, 23, 24};
  const uint64_t expected_gap_sizes[] = {1, 2, 1, 2};
  uint32_t gap_index = 0;
  for (size_t index = 1; index < sizeof(raw_sequences) / sizeof(raw_sequences[0]); ++index) {
    assert(raw_sequences[index] > raw_sequences[index - 1]);
    const uint64_t gap = raw_sequences[index] - raw_sequences[index - 1] - 1;
    if (gap != 0) assert(gap == expected_gap_sizes[gap_index++]);
  }
  assert(gap_index == sizeof(expected_gap_sizes) / sizeof(expected_gap_sizes[0]));
  auto attestation = MakeAttestation();
  attestation.source_sha256[0] = 'G';
  assert(!collector.AttestNaturalOverflow(attestation));  // arbitrary non-empty text is not a digest.
  attestation = MakeAttestation(); attestation.force_pressure_requested = true;
  assert(!collector.AttestNaturalOverflow(attestation));
  attestation = MakeAttestation(); attestation.masking_requested = true;
  assert(!collector.AttestNaturalOverflow(attestation));
  attestation = MakeAttestation(); attestation.offline_deletion_requested = true;
  assert(!collector.AttestNaturalOverflow(attestation));
  attestation = MakeAttestation(); attestation.synthetic_marker_requested = true;
  assert(!collector.AttestNaturalOverflow(attestation));
  attestation = MakeAttestation(); attestation.producer_drain_competition = false;
  assert(!collector.AttestNaturalOverflow(attestation));
  assert(collector.AttestNaturalOverflow(MakeAttestation()));
  const auto wire_before_aux = collector.Counters();
  collector.NoteWireFrame(19);  // typed boot/config/counter frame inventory.
  const auto counters = collector.Counters();
  assert(counters.buffer_capacity_records == 4 && counters.buffer_high_watermark == 4);  // units are records.
  assert(counters.attempted_records == counters.accepted_records + counters.dropped_records);
  assert(counters.attempted_records == 18 && counters.accepted_records == 12 && counters.dropped_records == 6);
  assert(counters.buffer_overflow_dropped_records == 5 && counters.rejected_payload_records == 1);
  assert(loss_delta + second_loss_delta + final_loss_delta == counters.buffer_overflow_dropped_records);
  assert(overflow_delta + second_loss_delta + final_overflow_delta == counters.buffer_overflow_dropped_records);
  assert(counters.integrity_markers == 6 && counters.flushed_records == counters.accepted_records + counters.integrity_markers);
  assert(counters.flushed_records == 18 && counters.crc_failures == 1 && counters.truncations >= 2);
  assert(counters.wire_frames == wire_before_aux.wire_frames + 1 && counters.wire_bytes == wire_before_aux.wire_bytes + 19);
  assert(counters.natural_overflow);
  return 0;
}
