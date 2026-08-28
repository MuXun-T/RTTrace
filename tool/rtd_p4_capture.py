#!/usr/bin/env python3
"""Entry point for the fail-closed P4 preparation and bounded UART-wire CLI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from p4_capture.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
