#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_VENV_DIR="${ROOT_DIR}/.venv"
VENV_DIR="${RTTRACE_VENV_DIR:-${DEFAULT_VENV_DIR}}"

create_venv() {
  python3 -m venv --copies "$1"
}

have_pip() {
  python3 -m pip --version >/dev/null 2>&1
}

if ! create_venv "${VENV_DIR}" >/dev/null 2>&1; then
  rm -rf "${VENV_DIR}"
  if [ "${VENV_DIR}" = "${DEFAULT_VENV_DIR}" ]; then
    VENV_DIR="/tmp/rttrace-desktop-venv"
    rm -rf "${VENV_DIR}"
    if ! create_venv "${VENV_DIR}" >/dev/null 2>&1; then
      if have_pip; then
        python3 -m pip install --user -e "${ROOT_DIR}[desktop,dev]"
        echo "Desktop dependencies installed to user site-packages."
        echo "Smoke test with: QT_QPA_PLATFORM=offscreen python3 ${ROOT_DIR}/tool/check_desktop_env.py"
        exit 0
      fi
      cat <<'EOF'
Unable to create a virtual environment or fall back to pip.

This host is missing Python packaging components. Install them first:
  Ubuntu/Debian: sudo apt-get install python3-pip python3-venv

Then rerun:
  bash tool/bootstrap_desktop_env.sh
EOF
      exit 1
    fi
  else
    exit 1
  fi
fi
source "${VENV_DIR}/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python -m pip install -e "${ROOT_DIR}[desktop,dev]"

echo "Desktop environment ready."
echo "Activate with: source ${VENV_DIR}/bin/activate"
echo "Smoke test with: QT_QPA_PLATFORM=offscreen python ${ROOT_DIR}/tool/check_desktop_env.py"
