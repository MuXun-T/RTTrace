from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop.context import ContextStore
from desktop.repository import DatasetRepository
from desktop.services import BackgroundJobManager, ExportService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="compare_normalized_packages")
    parser.add_argument("--left", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    export = ExportService(DatasetRepository(), ContextStore(), BackgroundJobManager())
    left = export.export_NormalizePackage(args.left)
    right = export.export_NormalizePackage(args.right)
    if not left.ok:
        raise SystemExit(left.message)
    if not right.ok:
        raise SystemExit(right.message)

    mismatched_sections = sorted(
        section
        for section in set(left.data) | set(right.data)
        if left.data.get(section) != right.data.get(section)
    )
    payload = {
        "left": args.left,
        "right": args.right,
        "match": not mismatched_sections,
        "mismatched_sections": mismatched_sections,
    }
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if payload["match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
