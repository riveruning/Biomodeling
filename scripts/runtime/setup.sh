#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if command -v python3.12 >/dev/null 2>&1; then
  interpreter=python3.12
else
  interpreter=python3
fi
"$interpreter" -c 'import struct,sys; assert sys.version_info[:2] == (3,12) and struct.calcsize("P") == 8, "64-bit Python 3.12 is required"'
if [[ ! -x .venv/bin/python ]]; then "$interpreter" -m venv .venv; fi
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation .
.venv/bin/python -m biomodel_agent workspace-doctor
printf '\nReady. Run: bash start.sh\n'
