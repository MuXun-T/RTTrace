from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parser.agent_doc_rag import (  # noqa: E402
    DEFAULT_EXCLUDE_PARTS,
    DEFAULT_INCLUDE_GLOBS,
    build_agent_doc_rag_index,
    write_agent_doc_rag_index,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_agent_doc_rag_index")
    parser.add_argument("--repo-root", type=Path, default=WORKSPACE_ROOT)
    parser.add_argument("--output", type=Path, default=ROOT_DIR / "docs" / "rag_index" / "agent_doc_index.json")
    parser.add_argument("--max-file-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--chunk-lines", type=int, default=32)
    parser.add_argument("--overlap-lines", type=int, default=4)
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args(argv)

    index = build_agent_doc_rag_index(
        repo_root=args.repo_root,
        include_globs=args.include or DEFAULT_INCLUDE_GLOBS,
        exclude_parts=args.exclude or DEFAULT_EXCLUDE_PARTS,
        max_file_bytes=args.max_file_bytes,
        chunk_lines=args.chunk_lines,
        overlap_lines=args.overlap_lines,
    )
    write_agent_doc_rag_index(args.output, index)
    print(
        json.dumps(
            {
                "rag_index": str(args.output),
                "file_count": index["summary"]["file_count"],
                "chunk_count": index["summary"]["chunk_count"],
                "skipped_file_count": index["summary"]["skipped_file_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
