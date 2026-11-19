#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ! -x .venv/bin/python ]]; then printf 'Run bash setup.sh first.\n' >&2; exit 1; fi
if [[ -z "${BIOMODEL_TESSDATA:-}" && -f runs/ocr-data/eng.traineddata ]]; then
  export BIOMODEL_TESSDATA="$PWD/runs/ocr-data"
fi
exec .venv/bin/python -m biomodel_agent workbench --store runs/workbench --port 8765 "$@"
