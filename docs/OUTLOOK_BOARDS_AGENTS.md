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
- Boards: Graph user profile + AzDO WIQL (never `@Me`)
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

## Entra / Graph permissions (application)

| Permission | Purpose |
|---|---|
| `Mail.Read` | Outlook inbox |
| `User.Read.All` (or `Directory.Read.All`) | OID → mail/UPN for Boards |

## Azure DevOps PAT

Work Items **Read** + ability to list projects/repos (same PAT used for coding is fine).

## Teams E2E

1. Enable flags + restart.
2. `my action items` → pick project → see metrics + priority table.
3. `#42` → pick repo → Dev plan starts with work-item context.
4. `check my repos` → classic flow still works.
5. WhatsApp `check my emails` → Gmail.

## Health

`GET /api/channels/copilot/health` → `fabric.outlook_*` / `fabric.boards_*`.
