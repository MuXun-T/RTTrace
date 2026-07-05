from __future__ import annotations

import argparse
import io
import json
import os
import platform
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _render(payload: dict[str, object], output_path: str) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    Path(output_path).write_text(rendered, encoding="utf-8")
    print(rendered)


def _test_ids(results: list[tuple[object, str]]) -> list[str]:
    return [case.id() for case, _ in results]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_desktop_runtime_report")
    parser.add_argument("--module", default="tests.python.test_desktop_runtime")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    suite = unittest.defaultTestLoader.loadTestsFromName(args.module)
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    result = runner.run(suite)

    tests_run = result.testsRun
    skipped = len(result.skipped)
    executed = tests_run - skipped
    failure_ids = _test_ids(result.failures)
    error_ids = _test_ids(result.errors)
    runtime_contract_ok = result.wasSuccessful() and executed > 0 and not failure_ids and not error_ids
    failure_summary = None
    if failure_ids or error_ids:
        failure_summary = ", ".join(failure_ids + error_ids)
    elif executed == 0:
        failure_summary = "desktop runtime tests were skipped or not executed"

    payload = {
        "platform": platform.system(),
        "python_version": platform.python_version(),
        "qt_qpa_platform": os.environ.get("QT_QPA_PLATFORM"),
        "module": args.module,
        "command": f"{Path(sys.executable)} -m unittest {args.module}",
        "tests_run": tests_run,
        "executed": executed,
        "skipped": skipped,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "successful": result.wasSuccessful(),
        "runtime_contract_ok": runtime_contract_ok,
        "failure_summary": failure_summary,
        "failed_tests": failure_ids,
        "error_tests": error_ids,
        "output": stream.getvalue(),
    }
    _render(payload, args.output)
    return 0 if runtime_contract_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
