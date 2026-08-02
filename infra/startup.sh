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

# Persistent storage survives Oryx zip deploy (wwwroot is replaced each release).
# NEVER truncate agent.db — that wipes WhatsApp sessions and LangGraph checkpoints.
if [ -n "${WEBSITE_SITE_NAME:-}" ]; then
  mkdir -p /home/site/data/workspaces
  if [ ! -f /home/site/data/agent.db ]; then
    touch /home/site/data/agent.db
  fi
  export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:////home/site/data/agent.db}"
  export WORKSPACE_DIR="${WORKSPACE_DIR:-/home/site/data/workspaces}"
  echo "Persistent data dir: /home/site/data (db exists=$( [ -f /home/site/data/agent.db ] && echo yes || echo no ))"
fi

PORT="${PORT:-${WEBSITES_PORT:-8000}}"

echo "Starting WhatsApp AI Agent on port ${PORT}"
echo "Python: $(command -v python)"
echo "git: $(command -v git || echo 'missing — using HTTP API fallback for GitHub')"
python -c "import uvicorn, fastapi; print('deps-ok')"

exec python -m uvicorn agent.main:app --host 0.0.0.0 --port "$PORT"
