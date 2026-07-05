from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser import decode_trace
from parser.codec import CHUNK_HEADER_STRUCT, GLOBAL_HEADER_STRUCT, TRACE_CHUNK_MAGIC

DEFAULT_TARGET_SIZE_BYTES = 1024 * 1024 * 1024
DEFAULT_FILLER_CHUNK_BYTES = 64 * 1024 * 1024
ZERO_BLOCK = b"\0" * (1024 * 1024)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _crc32_zeroes(length: int) -> int:
    crc = 0
    remaining = int(length)
    while remaining > 0:
        chunk = min(remaining, len(ZERO_BLOCK))
        crc = zlib.crc32(ZERO_BLOCK[:chunk], crc)
        remaining -= chunk
    return crc & 0xFFFFFFFF


def _write_zeroes(handle, length: int) -> None:
    remaining = int(length)
    while remaining > 0:
        chunk = min(remaining, len(ZERO_BLOCK))
        handle.write(ZERO_BLOCK[:chunk])
        remaining -= chunk


def _seed_metadata(seed_path: Path) -> tuple[bytes, int, float]:
    seed_bytes = seed_path.read_bytes()
    if len(seed_bytes) < GLOBAL_HEADER_STRUCT.size:
        raise SystemExit(f"seed trace header is truncated: {seed_path}")
    header_tuple = GLOBAL_HEADER_STRUCT.unpack_from(seed_bytes, 0)
    dict_ver = int(header_tuple[4])
    decoded = decode_trace(seed_path)
    if not decoded.ok:
        raise SystemExit(f"seed trace is not loadable: {seed_path} ({decoded.code}: {decoded.message})")
    events = decoded.data.get("events") or []
    last_event_ts = float(events[-1].timestamp_aligned) if events else 0.0
    return seed_bytes, dict_ver, last_event_ts


def build_formal_large_input(
    *,
    seed_input: Path,
    output: Path,
    target_size_bytes: int,
    filler_chunk_bytes: int,
) -> dict[str, object]:
    if target_size_bytes <= 0:
        raise SystemExit("--target-size-bytes must be positive")
    if filler_chunk_bytes <= 0:
        raise SystemExit("--filler-chunk-mb must be positive")

    seed_bytes, dict_ver, last_event_ts = _seed_metadata(seed_input)
    output.parent.mkdir(parents=True, exist_ok=True)

    appended_chunks = 0
    current_size = len(seed_bytes)
    with output.open("wb") as handle:
        handle.write(seed_bytes)
        while current_size < target_size_bytes:
            payload_size = min(filler_chunk_bytes, target_size_bytes - current_size)
            chunk_header = CHUNK_HEADER_STRUCT.pack(
                TRACE_CHUNK_MAGIC,
                1,
                0,
                0,
                payload_size,
                int(last_event_ts),
                int(last_event_ts),
                0,
                0,
                dict_ver,
                _crc32_zeroes(payload_size),
            )
            handle.write(chunk_header)
            _write_zeroes(handle, payload_size)
            current_size += len(chunk_header) + payload_size
            appended_chunks += 1

    output_size = output.stat().st_size
    return {
        "seed_input": str(seed_input),
        "output": str(output),
        "target_size_bytes": int(target_size_bytes),
        "output_size_bytes": int(output_size),
        "filler_chunk_bytes": int(filler_chunk_bytes),
        "appended_chunk_count": appended_chunks,
        "dict_ver": dict_ver,
        "last_event_timestamp": last_event_ts,
        "sha256": _sha256(output),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_formal_large_input")
    parser.add_argument("--seed-input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--target-size-bytes", type=int, default=DEFAULT_TARGET_SIZE_BYTES)
    parser.add_argument("--filler-chunk-mb", type=int, default=DEFAULT_FILLER_CHUNK_BYTES // (1024 * 1024))
    parser.add_argument("--report")
    args = parser.parse_args(argv)

    payload = build_formal_large_input(
        seed_input=Path(args.seed_input).expanduser().resolve(),
        output=Path(args.output).expanduser().resolve(),
        target_size_bytes=int(args.target_size_bytes),
        filler_chunk_bytes=int(args.filler_chunk_mb) * 1024 * 1024,
    )
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.report:
        Path(args.report).expanduser().resolve().write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
