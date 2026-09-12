#!/usr/bin/env bash
# Start Topher Property Hunter locally.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PH_PORT:-8234}"
echo "TOPHER PROPERTY HUNTER -> http://127.0.0.1:${PORT}"
exec python3 -m uvicorn hunter.api:app --host "${PH_HOST:-127.0.0.1}" --port "$PORT" "$@"
