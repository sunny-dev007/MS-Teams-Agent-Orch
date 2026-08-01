#!/usr/bin/env bash
set -euo pipefail

# Oryx may run this from an extracted /tmp app path (not always wwwroot).
# Activate virtualenv from the current working directory first.
# Do NOT use host-built .python_packages — those wheels often need newer GLIBC
# than Azure App Service provides (breaks cryptography → WhatsApp graph import).
if [ -f antenv/bin/activate ]; then
  # shellcheck disable=SC1091
  source antenv/bin/activate
elif [ -f /antenv/bin/activate ]; then
  # shellcheck disable=SC1091
  source /antenv/bin/activate
elif [ -f /home/site/wwwroot/antenv/bin/activate ]; then
  # shellcheck disable=SC1091
  source /home/site/wwwroot/antenv/bin/activate
fi

if [ -d src ]; then
  export PYTHONPATH="${PYTHONPATH:-}:$(pwd)/src"
elif [ -d /home/site/wwwroot/src ]; then
  cd /home/site/wwwroot
  export PYTHONPATH="${PYTHONPATH:-}:$(pwd)/src"
fi

PORT="${PORT:-${WEBSITES_PORT:-8000}}"

echo "Starting WhatsApp AI Agent on port ${PORT}"
echo "Python: $(command -v python)"
echo "git: $(command -v git || echo 'missing — using HTTP API fallback for GitHub')"
python -c "import uvicorn, fastapi; print('deps-ok')"

exec python -m uvicorn agent.main:app --host 0.0.0.0 --port "$PORT"
