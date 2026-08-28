#ifndef RTD_P4_FREERTOS_STM32F103_TARGET_CONTRACT_HPP_
#define RTD_P4_FREERTOS_STM32F103_TARGET_CONTRACT_HPP_

#include <cstddef>
#include <cstdint>
#include <array>

#include "trace_api.h"

namespace rtd::p4::freertos_stm32f103 {

// Board-facing bounded storage: no heap allocation or host containers.
constexpr uint32_t kMaxRecords = 64;
constexpr uint32_t kMaxPayloadBytes = 64;
constexpr uint32_t kMaxTraceBytes = 8192;
constexpr uint32_t kMaxWireBytes = kMaxTraceBytes + 12;
constexpr uint32_t kMaxRebuiltRawBytes = kMaxTraceBytes * 2;
struct Event {
  uint16_t core_id{};
  uint16_t event_id{};
  uint64_t timestamp{};
  uint16_t payload_len{};
  std::array<uint8_t, kMaxPayloadBytes> payload{};
};

struct ByteBuffer { std::array<uint8_t, kMaxTraceBytes> bytes{}; uint32_t size{}; };
struct WireBuffer { std::array<uint8_t, kMaxWireBytes> bytes{}; uint32_t size{}; };
struct CanonicalRawBuffer { std::array<uint8_t, kMaxRebuiltRawBytes> bytes{}; uint32_t size{}; };

struct CounterSnapshot {
  uint64_t attempted_records{};
  uint64_t accepted_records{};
  uint64_t dropped_records{};
  uint64_t buffer_overflow_dropped_records{};
  uint64_t rejected_payload_records{};
  uint64_t integrity_markers{};
  uint64_t flushed_records{};
  uint64_t decoded_records{};
  uint64_t wire_frames{};
  uint64_t wire_bytes{};
  uint64_t crc_failures{};
  uint64_t truncations{};
  uint64_t backpressure_total{};
  uint32_t buffer_high_watermark{};
  uint32_t buffer_capacity_records{};
  bool natural_overflow{};
};

struct NaturalOverflowAttestation {
  std::array<char, 65> source_sha256{};
  std::array<char, 65> workload_config_sha256{};
  bool producer_drain_competition{};
  bool force_pressure_requested{};
  bool masking_requested{};
  bool offline_deletion_requested{};
  bool synthetic_marker_requested{};
};

// A bounded drain hand-off. The target moves collector-owned records into this
// snapshot while interrupts are masked, then serializes and CRCs the snapshot
// with interrupts enabled. This keeps calibration ISRs serviceable while
// preserving one writer for each collector ring slot.
struct CanonicalDrainSnapshot {
  std::array<Event, kMaxRecords> records{};
  std::array<uint64_t, kMaxRecords> sequences{};
  std::array<char, 32> capture_id{};
  uint32_t record_count{};
  uint32_t segment_seq{};
  bool emit_global{};
};

constexpr uint16_t kUartFrameMagic = 0x5234u;
constexpr uint8_t kUartFrameVersion = 1u;
constexpr uint8_t kUartFrameTypeCanonicalTrace = 1u;
constexpr uint8_t kUartFrameTypeBoot = 2u;
constexpr uint8_t kUartFrameTypeConfig = 3u;
constexpr uint8_t kUartFrameTypeCounter = 4u;
// Fixed P4 auxiliary payload ABI.  These are byte layouts, not C/C++ object
// layouts: every multi-byte field is little-endian and digest fields are 64
// lowercase ASCII hex bytes without a terminator.
constexpr uint8_t kP4CapacityUnitRecords = 1u;
constexpr uint8_t kP4ObservationNotObserved = 0u;
constexpr uint32_t kP4BootPayloadBytes = 40u;     // selector:u32, clock_hz:u32, run_id:[32]
constexpr uint32_t kP4ConfigPayloadBytes = 147u; // capacity:u32, unit:u8, baud:u32, framing:u32, timestamp:u32, enabled:u8, decoded_state:u8, source:[64], workload:[64]
constexpr uint32_t kP4CounterPayloadBytes = 114u; // 13*u64, hwm:u32, capacity:u32, decoded_state:u8, natural_overflow:u8

class TargetCollector {
 public:
  TargetCollector(uint32_t capacity, const char* capture_id);

  // Drop-new. A subsequent successful write backfills LOSS and OVERFLOW when
  // room exists; otherwise those conditions remain in the counter snapshot.
  bool Record(const Event& event);
  // Board-facing, fixed-storage path. A null destination is rejected before
  // any collector state is changed.
  bool FlushCanonicalInto(ByteBuffer* output);
  ByteBuffer FlushCanonical();
  // Stop/drain protocol: call until PendingRecords()==0 and no pending
  // integrity facts remain. It may first return a source chunk, then a
  // marker-only chunk, never fabricating a source event.
  bool FinalizeCanonicalInto(ByteBuffer* output);
  ByteBuffer FinalizeCanonical();
  bool FrameForUart(const ByteBuffer& raw, WireBuffer* wire);
  // These three operations split the board drain into a short ownership
  // transfer, an interruptible serialization/CRC step, and a short commit.
  // The caller must externally serialize drain callers.
  bool TakeCanonicalDrainSnapshot(bool finalize, CanonicalDrainSnapshot* snapshot);
  bool SerializeCanonicalDrainSnapshot(const CanonicalDrainSnapshot& snapshot, ByteBuffer* output) const;
  bool FrameCanonicalDrainSnapshotForUart(const ByteBuffer& raw, WireBuffer* wire) const;
  void CommitCanonicalDrainSnapshot(const CanonicalDrainSnapshot& snapshot, uint32_t wire_bytes);
  bool RebuildRawFromUart(const uint8_t* wire, uint32_t wire_size, CanonicalRawBuffer* raw);
  bool AttestNaturalOverflow(const NaturalOverflowAttestation& attestation);
  void NoteDecodedRecords(uint64_t decoded_records);
  // Boot/config/counter frames use the same typed UART envelope as canonical
  // trace frames and must be included in the immutable wire inventory.
  void NoteWireFrame(uint32_t wire_bytes);
  void NoteBackpressure();
  void NoteTruncation();
  CounterSnapshot Counters() const;
  size_t PendingRecords() const;

 private:
  bool Append(const Event& event, uint64_t sequence);
  bool AppendMarker(uint16_t event_id, uint16_t core_id, uint64_t timestamp, uint32_t delta, uint16_t reason);
  bool AppendPendingMarkers();
  bool Write(ByteBuffer* output, const void* data, uint32_t size) const;
  uint32_t Crc32(const uint8_t* data, uint32_t size) const;

  uint32_t capacity_{};
  uint32_t ring_count_{};
  uint64_t next_sequence_{1};
  uint64_t pending_loss_delta_{};
  uint64_t pending_overflow_delta_{};
  uint64_t emitted_loss_delta_total_{};
  uint64_t emitted_overflow_delta_total_{};
  uint16_t pending_core_id_{};
  uint64_t pending_timestamp_{};
  bool markers_finalized_{};
  bool uart_global_sent_{};
  uint32_t segment_seq_{};
  std::array<char, 32> capture_id_{};
  std::array<Event, kMaxRecords> ring_{};
  std::array<uint64_t, kMaxRecords> sequences_{};
  CounterSnapshot counters_{};
};

}  // namespace rtd::p4::freertos_stm32f103

#endif  // RTD_P4_FREERTOS_STM32F103_TARGET_CONTRACT_HPP_
