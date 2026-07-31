#!/usr/bin/env bash
set -euo pipefail
# Deliberately read-only: does not reset, halt, flash, or erase the target.
pyocd list | tee /dev/stderr | grep -Eqi 'c251:f001|CMSIS-DAP'
pyocd list --targets | grep -qi 'stm32f103ze'
echo "target catalogue and probe presence verified; connection-to-target still requires pyOCD session check"
