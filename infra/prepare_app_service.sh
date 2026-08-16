#!/usr/bin/env bash
# Idempotent App Service prepare for WhatsApp AI Agent deploy.
# Never wipes MS_GRAPH_* / fabric ENABLE_* settings.
# On Azure Conflict: retry, then succeed if desired state is already applied.
set -euo pipefail

RESOURCE_GROUP="${1:?resource group required}"
APP_NAME="${2:?app name required}"

echo "Preparing App Service ${APP_NAME} in ${RESOURCE_GROUP}"

desired_settings=(
  "SCM_DO_BUILD_DURING_DEPLOYMENT=true"
  "ENABLE_ORYX_BUILD=true"
  "ORYX_DISABLE_OUTPUT_COPY_COMPRESSION=true"
  "WEBSITES_PORT=8000"
  "WEBSITES_CONTAINER_START_TIME_LIMIT=1800"
  "SCM_COMMAND_IDLE_TIMEOUT=1800"
  "GIT_PYTHON_REFRESH=quiet"
  "DATABASE_URL=sqlite+aiosqlite:////home/site/data/agent.db"
  "WORKSPACE_DIR=/home/site/data/workspaces"
)

settings_already_ok() {
  local raw
  raw=$(az webapp config appsettings list \
    --resource-group "${RESOURCE_GROUP}" \
    --name "${APP_NAME}" -o json 2>/dev/null || echo "[]")
  SETTINGS_JSON="$raw" python3 <<'PY'
import json, os, sys
try:
    items = json.loads(os.environ.get("SETTINGS_JSON") or "[]")
except Exception:
    sys.exit(1)
have = {i.get("name"): str(i.get("value") or "") for i in items}
need = {
    "SCM_DO_BUILD_DURING_DEPLOYMENT": "true",
    "ENABLE_ORYX_BUILD": "true",
    "ORYX_DISABLE_OUTPUT_COPY_COMPRESSION": "true",
    "WEBSITES_PORT": "8000",
    "WEBSITES_CONTAINER_START_TIME_LIMIT": "1800",
    "SCM_COMMAND_IDLE_TIMEOUT": "1800",
    "GIT_PYTHON_REFRESH": "quiet",
    "DATABASE_URL": "sqlite+aiosqlite:////home/site/data/agent.db",
    "WORKSPACE_DIR": "/home/site/data/workspaces",
}
missing = [k for k, v in need.items() if have.get(k) != v]
if missing:
    print("missing_or_mismatch:", ", ".join(missing))
    sys.exit(1)
sys.exit(0)
PY
}

startup_already_ok() {
  local cmd
  cmd=$(az webapp config show \
    --resource-group "${RESOURCE_GROUP}" \
    --name "${APP_NAME}" \
    --query appCommandLine -o tsv 2>/dev/null || true)
  [ "${cmd}" = "bash infra/startup.sh" ]
}

always_on_ok() {
  local on
  on=$(az webapp config show \
    --resource-group "${RESOURCE_GROUP}" \
    --name "${APP_NAME}" \
    --query alwaysOn -o tsv 2>/dev/null || echo false)
  [ "${on}" = "true" ] || [ "${on}" = "True" ]
}

apply_settings() {
  az webapp config appsettings set \
    --resource-group "${RESOURCE_GROUP}" \
    --name "${APP_NAME}" \
    --settings "${desired_settings[@]}" \
    --output none
}

apply_startup_file() {
  # Only startup command — avoid bundling always-on (separate, optional).
  az webapp config set \
    --resource-group "${RESOURCE_GROUP}" \
    --name "${APP_NAME}" \
    --startup-file "bash infra/startup.sh" \
    --output none
}

apply_always_on() {
  az webapp config set \
    --resource-group "${RESOURCE_GROUP}" \
    --name "${APP_NAME}" \
    --always-on true \
    --output none
}

# --- App settings (required for Oryx) ---
if settings_already_ok; then
  echo "Oryx app settings already correct — skip update"
else
  ok=0
  for attempt in $(seq 1 10); do
    if apply_settings; then
      echo "Oryx app settings OK (attempt ${attempt})"
      ok=1
      break
    fi
    echo "App settings conflict/busy — retry ${attempt}/10 (sleep $((15 + attempt * 5))s)..."
    sleep $((15 + attempt * 5))
    if settings_already_ok; then
      echo "Oryx app settings reached desired state during wait — continue"
      ok=1
      break
    fi
  done
  if [ "$ok" -ne 1 ]; then
    if settings_already_ok; then
      echo "Settings OK after final check despite Conflict — continue"
    else
      echo "Failed to apply required Oryx app settings"
      exit 1
    fi
  fi
fi

# Best-effort cleanup — never fail deploy; suppress noisy JSON dump
az webapp config appsettings delete \
  --resource-group "${RESOURCE_GROUP}" \
  --name "${APP_NAME}" \
  --setting-names PYTHONPATH \
  --output none \
  2>/dev/null || true

# --- Startup file (required) ---
if startup_already_ok; then
  echo "Startup file already 'bash infra/startup.sh' — skip update"
else
  ok=0
  for attempt in $(seq 1 10); do
    if apply_startup_file; then
      echo "Startup file OK (attempt ${attempt})"
      ok=1
      break
    fi
    echo "Startup config conflict/busy — retry ${attempt}/10..."
    sleep $((15 + attempt * 5))
    if startup_already_ok; then
      echo "Startup file reached desired state during wait — continue"
      ok=1
      break
    fi
  done
  if [ "$ok" -ne 1 ]; then
    if startup_already_ok; then
      echo "Startup OK after final check despite Conflict — continue"
    else
      echo "Failed to set required startup file"
      exit 1
    fi
  fi
fi

# --- Always On (optional on B1; Conflict must NOT fail deploy) ---
if always_on_ok; then
  echo "Always On already enabled — skip"
else
  if apply_always_on 2>/tmp/always_on_err.txt; then
    if always_on_ok; then
      echo "Always On enabled"
    else
      echo "##vso[task.logissue type=warning]Always On command returned OK but not reflected yet — continuing deploy."
    fi
  else
    echo "##vso[task.logissue type=warning]Could not enable Always On (Conflict/busy). Startup file is set — continuing deploy."
    head -c 400 /tmp/always_on_err.txt 2>/dev/null || true
    echo
  fi
fi

# Preserve fabric Graph config warning only
GRAPH_ID=$(az webapp config appsettings list \
  --resource-group "${RESOURCE_GROUP}" \
  --name "${APP_NAME}" \
  --query "[?name=='MS_GRAPH_CLIENT_ID'].value | [0]" -o tsv || true)
if [ -n "${GRAPH_ID}" ]; then
  echo "MS_GRAPH_CLIENT_ID present (fabric Graph settings preserved)"
else
  echo "##vso[task.logissue type=warning]MS_GRAPH_CLIENT_ID not set — Docs Agent Graph config missing (non-fatal)"
fi

echo "App Service prepare complete"
