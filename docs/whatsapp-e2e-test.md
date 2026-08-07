# WhatsApp E2E test script — Sunny's Personal AI Agent

Prerequisites: agent deployed, long-lived WhatsApp token set, Gmail OAuth working.
For meetings: re-run `python scripts/setup_gmail_oauth.py --credentials ...` (Calendar scope) and update `GOOGLE_REFRESH_TOKEN` in Azure.

## 1) Latency / persona
1. Send `hello`
2. Expect instant ack: `Got it, Sunny — working on it…`
3. Then greeting from Sunny's Personal AI Agent
4. Send `help` → menu

## 2) Emails
1. Send `check my emails` or `1`
2. Expect a formatted digest (From / Subject / Summary / Suggested action)
3. Final evaluation steps after the digest

## 3) Meetings
1. Send `schedule a meeting tomorrow 4pm with kushwaha.sunny2602@gmail.com titled Agent sync`
2. Or multi-turn: `schedule a meeting` → answer title → when → attendees
3. Expect calendar confirmation (or Gmail invite fallback if Calendar scope missing)

## 4) Repos wizard
1. Send `check my repos`
2. Choose `1` GitHub or `2` Azure DevOps
3. Pick project (AzDO) then repo by number
4. Send coding instruction

## 5) Code change on sample app (phone browser path)

Open on phone browser first:
- App: https://personal-task-api-sunny.azurewebsites.net/
- Docs: https://personal-task-api-sunny.azurewebsites.net/docs
- Health: https://personal-task-api-sunny.azurewebsites.net/health

Sample repo: https://github.com/sunny-dev007/personal-task-api

WhatsApp steps (do this from phone only):
1. `hello`
2. `help`
3. `check my emails` (optional)
4. `check my repos`
5. Reply `1` (GitHub)
6. Pick **personal-task-api** by number
7. `Add DELETE /tasks/{id} endpoint and reject empty titles with HTTP 400`
8. When asked, reply `APPROVE <task_id>` (exact task id from the bot)
9. Wait for evaluation: PR + merged + **Live app** URL
10. Refresh phone browser `/` and `/docs` — DELETE should appear; empty title POST should return 400

Azure DevOps path (Sunny Portal on this agent app):
1. Open phone browser: https://whatsapp-ai-agent-sunny.azurewebsites.net/portal
2. WhatsApp: `check my repos` → `2` → `Project-NIT` → `web.Whatsapp-AI-Agent`
3. Ask a small UI change, e.g. `Add a feature bullet "Dark mode toggle" on the portal page and bump portal version to v0.2.0`
4. Review the WhatsApp code preview
5. Reply `Approve` / `final approval`
6. Expect merge to main + live portal URL
7. Refresh `/portal` on phone — no need to open Azure DevOps

## 6) Status
Send `task status` or `5`

## Updated Portal Screenshots

![Dashboard](screenshots/dashboard.png)
![Tasks](screenshots/tasks.png)
![Quick Actions](screenshots/quick-actions.png)
