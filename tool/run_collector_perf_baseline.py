from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _environment() -> dict[str, object]:
    return {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
    }


def _compile_runner(runner_path: Path) -> list[str]:
    compile_cmd = [
        "g++",
        "-std=c++17",
        "-O2",
        "-pthread",
        "-Icollector/include",
        "collector/core/trace_collector.cpp",
        "tool/collector_bench_runner.cpp",
        "-o",
        str(runner_path),
    ]
    subprocess.run(compile_cmd, cwd=ROOT, check=True)
    return compile_cmd


def _run_scenario(runner_path: Path, scenario: str, *extra_args: str) -> dict[str, object]:
    cmd = [str(runner_path), "--scenario", scenario, *extra_args]
    completed = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    stdout = completed.stdout.strip()
    if not stdout:
        raise RuntimeError(f"{scenario} produced no output: {completed.stderr.strip()}")
    payload = json.loads(stdout)
    payload["command"] = cmd
    payload["returncode"] = completed.returncode
    payload["stderr"] = completed.stderr.strip()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate collector performance baseline JSON.")
    parser.add_argument(
        "--output",
        default="docs/collector_perf_baseline_20260316_linux.json",
        help="Path to output JSON report.",
    )
    parser.add_argument("--runner", default="build/collector_bench_runner", help="Path to compiled runner.")
    parser.add_argument("--single-events", type=int, default=20000)
    parser.add_argument("--multi-events-per-core", type=int, default=15000)
    parser.add_argument("--cores", type=int, default=4)
    parser.add_argument("--fallback-events", type=int, default=4000)
    args = parser.parse_args()

    runner_path = ROOT / args.runner
    runner_path.parent.mkdir(parents=True, exist_ok=True)
    compile_cmd = _compile_runner(runner_path)

    scenarios = [
        _run_scenario(runner_path, "single_core", "--events", str(args.single_events)),
        _run_scenario(
            runner_path,
            "multi_core",
            "--events",
            str(args.multi_events_per_core),
            "--cores",
            str(args.cores),
        ),
        _run_scenario(runner_path, "slow_fallback", "--events", str(args.fallback_events)),
    ]
    report = {
        "generated_at": _iso_now(),
        "environment": _environment(),
        "compile_command": compile_cmd,
        "runner": str(runner_path),
        "scenarios": scenarios,
        "status": "ok" if all(item.get("status") == "ok" for item in scenarios) else "error",
    }

    output_path = ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output_path))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
