#!/usr/bin/env bash
# Post-deploy live checks for WhatsApp AI Agent App Service.
# Kept OUT of azure-pipelines.yml so nested scripts cannot break YAML parsing.
#
# Graceful on B1 cold starts: retries timeouts / 5xx; never treats curl
# timeout as "000000" (curl -w already prints 000 — do not || echo 000).
set -euo pipefail

APP_NAME="${1:?app name required}"
RESOURCE_GROUP="${2:?resource group required}"
BASE_URL="https://${APP_NAME}.azurewebsites.net"

# curl http_code helper: on timeout/network fail curl often prints 000 via -w
# AND exits non-zero. Appending another 000 via "|| echo 000" yields "000000".
http_code() {
  local out_file="$1"
  shift
  local code
  code=$(curl -sS -o "${out_file}" -w "%{http_code}" "$@" || true)
  if [ -z "${code}" ]; then
    code="000"
  fi
  # Strip accidental CR / whitespace
  code=$(printf '%s' "${code}" | tr -d '\r\n ')
  printf '%s' "${code}"
}

# Retry until expected code(s), or give up. Transient: 000, 408, 429, 502, 503, 504.
wait_for_http() {
  local label="$1"
  local out_file="$2"
  local expect_csv="$3" # e.g. "200" or "401,403"
  local max_attempts="$4"
  local sleep_s="$5"
  shift 5
  local attempt code
  for attempt in $(seq 1 "${max_attempts}"); do
    code=$(http_code "${out_file}" "$@")
    echo "${label} attempt ${attempt}/${max_attempts} -> HTTP ${code}"
    case ",${expect_csv}," in
      *",${code},"*)
        return 0
        ;;
    esac
    case "${code}" in
      000|408|429|502|503|504)
        echo "  transient (${code}) — retrying in ${sleep_s}s (B1 cold start / warm-up)"
        sleep "${sleep_s}"
        ;;
      *)
        echo "  unexpected HTTP ${code} (not in [${expect_csv}])"
        cat "${out_file}" 2>/dev/null || true
        echo
        return 1
        ;;
    esac
  done
  echo "${label} gave up after ${max_attempts} attempts (last HTTP ${code:-unknown})"
  cat "${out_file}" 2>/dev/null || true
  echo
  return 1
}

az webapp start --resource-group "${RESOURCE_GROUP}" --name "${APP_NAME}" || true

echo "Waiting for /health then Copilot channel (Oryx antenv build may take 10-20 min on B1)..."
for i in $(seq 1 80); do
  CODE=$(http_code /tmp/health.json --max-time 20 "${BASE_URL}/health")
  echo "attempt $i -> /health HTTP $CODE"
  if [ "$CODE" = "200" ]; then
    cat /tmp/health.json
    echo

    if ! wait_for_http \
      "copilot health" /tmp/copilot_health.json "200" 30 10 \
      --max-time 20 "${BASE_URL}/api/channels/copilot/health"; then
      echo "Copilot /api/channels/copilot/health did not return 200 after /health OK"
      exit 1
    fi
    cat /tmp/copilot_health.json
    echo

    # Auth + authorization + real "check my repos" must not 500.
    # Keys stay in App Service settings — never bake into the repo.
    COPILOT_KEY=$(az webapp config appsettings list \
      --resource-group "${RESOURCE_GROUP}" \
      --name "${APP_NAME}" \
      --query "[?name=='COPILOT_API_KEY'].value | [0]" -o tsv)
    ALLOW_RAW=$(az webapp config appsettings list \
      --resource-group "${RESOURCE_GROUP}" \
      --name "${APP_NAME}" \
      --query "[?name=='ALLOWED_TEAMS_USER_IDS'].value | [0]" -o tsv)
    if [ -z "${COPILOT_KEY}" ]; then
      echo "COPILOT_API_KEY missing on App Service — cannot verify auth"
      exit 1
    fi
    USER_ID=$(ALLOW_RAW="${ALLOW_RAW}" python3 -c "import json,os; r=(os.environ.get('ALLOW_RAW') or '').strip(); ids=[];
ids=json.loads(r) if r.startswith('[') else [];
ids=[str(x).strip() for x in ids if str(x).strip()] if ids else [p.strip().strip(chr(34)).strip(chr(39)) for p in r.split(',') if p.strip()];
print(ids[0] if ids else '')")
    if [ -z "${USER_ID}" ]; then
      echo "ALLOWED_TEAMS_USER_IDS empty — cannot verify authorization"
      exit 1
    fi

    MSG_URL="${BASE_URL}/api/channels/copilot/message"

    # First POST after deploy often times out on B1 while worker warms — retry.
    if ! wait_for_http \
      "copilot auth missing key" /tmp/copilot_unauth.json "401" 12 15 \
      --max-time 45 -X POST "${MSG_URL}" \
      -H "Content-Type: application/json" \
      -d "{\"user_id\":\"${USER_ID}\",\"message\":\"ping\"}"; then
      echo "Expected 401 without X-Copilot-Api-Key (after retries)"
      exit 1
    fi

    if ! wait_for_http \
      "copilot auth bad user" /tmp/copilot_forbidden.json "403" 8 10 \
      --max-time 45 -X POST "${MSG_URL}" \
      -H "Content-Type: application/json" \
      -H "X-Copilot-Api-Key: ${COPILOT_KEY}" \
      -d '{"user_id":"not-allowlisted-user","message":"ping"}'; then
      echo "Expected 403 for non-allowlisted user (after retries)"
      exit 1
    fi

    if ! wait_for_http \
      "copilot check my repos" /tmp/copilot_repos.json "200" 8 20 \
      --max-time 120 -X POST "${MSG_URL}" \
      -H "Content-Type: application/json" \
      -H "X-Copilot-Api-Key: ${COPILOT_KEY}" \
      -d "{\"user_id\":\"${USER_ID}\",\"message\":\"check my repos\"}"; then
      echo "check my repos must return 200 (not 500) after retries"
      exit 1
    fi
    cat /tmp/copilot_repos.json
    echo
    python3 -c "import json; b=json.load(open('/tmp/copilot_repos.json')); r=(b.get('reply') or '').strip();
assert r, 'check my repos returned empty reply'; print('check my repos reply chars=', len(r))"
    echo "Post-deploy verify OK"
    exit 0
  fi
  if [ "$CODE" = "403" ]; then
    az webapp start --resource-group "${RESOURCE_GROUP}" --name "${APP_NAME}" || true
  fi
  sleep 15
done
echo "Health check failed after deploy (Oryx may still be building — check Kudu logs)"
exit 1
