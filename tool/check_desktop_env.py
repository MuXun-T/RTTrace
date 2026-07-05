from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from desktop import PG_AVAILABLE, PYSIDE_AVAILABLE  # noqa: E402
from desktop.app.gui import RuntimeProbeWindow  # noqa: E402
from desktop.qt_compat import QTimer, ensure_qapplication  # noqa: E402
from desktop.sample_data import write_scenario  # noqa: E402


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _render(payload: dict[str, object], output_path: str | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if output_path:
        Path(output_path).write_text(rendered, encoding="utf-8")
    print(rendered)


def main() -> int:
    parser = argparse.ArgumentParser(prog="check_desktop_env")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        app = ensure_qapplication(sys.argv)
    except RuntimeError as exc:
        _render(
            {
                "platform": platform.system(),
                "python_version": platform.python_version(),
                "qt_qpa_platform": os.environ.get("QT_QPA_PLATFORM"),
                "pyside6": PYSIDE_AVAILABLE,
                "pyqtgraph": PG_AVAILABLE,
                "pyside6_version": _package_version("PySide6"),
                "pyqtgraph_version": _package_version("pyqtgraph"),
                "error": str(exc),
            },
            args.output,
        )
        return 1
    fd, temp_path = tempfile.mkstemp(prefix="rttrace-env-", suffix=".trace")
    os.close(fd)
    path = Path(temp_path)
    write_scenario(path, name="basic")
    try:
        window = RuntimeProbeWindow(source=str(path))
    except RuntimeError as exc:
        _render(
            {
                "platform": platform.system(),
                "python_version": platform.python_version(),
                "qt_qpa_platform": os.environ.get("QT_QPA_PLATFORM"),
                "pyside6": PYSIDE_AVAILABLE,
                "pyqtgraph": PG_AVAILABLE,
                "pyside6_version": _package_version("PySide6"),
                "pyqtgraph_version": _package_version("pyqtgraph"),
                "error": str(exc),
            },
            args.output,
        )
        path.unlink(missing_ok=True)
        return 1
    window.show()
    payload = {
        "platform": platform.system(),
        "python_version": platform.python_version(),
        "qt_qpa_platform": os.environ.get("QT_QPA_PLATFORM"),
        "pyside6": PYSIDE_AVAILABLE,
        "pyqtgraph": PG_AVAILABLE,
        "pyside6_version": _package_version("PySide6"),
        "pyqtgraph_version": _package_version("pyqtgraph"),
        "trace": str(path),
        "window_title": getattr(window, "windowTitle", lambda: "n/a")(),
    }
    _render(payload, args.output)
    QTimer.singleShot(0, app.quit)  # type: ignore[attr-defined]
    code = app.exec()
    path.unlink(missing_ok=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
