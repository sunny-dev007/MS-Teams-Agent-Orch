# Teams + Copilot Studio dual channel

Use **AI Enterprise Assistant** in Microsoft Teams for the same coding/repo workflow as WhatsApp, without changing WhatsApp.

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

## Copilot Studio — manual setup

1. Open **AI Enterprise Assistant** in Copilot Studio (environment CopilotStudio-Dev).
2. **Tools** → add a tool (HTTP / Custom connector / REST):
   - URL: `https://whatsapp-ai-agent-sunny.azurewebsites.net/api/channels/copilot/message`
   - Method: `POST`
   - Header: `X-Copilot-Api-Key` = your secret
   - Body parameters mapped from conversation: `user_id` (signed-in user id), `message` (user text), optional `conversation_id`, `action`
3. Name the tool e.g. `PersonalAIAgent`.
4. Append this to **Instructions** (keep your existing enterprise architecture instructions):

```text
You are also connected to Sunny's Personal AI Agent backend for coding and Azure DevOps / GitHub work.

Decision rules:
- Answer architecture, RAG, Azure, Generative AI, and general enterprise questions yourself using your knowledge.
- When the user wants to work on repositories, code changes, PRs, CI, or deploy gates — including phrases like:
  "check my repos", "Proceed", "1", "AI REVIEW", "2", "APPROVE", "REJECT", "FIX TESTS", "SKIP", "stop", "status", "help" while a coding task is active —
  you MUST call the PersonalAIAgent tool and return its `reply` field to the user verbatim.
- Do not invent plan text, PR URLs, AI review scores, or deploy results.
- After calling the tool for a long task, if the user asks what happened next, call the tool again with action=poll (or message "status") and show pending updates.
- Maintain conversation context. Only clear the coding workflow when the user says STOP or starts check my repos (the backend clears session then).
```

5. **Publish** the agent so Teams gets the update.
6. In Teams, open **AI Enterprise Assistant** and try: `check my repos`.

## WhatsApp unchanged

- Meta webhook `/webhooks/whatsapp` is untouched in behavior.
- You can use WhatsApp and Teams at the same time (separate session keys).
- STOP on one channel does not clear the other channel’s session.

## Optional later (not required for v1)

Bot Framework proactive Teams messages so CI/Final evaluation push without `status` / poll. Outbox + poll is enough for v1.
