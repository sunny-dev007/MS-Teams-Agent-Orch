#!/usr/bin/env bash
set -euo pipefail

cd /home/site/wwwroot 2>/dev/null || cd "$(dirname "$0")/.."

# Oryx remote-build installs packages into antenv; activate it for runtime.
if [ -f /antenv/bin/activate ]; then
  # shellcheck disable=SC1091
  source /antenv/bin/activate
elif [ -f antenv/bin/activate ]; then
  # shellcheck disable=SC1091
  source antenv/bin/activate
elif [ -f /home/site/wwwroot/antenv/bin/activate ]; then
  # shellcheck disable=SC1091
  source /home/site/wwwroot/antenv/bin/activate
fi

export PYTHONPATH="${PYTHONPATH:-}:$(pwd)/src"
PORT="${PORT:-${WEBSITES_PORT:-8000}}"

echo "Starting WhatsApp AI Agent on port ${PORT}"
echo "Python: $(command -v python)"
python -c "import uvicorn, fastapi; print('deps-ok')"

exec python -m uvicorn agent.main:app --host 0.0.0.0 --port "$PORT"
