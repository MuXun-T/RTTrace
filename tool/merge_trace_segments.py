from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parser.codec import GLOBAL_HEADER_STRUCT, SEGMENT_META_STRUCT, TRACE_FORMAT_MAGIC, TRACE_SEGMENT_META_MAGIC


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _chunk_stream_offset(path: Path) -> int:
    with path.open("rb") as handle:
        raw_header = handle.read(GLOBAL_HEADER_STRUCT.size)
        if len(raw_header) != GLOBAL_HEADER_STRUCT.size:
            raise SystemExit(f"trace header truncated: {path}")
        header_tuple = GLOBAL_HEADER_STRUCT.unpack_from(raw_header, 0)
        if int(header_tuple[0]) != TRACE_FORMAT_MAGIC:
            raise SystemExit(f"invalid trace header magic: {path}")
        format_ver = int(header_tuple[3])
        offset = GLOBAL_HEADER_STRUCT.size
        if format_ver >= 2:
            raw_segment_meta = handle.read(SEGMENT_META_STRUCT.size)
            if len(raw_segment_meta) != SEGMENT_META_STRUCT.size:
                raise SystemExit(f"segment meta truncated: {path}")
            meta_tuple = SEGMENT_META_STRUCT.unpack_from(raw_segment_meta, 0)
            if int(meta_tuple[0]) != TRACE_SEGMENT_META_MAGIC:
                raise SystemExit(f"invalid segment meta magic: {path}")
            offset += SEGMENT_META_STRUCT.size
        return offset


def merge_trace_segments(*, inputs: list[Path], output: Path) -> dict[str, object]:
    if len(inputs) < 2:
        raise SystemExit("at least two input traces are required")
    output.parent.mkdir(parents=True, exist_ok=True)

    merged_inputs: list[dict[str, object]] = []
    with output.open("wb") as dst:
        for index, source in enumerate(inputs):
            offset = 0 if index == 0 else _chunk_stream_offset(source)
            with source.open("rb") as src:
                if offset:
                    src.seek(offset)
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
            merged_inputs.append(
                {
                    "path": str(source),
                    "size_bytes": int(source.stat().st_size),
                    "sha256": _sha256(source),
                    "copied_from_offset": int(offset),
                }
            )

    return {
        "merged_at": output.as_posix(),
        "output_size_bytes": int(output.stat().st_size),
        "output_sha256": _sha256(output),
        "inputs": merged_inputs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="merge_trace_segments")
    parser.add_argument("--input", action="append", dest="inputs", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report")
    args = parser.parse_args(argv)

    payload = merge_trace_segments(
        inputs=[Path(item).expanduser().resolve() for item in args.inputs],
        output=Path(args.output).expanduser().resolve(),
    )
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.report:
        Path(args.report).expanduser().resolve().write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
