# Release Agent Fabric — Development Plan (Prod-Safe)

**Status:** Phase 0–1 scaffold implemented in code (flags default **OFF** on App Service).  
**Constraint:** Do not change WhatsApp or live Dev Agent behavior unless behind an explicit feature flag defaulting to **off**.  
**Product name (GTM):** Release Agent Fabric — Dev → QA → Docs on Microsoft 365 + Azure DevOps.

Related strategy canvas (open beside chat): leadership / sales architecture review.

---

## 1. Decision summary

| Choice | Decision |
|--------|----------|
| Topology | **Orchestrator + 3 specialists** (not 3 chat-only bots) |
| Correlation | `ReleaseEvent` with `pr_id` + `pipeline_id` (+ `release_id`) |
| Teams UX | Messages + human gates; events are system of record |
| M365 writes | Microsoft Graph (SharePoint first; OneDrive/OneNote next) |
| QA runtime | Isolated worker; later Azure AI Foundry Connected Agent |
| Studio | Parent agent + **Connected Agents** (Microsoft pattern) |
| Prod safety | `ENABLE_QA_AGENT`, `ENABLE_DOCS_AGENT`, `ENABLE_RELEASE_HANDOFF` default `false` |

---

## 2. Agents

### 2.1 AI Dev Agent (existing — protect)

- Keep current Copilot connector and App Service API.
- Optional **emit-only** hook after successful deploy / CI green: write `ReleaseEvent`, optionally enqueue Teams prompt “Start QA?”.
- Must not alter gate routing or WhatsApp paths.

### 2.2 QA Agent (new)

- Input: `ReleaseEvent` (`app_url`, `pr_id`, `pipeline_id`).
- Action: Playwright smoke (then expand to tagged suites).
- Output: HTML/JSON report URL + pass/fail on the event.
- Host: App Service job or Container Apps / Foundry agent (Phase 3–5).

### 2.3 Documentation Agent (new)

- Input: `ReleaseEvent` + QA summary (optional).
- Action: Graph create/update SharePoint page (then OneNote section / OneDrive file).
- Output: document URL posted to Teams outbox.
- Auth: dedicated Entra app; least-privilege Graph scopes.

### 2.4 Orchestrator (thin)

- Copilot Studio parent **or** planner intents in existing LangGraph.
- Routes: “run QA for this release”, “publish release notes”.
- Never owns Playwright or Graph implementation details.

---

## 3. `ReleaseEvent` contract (v1)

```json
{
  "release_id": "rel_…",
  "pr_id": "42",
  "pipeline_id": "15",
  "build_id": "200",
  "commit_sha": "…",
  "env": "prod|staging",
  "app_url": "https://…",
  "status": "deployed|qa_running|qa_passed|qa_failed|docs_published",
  "artifacts": [{ "type": "playwright_report", "url": "…" }],
  "requested_by": "teams:user@…",
  "channel": "teams"
}
```

Store in SQLite (same App Service DB pattern) or Table Storage later. Idempotent upserts by `release_id` / (`pipeline_id`,`build_id`).

---

## 4. Phased work

### Phase 0 — Contracts & flags (3–5 days) — **done in repo**

- [x] Add settings: `ENABLE_DOCS_AGENT`, `ENABLE_QA_AGENT`, `ENABLE_RELEASE_HANDOFF` (default false).
- [x] Add `ReleaseEvent` model + repository (create tables via existing `ensure_db_schema`).
- [x] Document Entra app registration steps for Graph (`docs/RELEASE_AGENT_FABRIC_GRAPH_SETUP.md`).
- [x] Studio storyboard: parent + 3 agent descriptions (`docs/copilot-studio/RELEASE_FABRIC_CONNECTED_AGENTS.md`).
- [x] Unit tests for event upsert / flag-off no-ops.

### Phase 1 — Documentation Agent (1–2 weeks) — **code ready; Graph credentials manual**

- [x] `services/ms_graph.py` + Docs Agent SharePoint publisher (flagged).
- [x] Specialist + planner intent: “create release notes for PR/pipeline …”.
- [x] Teams/Copilot path only; WhatsApp unchanged when flags off.
- [ ] Manual demo: user asks Docs Agent → SharePoint page (needs Entra + enable flag).
- [x] Soft-fail + health probe (`fabric` block on Copilot health).

### Phase 2 — Dev → handoff (3–5 days)

- [ ] After deploy success / CI final OK, if `ENABLE_RELEASE_HANDOFF`: upsert event + Teams “Start QA?” gate.
- [ ] No automatic QA until human APPROVE (enterprise story).

### Phase 3 — QA Agent (2–3 weeks)

- [ ] Playwright worker + artifact upload (blob or App Service wwwroot/reports).
- [ ] Intent / connected tool: “run QA for release_id / pipeline_id”.
- [ ] Update event + Teams summary; on fail do **not** auto-call Docs.

### Phase 4 — Copilot Studio Connected Agents (1–2 weeks)

- [ ] Parent orchestrator agent in Studio.
- [ ] Connected: Dev (existing connector), QA connector, Docs connector.
- [ ] Leadership 20‑min demo script end-to-end.

### Phase 5 — Foundry + governance (2+ weeks)

- [ ] Move long QA runs to Azure AI Foundry agent (Connected from Studio).
- [ ] Entra Agent ID / Purview narrative for US/UK security reviews.
- [ ] Optional OneDrive + OneNote writers behind same Docs flag.

---

## 5. Non-goals (protect prod)

- Do not rewrite the LangGraph Dev coding path for multi-agent.
- Do not require Bot Framework proactive messaging for v1 (keep outbox + poll).
- Do not grant Docs Agent AzDO write or QA Agent SharePoint write.
- Do not auto-publish customer-facing docs without APPROVE gate in v1.

---

## 6. Demo metrics for sales

| Metric | How shown |
|--------|-----------|
| Time deploy → QA report | Event timestamps |
| Time QA → SharePoint URL | Event timestamps |
| Human gates used | Gate audit / session |
| Correlation integrity | Same `pr_id`/`pipeline_id` on PR, report, and doc |

---

## 7. Immediate next engineering tasks

1. Implement Phase 0 flags + `ReleaseEvent` storage (no Graph calls yet).  
2. Scaffold Docs Agent module stubs that return “disabled” when flag off.  
3. Add Graph configuration checklist under `docs/copilot-studio/` when Phase 1 starts.

**Do not enable any new flag on App Service until Phase 1 is tested on a non-prod slot or allowlisted user.**
