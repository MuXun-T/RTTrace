from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(path: str) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="compare_acceptance_baselines")
    parser.add_argument("--left", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    left = _load(args.left)
    right = _load(args.right)

    structural_match = (
        left.get("dataset_scale") == right.get("dataset_scale")
        and left.get("inputs", {}).get("baseline_sha256") == right.get("inputs", {}).get("baseline_sha256")
        and left.get("inputs", {}).get("candidate_sha256") == right.get("inputs", {}).get("candidate_sha256")
    )
    consistency_match = left.get("consistency") == right.get("consistency")
    duration_delta = {
        key: round(
            abs(float(left.get("durations", {}).get(key, 0.0)) - float(right.get("durations", {}).get(key, 0.0))),
            6,
        )
        for key in sorted(set(left.get("durations", {})) | set(right.get("durations", {})))
    }
    payload = {
        "left": args.left,
        "right": args.right,
        "match": structural_match and consistency_match,
        "structural_match": structural_match,
        "consistency_match": consistency_match,
        "left_environment": left.get("environment"),
        "right_environment": right.get("environment"),
        "duration_delta": duration_delta,
    }
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if payload["match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
