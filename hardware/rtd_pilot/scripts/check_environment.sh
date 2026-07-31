#!/usr/bin/env bash
set -euo pipefail
command -v pyocd >/dev/null
command -v arm-none-eabi-gcc >/dev/null
command -v cmake >/dev/null
command -v ninja >/dev/null
test -c /dev/ttyUSB0 || { echo "board CH340 /dev/ttyUSB0 is required by phase1-pinmap-v2" >&2; exit 2; }
id -nG | tr ' ' '\n' | grep -qx dialout
id -nG | tr ' ' '\n' | grep -qx plugdev
pyocd list
echo "environment checks passed; this does not prove DSLogic capture or target wiring"
