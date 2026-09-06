# Orbit — Azure Bot → Microsoft Teams (complete guide)

**Audience:** engineers, admins, or partners who need to understand what was built, how it works, what it costs, and how to scale it.  
**Status:** Live parallel channel beside WhatsApp and Copilot Studio. Does **not** replace or modify the Copilot Studio **AI Dev Agent**.  
**Last updated:** September 2026

---

## 1. Executive summary

**Orbit** is a Microsoft Teams personal bot that talks to the same Personal AI Agent backend already used by WhatsApp (and by Copilot Studio via a different path).

| Question | Answer |
|----------|--------|
| What is Orbit? | Teams chat bot named **Orbit** |
| How does it connect? | **Azure Bot Service** (Bot Framework) → App Service messaging endpoint |
| What does it run? | Same agent pipeline as Copilot/WhatsApp (coding gates, docs RAG, FinOps, Outlook, Boards, etc.) |
| Does it use Copilot Studio? | **No** — no Copilot Credits for the Teams chat path |
| Does it break AI Dev Agent? | **No** — separate channel, separate endpoint, additive feature flags |
| How do users open it? | Sideload `Orbit-Teams.zip` (POC) or publish org-wide via Teams Admin Center |

**Why this path exists:** Copilot Studio trials can run out of **Copilot Credits** even when calendar days remain. Orbit keeps Teams access without that credit meter for the chat channel itself.

---

## 2. Architecture (how it works)

```text
User in Microsoft Teams (1:1 chat with Orbit)
        │
        │  Bot Framework activity (channelId = msteams)
        ▼
Azure Bot Service  —  resource: sunny-orbit-bot (SKU F0)
        │  messaging endpoint
        ▼
App Service  —  whatsapp-ai-agent-sunny
  POST /api/channels/teamsbot/messages
        │
        ├─ Validate Bot Framework JWT (Microsoft App ID + secret + tenant)
        ├─ OrbitTeamsBot.on_turn (src/agent/api/teams_bot.py)
        ├─ Optional allowlist: ALLOWED_TEAMS_USER_IDS
        └─ Reuse Copilot pipeline: _handle_copilot_message
                │
                ▼
        Shared gates / LangGraph / specialists
                │
                ▼
        Reply via Bot Framework (markdown + Adaptive Cards)
```

### Parallel channels (same backend, isolated entry points)

| Channel | Entry | Session key | Studio credits? |
|---------|--------|-------------|-----------------|
| WhatsApp | `POST /webhooks/whatsapp` | Phone number | No |
| Copilot Studio (AI Dev Agent) | `POST /api/channels/copilot/message` | `teams:{user_id}` | **Yes** (Studio usage) |
| **Orbit (Azure Bot)** | `POST /api/channels/teamsbot/messages` | `teams:{aad_object_id}` | **No** |

WhatsApp and both Teams paths can run at the same time. Stopping a session on one channel does not clear the other.

### Code map

| Piece | Location |
|-------|----------|
| Teams Bot router | `src/agent/api/teams_bot.py` |
| Wired in app | `src/agent/main.py` → `teams_bot_router` |
| Shared message handler | `src/agent/api/copilot.py` → `_handle_copilot_message` |
| Shared gates | `src/agent/api/channel_gates.py` |
| Config flags | `src/agent/config.py` (`enable_teams_bot_channel`, `microsoft_app_*`) |
| Sideload package | `docs/teams-bot/Orbit-Teams.zip` |
| Manifest | `docs/teams-bot/manifest.json` |

---

## 3. Live Azure inventory (what we created)

| Resource | Name / value | Notes |
|----------|----------------|-------|
| Subscription | Pay-As-You-Go | Tenant used by Sunny’s personal Azure |
| Tenant ID | `d4740603-c108-4cbe-9be8-c75289d4da2a` | AI Enterprise Labs / publisher domain `aienterpriselabs.com` |
| Resource group | `ai-agent-rg` | Central India |
| App Service | `whatsapp-ai-agent-sunny` | Python 3.12 agent API |
| App Service plan | `ai-agent-plan` | Often **F1 Free** for POC (cold starts) |
| Entra app registration | **Orbit** | App (client) ID below; **Single tenant** (`AzureADMyOrg`) |
| Microsoft App ID | `f9cc9178-67d7-40de-afea-6b0a83cb9360` | Same as Teams `botId` / manifest `id` |
| Enterprise application (SP) | Required in tenant | Must exist or replies fail with `AADSTS7000229` |
| Azure Bot | `sunny-orbit-bot` | SKU **F0**; type **SingleTenant** |
| Messaging endpoint | `https://whatsapp-ai-agent-sunny.azurewebsites.net/api/channels/teamsbot/messages` | |
| Teams channel on bot | Enabled | `acceptedTerms: true` (required) |
| Health check | `GET /api/channels/teamsbot/health` | Expect `enabled: true` |

### App Service application settings (Orbit)

| Setting | Purpose |
|---------|---------|
| `ENABLE_TEAMS_BOT_CHANNEL` | `true` to enable the route |
| `MICROSOFT_APP_ID` | Entra app / bot App ID |
| `MICROSOFT_APP_PASSWORD` | Client secret value (never commit) |
| `MICROSOFT_APP_TENANT_ID` | Single-tenant directory ID |
| `TEAMS_BOT_DISPLAY_NAME` | `Orbit` |
| `MicrosoftAppType` | `SingleTenant` (Bot Framework convention) |
| `MicrosoftAppTenantId` | Same tenant ID |
| `ALLOWED_TEAMS_USER_IDS` | Optional JSON/CSV of Entra OIDs and/or UPNs; **empty = allow all** |

Existing WhatsApp and Copilot (`ENABLE_TEAMS_COPILOT_CHANNEL`, `COPILOT_API_KEY`) settings stay unchanged.

---

## 4. What we did (build & publish checklist)

Use this as a runbook to recreate or hand over the work.

### Phase A — Backend (code)

1. Added Bot Framework endpoint `/api/channels/teamsbot/messages` (+ `/health`).
2. Wired Bot Framework adapter (SingleTenant via `channel_auth_tenant`).
3. Mapped Teams activities → `_handle_copilot_message` (same reply formats: markdown + Adaptive Cards).
4. Added dependencies: `botbuilder-core`, `botbuilder-schema`.
5. Deployed to App Service with Oryx build (`SCM_DO_BUILD_DURING_DEPLOYMENT=true`).
6. Verified `GET /api/channels/teamsbot/health` → `enabled: true`.

### Phase B — Identity (Entra ID)

1. Created app registration **Orbit** (single-tenant).
2. Created a **client secret**; stored only in App Service as `MICROSOFT_APP_PASSWORD`.
3. Created the **service principal / enterprise application** for that App ID in the same tenant  
   (`az ad sp create --id <APP_ID>`).  
   **Without this, Teams delivers the user message but the bot cannot send a reply** (`AADSTS7000229: missing service principal`).

### Phase C — Azure Bot

1. Created Azure Bot **`sunny-orbit-bot`** (F0) linked to the App ID.
2. Set messaging endpoint to the App Service `/api/channels/teamsbot/messages` URL.
3. Set **Microsoft App Type = SingleTenant** and **Microsoft App Tenant ID**.
4. Enabled the **Microsoft Teams** channel and accepted channel terms (`acceptedTerms: true`).

### Phase D — Teams package & install (POC)

1. Built Teams app package: `docs/teams-bot/Orbit-Teams.zip`  
   Contents: `manifest.json`, `color.png`, `outline.png`.
2. Manifest `botId` / `id` = Microsoft App ID; scopes = **personal** (1:1 chat).
3. In Teams: **Apps** → **Manage your apps** → **Upload a custom app** → sideload the zip (requires org permission to sideload).
4. Open **Orbit** chat → send `Hello` / `help` / `status`.

### Phase E — Hard lessons (troubleshooting we already hit)

| Symptom | Cause | Fix |
|---------|--------|-----|
| Message shows sent (hollow tick), no reply | App Service **503** / crash | Fix deploy; check startup logs (`starlette` / missing `antenv`) |
| Message reaches backend, no reply | Missing Entra **service principal** | `az ad sp create --id <APP_ID>` |
| Teams never hits endpoint | Teams channel terms not accepted | Set `acceptedTerms: true` on MsTeams channel |
| Auth / reply token failures | Wrong secret, wrong tenant, SingleTenant mismatch | Align App ID, secret, tenant on bot + App Service |
| User blocked | Allowlist OID/UPN mismatch | Add Entra Object ID to `ALLOWED_TEAMS_USER_IDS` or clear list for POC |

---

## 5. Prerequisites

### People / org

- Microsoft 365 / Teams account in the **same Entra tenant** as the SingleTenant bot (or change app type — see scaling).
- Permission to **sideload** custom Teams apps (POC), **or** Teams admin to publish to the org catalog.
- Azure subscription Owner / Contributor on `ai-agent-rg` (or equivalent).
- Entra permission to create app registrations + service principals (Application Administrator or Cloud Application Administrator often enough).

### Technical

- Running App Service with the agent code that includes `teams_bot` router.
- Valid HTTPS public endpoint (App Service default hostname is fine).
- Azure OpenAI (or configured LLM) and other agent secrets already working for WhatsApp/Copilot.
- Python packages built on deploy (`botbuilder-*` in `requirements.txt`).

### Security baseline (recommended even for 1 user)

- Keep `MICROSOFT_APP_PASSWORD` only in App Service / Key Vault — never in git.
- Prefer a non-empty `ALLOWED_TEAMS_USER_IDS` for personal POC.
- Rotate client secrets periodically; update App Service after rotation.
- Do not paste secrets into Teams manifests or docs.

---

## 6. Cost model (estimates — verify on Azure Pricing Calculator)

Prices change by region, currency, and agreement. Treat numbers below as **order-of-magnitude guidance** for planning, not a quote.

### 6.1 Orbit path (Azure Bot → Teams) — what you pay for

| Component | POC (1 person) | Org / multi-user | Notes |
|-----------|----------------|------------------|-------|
| **Azure Bot F0** | **$0** | **$0** for standard channels | Microsoft Teams is a **standard** channel → unlimited messages on F0/S1 for that channel |
| **Azure Bot S1** | Usually unnecessary for Teams-only | Optional | Needed mainly for **premium** channels (Direct Line / Web Chat volume billing) |
| **App Service** | F1 Free ≈ **$0** (limits) | **B1+** recommended (~tens of USD/month) | F1: no Always On, cold starts, 1 instance — poor for many users |
| **Entra app + secret** | **$0** | **$0** | Identity only |
| **LLM (Azure OpenAI)** | Pay per token | Scales with users × messages | Usually the **largest** variable cost |
| **Storage / SQLite on App Service** | Included in plan disk | Move DB if scaling | `/home/site/data` on Linux App Service |
| **Copilot Studio credits** | **Not used** by Orbit chat | **Not used** | Main cost win vs Studio agents |
| **Teams / M365 licenses** | Users already need Teams | Same | Orbit does not replace M365 licensing |

**Single-person POC (typical):** Azure Bot F0 + existing App Service + existing Azure OpenAI ≈ **incremental Azure Bot cost ≈ $0**, plus whatever OpenAI tokens that one user burns.

**Multi-user / whole organization:**

1. Upgrade App Service off **F1** (B1/B2 or higher, or Container Apps / AKS later).
2. Budget **Azure OpenAI** for concurrent load (TPM quotas, PTU or Pay-as-you-go).
3. Azure Bot Teams channel itself usually stays **$0** message fees.
4. Optional: Application Insights, Key Vault, Front Door — small ops costs.
5. Admin time for Teams org publish + support.

### 6.2 Rough monthly scenarios (illustrative)

| Scenario | Infra (Ballpark) | AI tokens | Copilot Studio |
|----------|------------------|-----------|----------------|
| You only (Orbit POC on F1) | ~$0–30 if you move off F1 | Low–medium | $0 |
| 10–50 internal users (Orbit) | App Service B1/B2 + monitoring | Medium–high | $0 |
| Entire org on Orbit | Proper compute + quotas + HA | Dominant cost | $0 |
| Same usage via Copilot Studio agents | App Service still needed for backend | Same backend tokens **plus** | **Copilot Credits** (often $200 / 25k credit pack or PAYG ~$0.01/credit; trials expire) |

Always confirm: [Azure Bot pricing](https://azure.microsoft.com/pricing/details/bot-services/), [App Service pricing](https://azure.microsoft.com/pricing/details/app-service/linux/), [Copilot Studio pricing](https://www.microsoft.com/microsoft-copilot/microsoft-copilot-studio).

---

## 7. Scaling up & going “public face”

### 7.1 Organization-wide (internal employees)

| Step | Action |
|------|--------|
| 1 | Package a production Teams app (bump `manifest.json` `version`, privacy/terms URLs, support email). |
| 2 | Teams Admin Center → **Teams apps** → **Manage apps** → Upload / approve **Orbit**. |
| 3 | Set availability to **Everyone** or specific security groups. |
| 4 | Replace sideload with catalog install; remove old personal sideloads. |
| 5 | Decide allowlist: empty (all authenticated users) **or** Entra group-based gate in code (stronger). |
| 6 | Upgrade App Service (Always On, larger SKU); add Application Insights. |
| 7 | Load-test `/api/channels/teamsbot/messages` and Azure OpenAI quotas. |
| 8 | Document support owners and secret rotation. |

Manifest today is **personal** scope only. For team/channel chats, add `team` / `groupchat` scopes and handle `@mention` (code already strips mentions).

### 7.2 Public / multi-tenant / customer-facing

SingleTenant bots only work inside **your** Entra tenant. For external customers:

| Option | When to use |
|--------|-------------|
| **Publish to Microsoft Teams app store** | Broad public distribution; store review, privacy policy, support, certification |
| **Multi-tenant Entra app** | Other orgs install into their tenant (admin consent) |
| **User-assigned managed identity bot** | Advanced Azure-only identity pattern |
| **Web Chat / Direct Line on a public website** | Non-Teams surface; may use **premium** Bot channel pricing (S1) |
| **Separate production subscription / brand** | Isolation for SLA, billing, and data residency |

Also required for a real public face:

- Legal: privacy policy, terms, data retention, DPA.
- Security: Key Vault, WAF, rate limits, abuse allowlists, PII logging policy.
- Reliability: multi-instance App Service or containers, external DB (not SQLite), CI/CD gates.
- Observability: traces for `Orbit /messages hit`, auth failures, reply latency.
- Branding: icons, accent color, store listing screenshots.

### 7.3 What you do **not** need for Orbit scale

- Copilot Studio capacity packs (for the Orbit channel itself).
- Dataverse 1 GB just to host this Teams bot (that was a Copilot Studio / Power Platform concern).

---

## 8. Day-2 operations

### Health

```bash
curl -sS https://whatsapp-ai-agent-sunny.azurewebsites.net/api/channels/teamsbot/health
```

Expect `"enabled": true`.

### Logs to look for

- `Orbit /messages hit type=message channel=msteams auth=yes`
- `Orbit Teams activity type=message ...`
- Failures: `Teams bot process_activity failed`, `AADSTS...`, `Orbit bot auth failed`

Download logs:

```bash
az webapp log download -g ai-agent-rg -n whatsapp-ai-agent-sunny --log-file ./applogs.zip
```

### Redeploy (zip / Oryx)

Deploy `src`, `requirements.txt`, `infra` with build-during-deploy enabled. Startup command: `bash infra/startup.sh`.

### Rotate secret

1. Entra → App registration Orbit → Certificates & secrets → new secret.  
2. Update App Service `MICROSOFT_APP_PASSWORD`.  
3. Delete old secret after verification.  
4. Confirm Web Chat / Teams reply still works.

### Isolation test

Azure Portal → **sunny-orbit-bot** → **Test in Web Chat**.  
- Works in Web Chat, fails in Teams → Teams install / channel / catalog issue.  
- Fails in both → credentials, endpoint, or App Service.

---

## 9. Comparison: Orbit vs Copilot Studio AI Dev Agent

| Topic | Orbit (this doc) | AI Dev Agent (Copilot Studio) |
|-------|------------------|-------------------------------|
| Teams UX | Native Azure Bot chat | Copilot / Studio agent chat |
| Path to backend | Bot Framework → `/api/channels/teamsbot/messages` | Connector → `/api/channels/copilot/message` |
| Studio credits | None for chat channel | Consumes Copilot Credits |
| Authoring UX | Code + Teams manifest | Low-code Studio + connector |
| Best for | Cost-efficient Teams + same backend | Studio orchestration, connected agents, makers |
| Isolation | Parallel; does not edit Studio agent | Keep as previous production Teams experience |

Full Copilot Studio handoff: [`../copilot-studio/AI-DEV-AGENT-COPILOT-STUDIO.md`](../copilot-studio/AI-DEV-AGENT-COPILOT-STUDIO.md)

---

## 10. Quick start for a new teammate

1. Confirm health URL returns `enabled: true`.  
2. Sideload `docs/teams-bot/Orbit-Teams.zip` (or install from org catalog).  
3. Chat with **Orbit** → `help`.  
4. If no reply: check App Service running, service principal exists, Teams channel terms accepted, then logs for `Orbit /messages`.  
5. Do **not** change Copilot Studio AI Dev Agent settings unless that is an explicit separate task.

---

## 11. File index

| File | Purpose |
|------|---------|
| `docs/teams-bot/ORBIT-AZURE-BOT-TEAMS.md` | This document |
| `docs/teams-bot/Orbit-Teams.zip` | Sideload package |
| `docs/teams-bot/manifest.json` | Teams app manifest source |
| `src/agent/api/teams_bot.py` | Bot Framework handler |
| `docs/copilot-studio/AI-DEV-AGENT-COPILOT-STUDIO.md` | Previous Copilot Studio flow |
| `docs/teams-copilot-channel.md` | Copilot API details |
| `docs/E2E-OPERATIONS.md` | Broader ops (WhatsApp + Copilot) |
