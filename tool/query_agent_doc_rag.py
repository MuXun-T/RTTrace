from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parser.agent_doc_rag import (  # noqa: E402
    format_rag_query_markdown,
    load_agent_doc_rag_index,
    query_agent_doc_rag_index,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="query_agent_doc_rag")
    parser.add_argument("--index", type=Path, default=ROOT_DIR / "docs" / "rag_index" / "agent_doc_index.json")
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    args = parser.parse_args(argv)

    index = load_agent_doc_rag_index(args.index)
    report = query_agent_doc_rag_index(index, query=args.query, top_k=args.top_k)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.markdown_output is not None:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(format_rag_query_markdown(report), encoding="utf-8")
    print(json.dumps({"query": args.query, "result_count": report["result_count"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
