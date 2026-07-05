from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop.sample_data import write_scenario


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prepare_external_validation_fixture")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--repeat", type=int, default=48)
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    baseline_path = write_scenario(output_dir / "baseline.trace", name="multi_core", repeat=args.repeat)
    candidate_path = write_scenario(
        output_dir / "candidate.trace",
        name="multi_core",
        candidate_variant=True,
        repeat=args.repeat,
    )
    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fixture_name": "external_validation_pair",
        "scenario": "multi_core",
        "repeat": args.repeat,
        "files": {
            "baseline": {
                "path": str(baseline_path),
                "sha256": _sha256(baseline_path),
            },
            "candidate": {
                "path": str(candidate_path),
                "sha256": _sha256(candidate_path),
            },
        },
    }
    manifest_path = output_dir / "validation_fixture_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
