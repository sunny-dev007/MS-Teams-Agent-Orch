# Outlook + Azure Boards Agents (Teams user-scoped)

Additive specialists for **Teams / Copilot** signed-in users. Defaults **OFF**.

| Agent | Flag | Phrases | Scope |
|---|---|---|---|
| **Outlook** | `ENABLE_OUTLOOK_AGENT` | `check my outlook`, Teams `check my emails` | Inbox of Entra OID only |
| **Boards** | `ENABLE_BOARDS_AGENT` | `my work items`, `my action items`, `my tickets` | Project picker → priority-sorted assigned items → ticket → Dev |

**WhatsApp Gmail** (`check my emails`) and **AzDO coding/PR/CI** classic path (`check my repos`) are unchanged.

## Boards flow (graceful Dev handoff)

```
my work items / my action items
  → if multiple AzDO projects: ask which project (reply 1, 2, …)
  → list assigned Bugs/Stories/Tasks sorted by Priority (metrics table)
  → reply 42 or #42 or "work on 42"
  → Boards loads work-item title+description as coding instruction
  → lists repos in that project (same Dev Agent)
  → pick repo number → plan → PROCEED → … (unchanged gates)
```

Classic path still works anytime: **check my repos**.

## Architecture

```
Teams message
  → channel_gates bypass (outlook / boards phrases)
  → planner → outlook_agent | boards_agent → notify_result
  → (ticket pick) boards seeds repo_wizard with pending_code_instruction
```

- Outlook: Graph app-only `Mail.Read` → `GET /users/{oid}/mailFolders/Inbox/messages`
- Boards: Graph user profile + AzDO WIQL (never `@Me`). Matches **mail, UPN, guest `#EXT#` UPN decoded to the original mailbox**, and AzDO `uniqueName` aliases so MSA Gmail assignments still appear for a Teams Entra identity.
- Soft workspace handoff: `productivity` lane (does not clear PROCEED/APPROVE except when entering productivity from a fabric clear — same as Knowledge)

## App Service settings

```bash
az webapp config appsettings set -g ai-agent-rg -n whatsapp-ai-agent-sunny --settings \
  ENABLE_OUTLOOK_AGENT=false \
  ENABLE_BOARDS_AGENT=false \
  OUTLOOK_MAIL_TOP=10 \
  BOARDS_WORK_ITEM_TOP=15 \
  AZDO_BOARDS_PROJECT=
```

Leave `AZDO_BOARDS_PROJECT` empty to always ask which project when more than one exists.

## Entra / Graph permissions (must be Application type)

This agent uses **client credentials** (app-only).  
**Delegated** `Mail.Read` (user signed-in) does **not** work and returns **403**.

| Permission | Type | Purpose |
|---|---|---|
| `Mail.Read` | **Application** | Read `/users/{id}/mailFolders/Inbox/messages` |
| `User.Read.All` (or `Directory.Read.All`) | **Application** | OID/UPN → mail for Boards |

### Exact clicks (your app: Release-Agent-Fabric-Docs)

1. Entra ID → **App registrations** → **Release-Agent-Fabric-Docs** → **API permissions**
2. Confirm you currently have `Mail.Read` as **Delegated** — keep it if you want; it is unused by this agent
3. **+ Add a permission** → **Microsoft Graph** → **Application permissions**
4. Search and add:
   - **Mail.Read**
   - **User.Read.All**
5. **Grant admin consent for Default Directory** (status must show green Granted)
6. Wait 1–2 minutes, restart App Service if needed, retry `check my outlook`

Optional hardening: [Application access policy](https://learn.microsoft.com/en-us/graph/auth-limit-mailbox-access) so the app can only access `sunny@aienterpriselabs.com`.

## Azure DevOps PAT

Work Items **Read** + ability to list projects/repos (same PAT used for coding is fine).

## Troubleshooting 403 on Outlook

| Symptom | Cause | Fix |
|---|---|---|
| `403` on `/users/.../messages` | Only **Delegated** Mail.Read | Add **Application** Mail.Read + admin consent |
| `403` right after adding Application Mail.Read | **Stale in-memory token** (no `Mail.Read` role yet) | **Restart** App Service `whatsapp-ai-agent-sunny`; agent also auto-retries once |
| `403` with Application Mail.Read granted + restart | **Exchange Application Access Policy** Denied | Run `Test-ApplicationAccessPolicy` (below); Allow your mailbox |
| Wrong mailbox | App Access Policy restricts others | Ensure your UPN is allowed in the policy |
| Token missing `Mail.Read` role | Consent not on the app used by `MS_GRAPH_CLIENT_ID` | Confirm App Service client id = `0a98eb76-b5ca-4269-9a47-f64456b2f776` |

### Exchange Application Access Policy (common after Mail.Read is granted)

If **any** application access policy exists in the tenant, apps without an explicit Allow are **Denied**.

```powershell
Connect-ExchangeOnline
Test-ApplicationAccessPolicy -Identity sunny@aienterpriselabs.com `
  -AppId 0a98eb76-b5ca-4269-9a47-f64456b2f776
# Expect AccessCheckResult = Granted

# If Denied — allow only your mailbox (least privilege):
New-DistributionGroup -Name "Graph-Mail-Allowed" -Type Security -Members sunny@aienterpriselabs.com
New-ApplicationAccessPolicy -AppId 0a98eb76-b5ca-4269-9a47-f64456b2f776 `
  -PolicyScopeGroupId "Graph-Mail-Allowed" -AccessRight RestrictAccess `
  -Description "Outlook agent — Sunny mailbox only"
```

Then wait a few minutes and retry `check my outlook`.

## Teams E2E

1. Enable flags + restart.
2. `my action items` → pick project → see metrics + priority table.
3. `#42` → pick repo → Dev plan starts with work-item context.
4. `check my repos` → classic flow still works.
5. WhatsApp `check my emails` → Gmail.

## Health

`GET /api/channels/copilot/health` → `fabric.outlook_*` / `fabric.boards_*`.
