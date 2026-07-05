from __future__ import annotations

import argparse
import io
import json
import os
import platform
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

DEFAULT_PYTEST_ARGS = ["tests/python", "-q"]
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
os.chdir(ROOT_DIR)


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class _PytestStatsPlugin:
    def __init__(self) -> None:
        self.tests_collected = 0
        self.counts = {
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "xfailed": 0,
            "xpassed": 0,
            "deselected": 0,
        }

    def pytest_collection_finish(self, session: "pytest.Session") -> None:
        self.tests_collected = session.testscollected

    def pytest_terminal_summary(
        self,
        terminalreporter: "pytest.TerminalReporter",
        exitstatus: int,
        config: "pytest.Config",
    ) -> None:
        stats = terminalreporter.stats
        self.counts["passed"] = len(stats.get("passed", []))
        self.counts["failed"] = len(stats.get("failed", []))
        # Pytest reports collection/runtime errors under "error".
        self.counts["errors"] = len(stats.get("error", []))
        self.counts["skipped"] = len(stats.get("skipped", []))
        self.counts["xfailed"] = len(stats.get("xfailed", []))
        self.counts["xpassed"] = len(stats.get("xpassed", []))
        self.counts["deselected"] = len(stats.get("deselected", []))
        self.tests_collected = max(
            self.tests_collected,
            getattr(terminalreporter, "_numcollected", 0) or 0,
            self.counts["passed"]
            + self.counts["failed"]
            + self.counts["errors"]
            + self.counts["skipped"]
            + self.counts["xfailed"]
            + self.counts["xpassed"]
            + self.counts["deselected"],
        )


def _empty_counts() -> dict[str, int]:
    return {
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
        "deselected": 0,
    }


def _executed_count(counts: dict[str, int]) -> int:
    return (
        counts["passed"]
        + counts["failed"]
        + counts["errors"]
        + counts["xfailed"]
        + counts["xpassed"]
    )


def _all_skipped(tests_collected: int, counts: dict[str, int]) -> bool:
    selected = max(0, tests_collected - counts["deselected"])
    return selected > 0 and counts["skipped"] == selected and _executed_count(counts) == 0


def _build_payload(
    *,
    pytest_args: list[str],
    pytest_version: str | None,
    tests_collected: int,
    counts: dict[str, int],
    duration_seconds: float,
    exit_code: int,
    successful: bool,
    stdout_text: str,
    stderr_text: str,
) -> dict[str, object]:
    return {
        "generated_at": _iso_now(),
        "platform": platform.system(),
        "python_version": platform.python_version(),
        "pytest_version": pytest_version,
        "command": f"{Path(sys.executable)} -m pytest {' '.join(pytest_args)}",
        "args": pytest_args,
        "tests_collected": tests_collected,
        "passed": counts["passed"],
        "failed": counts["failed"],
        "errors": counts["errors"],
        "skipped": counts["skipped"],
        "xfailed": counts["xfailed"],
        "xpassed": counts["xpassed"],
        "deselected": counts["deselected"],
        "executed": _executed_count(counts),
        "all_skipped": _all_skipped(tests_collected, counts),
        "duration_seconds": duration_seconds,
        "exit_code": exit_code,
        "successful": successful,
        "stdout": stdout_text,
        "stderr": stderr_text,
    }


def _render(payload: dict[str, object], output_path: str) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    Path(output_path).write_text(rendered, encoding="utf-8")
    print(rendered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_pytest_report")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--pytest-args",
        nargs=argparse.REMAINDER,
        help="override pytest args; defaults to: tests/python -q",
    )
    args = parser.parse_args(argv)

    pytest_args = list(args.pytest_args or DEFAULT_PYTEST_ARGS)
    try:
        import pytest
    except ModuleNotFoundError as exc:
        payload = _build_payload(
            pytest_args=pytest_args,
            pytest_version=None,
            tests_collected=0,
            counts=_empty_counts(),
            duration_seconds=0.0,
            exit_code=1,
            successful=False,
            stdout_text="",
            stderr_text=f"{exc.__class__.__name__}: {exc}. Install the optional dev dependency set before running pytest regression.",
        )
        _render(payload, args.output)
        return 1

    plugin = _PytestStatsPlugin()
    stdout = io.StringIO()
    stderr = io.StringIO()
    started = time.perf_counter()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = pytest.main(pytest_args, plugins=[plugin])
    duration_seconds = round(time.perf_counter() - started, 6)
    payload = _build_payload(
        pytest_args=pytest_args,
        pytest_version=pytest.__version__,
        tests_collected=plugin.tests_collected,
        counts=plugin.counts,
        duration_seconds=duration_seconds,
        exit_code=int(exit_code),
        successful=int(exit_code) == 0,
        stdout_text=stdout.getvalue(),
        stderr_text=stderr.getvalue(),
    )
    _render(payload, args.output)
    return 0 if payload["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
