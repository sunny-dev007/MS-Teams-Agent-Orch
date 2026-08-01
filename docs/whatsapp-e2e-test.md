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

## 5) Code change on sample app
1. Ensure `sample-apps/personal-task-api` is in a cloneable GitHub/AzDO repo
2. Through the wizard, select that repo
3. Ask: `Add DELETE /tasks/{id} endpoint and reject empty titles with 400`
4. Agent develops + reviews → asks `APPROVE <id>` / `REJECT <id>`
5. Reply `APPROVE <id>`
6. Expect PR/pipeline links + **Final evaluation** step-by-step message

## 6) Status
Send `task status` or `5`
