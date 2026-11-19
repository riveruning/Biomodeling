#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
pdf=0
ocr=0
for option in "$@"; do
  case "$option" in
    --pdf) pdf=1 ;;
    --ocr) pdf=1; ocr=1 ;;
    *) printf 'Usage: bash setup.sh [--pdf] [--ocr]\n' >&2; exit 1 ;;
  esac
done
if command -v python3.12 >/dev/null 2>&1; then
  interpreter=python3.12
else
  interpreter=python3
fi
"$interpreter" -c 'import struct,sys; assert sys.version_info[:2] == (3,12) and struct.calcsize("P") == 8, "64-bit Python 3.12 is required"'
if [[ ! -x .venv/bin/python ]]; then "$interpreter" -m venv .venv; fi
dependency_lock=requirements.lock
if [[ "$pdf" == 1 ]]; then dependency_lock=requirements-pdf.lock; fi
.venv/bin/python -m pip install -r "$dependency_lock"
.venv/bin/python -m pip install --no-deps --no-build-isolation .
if [[ "$ocr" == 1 ]]; then .venv/bin/python prepare_ocr_data.py --output runs/ocr-data; fi
.venv/bin/python -m biomodel_agent workspace-doctor
printf '\nReady. Run: bash start.sh\n'
