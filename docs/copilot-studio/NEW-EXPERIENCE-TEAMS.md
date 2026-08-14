# Teams agent — Copilot Studio **new experience** (no REST API tile)

In the new Copilot Studio UI (`Sunny Personal Dev Agent`), **Add a tool** shows:

- Featured
- Model Context Protocol (MCP)
- Connectors
- Workflows

There is often **no REST API tile** on Featured. That is expected. Use one of the paths below.

Your Azure backend is already live:

- `POST https://whatsapp-ai-agent-sunny.azurewebsites.net/api/channels/copilot/message`
- Header: `X-Copilot-Api-Key`
- Body: `{ "user_id", "message", "action" }`

---

## Recommended: Custom Connector → Connectors tab

This replaces the old REST API wizard.

**Updated UI (2025/2026):** Custom connectors are often **hidden** under left-nav **More (⋯)** — not under Tables. Use the dedicated guide:

→ [`FIND-CUSTOM-CONNECTOR.md`](./FIND-CUSTOM-CONNECTOR.md)

### Short version (Power Automate)

1. Open [https://make.powerautomate.com](https://make.powerautomate.com) → environment **CopilotStudio-Dev**.
2. Left rail → **More (⋯)** → search **`Custom connectors`** → open it.
3. **+ New custom connector** → **Import an OpenAPI file**.
4. Upload `docs/copilot-studio/personal-ai-agent.swagger.json`, name `Sunny Personal AI Agent`.
5. Security: API key header **`X-Copilot-Api-Key`** (label = `Copilot API Key` only — do not paste the secret in the label).
6. **Create connector** → **Test** with `status` + your Entra object id + App Service API key.
7. In Copilot Studio agent → **Add a tool** → **Connectors** tab → add **PersonalAIAgent**.

---

## Alternative: Workflows (HTTP) — no connector needed

Use when Custom Connector is blocked by admin policy.

1. **Add a tool** → **Workflows** → create / add an **Agent flow**.
2. Trigger: when the agent calls this tool (or “Run a flow from Copilot”).
3. Add action **HTTP**:
   - Method: POST
   - URI: `https://whatsapp-ai-agent-sunny.azurewebsites.net/api/channels/copilot/message`
   - Headers: `Content-Type` = `application/json`, `X-Copilot-Api-Key` = your secret (use environment variable / secure input if available)
   - Body:

```json
{
  "user_id": "@{triggerBody()?['user_id']}",
  "message": "@{triggerBody()?['message']}",
  "action": "message"
}
```

4. Return the HTTP `reply` field to the agent as the flow output.
5. Add that workflow as a tool named **PersonalAIAgent**.

---

## MCP tab (optional later)

MCP is for MCP servers, not raw REST. Only use this if you later host an MCP wrapper around the same API. Not required for Teams v1.

---

## Agent instructions (paste / keep)

Your new agent already has MODE A / MODE B. Ensure it still includes:

```text
For MODE B (coding / repos / gates), always call the PersonalAIAgent tool.
Pass user_id = signed-in Entra object id, message = user text.
Show the tool reply verbatim. Never invent PR URLs, plans, or deploy results.
If the user asks for status after a long task, call PersonalAIAgent with action=status.
```

Turn **Search all websites** off for this agent if you only want backend + your knowledge.

---

## Publish to Teams

1. Agent **Publish**.
2. Channels / availability → **Microsoft Teams** → show to you / org.
3. In Teams open **Sunny Personal Dev Agent** → send `status`, then `check my repos`.

---

## Quick backend proof (already verified)

```bash
curl -sS -X POST "https://whatsapp-ai-agent-sunny.azurewebsites.net/api/channels/copilot/message" \
  -H "Content-Type: application/json" \
  -H "X-Copilot-Api-Key: $COPILOT_API_KEY" \
  -d '{"user_id":"<your-aad-oid>","message":"status","action":"status"}'
```

WhatsApp continues to use `/webhooks/whatsapp` unchanged.
