# End-to-End Operations Guide

Complete reference for **Sunny Personal AI Agent**: code change → test → deploy → Teams/Copilot usage.

Covers Azure resources, Azure DevOps, App Service, Copilot Studio, custom connectors, WhatsApp, and the rich reply UX.

---

## 1. Product overview

| Channel | Entry | Session key |
|---------|--------|-------------|
| WhatsApp | Meta Cloud API → `POST /webhooks/whatsapp` | Phone number |
| Microsoft Teams / Copilot Studio | Custom connector → `POST /api/channels/copilot/message` | `teams:{user_id}` (OID or email/UPN) |

**Core value:** multi-gate coding workflow — plan → develop → PR → CI → human **APPROVE** → merge/deploy — with human-in-the-loop on WhatsApp and Teams.

WhatsApp behavior is unchanged when Teams is enabled. Both can run in parallel.

---

## 2. Azure & Microsoft components (inventory)

### 2.1 Azure (runtime)

| Component | Name / value | Purpose |
|-----------|----------------|---------|
| Subscription | Pay-As-You-Go (tenant used by project) | Billing |
| Resource Group | `ai-agent-rg` | Central India |
| App Service Plan | `ai-agent-plan` (B1 Linux typical) | Host |
| Web App | `whatsapp-ai-agent-sunny` | Python 3.12 agent API |
| Production URL | `https://whatsapp-ai-agent-sunny.azurewebsites.net` | Public HTTPS |
| Persistent data | `/home/site/data/agent.db`, `workspaces/`, `workflow_gates/` | Survives zip deploy |
| Secrets | App Service **Application settings** (not in git) | Tokens, keys |

### 2.2 Azure DevOps (source + CI/CD)

| Component | Value |
|-----------|--------|
| Organization | `https://dev.azure.com/Az-FullStack` |
| Project | `Project-NIT` |
| Repo | `web.Whatsapp-AI-Agent` |
| Pipeline | `web.Whatsapp-AI-Agent` (definition id **15**) |
| Pipeline file | `azure-pipelines.yml` |
| Service connection | `AI Copilot` (Azure Resource Manager) |
| Trigger | Push to `main` → Build + Deploy; PRs → Build/tests only |

### 2.3 Copilot Studio / Power Platform

| Component | Typical value |
|-----------|----------------|
| Environment | `CopilotStudio-Dev` |
| Agent (new experience) | e.g. **Sunny Personal Dev Agent** |
| Tool | Custom connector **SunnyPersonalAI** → action **PersonalAIAgent** |
| Spec file | `docs/copilot-studio/personal-ai-agent.swagger.json` (OpenAPI 2.0) |
| Auth | API key header `X-Copilot-Api-Key` (= App Service `COPILOT_API_KEY`) |
| Channel | Microsoft Teams (after agent Publish) |

### 2.4 Other integrations (agent features)

| System | Used for |
|--------|----------|
| Meta WhatsApp Cloud API | User chat + gate commands |
| Azure OpenAI | LLM planning / coding / review |
| Azure DevOps REST | Repos, PRs, pipelines, CI watch |
| GitHub (optional) | Sample app / alternate SCM |
| Gmail / Google Calendar | Email digest + meetings (WhatsApp path) |

---

## 3. Application architecture (code map)

```text
Teams / Copilot Studio
        │  Custom connector (PersonalAIAgent)
        ▼
POST /api/channels/copilot/message   ← src/agent/api/copilot.py
        │
        ├─ Auth: X-Copilot-Api-Key
        ├─ Allowlist: ALLOWED_TEAMS_USER_IDS (OID and/or email)
        └─ route_inbound_message (shared gates) ← src/agent/api/channel_gates.py
                │
WhatsApp ───────┘
POST /webhooks/whatsapp
                │
                ▼
        LangGraph / specialists (plan, code, review, deploy, CI)
                │
                ▼
        send_channel_message ← src/agent/services/channel_notify.py
           ├─ WhatsApp → Meta Graph
           └─ Teams → channel_outbox → Copilot drains as `reply`
```

### Key modules

| Area | Path |
|------|------|
| FastAPI app | `src/agent/main.py` |
| Settings | `src/agent/config.py` |
| Copilot API | `src/agent/api/copilot.py` |
| Shared gates | `src/agent/api/channel_gates.py` |
| Rich UX replies | `src/agent/services/rich_response.py` |
| CI watch / Final evaluation | `src/agent/services/ci_watch.py` |
| Deploy progress | `src/agent/services/deploy_notify.py` |
| Notifications | `src/agent/agents/notification.py` |
| Startup (Oryx) | `infra/startup.sh` |
| Pipeline | `azure-pipelines.yml` |

### App Service settings (names only — values in Azure Portal)

| Setting | Purpose |
|---------|---------|
| `ENABLE_TEAMS_COPILOT_CHANNEL` | Feature flag for Teams API |
| `COPILOT_API_KEY` | Shared secret with Copilot connector |
| `ALLOWED_TEAMS_USER_IDS` | JSON array or CSV of Entra OIDs **and/or** emails |
| `DATABASE_URL` | Prefer `sqlite+aiosqlite:////home/site/data/agent.db` |
| `WORKSPACE_DIR` | `/home/site/data/workspaces` |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` / `ENABLE_ORYX_BUILD` | Remote Python build |
| WhatsApp / AzDO / Azure OpenAI keys | Existing WhatsApp coding flows |

Startup command: `bash infra/startup.sh` → uvicorn `agent.main:app` on port 8000.

---

## 4. Code modify → test → deploy (procedure)

### 4.1 Local modify

1. Branch from `main` (or work on `main` for hotfixes as your team prefers).
2. Change code under `src/agent/…` and tests under `tests/…`.
3. Never commit `.env`, PATs, or `COPILOT_API_KEY`.

### 4.2 Local test

```bash
cd /path/to/My-Personal-AI-Agent
python -m venv .venv && source .venv/bin/activate   # if needed
pip install -r requirements.txt pytest pytest-asyncio httpx
pip install .
PYTHONPATH=src python -m pytest tests/ -q --tb=short
```

**Guards also run in CI:**

- Pydantic `BaseSettings` must come from `pydantic-settings`
- OpenAPI must include WhatsApp + Copilot routes
- Plain `ALLOWED_TEAMS_USER_IDS` UUID must parse (NoDecode)

Rich UX unit tests: `tests/test_services/test_rich_response.py`.

### 4.3 Commit & push

```bash
git add <files>
git commit -m "Describe why the change exists."
git push -u origin HEAD
```

Push to **`main`** triggers pipeline definition **15**.

### 4.4 Pipeline behavior

| Branch / reason | Build & Test | Deploy to App Service |
|-----------------|--------------|------------------------|
| `main` (CI) | Yes | **Yes** |
| PR into `main` | Yes | **No** (validate only) |
| `agent/*` feature branches | Via PR | Deploy only after merge to `main` + APPROVE flow |

**Deploy stage:**

1. Enable Oryx settings + startup file  
2. Kudu async zipdeploy (retries on HTTP 409)  
3. Poll `/health` then `/api/channels/copilot/health` until 200  

Oryx antenv build on B1 may take **10–20 minutes**.

### 4.5 Live verification

```bash
curl -sS https://whatsapp-ai-agent-sunny.azurewebsites.net/health
curl -sS https://whatsapp-ai-agent-sunny.azurewebsites.net/api/channels/copilot/health
```

Expect Copilot health JSON like: `enabled`, `api_key_configured`, `allowlist_size`.

Authenticated smoke (do not log the key):

```bash
curl -sS -X POST \
  "https://whatsapp-ai-agent-sunny.azurewebsites.net/api/channels/copilot/message" \
  -H "Content-Type: application/json" \
  -H "X-Copilot-Api-Key: $COPILOT_API_KEY" \
  -d '{"user_id":"<oid-or-email>","message":"status","action":"status"}'
```

### 4.6 After deploy — Copilot / Teams

| Item | Action |
|------|--------|
| Custom connector | **No re-import required** for reply UX-only changes |
| Agent instructions | Keep: call `PersonalAIAgent`; show `reply` **verbatim** (sections/tables/bars) |
| Connection API key | Must match App Service `COPILOT_API_KEY` |
| Allowlist | Include both Entra OID and email/UPN if Studio sends email |
| Publish | Re-publish agent only if instructions/tools changed; backend-only deploy needs no Studio republish |

---

## 5. Teams / Copilot setup (summary)

Detailed click-paths:

- [`docs/copilot-studio/NEW-EXPERIENCE-TEAMS.md`](./copilot-studio/NEW-EXPERIENCE-TEAMS.md)  
- [`docs/copilot-studio/FIND-CUSTOM-CONNECTOR.md`](./copilot-studio/FIND-CUSTOM-CONNECTOR.md)  
- [`docs/teams-copilot-channel.md`](./teams-copilot-channel.md)

**Short path:**

1. Power Automate → environment **CopilotStudio-Dev** → **More → Discover all → Custom connectors**  
2. Import `docs/copilot-studio/personal-ai-agent.swagger.json`  
3. Security: API key header `X-Copilot-Api-Key` (label is display name only)  
4. Test operation with allowlisted `user_id` + `status`  
5. Copilot Studio agent → **Add tool → Connectors → PersonalAIAgent**  
6. Publish → Teams channel  

---

## 6. Rich response UX (current release)

Module: `src/agent/services/rich_response.py`

| Channel | Layout |
|---------|--------|
| WhatsApp | Bold sections, bullets, ASCII metric bars |
| Teams (`teams:…`) | Same + markdown tables for multi-field summaries |

Used for CI final evaluation, deploy progress, PR checks, evaluation, task status, PR review scores, and notification templates.

**No connector schema change** — formatting lives inside the `reply` string.

---

## 7. Operational runbooks

### Pipeline failed on zipdeploy 409

Another Oryx/deploy in progress. Pipeline retries; or wait and re-run definition 15 on `main`.

### Teams 403 “not allowlisted”

Studio sent email as `user_id` but allowlist had only OID (or vice versa). Update `ALLOWED_TEAMS_USER_IDS` to include both; restart App Service if needed.

### Teams 401

Connector connection key ≠ `COPILOT_API_KEY`.

### Copilot 404 on `/api/channels/copilot/*`

Old build without dual-channel routes, or site still restarting. Check OpenAPI and pipeline Deploy stage.

### Site boot crash on allowlist

Fixed with `NoDecode` + CSV/JSON parser in `config.py`. Prefer JSON array in App Service.

---

## 8. Security checklist (US/UK readiness)

- [ ] Secrets only in App Service / Key Vault — never git  
- [ ] Rotate any key that appeared in chat or screenshots  
- [ ] Least-privilege AzDO PAT and WhatsApp token  
- [ ] Allowlist users/groups; empty allowlist = allow all (avoid in prod)  
- [ ] Audit: prefer logging task_id, channel, gate (extend as needed)  
- [ ] Human APPROVE before merge/deploy remains mandatory for coding flows  

---

## 9. Demo script (leadership / customer)

1. Teams: `status` → formatted card  
2. `check my repos` → pick AzDO repo  
3. Plan → *PROCEED* → develop → PR  
4. CI validate / *FIX TESTS* if needed  
5. *APPROVE* → merge → Final evaluation with metrics + Live portal  
6. Optional: same gates on WhatsApp  

---

## 10. Document index

| Doc | Topic |
|-----|--------|
| This file | E2E ops + inventory |
| `docs/architecture.md` | LangGraph / agents |
| `docs/deployment.md` | App Service / pipeline deep dive |
| `docs/setup-guide.md` | Local setup |
| `docs/teams-copilot-channel.md` | Dual channel API |
| `docs/copilot-studio/*` | Connector + new Copilot UI |
| `docs/whatsapp-e2e-test.md` | WhatsApp E2E |
| `README.md` | Product overview |

---

*Last updated with rich-response UX release and Teams custom-connector path.*
