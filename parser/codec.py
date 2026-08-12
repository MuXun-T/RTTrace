from __future__ import annotations

import json
import struct
import time
import zlib
from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path
import sys
from typing import Any, Iterable

from spec.events import EventCatalog, event_name_for, load_event_catalog
from spec.schema_loader import load_dictionary

from .models import (
    ChunkHeader,
    DecodedEvent,
    EventCursor,
    FeedResult,
    GlobalHeader,
    ParseSummary,
    SegmentMeta,
    UnifiedEvent,
    UntrustedWindow,
    event_sort_key,
    materialize_unified_event,
)
from .result import Result, err_result, ok_result
from .telemetry import StageObserver, add_stage_timing, emit_stage_update, memory_snapshot


TRACE_FORMAT_MAGIC = 0x54524345
TRACE_CHUNK_MAGIC = 0x43484B31
TRACE_SEGMENT_META_MAGIC = 0x53474D32
GLOBAL_HEADER_STRUCT = struct.Struct("<IHHHHHH16s32s32s")
SEGMENT_META_STRUCT = struct.Struct("<IHHIIHHI")
CHUNK_HEADER_STRUCT = struct.Struct("<IHHIIQQQQII")
EVENT_HEADER_STRUCT = struct.Struct("<HHHHQQI")
PAYLOAD_CACHE_LIMIT = 4096
DECODE_PROGRESS_CHUNK_INTERVAL = 64
PAYLOAD_FORMATS = {
    0x1001: (struct.Struct("<IhHH"), ("task_id", "prio", "core_hint", "reason")),
    0x1002: (struct.Struct("<IQHI"), ("task_id", "wait_obj_id", "reason", "owner_task_id")),
    0x1003: (struct.Struct("<IHQ"), ("task_id", "wake_src", "obj_id")),
    0x1004: (struct.Struct("<IHhH"), ("task_id", "core_id", "prio", "reason")),
    0x1005: (struct.Struct("<Ii"), ("task_id", "exit_code")),
    0x1006: (struct.Struct("<HIIH"), ("core_id", "prev_task_id", "next_task_id", "reason")),
    0x1007: (struct.Struct("<HIHH"), ("core_id", "selected_task_id", "rq_len", "reason")),
    0x2001: (struct.Struct("<IQHQ"), ("task_id", "obj_id", "obj_type", "timeout")),
    0x2002: (struct.Struct("<IQHQ"), ("task_id", "obj_id", "obj_type", "timeout")),
    0x2003: (struct.Struct("<IQHQ"), ("task_id", "obj_id", "obj_type", "timeout")),
    0x3001: (struct.Struct("<HHB"), ("irq_id", "core_id", "nesting_depth")),
    0x3002: (struct.Struct("<HHB"), ("irq_id", "core_id", "nesting_depth")),
    0x4001: (struct.Struct("<HIH"), ("core_id", "lost_count", "reason")),
    0x4002: (struct.Struct("<HIH"), ("core_id", "overflow_count", "reason")),
}

# Shared empty payload to reduce per-event allocations for payload-less events.
# Treat as read-only to avoid cross-event mutation.
EMPTY_PAYLOAD: dict[str, Any] = {}

# Shared empty trust-tag container for clean events. Treat as read-only and use
# copy-on-write helpers before adding any tag.
EMPTY_TRUST_TAGS: list[str] = []

PayloadBytesLike = bytes | bytearray | memoryview


@dataclass
class DecodeChunkStats:
    chunk_index: int
    record_count: int
    payload_bytes: int
    decoded_records: int = 0
    payload_empty_reuse_count: int = 0
    payload_cache_hit_count: int = 0
    payload_new_unique_count: int = 0
    payload_uncached_count: int = 0
    trust_tags_empty_reuse_count: int = 0
    trust_tags_nonempty_count: int = 0
    estimated_event_structure_bytes: int = 0
    estimated_event_key_bytes: int = 0
    estimated_payload_dict_bytes: int = 0
    dominant_object: str | None = None

    def to_dict(self) -> dict[str, object]:
        estimated_bytes = {
            "event_structures": int(self.estimated_event_structure_bytes),
            "event_keys": int(self.estimated_event_key_bytes),
            "payload_dicts": int(self.estimated_payload_dict_bytes),
            "chunk_buffer": int(self.payload_bytes),
        }
        return {
            "chunk_index": int(self.chunk_index),
            "record_count": int(self.record_count),
            "payload_bytes": int(self.payload_bytes),
            "decoded_records": int(self.decoded_records),
            "payload_empty_reuse_count": int(self.payload_empty_reuse_count),
            "payload_cache_hit_count": int(self.payload_cache_hit_count),
            "payload_new_unique_count": int(self.payload_new_unique_count),
            "payload_uncached_count": int(self.payload_uncached_count),
            "trust_tags_empty_reuse_count": int(self.trust_tags_empty_reuse_count),
            "trust_tags_nonempty_count": int(self.trust_tags_nonempty_count),
            "estimated_bytes": estimated_bytes,
            "dominant_object": self.dominant_object or _dominant_object_name(estimated_bytes),
        }


def _dominant_object_name(estimated_bytes: dict[str, int]) -> str:
    if not estimated_bytes:
        return "unknown"
    name, _ = max(estimated_bytes.items(), key=lambda item: (int(item[1]), item[0]))
    return str(name)


def _dominant_chunk_object(chunk_stats: DecodeChunkStats) -> str:
    dominant = "event_structures"
    dominant_value = int(chunk_stats.estimated_event_structure_bytes)
    if int(chunk_stats.estimated_event_key_bytes) > dominant_value:
        dominant = "event_keys"
        dominant_value = int(chunk_stats.estimated_event_key_bytes)
    if int(chunk_stats.estimated_payload_dict_bytes) > dominant_value:
        dominant = "payload_dicts"
        dominant_value = int(chunk_stats.estimated_payload_dict_bytes)
    if int(chunk_stats.payload_bytes) > dominant_value:
        dominant = "chunk_buffer"
    return dominant


class DecodeHotspotTracker:
    def __init__(
        self,
        *,
        observer: StageObserver | None = None,
        stage_timings: dict[str, float] | None = None,
    ) -> None:
        self.observer = observer
        self.stage_timings = stage_timings
        self.feed_call_count = 0
        self.feed_input_bytes = 0
        self.decoded_chunk_count = 0
        self.decoded_records_total = 0
        self.payload_empty_reuse_count = 0
        self.payload_cache_hit_count = 0
        self.payload_new_unique_count = 0
        self.payload_uncached_count = 0
        self.trust_tags_empty_reuse_count = 0
        self.trust_tags_nonempty_count = 0
        self.estimated_event_structure_bytes = 0
        self.estimated_event_key_bytes = 0
        self.estimated_payload_dict_bytes = 0
        self.events_list_size_bytes = 0
        self.events_list_growth_bytes = 0
        self.events_list_peak_bytes = 0
        self.buffer_peak_len = 0
        self.buffer_peak_bytes = 0
        self.max_chunk_payload_bytes = 0
        self.max_chunk_record_count = 0
        self.latest_progress_payload: dict[str, object] = {}
        self.hottest_chunk_payload: dict[str, object] = {}
        self._emit_progress_hint = False

    def note_feed_call(self, *, data_len: int, buffer_len: int, buffer_size_bytes: int) -> None:
        self.feed_call_count += 1
        self.feed_input_bytes += int(data_len)
        if buffer_len > self.buffer_peak_len:
            self.buffer_peak_len = int(buffer_len)
        if buffer_size_bytes > self.buffer_peak_bytes:
            self.buffer_peak_bytes = int(buffer_size_bytes)

    def record_chunk(
        self,
        *,
        chunk_stats: DecodeChunkStats,
        events_list_before_size: int,
        events_list_after_size: int,
        events_retained: int,
        payload_cache_entries: int,
        buffer_len: int,
        buffer_size_bytes: int,
    ) -> None:
        self.decoded_chunk_count += 1
        self.decoded_records_total += int(chunk_stats.decoded_records)
        self.payload_empty_reuse_count += int(chunk_stats.payload_empty_reuse_count)
        self.payload_cache_hit_count += int(chunk_stats.payload_cache_hit_count)
        self.payload_new_unique_count += int(chunk_stats.payload_new_unique_count)
        self.payload_uncached_count += int(chunk_stats.payload_uncached_count)
        self.trust_tags_empty_reuse_count += int(chunk_stats.trust_tags_empty_reuse_count)
        self.trust_tags_nonempty_count += int(chunk_stats.trust_tags_nonempty_count)
        self.estimated_event_structure_bytes += int(chunk_stats.estimated_event_structure_bytes)
        self.estimated_event_key_bytes += int(chunk_stats.estimated_event_key_bytes)
        self.estimated_payload_dict_bytes += int(chunk_stats.estimated_payload_dict_bytes)
        self.events_list_size_bytes = int(events_list_after_size)
        growth = max(int(events_list_after_size) - int(events_list_before_size), 0)
        self.events_list_growth_bytes += growth
        if self.events_list_size_bytes > self.events_list_peak_bytes:
            self.events_list_peak_bytes = self.events_list_size_bytes
        if int(chunk_stats.payload_bytes) > self.max_chunk_payload_bytes:
            self.max_chunk_payload_bytes = int(chunk_stats.payload_bytes)
        if int(chunk_stats.record_count) > self.max_chunk_record_count:
            self.max_chunk_record_count = int(chunk_stats.record_count)
        if buffer_len > self.buffer_peak_len:
            self.buffer_peak_len = int(buffer_len)
        if buffer_size_bytes > self.buffer_peak_bytes:
            self.buffer_peak_bytes = int(buffer_size_bytes)

        chunk_payload = chunk_stats.to_dict()
        chunk_payload["events_retained_after_chunk"] = int(events_retained)
        chunk_payload["events_list_growth_bytes"] = growth
        chunk_payload["events_list_size_bytes"] = int(events_list_after_size)
        chunk_payload["payload_cache_entries"] = int(payload_cache_entries)
        chunk_payload["buffer_len"] = int(buffer_len)
        chunk_payload["buffer_size_bytes"] = int(buffer_size_bytes)
        chunk_total = sum(int(value) for value in (chunk_payload.get("estimated_bytes") or {}).values()) + growth
        chunk_payload["estimated_total_bytes"] = int(chunk_total)
        if chunk_total >= int(self.hottest_chunk_payload.get("estimated_total_bytes", -1)):
            self.hottest_chunk_payload = dict(chunk_payload)
            self._emit_progress_hint = True

        self.latest_progress_payload = {
            "feed_call_count": int(self.feed_call_count),
            "input_bytes_total": int(self.feed_input_bytes),
            "decoded_chunk_count": int(self.decoded_chunk_count),
            "decoded_records_total": int(self.decoded_records_total),
            "events_retained": int(events_retained),
            "events_list_size_bytes": int(events_list_after_size),
            "payload_cache_entries": int(payload_cache_entries),
            "buffer_len": int(buffer_len),
            "buffer_size_bytes": int(buffer_size_bytes),
            "last_chunk": chunk_payload,
        }
        if self.decoded_chunk_count <= 4 or self.decoded_chunk_count % DECODE_PROGRESS_CHUNK_INTERVAL == 0:
            self._emit_progress_hint = True

    def should_emit_progress(self) -> bool:
        return bool(self._emit_progress_hint)

    def mark_progress_emitted(self) -> None:
        self._emit_progress_hint = False

    def summary(self) -> dict[str, object]:
        estimated_bytes = {
            "event_structures": int(self.estimated_event_structure_bytes),
            "event_keys": int(self.estimated_event_key_bytes),
            "payload_dicts": int(self.estimated_payload_dict_bytes),
            "events_list": int(self.events_list_size_bytes),
            "chunk_buffer_peak": int(self.buffer_peak_bytes),
        }
        return {
            "feed_call_count": int(self.feed_call_count),
            "input_bytes_total": int(self.feed_input_bytes),
            "decoded_chunk_count": int(self.decoded_chunk_count),
            "decoded_records_total": int(self.decoded_records_total),
            "payload_empty_reuse_count": int(self.payload_empty_reuse_count),
            "payload_cache_hit_count": int(self.payload_cache_hit_count),
            "payload_new_unique_count": int(self.payload_new_unique_count),
            "payload_uncached_count": int(self.payload_uncached_count),
            "trust_tags_empty_reuse_count": int(self.trust_tags_empty_reuse_count),
            "trust_tags_nonempty_count": int(self.trust_tags_nonempty_count),
            "payload_cache_entries": int(self.latest_progress_payload.get("payload_cache_entries", 0)),
            "events_retained": int(self.latest_progress_payload.get("events_retained", 0)),
            "estimated_retained_bytes": estimated_bytes,
            "events_list_growth_bytes": int(self.events_list_growth_bytes),
            "events_list_peak_bytes": int(self.events_list_peak_bytes),
            "buffer_peak_len": int(self.buffer_peak_len),
            "buffer_peak_bytes": int(self.buffer_peak_bytes),
            "max_chunk_payload_bytes": int(self.max_chunk_payload_bytes),
            "max_chunk_record_count": int(self.max_chunk_record_count),
            "dominant_object": _dominant_object_name(estimated_bytes),
            "hottest_chunk": dict(self.hottest_chunk_payload),
        }

    def emit_progress(
        self,
        stage: str,
        *,
        seconds: float | None = None,
        failed: bool = False,
        chunk_progress: dict[str, object] | None = None,
        hotspot_summary: dict[str, object] | None = None,
        stage_label: str | None = None,
    ) -> None:
        emit_stage_update(
            self.observer,
            stage,
            status="failed" if failed else "progress",
            seconds=seconds,
            memory_snapshot_payload=memory_snapshot(stage_label) if stage_label else None,
            stage_timings=self.stage_timings,
            chunk_progress=chunk_progress or dict(self.latest_progress_payload),
            hotspot_summary=hotspot_summary or self.summary(),
        )


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def _event_cursor(event: UnifiedEvent | DecodedEvent | None) -> EventCursor | None:
    if event is None:
        return None
    return EventCursor(
        ts=event.timestamp_aligned,
        core_id=event.core_id,
        seq=event.seq,
        sort_key=event_sort_key(event),
    )


def _with_trust_tag(trust_tags: list[str], tag: str) -> list[str]:
    if trust_tags is EMPTY_TRUST_TAGS:
        return [tag]
    trust_tags.append(tag)
    return trust_tags


def _append_event_trust_tag(event: UnifiedEvent | DecodedEvent, tag: str) -> None:
    if event.trust_tags is EMPTY_TRUST_TAGS:
        event.trust_tags = [tag]
        return
    event.trust_tags.append(tag)


def _header_from_tuple(header_tuple: tuple[int, ...]) -> GlobalHeader:
    (
        magic,
        endian,
        time_unit,
        format_ver,
        dict_ver,
        _header_ver,
        _core_count,
        _clock_source,
        producer_ver,
        run_id,
    ) = header_tuple
    try:
        producer_ver_text = producer_ver.decode("utf-8")
        run_id_text = run_id.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("trace header contains invalid UTF-8 identity") from exc
    return GlobalHeader(
        magic=hex(magic),
        endian=endian,
        time_unit=time_unit,
        clock_source=1,
        format_ver=format_ver,
        dict_ver=dict_ver,
        producer_ver=producer_ver_text.rstrip("\0"),
        run_id=run_id_text.rstrip("\0") or None,
    )


def _segment_meta_from_tuple(meta_tuple: tuple[int, ...]) -> SegmentMeta:
    meta_size = int(meta_tuple[2])
    if meta_size != SEGMENT_META_STRUCT.size:
        raise ValueError(
            f"unsupported segment meta size: expected {SEGMENT_META_STRUCT.size}, got {meta_size}"
        )
    return SegmentMeta(
        segment_seq=int(meta_tuple[3]),
        prev_segment_seq=int(meta_tuple[4]),
        dict_ver=int(meta_tuple[5]),
        dict_ref_algo=int(meta_tuple[6]),
        dict_ref_checksum=int(meta_tuple[7]),
        header_ver=int(meta_tuple[1]),
        meta_size=meta_size,
    )


def _encode_payload(event_id: int, payload: dict[str, Any]) -> bytes:
    if event_id in PAYLOAD_FORMATS:
        fmt, fields = PAYLOAD_FORMATS[event_id]
        values = []
        for field in fields:
            if field == "timeout" and "timeout" not in payload:
                values.append(int(payload.get("timeout_ns", 0)))
            else:
                values.append(int(payload.get(field, 0)))
        return fmt.pack(*values)
    if "raw_hex" in payload:
        return bytes.fromhex(str(payload["raw_hex"]))
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode_payload(event_id: int, payload_bytes: PayloadBytesLike) -> dict[str, Any]:
    if event_id in PAYLOAD_FORMATS:
        fmt, fields = PAYLOAD_FORMATS[event_id]
        if len(payload_bytes) < fmt.size:
            return {"raw_hex": payload_bytes.hex(), "decode_error": "payload_too_small"}
        values = fmt.unpack_from(payload_bytes, 0)
        payload = dict(zip(fields, values))
        if event_id in {0x2001, 0x2002, 0x2003}:
            payload["timeout_ns"] = payload.pop("timeout")
        return payload
    try:
        if not payload_bytes:
            return EMPTY_PAYLOAD
        if isinstance(payload_bytes, memoryview):
            return json.loads(payload_bytes.tobytes())
        return json.loads(payload_bytes)
    except Exception:
        return {"raw_hex": payload_bytes.hex()}


def _intern_payload(
    event_id: int,
    payload: dict[str, Any],
    payload_cache: dict[tuple[int, tuple[tuple[str, Any], ...]], dict[str, Any]] | None,
) -> tuple[dict[str, Any], str]:
    if payload_cache is None or payload is EMPTY_PAYLOAD or not payload:
        return payload, "empty" if payload is EMPTY_PAYLOAD or not payload else "cache_bypass"
    if len(payload) > 4:
        return payload, "cache_bypass"
    try:
        key = (event_id, tuple(payload.items()))
        hash(key)
    except TypeError:
        return payload, "cache_bypass"
    cached = payload_cache.get(key)
    if cached is not None:
        return cached, "cache_hit"
    if len(payload_cache) >= PAYLOAD_CACHE_LIMIT:
        return payload, "cache_bypass"
    payload_cache[key] = payload
    return payload, "cache_store"


def _chunk_header(
    events: list[dict[str, Any]],
    core_id: int,
    dict_ver: int,
    chunk_bytes: bytes,
    header_ver: int = 1,
) -> bytes:
    timestamps = [int(event["timestamp"]) for event in events]
    seqs = [int(event["seq"]) for event in events]
    crc = zlib.crc32(chunk_bytes) & 0xFFFFFFFF
    return CHUNK_HEADER_STRUCT.pack(
        TRACE_CHUNK_MAGIC,
        header_ver,
        core_id,
        len(events),
        len(chunk_bytes),
        min(timestamps) if timestamps else 0,
        max(timestamps) if timestamps else 0,
        min(seqs) if seqs else 0,
        max(seqs) if seqs else 0,
        dict_ver,
        crc,
    )


def _decode_chunk(
    *,
    dataset_name: str,
    header: GlobalHeader,
    event_catalog: Any,
    chunk_index: int,
    seq_by_core: dict[int, tuple[int, float]],
    chunk_tuple: tuple[int, ...],
    chunk_payload: PayloadBytesLike,
    source_core_id: int | None = None,
    payload_cache: dict[tuple[int, tuple[tuple[str, Any], ...]], dict[str, Any]] | None = None,
    hotspot_tracker: DecodeHotspotTracker | None = None,
) -> tuple[list[DecodedEvent], list[UntrustedWindow], list[str], DecodeChunkStats]:
    chunk_header = ChunkHeader(
        chunk_start_ts=chunk_tuple[5],
        chunk_end_ts=chunk_tuple[6],
        core_mask=1 << chunk_tuple[2],
        record_count=chunk_tuple[3],
        seq_start=chunk_tuple[7],
        seq_end=chunk_tuple[8],
        dict_ver=chunk_tuple[9],
        chunk_crc=chunk_tuple[10],
    )
    windows: list[UntrustedWindow] = []
    warnings: list[str] = []
    events: list[DecodedEvent] = []
    chunk_payload_view = chunk_payload if isinstance(chunk_payload, memoryview) else memoryview(chunk_payload)
    release_chunk_payload_view = not isinstance(chunk_payload, memoryview)
    chunk_stats = DecodeChunkStats(
        chunk_index=chunk_index,
        record_count=int(chunk_header.record_count),
        payload_bytes=len(chunk_payload_view),
    )
    offset = 0

    if source_core_id is not None and int(chunk_tuple[2]) != int(source_core_id):
        warnings.append(
            f"feed core hint {source_core_id} mismatches chunk core {int(chunk_tuple[2])} at chunk {chunk_index}"
        )

    try:
        for _ in range(chunk_header.record_count):
            if offset + EVENT_HEADER_STRUCT.size > len(chunk_payload_view):
                windows.append(
                    UntrustedWindow(
                        window_id=f"uw:{dataset_name}:truncate:{chunk_index}:{offset}",
                        source="truncate",
                        scope="event",
                        t_begin=float(chunk_header.chunk_start_ts),
                        t_end=float(chunk_header.chunk_end_ts),
                        reason_code="TRUNCATED_EVENT",
                        severity="error",
                    )
                )
                break
            ver, flags, core_id, event_id, seq, ts, payload_len = EVENT_HEADER_STRUCT.unpack_from(
                chunk_payload_view,
                offset,
            )
            offset += EVENT_HEADER_STRUCT.size
            if offset + payload_len > len(chunk_payload_view):
                windows.append(
                    UntrustedWindow(
                        window_id=f"uw:{dataset_name}:truncate:{chunk_index}:{offset}",
                        source="truncate",
                        scope="event",
                        t_begin=float(chunk_header.chunk_start_ts),
                        t_end=float(chunk_header.chunk_end_ts),
                        reason_code="TRUNCATED_PAYLOAD",
                        severity="error",
                    )
                )
                break
            payload_bytes = chunk_payload_view[offset : offset + payload_len]
            offset += payload_len
            payload = _decode_payload(event_id, payload_bytes)
            payload, payload_status = _intern_payload(event_id, payload, payload_cache)
            if payload_status == "empty":
                chunk_stats.payload_empty_reuse_count += 1
            elif payload_status == "cache_hit":
                chunk_stats.payload_cache_hit_count += 1
            elif payload_status == "cache_store":
                chunk_stats.payload_new_unique_count += 1
                chunk_stats.estimated_payload_dict_bytes += sys.getsizeof(payload)
            else:
                chunk_stats.payload_uncached_count += 1
                if payload and payload is not EMPTY_PAYLOAD:
                    chunk_stats.estimated_payload_dict_bytes += sys.getsizeof(payload)
            event_name = sys.intern(event_name_for(event_id, catalog=event_catalog))
            trusted_tags: list[str] = EMPTY_TRUST_TAGS
            previous_seq = seq_by_core.get(core_id)
            if core_id in seq_by_core and seq != seq_by_core[core_id][0] + 1:
                windows.append(
                    UntrustedWindow(
                        window_id=f"uw:{dataset_name}:seq_gap:{core_id}:{seq}",
                        source="seq_gap",
                        scope="event",
                        t_begin=float(seq_by_core[core_id][1]),
                        t_end=float(ts),
                        reason_code="SEQ_GAP",
                        severity="warning",
                    )
                )
                trusted_tags = _with_trust_tag(trusted_tags, "seq_gap")
            if event_name in {"LOSS", "OVERFLOW"}:
                reason_value = int(payload.get("reason", 0) or 0)
                if event_name == "LOSS":
                    reason_code = "SEQ_GAP"
                    source = "loss"
                else:
                    reason_code = "IO_BACKPRESSURE" if reason_value == 3 else "BUFFER_OVERFLOW"
                    source = "io_backpressure" if reason_code == "IO_BACKPRESSURE" else "overflow"
                count_key = "lost_count" if event_name == "LOSS" else "overflow_count"
                count = int(payload.get(count_key, 1) or 1)
                trusted_tags = _with_trust_tag(trusted_tags, source)
                windows.append(
                    UntrustedWindow(
                        window_id=f"uw:{dataset_name}:{source}:{core_id}:{seq}",
                        source=source,
                        scope="event",
                        t_begin=float(previous_seq[1]) if previous_seq is not None else float(ts),
                        t_end=float(ts),
                        reason_code=reason_code,
                        severity="warning",
                    )
                )
                warnings.append(f"chunk {chunk_index} reported {source} count={count}")
            seq_by_core[core_id] = (seq, float(ts))
            event = DecodedEvent(
                core_id=core_id,
                seq=seq,
                timestamp_raw=float(ts),
                timestamp_aligned=float(ts),
                event_id=event_id,
                event_name=event_name,
                task_id=_first_present(
                    payload,
                    "task_id",
                    "selected_task_id",
                    "next_task_id",
                    "prev_task_id",
                ),
                obj_id=_first_present(payload, "obj_id", "wait_obj_id"),
                irq_id=payload.get("irq_id"),
                job_id=payload.get("job_id"),
                instance_id=payload.get("instance_id"),
                payload=payload,
                trust_tags=trusted_tags,
                chunk_id=chunk_index,
            )
            chunk_stats.decoded_records += 1
            if trusted_tags is EMPTY_TRUST_TAGS:
                chunk_stats.trust_tags_empty_reuse_count += 1
                trust_tags_size = 0
            else:
                chunk_stats.trust_tags_nonempty_count += 1
                trust_tags_size = sys.getsizeof(trusted_tags)
            chunk_stats.estimated_event_structure_bytes += (
                sys.getsizeof(event) + trust_tags_size
            )
            events.append(event)
    except MemoryError:
        chunk_stats.dominant_object = _dominant_chunk_object(chunk_stats)
        if hotspot_tracker is not None:
            failure_summary = hotspot_tracker.summary()
            failure_summary.update(
                {
                    "failure": "MemoryError",
                    "dominant_object": chunk_stats.dominant_object,
                    "partial_chunk": chunk_stats.to_dict(),
                    "last_progress": dict(hotspot_tracker.latest_progress_payload),
                }
            )
            hotspot_tracker.emit_progress(
                "_decode_chunk",
                failed=True,
                chunk_progress={
                    "chunk_index": int(chunk_index),
                    "decoded_records": int(chunk_stats.decoded_records),
                    "record_count": int(chunk_stats.record_count),
                    "payload_bytes": int(chunk_stats.payload_bytes),
                },
                hotspot_summary=failure_summary,
            )
        raise

    actual_crc = zlib.crc32(chunk_payload_view) & 0xFFFFFFFF
    if actual_crc != chunk_header.chunk_crc:
        warnings.append(f"chunk {chunk_index} crc mismatch")
        windows.append(
            UntrustedWindow(
                window_id=f"uw:{dataset_name}:crc:{chunk_index}",
                source="crc_fail",
                scope="event",
                t_begin=float(chunk_header.chunk_start_ts),
                t_end=float(chunk_header.chunk_end_ts),
                reason_code="CRC_FAIL",
                severity="warning",
            )
        )
        for event in events:
            _append_event_trust_tag(event, "crc_fail")
    if chunk_header.dict_ver != header.dict_ver:
        warnings.append(f"chunk {chunk_index} dict mismatch")
        windows.append(
            UntrustedWindow(
                window_id=f"uw:{dataset_name}:dict:{chunk_index}",
                source="dict_mismatch",
                scope="event",
                t_begin=float(chunk_header.chunk_start_ts),
                t_end=float(chunk_header.chunk_end_ts),
                reason_code="DICT_MISMATCH",
                severity="warning",
            )
        )
    if offset != len(chunk_payload_view) and int(chunk_header.record_count) > 0:
        warnings.append(f"chunk {chunk_index} has {len(chunk_payload_view) - offset} trailing bytes")
    chunk_stats.dominant_object = _dominant_chunk_object(chunk_stats)
    if release_chunk_payload_view:
        chunk_payload_view.release()
    return events, windows, warnings, chunk_stats


class TraceDecodeSession:
    def __init__(
        self,
        dataset_id: str | None = None,
        dictionary: dict[str, Any] | str | Path | None = None,
        catalog: EventCatalog | None = None,
        stage_observer: StageObserver | None = None,
        stage_timings: dict[str, float] | None = None,
        materialize_events: bool = True,
        expected_capture_id: str | None = None,
    ) -> None:
        self._requested_dataset_id = dataset_id
        self.dataset_id = dataset_id or "stream"
        self.buffer = bytearray()
        self.header: GlobalHeader | None = None
        self.current_header: GlobalHeader | None = None
        self.events: list[UnifiedEvent | DecodedEvent] = []
        self.warnings: list[str] = []
        self.windows: list[UntrustedWindow] = []
        self.segment_metas: list[SegmentMeta] = []
        self._last_segment_meta: SegmentMeta | None = None
        self.segment_count = 0
        self.seq_by_core: dict[int, tuple[int, float]] = {}
        self.payload_cache: dict[tuple[int, tuple[tuple[str, Any], ...]], dict[str, Any]] = {}
        self.chunk_index = 0
        self.materialize_events = bool(materialize_events)
        self.expected_capture_id = expected_capture_id
        self.stage_observer = stage_observer
        self.stage_timings = stage_timings
        self.hotspot_tracker = (
            DecodeHotspotTracker(observer=stage_observer, stage_timings=stage_timings)
            if stage_observer is not None or stage_timings is not None
            else None
        )
        self._finalized = False
        self._expecting_header = True
        self._expecting_segment_meta = False
        if catalog is not None:
            self.event_catalog = catalog
            self.dictionary_info = {
                "requested_source": "preloaded",
                "requested_path": None,
                "requested_dict_ver": catalog.dict_ver,
                "resolved_source": "preloaded",
                "resolved_dict_ver": catalog.dict_ver,
                "expected_dict_ver": None,
                "fallback_used": False,
                "reason_codes": [],
                "version_mismatch": False,
                "warnings": [],
                "resolved_dictionary": {},
            }
            self.catalog_warnings: list[str] = []
        else:
            self.event_catalog, self.dictionary_info, self.catalog_warnings = load_event_catalog(dictionary=dictionary)
        self._catalog_warnings_emitted = False
        self._catalog_reason_windows_emitted = False

    def latest_chunk_progress(self) -> dict[str, object]:
        if self.hotspot_tracker is None:
            return {}
        return dict(self.hotspot_tracker.latest_progress_payload)

    def decode_hotspot_summary(self) -> dict[str, object]:
        if self.hotspot_tracker is None:
            return {}
        return dict(self.hotspot_tracker.summary())

    def _discard_buffer_prefix(self, prefix_len: int) -> None:
        try:
            del self.buffer[:prefix_len]
        except BufferError:
            # Fall back to rebinding when an external observer/mock retained a
            # memoryview slice from the just-decoded chunk.
            self.buffer = self.buffer[prefix_len:]

    def should_emit_progress(self) -> bool:
        return self.hotspot_tracker is not None and self.hotspot_tracker.should_emit_progress()

    def _record_header_dict_mismatch(
        self,
        header: GlobalHeader,
        *,
        suffix: str,
        new_warnings: list[str],
        new_windows: list[UntrustedWindow],
    ) -> None:
        if self.event_catalog.dict_ver == header.dict_ver:
            return
        mismatch_warning = (
            f"event dictionary version mismatch: trace header dict_ver={header.dict_ver}, "
            f"catalog dict_ver={self.event_catalog.dict_ver}"
        )
        mismatch_window = UntrustedWindow(
            window_id=f"uw:{self.dataset_id}:dict:{suffix}",
            source="dict_mismatch",
            scope="event",
            t_begin=0.0,
            t_end=0.0,
            reason_code="DICT_MISMATCH",
            severity="warning",
        )
        new_warnings.append(mismatch_warning)
        self.warnings.append(mismatch_warning)
        new_windows.append(mismatch_window)
        self.windows.append(mismatch_window)
        self.dictionary_info["version_mismatch"] = True

    def _consume_header(
        self,
        *,
        new_warnings: list[str],
        new_windows: list[UntrustedWindow],
    ) -> bool:
        if len(self.buffer) < GLOBAL_HEADER_STRUCT.size:
            return False
        header_tuple = GLOBAL_HEADER_STRUCT.unpack_from(self.buffer, 0)
        if header_tuple[0] != TRACE_FORMAT_MAGIC:
            raise ValueError("invalid trace magic" if self.header is None else "invalid segment header")
        segment_header = _header_from_tuple(header_tuple)
        # P3 callers provide this only from a validated CaptureLineageContext.
        if self.expected_capture_id is not None and segment_header.run_id != self.expected_capture_id:
            raise ValueError("capture lineage context does not match trace header capture identity")
        if self.header is None:
            self.header = segment_header
            if self.header.run_id and self._requested_dataset_id in {None, "", "stream"}:
                self.dataset_id = self.header.run_id
            if self.catalog_warnings and not self._catalog_warnings_emitted:
                new_warnings.extend(self.catalog_warnings)
                self.warnings.extend(self.catalog_warnings)
                self._catalog_warnings_emitted = True
            self.dictionary_info["header_dict_ver"] = self.header.dict_ver
            self._record_header_dict_mismatch(
                self.header,
                suffix="header",
                new_warnings=new_warnings,
                new_windows=new_windows,
            )
        else:
            if segment_header.run_id and segment_header.run_id not in {"", self.dataset_id}:
                run_warning = (
                    f"segment header run_id mismatch: expected dataset_id={self.dataset_id}, "
                    f"got run_id={segment_header.run_id}"
                )
                new_warnings.append(run_warning)
                self.warnings.append(run_warning)
            self._record_header_dict_mismatch(
                segment_header,
                suffix=f"segment-header:{self.segment_count + 1}",
                new_warnings=new_warnings,
                new_windows=new_windows,
            )
        self.current_header = segment_header
        self.segment_count += 1
        self._expecting_header = False
        self._expecting_segment_meta = segment_header.format_ver >= 2
        del self.buffer[: GLOBAL_HEADER_STRUCT.size]
        return True

    def _consume_segment_meta(
        self,
        *,
        new_warnings: list[str],
        new_windows: list[UntrustedWindow],
    ) -> bool:
        if len(self.buffer) < SEGMENT_META_STRUCT.size:
            return False
        meta_tuple = SEGMENT_META_STRUCT.unpack_from(self.buffer, 0)
        if meta_tuple[0] != TRACE_SEGMENT_META_MAGIC:
            raise ValueError(f"missing segment meta at chunk {self.chunk_index}")
        segment_meta = _segment_meta_from_tuple(meta_tuple)
        self.segment_metas.append(segment_meta)
        if self.current_header is not None and segment_meta.dict_ver != self.current_header.dict_ver:
            warning = (
                f"segment meta dict mismatch: header dict_ver={self.current_header.dict_ver}, "
                f"segment dict_ver={segment_meta.dict_ver}"
            )
            new_warnings.append(warning)
            self.warnings.append(warning)
            window = UntrustedWindow(
                window_id=f"uw:{self.dataset_id}:segment-meta:{segment_meta.segment_seq}",
                source="dict_mismatch",
                scope="event",
                t_begin=0.0,
                t_end=0.0,
                reason_code="DICT_MISMATCH",
                severity="warning",
            )
            new_windows.append(window)
            self.windows.append(window)
        if self._last_segment_meta is not None:
            expected_prev_segment_seq = self._last_segment_meta.segment_seq
            expected_segment_seq = expected_prev_segment_seq + 1
            chain_issues: list[str] = []
            if segment_meta.prev_segment_seq != expected_prev_segment_seq:
                chain_issues.append(
                    f"expected prev_segment_seq={expected_prev_segment_seq}, got {segment_meta.prev_segment_seq}"
                )
            if segment_meta.segment_seq != expected_segment_seq:
                chain_issues.append(
                    f"expected segment_seq={expected_segment_seq}, got {segment_meta.segment_seq}"
                )
            if chain_issues:
                warning = f"segment chain break: {'; '.join(chain_issues)}"
                new_warnings.append(warning)
                self.warnings.append(warning)
                window = UntrustedWindow(
                    window_id=f"uw:{self.dataset_id}:segment-chain:{segment_meta.segment_seq}",
                    source="segment_chain",
                    scope="event",
                    t_begin=0.0,
                    t_end=0.0,
                    reason_code="SEGMENT_CHAIN_BREAK",
                    severity="warning",
                )
                new_windows.append(window)
                self.windows.append(window)
            if (
                self._last_segment_meta.dict_ref_checksum != 0
                and segment_meta.dict_ref_checksum != 0
                and segment_meta.dict_ref_checksum != self._last_segment_meta.dict_ref_checksum
            ):
                warning = (
                    "segment dict checksum conflict: "
                    f"expected {self._last_segment_meta.dict_ref_checksum}, "
                    f"got {segment_meta.dict_ref_checksum}"
                )
                new_warnings.append(warning)
                self.warnings.append(warning)
                window = UntrustedWindow(
                    window_id=f"uw:{self.dataset_id}:segment-dict:{segment_meta.segment_seq}",
                    source="segment_chain",
                    scope="event",
                    t_begin=0.0,
                    t_end=0.0,
                    reason_code="SEGMENT_DICT_CONFLICT",
                    severity="warning",
                )
                new_windows.append(window)
                self.windows.append(window)
        self._last_segment_meta = segment_meta
        self._expecting_segment_meta = False
        del self.buffer[: SEGMENT_META_STRUCT.size]
        return True

    def feed(self, data: PayloadBytesLike, source_core_id: int | None = None) -> Result[FeedResult]:
        if self._finalized:
            return err_result("INVALID_ARG", "parser session already finalized")
        if not data:
            return ok_result(
                FeedResult(
                    decoded_records=0,
                    gaps=[],
                    warnings=[],
                    online_cursor=_event_cursor(self.events[-1] if self.events else None),
                )
            )

        feed_started = time.perf_counter()
        self.buffer.extend(data)
        if self.hotspot_tracker is not None:
            self.hotspot_tracker.note_feed_call(
                data_len=len(data),
                buffer_len=len(self.buffer),
                buffer_size_bytes=sys.getsizeof(self.buffer),
            )
        new_windows: list[UntrustedWindow] = []
        new_warnings: list[str] = []
        decoded_records = 0

        try:
            while True:
                if self._expecting_header:
                    if not self._consume_header(new_warnings=new_warnings, new_windows=new_windows):
                        break
                    continue
                if self._expecting_segment_meta:
                    if not self._consume_segment_meta(new_warnings=new_warnings, new_windows=new_windows):
                        break
                    continue
                if len(self.buffer) >= 4:
                    next_magic = struct.unpack_from("<I", self.buffer, 0)[0]
                    if next_magic == TRACE_FORMAT_MAGIC:
                        if len(self.buffer) < GLOBAL_HEADER_STRUCT.size:
                            break
                        self._expecting_header = True
                        continue
                if len(self.buffer) < CHUNK_HEADER_STRUCT.size:
                    break
                chunk_tuple = CHUNK_HEADER_STRUCT.unpack_from(self.buffer, 0)
                if chunk_tuple[0] != TRACE_CHUNK_MAGIC:
                    return err_result("INVALID_ARG", f"invalid chunk magic at {self.chunk_index}")
                payload_bytes = int(chunk_tuple[4])
                total_bytes = CHUNK_HEADER_STRUCT.size + payload_bytes
                if len(self.buffer) < total_bytes:
                    break
                buffer_view = memoryview(self.buffer)
                chunk_payload = buffer_view[CHUNK_HEADER_STRUCT.size : total_bytes]
                decode_started = time.perf_counter()
                try:
                    chunk_events, chunk_windows, chunk_warnings, chunk_stats = _decode_chunk(
                        dataset_name=self.dataset_id,
                        header=self.current_header or self.header,
                        event_catalog=self.event_catalog,
                        chunk_index=self.chunk_index,
                        seq_by_core=self.seq_by_core,
                        chunk_tuple=chunk_tuple,
                        chunk_payload=chunk_payload,
                        source_core_id=source_core_id,
                        payload_cache=self.payload_cache,
                        hotspot_tracker=self.hotspot_tracker,
                    )
                except MemoryError:
                    decode_seconds = time.perf_counter() - decode_started
                    add_stage_timing(self.stage_timings, "_decode_chunk_seconds", decode_seconds)
                    chunk_payload.release()
                    buffer_view.release()
                    raise
                decode_seconds = time.perf_counter() - decode_started
                add_stage_timing(self.stage_timings, "_decode_chunk_seconds", decode_seconds)
                chunk_payload.release()
                buffer_view.release()
                self._discard_buffer_prefix(total_bytes)
                events_list_before_size = sys.getsizeof(self.events)
                self.events.extend(chunk_events)
                events_list_after_size = sys.getsizeof(self.events)
                self.windows.extend(chunk_windows)
                self.warnings.extend(chunk_warnings)
                new_windows.extend(chunk_windows)
                new_warnings.extend(chunk_warnings)
                decoded_records += len(chunk_events)
                if self.hotspot_tracker is not None:
                    self.hotspot_tracker.record_chunk(
                        chunk_stats=chunk_stats,
                        events_list_before_size=events_list_before_size,
                        events_list_after_size=events_list_after_size,
                        events_retained=len(self.events),
                        payload_cache_entries=len(self.payload_cache),
                        buffer_len=len(self.buffer),
                        buffer_size_bytes=sys.getsizeof(self.buffer),
                    )
                    if self.hotspot_tracker.should_emit_progress():
                        self.hotspot_tracker.emit_progress(
                            "_decode_chunk",
                            seconds=decode_seconds,
                            stage_label="_decode_chunk_progress",
                        )
                        self.hotspot_tracker.mark_progress_emitted()
                self.chunk_index += 1
        except ValueError as exc:
            feed_seconds = time.perf_counter() - feed_started
            add_stage_timing(self.stage_timings, "TraceDecodeSession.feed_seconds", feed_seconds)
            return err_result("INVALID_ARG", str(exc))

        feed_seconds = time.perf_counter() - feed_started
        add_stage_timing(self.stage_timings, "TraceDecodeSession.feed_seconds", feed_seconds)
        if decoded_records > 0 and self.hotspot_tracker is not None:
            self.hotspot_tracker.emit_progress(
                "TraceDecodeSession.feed",
                seconds=feed_seconds,
                stage_label="TraceDecodeSession.feed_progress",
            )
        return ok_result(
            FeedResult(
                decoded_records=decoded_records,
                gaps=new_windows,
                warnings=new_warnings,
                online_cursor=_event_cursor(self.events[-1] if self.events else None),
            ),
            warnings=new_warnings,
            untrusted_windows=new_windows,
        )

    def finalize(self) -> Result[dict[str, Any]]:
        if self.header is None:
            return err_result("NOT_READY", "parser session missing global header")
        if self.dictionary_info.get("reason_codes") and not self._catalog_reason_windows_emitted:
            t_begin = self.events[0].timestamp_aligned if self.events else 0.0
            t_end = self.events[-1].timestamp_aligned if self.events else 0.0
            for index, reason_code in enumerate(self.dictionary_info.get("reason_codes", [])):
                self.windows.append(
                    UntrustedWindow(
                        window_id=f"uw:{self.dataset_id}:dict:compat:{index}",
                        source="dict_compat",
                        scope="event",
                        t_begin=float(t_begin),
                        t_end=float(t_end),
                        reason_code=reason_code,
                        severity="warning",
                    )
                )
            self._catalog_reason_windows_emitted = True
        if self.buffer:
            last_ts = self.events[-1].timestamp_aligned if self.events else 0.0
            self.windows.append(
                UntrustedWindow(
                    window_id=f"uw:{self.dataset_id}:truncate:finalize:{self.chunk_index}",
                    source="truncate",
                    scope="event",
                    t_begin=float(last_ts),
                    t_end=float(last_ts),
                    reason_code="TRUNCATED_CHUNK",
                    severity="error",
                )
            )
            self.buffer.clear()
        self._finalized = True
        retained_events = self.events
        summary = ParseSummary(
            dataset_id=self.dataset_id,
            chunk_count=self.chunk_index,
            segment_count=max(self.segment_count, 1),
            event_count=len(retained_events),
            warning_count=len(self.warnings),
            untrusted_window_count=len(self.windows),
            final_cursor=_event_cursor(retained_events[-1] if retained_events else None),
        )
        finalized_events = (
            [materialize_unified_event(event, self.dataset_id) for event in retained_events]
            if self.materialize_events
            else retained_events
        )
        self.events = []
        return ok_result(
            {
                "header": self.header,
                "events": finalized_events,
                "untrusted_windows": list(self.windows),
                "warnings": list(self.warnings),
                "segment_metas": list(self.segment_metas),
                "dictionary_info": deepcopy(self.dictionary_info),
                "decode_hotspot_summary": self.decode_hotspot_summary(),
                "summary": summary,
            },
            warnings=list(self.warnings),
            untrusted_windows=list(self.windows),
        )


def encode_trace(
    output_path: str | Path,
    events: Iterable[dict[str, Any]],
    producer_ver: str = "python-sim-0.1",
    run_id: str | None = None,
    chunk_size: int = 8,
    format_ver: int = 1,
    segment_meta: dict[str, int] | None = None,
) -> Path:
    dictionary = load_dictionary()
    dict_ver = int(dictionary["dict_ver"])
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    event_list = list(events)
    chunks: list[list[dict[str, Any]]] = []
    for core_id in sorted({int(event.get("core_id", 0)) for event in event_list}):
        core_events = [event for event in event_list if int(event.get("core_id", 0)) == core_id]
        for index in range(0, len(core_events), chunk_size):
            chunks.append(core_events[index : index + chunk_size])
    header_ver = 2 if int(format_ver) >= 2 else 1
    segment_meta_payload = dict(segment_meta or {})
    with path.open("wb") as handle:
        handle.write(
            GLOBAL_HEADER_STRUCT.pack(
                TRACE_FORMAT_MAGIC,
                1,
                1,
                int(format_ver),
                dict_ver,
                header_ver,
                max((int(event.get("core_id", 0)) for event in event_list), default=0) + 1,
                b"steady_clock".ljust(16, b"\0"),
                producer_ver.encode("utf-8")[:32].ljust(32, b"\0"),
                (run_id or path.stem).encode("utf-8")[:32].ljust(32, b"\0"),
            )
        )
        if int(format_ver) >= 2:
            handle.write(
                SEGMENT_META_STRUCT.pack(
                    TRACE_SEGMENT_META_MAGIC,
                    header_ver,
                    SEGMENT_META_STRUCT.size,
                    int(segment_meta_payload.get("segment_seq", 1)),
                    int(segment_meta_payload.get("prev_segment_seq", 0)),
                    int(segment_meta_payload.get("dict_ver", dict_ver)),
                    int(segment_meta_payload.get("dict_ref_algo", 0)),
                    int(segment_meta_payload.get("dict_ref_checksum", 0)),
                )
            )
        for chunk in chunks:
            chunk_bytes = bytearray()
            core_id = int(chunk[0]["core_id"]) if chunk else 0
            for event in chunk:
                payload = _encode_payload(int(event["event_id"]), event.get("payload", {}))
                event_id = int(event["event_id"])
                chunk_bytes.extend(
                    EVENT_HEADER_STRUCT.pack(
                        int(event.get("ver", 1)),
                        int(event.get("flags", 0)),
                        int(event["core_id"]),
                        event_id,
                        int(event["seq"]),
                        int(event["timestamp"]),
                        len(payload),
                    )
                )
                chunk_bytes.extend(payload)
            handle.write(
                _chunk_header(
                    chunk,
                    core_id=core_id,
                    dict_ver=int(segment_meta_payload.get("dict_ver", dict_ver)),
                    chunk_bytes=bytes(chunk_bytes),
                    header_ver=header_ver,
                )
            )
            handle.write(chunk_bytes)
    return path


def decode_trace(
    trace_path: str | Path,
    dataset_id: str | None = None,
    dictionary: dict[str, Any] | str | Path | None = None,
) -> Result[dict[str, Any]]:
    path = Path(trace_path)
    if not path.exists():
        return err_result("INVALID_ARG", f"trace file not found: {path}")
    session = TraceDecodeSession(dataset_id=dataset_id, dictionary=dictionary)
    fed = session.feed(path.read_bytes())
    if not fed.ok:
        return Result(
            code=fed.code,
            message=fed.message,
            warnings=fed.warnings,
            untrusted_windows=fed.untrusted_windows,
        )
    return session.finalize()
