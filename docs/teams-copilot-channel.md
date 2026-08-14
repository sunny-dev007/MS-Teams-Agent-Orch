# Teams + Copilot Studio dual channel

Use **Sunny Personal AI Agent** (or your Copilot Studio agent) in Microsoft Teams for the same coding/repo workflow as WhatsApp, without changing WhatsApp.

## Architecture

- Copilot keeps its smart brain for architecture / RAG / Azure Q&A.
- For coding and gate commands (`check my repos`, `Proceed`, `1`, `APPROVE`, `FIX TESTS`, `stop`, `status`), Copilot calls your App Service tool and relays the reply.
- Backend session key: `teams:{user_id}` (WhatsApp still uses the phone number).
- Long-running updates land in a Teams **outbox**; Copilot should call the tool with `action=poll` or the user can say `status`.

## App Service settings (same subscription)

Add these application settings on `whatsapp-ai-agent-sunny`:

| Name | Example | Notes |
|------|---------|--------|
| `ENABLE_TEAMS_COPILOT_CHANNEL` | `true` | Feature flag |
| `COPILOT_API_KEY` | long random secret | Must match Copilot tool header |
| `ALLOWED_TEAMS_USER_IDS` | your AAD object id | Comma-separated; empty = allow all (not recommended) |

Do **not** remove existing WhatsApp settings.

### Find your AAD object id

Azure Portal → Microsoft Entra ID → Users → `sunny@aienterpriselabs.com` → Object ID.

## API

`POST https://whatsapp-ai-agent-sunny.azurewebsites.net/api/channels/copilot/message`

Headers:

- `Content-Type: application/json`
- `X-Copilot-Api-Key: <COPILOT_API_KEY>`

Body:

```json
{
  "user_id": "<aad-object-id>",
  "message": "check my repos",
  "conversation_id": "optional",
  "display_name": "Sunny",
  "action": "message"
}
```

`action` values:

- `message` (default) — run the workflow / gates
- `poll` or `status` — drain pending outbox updates + session status

Response:

```json
{
  "reply": "...",
  "awaiting": "plan_approval",
  "task_id": "abc12345",
  "pending_updates": [],
  "channel": "teams",
  "session_id": "teams:<aad-object-id>"
}
```

Health: `GET /api/channels/copilot/health`

## Copilot Studio — create / wire “Sunny Personal AI Agent” for Teams

### What your screens mean

1. **Add tool → REST API → “No results”** is normal. There is no shared REST API yet in this environment. Click **Create the REST API you need**.
2. **Upload REST API specification** is the next required step. Copilot Studio does **not** take a raw URL alone here — it needs an **OpenAPI 3** file (JSON/YAML, max 5 MB). Until you upload one, **Next** stays disabled.
3. Your Overview **Instructions** already mention `PersonalAIAgent` — good. The tool name / `operationId` in the OpenAPI must match that.

### OpenAPI file to upload (use Swagger 2.0)

Copilot Studio / Power Platform expects **OpenAPI v2 (Swagger)**. Uploading only OpenAPI 3 often causes silent save failures (Save and Close shows nothing under REST API; Review never appears).

Upload this file:

[`docs/copilot-studio/personal-ai-agent.swagger.json`](./copilot-studio/personal-ai-agent.swagger.json)

(Same content is also in `personal-ai-agent.openapi.json` for convenience.)

**Tool name:** `SunnyPAIBackend_v4` (new every recreate)

### Backend status (verified live)

These are healthy independently of Copilot Studio:

- `GET /api/channels/copilot/health` → 200, channel enabled, API key configured
- `POST /api/channels/copilot/message` without key → 401 (auth working as designed)
- Routes present on live OpenAPI

If Studio cannot Review/Publish, the problem is the **Studio wizard / connector save**, not the Azure REST service.

### Wizard steps (8-step REST API flow)

| Step | What to do |
|------|------------|
| 1. Upload specification | Upload `personal-ai-agent.openapi.json` → **Next** |
| 2. API plugin details | **Tool name must be brand-new every time you recreate the connector.** Do **not** reuse `Sunny Personal AI Agent` or `SunnyPersonalAIAgentAPI` if those already exist. Use exactly: **`SunnyPAIBackend_v2`**. If the red banner still appears, bump again (`SunnyPAIBackend_v3`, …). Prefer **reusing** an existing tool under **Tools** instead of creating another REST API for the same host. Solution can stay auto-create. |
| 3. Authentication | **API key** → header name `X-Copilot-Api-Key` → paste the same value as App Service `COPILOT_API_KEY` |
| 4. Select Tools | Enable **PersonalAIAgent** only |
| 5–6. Configure / parameters | Map: `message` ← user utterance; `user_id` ← signed-in user id (Entra object id); `action` default `message` (use `poll`/`status` when user asks for updates); optional `conversation_id`, `display_name` |
| 7. Review | Confirm URL host is `whatsapp-ai-agent-sunny.azurewebsites.net` |
| 8. Publish | Publish the **API plugin / tool** (this is not the same as publishing the agent to Teams yet) |

Then back on the agent:

1. **Tools** tab — confirm `PersonalAIAgent` is listed.
2. Optional but recommended: Overview → **Web Search → Off** (coding gates should hit your backend, not the public web).
3. Right panel **Test**: try `status`, then `check my repos`. You should see the tool called and the backend `reply` shown.
4. Agent **Publish** (top right).
5. **Channels** → turn on / add **Microsoft Teams** → follow “availability” so it appears in Teams for you (or your org).
6. In Teams, open **Sunny Personal AI Agent** and send `check my repos`.

### Instructions (keep / paste if missing)

```text
You are Sunny Personal AI Agent, connected to Sunny's Personal AI Agent backend for coding and Azure DevOps / GitHub work.

Decision rules:
1. Always call PersonalAIAgent for repository / coding / PR / CI / deploy work — never invent plan text, PR URLs, review scores, or deploy results.
2. Pass the user's message in the message field.
3. Pass the signed-in user's id in user_id (AAD object ID / system user ID).
4. After long-running work, if the user asks what happened next, call PersonalAIAgent again with action=poll or message=status and show the reply.
5. Keep context. The backend clears the coding session on STOP or a new "check my repos".
6. Show the tool reply text as-is (pre-formatted).
7. Answer general architecture / Azure / RAG questions yourself when no coding tool call is needed.
```

### Stuck on Select Tools / cannot reach Review

Copilot Studio often **will not jump to Review** from Select Tools. You must open the tool card first:

1. On **Select Tools**, click the selected row **“Run Personal AI Agent coding / gate workflow”** (the green icon row) — do **not** only click **Next**.
2. That opens **Configure tool** → fill name `PersonalAIAgent` → **Next**.
3. **Select tool parameters** → every description filled; ensure inputs include **`user_id`** and **`message`** → **Next**.
4. Only then you get **Review** → **Publish**.

If **Next** loops back to Select Tools:

1. Click **Cancel**.
2. Re-upload the simplified OpenAPI: `docs/copilot-studio/personal-ai-agent.openapi.json`.
3. **Tool name:** `SunnyPAIBackend_v3` (never reuse old names).
4. Auth: label `Copilot API Key`, name `X-Copilot-Api-Key`, location Header.
5. On Select Tools, **click the tool row**, then continue Configure → Parameters → Review → Publish.

Prefer **Tools** tab → open an already-created connector instead of adding REST API again for the same host.

This agent cannot log into your Copilot Studio tenant; the steps above are the supported fix.

### Quick backend smoke test (optional)

```bash
curl -sS -X POST "https://whatsapp-ai-agent-sunny.azurewebsites.net/api/channels/copilot/message" \
  -H "Content-Type: application/json" \
  -H "X-Copilot-Api-Key: $COPILOT_API_KEY" \
  -d '{"user_id":"<your-aad-object-id>","message":"status","action":"status"}'
```

## WhatsApp unchanged

- Meta webhook `/webhooks/whatsapp` is untouched in behavior.
- You can use WhatsApp and Teams at the same time (separate session keys).
- STOP on one channel does not clear the other channel’s session.

## Optional later (not required for v1)

Bot Framework proactive Teams messages so CI/Final evaluation push without `status` / poll. Outbox + poll is enough for v1.
