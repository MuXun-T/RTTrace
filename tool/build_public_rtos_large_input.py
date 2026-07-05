from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser import decode_trace, encode_trace
from parser.codec import (
    EVENT_HEADER_STRUCT,
    GLOBAL_HEADER_STRUCT,
    SEGMENT_META_STRUCT,
    TRACE_FORMAT_MAGIC,
    TRACE_SEGMENT_META_MAGIC,
    _chunk_header,
    _encode_payload,
)
from spec.events import event_id_for

try:
    from build_formal_large_input import DEFAULT_TARGET_SIZE_BYTES, build_formal_large_input
except ModuleNotFoundError:  # pragma: no cover - import style depends on entrypoint
    from tool.build_formal_large_input import DEFAULT_TARGET_SIZE_BYTES, build_formal_large_input

PUBLIC_RTOS_PRODUCER_VER = "public-rtos-derived-0.1"
DEFAULT_REAL_SEED_TARGET_BYTES = 128 * 1024 * 1024
FAST_REAL_SEED_TARGET_BYTES = 8 * 1024 * 1024
DENSE_REAL_SEED_TARGET_BYTES = 128 * 1024 * 1024

LINE_PATTERN = re.compile(
    r"^\s*(?P<name>.+?)-(?P<tid>\d+)\s+\[(?P<cpu>\d+)\]\s+(?P<time>\d+(?:\.\d+)?):\s*(?P<payload>.+?)\s*$"
)
SCHED_WAKEUP_NEW_PATTERN = re.compile(
    r"^sched_wakeup_new: comm=(?P<comm>\S+) pid=(?P<pid>\d+) target_cpu=(?P<target_cpu>\d+)"
)
SCHED_WAKING_PATTERN = re.compile(
    r"^sched_(?:waking|wakeup): comm=(?P<comm>\S+) pid=(?P<pid>\d+) target_cpu=(?P<target_cpu>\d+)"
)
SCHED_SWITCH_PATTERN = re.compile(
    r"^sched_switch: prev_comm=(?P<prev_comm>\S+) prev_pid=(?P<prev_pid>\d+) prev_state=(?P<prev_state>\S+) "
    r"==> next_comm=(?P<next_comm>\S+) next_pid=(?P<next_pid>\d+)"
)
IRQ_ENTRY_PATTERN = re.compile(r"^irq_handler_entry: irq=(?P<irq>\d+)")
IRQ_EXIT_PATTERN = re.compile(r"^irq_handler_exit: irq=(?P<irq>\d+)")
SYSCALL_ENTER_PATTERN = re.compile(r"^sys_(?P<name>[A-Za-z0-9_]+)\((?P<args>.*)\)$")
SYSCALL_EXIT_PATTERN = re.compile(r"^sys_(?P<name>[A-Za-z0-9_]+)\s*->\s*(?P<result>.+)$")

PUBLIC_SOURCE_URLS = [
    "https://nuttx.apache.org/docs/latest/debugging/tasktrace.html",
    "https://nuttx.apache.org/docs/latest/debugging/tasktraceuser.html",
    "https://github.com/apache/nuttx/blob/master/tools/parsetrace.py",
]


@dataclass(frozen=True)
class SystraceLine:
    task_name: str
    tid: int
    cpu: int
    timestamp_ns: int
    payload: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_time_ns(raw: str) -> int:
    head, _, frac = raw.partition(".")
    frac = (frac + "000000000")[:9]
    return int(head) * 1_000_000_000 + int(frac or "0")


def parse_nuttx_systrace(path: Path) -> list[SystraceLine]:
    lines: list[SystraceLine] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        text = raw_line.strip()
        if not text or text.startswith("#"):
            continue
        match = LINE_PATTERN.match(raw_line)
        if match is None:
            continue
        lines.append(
            SystraceLine(
                task_name=match.group("name").strip(),
                tid=int(match.group("tid")),
                cpu=int(match.group("cpu")),
                timestamp_ns=_parse_time_ns(match.group("time")),
                payload=match.group("payload").strip(),
            )
        )
    if not lines:
        raise SystemExit(f"no parsable NuttX systrace lines found in: {path}")
    return lines


def _stable_obj_id(kind: str, task_id: int) -> int:
    digest = hashlib.blake2s(f"{kind}:{task_id}".encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "little") & 0x7FFF_FFFF_FFFF_FFFF


def _obj_type_for_syscall(syscall_name: str) -> int:
    syscall = syscall_name.lower()
    if "mutex" in syscall:
        return 1
    if "sem" in syscall:
        return 2
    if "mq_" in syscall or "queue" in syscall or "msgq" in syscall:
        return 3
    if "event" in syscall or "flag" in syscall:
        return 4
    if "task_sem" in syscall:
        return 5
    if "task_msgq" in syscall:
        return 6
    return 2


def _block_reason_for_syscall(syscall_name: str | None) -> int:
    if not syscall_name:
        return 2
    syscall = syscall_name.lower()
    if any(token in syscall for token in ("nanosleep", "usleep", "sleep", "delay")):
        return 1
    if "mutex" in syscall:
        return 3
    if "sem" in syscall:
        return 4
    if "mq_" in syscall or "queue" in syscall or "msgq" in syscall:
        return 5
    if "event" in syscall or "flag" in syscall:
        return 6
    if "task_sem" in syscall:
        return 7
    if "task_msgq" in syscall:
        return 8
    return 2


def _wake_source_for_reason(reason: int, irq_active: bool) -> int:
    if irq_active:
        return 4
    if reason == 1:
        return 2
    if reason == 2:
        return 3
    return 1


def _dispatch_reason(prev_state: str, irq_active: bool) -> int:
    if irq_active:
        return 5
    if "S" in prev_state or "D" in prev_state or "T" in prev_state:
        return 3
    return 1


def convert_nuttx_systrace_to_events(lines: list[SystraceLine]) -> tuple[list[dict[str, object]], dict[str, object]]:
    seq_by_core: dict[int, int] = {}
    task_names: dict[int, str] = {}
    irq_depth_by_core: dict[int, int] = {}
    active_syscall_by_task: dict[int, dict[str, object]] = {}
    last_block_by_task: dict[int, dict[str, object]] = {}
    events: list[dict[str, object]] = []
    counters = {
        "task_ready": 0,
        "task_block": 0,
        "task_wakeup": 0,
        "task_dispatch": 0,
        "ctx_switch": 0,
        "sync_try": 0,
        "sync_lock": 0,
        "sync_unlock": 0,
        "irq_enter": 0,
        "irq_exit": 0,
    }

    def emit(core_id: int, timestamp_ns: int, event_name: str, payload: dict[str, object]) -> None:
        next_seq = seq_by_core.get(core_id, 0) + 1
        seq_by_core[core_id] = next_seq
        events.append(
            {
                "core_id": int(core_id),
                "event_id": event_id_for(event_name),
                "seq": next_seq,
                "timestamp": int(timestamp_ns),
                "payload": dict(payload),
            }
        )

    for line in lines:
        task_names[line.tid] = line.task_name

        if (match := SCHED_WAKEUP_NEW_PATTERN.match(line.payload)) is not None:
            pid = int(match.group("pid"))
            target_cpu = int(match.group("target_cpu"))
            task_names[pid] = match.group("comm")
            emit(
                target_cpu,
                line.timestamp_ns,
                "TASK_READY",
                {
                    "task_id": pid,
                    "prio": 0,
                    "core_hint": target_cpu,
                    "reason": 1,
                },
            )
            counters["task_ready"] += 1
            continue

        if (match := SCHED_WAKING_PATTERN.match(line.payload)) is not None:
            pid = int(match.group("pid"))
            target_cpu = int(match.group("target_cpu"))
            task_names[pid] = match.group("comm")
            block_info = last_block_by_task.get(pid, {})
            block_reason = int(block_info.get("reason", 2))
            irq_active = irq_depth_by_core.get(line.cpu, 0) > 0
            emit(
                target_cpu,
                line.timestamp_ns,
                "TASK_WAKEUP",
                {
                    "task_id": pid,
                    "wake_src": _wake_source_for_reason(block_reason, irq_active),
                    "obj_id": int(block_info.get("obj_id", 0)),
                },
            )
            counters["task_wakeup"] += 1
            continue

        if (match := SCHED_SWITCH_PATTERN.match(line.payload)) is not None:
            prev_pid = int(match.group("prev_pid"))
            next_pid = int(match.group("next_pid"))
            prev_state = match.group("prev_state")
            irq_active = irq_depth_by_core.get(line.cpu, 0) > 0
            dispatch_reason = _dispatch_reason(prev_state, irq_active)
            active_syscall = active_syscall_by_task.get(prev_pid)

            if prev_pid > 0 and ("S" in prev_state or "D" in prev_state or "T" in prev_state):
                block_reason = _block_reason_for_syscall(
                    str(active_syscall.get("name")) if isinstance(active_syscall, dict) else None
                )
                obj_id = int(active_syscall.get("obj_id", 0)) if isinstance(active_syscall, dict) else 0
                emit(
                    line.cpu,
                    line.timestamp_ns,
                    "TASK_BLOCK",
                    {
                        "task_id": prev_pid,
                        "wait_obj_id": obj_id,
                        "reason": block_reason,
                        "owner_task_id": 0,
                    },
                )
                last_block_by_task[prev_pid] = {
                    "reason": block_reason,
                    "obj_id": obj_id,
                }
                counters["task_block"] += 1

            if next_pid > 0:
                emit(
                    line.cpu,
                    line.timestamp_ns,
                    "TASK_DISPATCH",
                    {
                        "task_id": next_pid,
                        "core_id": line.cpu,
                        "prio": 0,
                        "reason": dispatch_reason,
                    },
                )
                counters["task_dispatch"] += 1

            emit(
                line.cpu,
                line.timestamp_ns,
                "CTX_SWITCH",
                {
                    "core_id": line.cpu,
                    "prev_task_id": prev_pid,
                    "next_task_id": next_pid,
                    "reason": dispatch_reason,
                },
            )
            counters["ctx_switch"] += 1
            continue

        if (match := IRQ_ENTRY_PATTERN.match(line.payload)) is not None:
            depth = irq_depth_by_core.get(line.cpu, 0) + 1
            irq_depth_by_core[line.cpu] = depth
            emit(
                line.cpu,
                line.timestamp_ns,
                "IRQ_ENTER",
                {
                    "irq_id": int(match.group("irq")),
                    "core_id": line.cpu,
                    "nesting_depth": depth,
                },
            )
            counters["irq_enter"] += 1
            continue

        if (match := IRQ_EXIT_PATTERN.match(line.payload)) is not None:
            depth = max(irq_depth_by_core.get(line.cpu, 0), 1)
            emit(
                line.cpu,
                line.timestamp_ns,
                "IRQ_EXIT",
                {
                    "irq_id": int(match.group("irq")),
                    "core_id": line.cpu,
                    "nesting_depth": depth,
                },
            )
            irq_depth_by_core[line.cpu] = max(depth - 1, 0)
            counters["irq_exit"] += 1
            continue

        if (match := SYSCALL_ENTER_PATTERN.match(line.payload)) is not None:
            syscall_name = match.group("name")
            task_id = line.tid
            obj_id = _stable_obj_id(syscall_name, task_id)
            active_syscall_by_task[task_id] = {
                "name": syscall_name,
                "obj_id": obj_id,
                "cpu": line.cpu,
                "timestamp_ns": line.timestamp_ns,
            }
            if any(token in syscall_name.lower() for token in ("sem_", "mutex_", "mq_", "queue", "msgq", "event", "flag")):
                emit(
                    line.cpu,
                    line.timestamp_ns,
                    "SYNC_TRY",
                    {
                        "task_id": task_id,
                        "obj_id": obj_id,
                        "obj_type": _obj_type_for_syscall(syscall_name),
                        "timeout_ns": 0,
                    },
                )
                counters["sync_try"] += 1
            continue

        if (match := SYSCALL_EXIT_PATTERN.match(line.payload)) is not None:
            syscall_name = match.group("name")
            task_id = line.tid
            active = active_syscall_by_task.pop(task_id, None)
            obj_id = int(active.get("obj_id", _stable_obj_id(syscall_name, task_id))) if isinstance(active, dict) else _stable_obj_id(syscall_name, task_id)
            syscall_lower = syscall_name.lower()
            result = match.group("result").strip()
            success = not result.startswith("-")

            if success and any(token in syscall_lower for token in ("sem_wait", "sem_timedwait", "mutex_lock", "mutex_timedlock", "mq_receive", "mq_timedreceive", "event_wait", "flag_wait")):
                emit(
                    line.cpu,
                    line.timestamp_ns,
                    "SYNC_LOCK",
                    {
                        "task_id": task_id,
                        "obj_id": obj_id,
                        "obj_type": _obj_type_for_syscall(syscall_name),
                        "timeout_ns": 0,
                    },
                )
                counters["sync_lock"] += 1
            elif success and any(token in syscall_lower for token in ("sem_post", "mutex_unlock", "mq_send", "mq_timedsend", "event_post", "flag_post")):
                emit(
                    line.cpu,
                    line.timestamp_ns,
                    "SYNC_UNLOCK",
                    {
                        "task_id": task_id,
                        "obj_id": obj_id,
                        "obj_type": _obj_type_for_syscall(syscall_name),
                        "timeout_ns": 0,
                    },
                )
                counters["sync_unlock"] += 1
            continue

    events.sort(key=lambda item: (int(item["timestamp"]), int(item["core_id"]), int(item["seq"])))
    time_span_ns = 0
    if events:
        timestamps = [int(item["timestamp"]) for item in events]
        time_span_ns = max(timestamps) - min(timestamps)
    metadata = {
        "task_names": {str(task_id): name for task_id, name in sorted(task_names.items())},
        "event_count": len(events),
        "time_span_ns": time_span_ns,
        "event_breakdown": counters,
    }
    return events, metadata


def _repeat_events(events: list[dict[str, object]], *, repeat: int, cycle_gap_ns: int | None) -> list[dict[str, object]]:
    if repeat <= 0:
        raise SystemExit("--repeat must be positive")
    if repeat == 1:
        return [dict(item, payload=dict(item["payload"])) for item in events]

    max_seq_by_core: dict[int, int] = {}
    timestamps = [int(item["timestamp"]) for item in events]
    for item in events:
        core_id = int(item["core_id"])
        max_seq_by_core[core_id] = max(max_seq_by_core.get(core_id, 0), int(item["seq"]))
    base_span = max(timestamps) - min(timestamps) + 1_000_000
    cycle_span = max(base_span, int(cycle_gap_ns or 0))

    repeated: list[dict[str, object]] = []
    for index in range(repeat):
        for item in events:
            core_id = int(item["core_id"])
            repeated.append(
                {
                    **item,
                    "seq": int(item["seq"]) + index * max_seq_by_core[core_id],
                    "timestamp": int(item["timestamp"]) + index * cycle_span,
                    "payload": dict(item["payload"]),
                }
            )
    repeated.sort(key=lambda item: (int(item["timestamp"]), int(item["core_id"]), int(item["seq"])))
    return repeated


def _candidate_variant_events(
    events: list[dict[str, object]],
    *,
    mode: str,
    stride: int = 17,
    fallback_offset_ns: int = 37,
) -> list[dict[str, object]]:
    if mode == "mirror":
        return [dict(item, payload=dict(item["payload"])) for item in events]

    variant: list[dict[str, object]] = []
    mutated = False
    for index, item in enumerate(events):
        payload = dict(item["payload"])
        updated = dict(item)
        updated["payload"] = payload
        if index % max(stride, 1) == 0:
            if "reason" in payload and isinstance(payload["reason"], int):
                payload["reason"] = int(payload["reason"]) + 1
                mutated = True
            elif "prio" in payload and isinstance(payload["prio"], int):
                payload["prio"] = int(payload["prio"]) + 1
                mutated = True
            else:
                updated["timestamp"] = int(updated["timestamp"]) + int(fallback_offset_ns)
                mutated = True
        variant.append(updated)
    if not mutated and variant:
        variant[0]["timestamp"] = int(variant[0]["timestamp"]) + int(fallback_offset_ns)
    variant.sort(key=lambda item: (int(item["timestamp"]), int(item["core_id"]), int(item["seq"])))
    return variant


def _mutate_stream_event(
    event: dict[str, object],
    *,
    mode: str,
    global_index: int,
    stride: int = 17,
    fallback_offset_ns: int = 37,
) -> dict[str, object]:
    updated = dict(event)
    payload = dict(event["payload"])
    updated["payload"] = payload
    if mode == "mirror":
        return updated
    if global_index % max(stride, 1) == 0:
        if "reason" in payload and isinstance(payload["reason"], int):
            payload["reason"] = int(payload["reason"]) + 1
        elif "prio" in payload and isinstance(payload["prio"], int):
            payload["prio"] = int(payload["prio"]) + 1
        else:
            updated["timestamp"] = int(updated["timestamp"]) + int(fallback_offset_ns)
    return updated


def _encode_repeated_events_to_temp(
    base_events: list[dict[str, object]],
    *,
    repeat: int,
    cycle_gap_ns: int | None,
    producer_ver: str,
    candidate_mode: str = "mirror",
    chunk_size: int = 8,
) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="public-rtos-seed-", suffix=".trace", delete=False)
    temp_path = Path(handle.name)
    handle.close()

    core_ids = sorted({int(event.get("core_id", 0)) for event in base_events})
    event_count = len(base_events) * max(repeat, 1)
    timestamps = [int(event["timestamp"]) for event in base_events]
    base_span = (max(timestamps) - min(timestamps) + 1_000_000) if timestamps else 1_000_000
    cycle_span = max(base_span, int(cycle_gap_ns or 0))
    max_seq_by_core: dict[int, int] = {}
    base_events_by_core: dict[int, list[dict[str, object]]] = {core_id: [] for core_id in core_ids}
    for event in base_events:
        core_id = int(event["core_id"])
        base_events_by_core.setdefault(core_id, []).append(event)
        max_seq_by_core[core_id] = max(max_seq_by_core.get(core_id, 0), int(event["seq"]))

    with temp_path.open("wb") as output:
        output.write(
            GLOBAL_HEADER_STRUCT.pack(
                TRACE_FORMAT_MAGIC,
                1,
                1,
                1,
                1,
                1,
                max(core_ids, default=0) + 1,
                b"steady_clock".ljust(16, b"\0"),
                producer_ver.encode("utf-8")[:32].ljust(32, b"\0"),
                temp_path.stem.encode("utf-8")[:32].ljust(32, b"\0"),
            )
        )
        global_index = 0
        for core_id in core_ids:
            core_events = base_events_by_core.get(core_id, [])
            chunk_events: list[dict[str, object]] = []
            chunk_bytes = bytearray()
            for repeat_index in range(max(repeat, 1)):
                seq_offset = repeat_index * max_seq_by_core.get(core_id, 0)
                timestamp_offset = repeat_index * cycle_span
                for event in core_events:
                    mutated = _mutate_stream_event(
                        event,
                        mode=candidate_mode,
                        global_index=global_index,
                    )
                    global_index += 1
                    encoded = {
                        **mutated,
                        "core_id": core_id,
                        "seq": int(mutated["seq"]) + seq_offset,
                        "timestamp": int(mutated["timestamp"]) + timestamp_offset,
                    }
                    payload_bytes = _encode_payload(int(encoded["event_id"]), dict(encoded["payload"]))
                    chunk_events.append(encoded)
                    chunk_bytes.extend(
                        EVENT_HEADER_STRUCT.pack(
                            int(encoded.get("ver", 1)),
                            int(encoded.get("flags", 0)),
                            core_id,
                            int(encoded["event_id"]),
                            int(encoded["seq"]),
                            int(encoded["timestamp"]),
                            len(payload_bytes),
                        )
                    )
                    chunk_bytes.extend(payload_bytes)
                    if len(chunk_events) >= chunk_size:
                        output.write(_chunk_header(chunk_events, core_id=core_id, dict_ver=1, chunk_bytes=bytes(chunk_bytes)))
                        output.write(chunk_bytes)
                        chunk_events = []
                        chunk_bytes = bytearray()
            if chunk_events:
                output.write(_chunk_header(chunk_events, core_id=core_id, dict_ver=1, chunk_bytes=bytes(chunk_bytes)))
                output.write(chunk_bytes)
    return temp_path


def _encode_to_temp(events: list[dict[str, object]], producer_ver: str) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="public-rtos-seed-", suffix=".trace", delete=False)
    temp_path = Path(handle.name)
    handle.close()
    encode_trace(temp_path, events, producer_ver=producer_ver)
    return temp_path


def _materialize_large_trace(seed_trace_path: Path, output_path: Path, *, target_size_bytes: int) -> dict[str, object]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    seed_trace_size_bytes = seed_trace_path.stat().st_size
    if seed_trace_size_bytes >= target_size_bytes:
        shutil.copyfile(seed_trace_path, output_path)
        return {
            "output": str(output_path),
            "output_size_bytes": int(output_path.stat().st_size),
            "sha256": _sha256(output_path),
            "padding_applied": False,
            "appended_chunk_count": 0,
        }
    manifest = build_formal_large_input(
        seed_input=seed_trace_path,
        output=output_path,
        target_size_bytes=target_size_bytes,
        filler_chunk_bytes=64 * 1024 * 1024,
    )
    manifest["padding_applied"] = True
    return manifest


def build_public_rtos_large_input(
    *,
    seed_systrace: Path,
    baseline_output: Path,
    candidate_output: Path | None,
    target_size_bytes: int,
    real_seed_target_bytes: int,
    sample_tier: str,
    candidate_mode: str,
    cycle_gap_ns: int | None,
    source_urls: list[str],
) -> dict[str, object]:
    if target_size_bytes <= 0:
        raise SystemExit("--target-size-bytes must be positive")
    if real_seed_target_bytes <= 0:
        raise SystemExit("--real-seed-target-bytes must be positive")

    parsed_lines = parse_nuttx_systrace(seed_systrace)
    base_events, conversion_meta = convert_nuttx_systrace_to_events(parsed_lines)
    if not base_events:
        raise SystemExit("no formal RTOS events could be converted from seed systrace")

    base_temp = _encode_to_temp(base_events, PUBLIC_RTOS_PRODUCER_VER)
    base_size_bytes = base_temp.stat().st_size
    repeat = max(1, int(math.ceil(real_seed_target_bytes / max(base_size_bytes, 1))))
    repeated_event_count = len(base_events) * repeat

    seed_trace_path = _encode_repeated_events_to_temp(
        base_events,
        repeat=repeat,
        cycle_gap_ns=cycle_gap_ns,
        producer_ver=PUBLIC_RTOS_PRODUCER_VER,
        candidate_mode="mirror",
    )
    seed_trace_size_bytes = seed_trace_path.stat().st_size
    decoded = decode_trace(seed_trace_path)
    if not decoded.ok:
        raise SystemExit(
            f"generated public RTOS seed trace is not loadable: {decoded.code}: {decoded.message}"
        )

    baseline_manifest = _materialize_large_trace(
        seed_trace_path,
        baseline_output,
        target_size_bytes=target_size_bytes,
    )

    candidate_manifest: dict[str, object] | None = None
    if candidate_output is not None:
        if candidate_mode == "mirror":
            candidate_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(baseline_output, candidate_output)
            candidate_manifest = {
                "output": str(candidate_output),
                "output_size_bytes": int(candidate_output.stat().st_size),
                "sha256": _sha256(candidate_output),
                "mirrors_baseline": True,
                "candidate_mode": candidate_mode,
            }
        else:
            candidate_seed_trace = _encode_repeated_events_to_temp(
                base_events,
                repeat=repeat,
                cycle_gap_ns=cycle_gap_ns,
                producer_ver=PUBLIC_RTOS_PRODUCER_VER,
                candidate_mode=candidate_mode,
            )
            candidate_manifest = _materialize_large_trace(
                candidate_seed_trace,
                candidate_output,
                target_size_bytes=target_size_bytes,
            )
            candidate_manifest["mirrors_baseline"] = False
            candidate_manifest["candidate_mode"] = candidate_mode

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sample_profile": "public_rtos_prevalidation_large_input",
        "sample_tier": sample_tier,
        "public_source": {
            "seed_systrace": str(seed_systrace),
            "seed_systrace_sha256": _sha256(seed_systrace),
            "trace_family": "Apache NuttX task trace / systrace",
            "source_urls": list(dict.fromkeys(source_urls or PUBLIC_SOURCE_URLS)),
        },
        "derivation": {
            "producer_ver": PUBLIC_RTOS_PRODUCER_VER,
            "target_size_bytes": int(target_size_bytes),
            "real_seed_target_bytes": int(real_seed_target_bytes),
            "base_seed_trace_size_bytes": int(base_size_bytes),
            "expanded_seed_trace_size_bytes": int(seed_trace_size_bytes),
            "repeat": repeat,
            "cycle_gap_ns": int(cycle_gap_ns) if cycle_gap_ns is not None else None,
            "event_count": repeated_event_count,
            "candidate_mode": candidate_mode,
            "padding_mode": "seeded_zero_chunk_extension" if seed_trace_size_bytes < target_size_bytes else "none",
        },
        "conversion": conversion_meta,
        "outputs": {
            "baseline": baseline_manifest,
            "candidate": candidate_manifest,
        },
        "limitations": [
            "public_rtos_seeded_prevalidation_input",
            "not_final_formal_external_evidence",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_public_rtos_large_input")
    parser.add_argument("--seed-systrace", required=True)
    parser.add_argument("--baseline-output", required=True)
    parser.add_argument("--candidate-output")
    parser.add_argument("--sample-tier", choices=("fast", "dense"), default="dense")
    parser.add_argument("--target-size-bytes", type=int, default=DEFAULT_TARGET_SIZE_BYTES)
    parser.add_argument("--real-seed-target-bytes", type=int)
    parser.add_argument("--candidate-mode", choices=("mirror", "timestamp_jitter"))
    parser.add_argument("--cycle-gap-ns", type=int)
    parser.add_argument("--source-url", action="append", default=[])
    parser.add_argument("--report")
    args = parser.parse_args(argv)

    sample_tier = str(args.sample_tier)
    real_seed_target_bytes = (
        int(args.real_seed_target_bytes)
        if args.real_seed_target_bytes is not None
        else (FAST_REAL_SEED_TARGET_BYTES if sample_tier == "fast" else DENSE_REAL_SEED_TARGET_BYTES)
    )
    candidate_mode = str(args.candidate_mode or ("mirror" if sample_tier == "fast" else "timestamp_jitter"))

    payload = build_public_rtos_large_input(
        seed_systrace=Path(args.seed_systrace).expanduser().resolve(),
        baseline_output=Path(args.baseline_output).expanduser().resolve(),
        candidate_output=Path(args.candidate_output).expanduser().resolve() if args.candidate_output else None,
        target_size_bytes=int(args.target_size_bytes),
        real_seed_target_bytes=real_seed_target_bytes,
        sample_tier=sample_tier,
        candidate_mode=candidate_mode,
        cycle_gap_ns=int(args.cycle_gap_ns) if args.cycle_gap_ns is not None else None,
        source_urls=list(args.source_url),
    )
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.report:
        Path(args.report).expanduser().resolve().write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
