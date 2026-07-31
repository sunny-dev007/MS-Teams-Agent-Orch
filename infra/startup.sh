#!/usr/bin/env bash
set -euo pipefail

cd /home/site/wwwroot 2>/dev/null || cd "$(dirname "$0")/.."

export PYTHONPATH="${PYTHONPATH:-}:$(pwd)/src"
PORT="${PORT:-8000}"

exec python -m uvicorn agent.main:app --host 0.0.0.0 --port "$PORT"
