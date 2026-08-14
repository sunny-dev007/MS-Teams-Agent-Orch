#!/usr/bin/env bash
# Post-deploy live checks for WhatsApp AI Agent App Service.
# Kept OUT of azure-pipelines.yml so nested scripts cannot break YAML parsing.
set -euo pipefail

APP_NAME="${1:?app name required}"
RESOURCE_GROUP="${2:?resource group required}"
BASE_URL="https://${APP_NAME}.azurewebsites.net"

az webapp start --resource-group "${RESOURCE_GROUP}" --name "${APP_NAME}" || true

echo "Waiting for /health then Copilot channel (Oryx antenv build may take 10-20 min on B1)..."
for i in $(seq 1 80); do
  CODE=$(curl -sS --max-time 15 -o /tmp/health.json -w "%{http_code}" \
    "${BASE_URL}/health" || echo 000)
  echo "attempt $i -> /health HTTP $CODE"
  if [ "$CODE" = "200" ]; then
    cat /tmp/health.json
    echo
    for j in $(seq 1 20); do
      CCODE=$(curl -sS --max-time 15 -o /tmp/copilot_health.json -w "%{http_code}" \
        "${BASE_URL}/api/channels/copilot/health" || echo 000)
      echo "copilot attempt $j -> HTTP $CCODE"
      if [ "$CCODE" = "200" ]; then
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
        UNAUTH=$(curl -sS --max-time 30 -o /tmp/copilot_unauth.json -w "%{http_code}" \
          -X POST "${MSG_URL}" -H "Content-Type: application/json" \
          -d "{\"user_id\":\"${USER_ID}\",\"message\":\"ping\"}" || echo 000)
        echo "copilot auth missing key -> HTTP $UNAUTH"
        if [ "$UNAUTH" != "401" ]; then
          cat /tmp/copilot_unauth.json 2>/dev/null || true
          echo "Expected 401 without X-Copilot-Api-Key"
          exit 1
        fi
        FORBIDDEN=$(curl -sS --max-time 30 -o /tmp/copilot_forbidden.json -w "%{http_code}" \
          -X POST "${MSG_URL}" \
          -H "Content-Type: application/json" \
          -H "X-Copilot-Api-Key: ${COPILOT_KEY}" \
          -d '{"user_id":"not-allowlisted-user","message":"ping"}' || echo 000)
        echo "copilot auth bad user -> HTTP $FORBIDDEN"
        if [ "$FORBIDDEN" != "403" ]; then
          cat /tmp/copilot_forbidden.json 2>/dev/null || true
          echo "Expected 403 for non-allowlisted user"
          exit 1
        fi
        REPOS=$(curl -sS --max-time 90 -o /tmp/copilot_repos.json -w "%{http_code}" \
          -X POST "${MSG_URL}" \
          -H "Content-Type: application/json" \
          -H "X-Copilot-Api-Key: ${COPILOT_KEY}" \
          -d "{\"user_id\":\"${USER_ID}\",\"message\":\"check my repos\"}" || echo 000)
        echo "copilot check my repos -> HTTP $REPOS"
        cat /tmp/copilot_repos.json
        echo
        if [ "$REPOS" != "200" ]; then
          echo "check my repos must return 200 (not 500)"
          exit 1
        fi
        python3 -c "import json; b=json.load(open('/tmp/copilot_repos.json')); r=(b.get('reply') or '').strip();
assert r, 'check my repos returned empty reply'; print('check my repos reply chars=', len(r))"
        exit 0
      fi
      sleep 10
    done
    echo "Copilot /api/channels/copilot/health did not return 200 after /health OK"
    cat /tmp/copilot_health.json 2>/dev/null || true
    exit 1
  fi
  if [ "$CODE" = "403" ]; then
    az webapp start --resource-group "${RESOURCE_GROUP}" --name "${APP_NAME}" || true
  fi
  sleep 15
done
echo "Health check failed after deploy (Oryx may still be building — check Kudu logs)"
exit 1
