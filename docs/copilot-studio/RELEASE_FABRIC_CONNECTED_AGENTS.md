# Release Agent Fabric — Copilot Studio connected agents (draft)

Parent orchestrator + three specialists. **Do not publish to production Teams until App Service flags are validated.**

## Parent agent (Orchestrator)

**Description for Studio:**  
Coordinates software release workflows. Delegates coding/deploy to Dev Agent, verification to QA Agent, and release documentation to Docs Agent. Uses PR id / pipeline id / release id as correlation.

**Instructions (summary):**  
- For code, PR, deploy → call Dev (existing SunnyPersonalAI connector).  
- After deploy success or when user asks to test → call QA with release/pr/pipeline ids.  
- When user asks for release notes / SharePoint → call Docs.  
- Always keep human confirmation before QA and Docs in v1.

## Connected agent: AI Dev Agent (existing)

- Custom connector → `POST /api/channels/copilot/message` (already live).  
- No change required for Phase 0–1.

## Connected agent: QA Agent (Phase 3)

- Same App Service connector with messages like `run QA for rel_…` / `run QA for PR 42`.  
- Or separate Foundry agent later.

## Connected agent: Docs Agent (Phase 1)

- Same connector: `write release notes for PR 42` / `write release notes for rel_…`.  
- Requires `ENABLE_DOCS_AGENT=true` + Graph settings.

## Safety

| Flag | Default | Meaning |
|------|---------|---------|
| `ENABLE_DOCS_AGENT` | false | Graph publish |
| `ENABLE_QA_AGENT` | false | Playwright |
| `ENABLE_RELEASE_HANDOFF` | false | Auto Teams prompt after CI |

Existing WhatsApp and Dev coding paths do not require these flags.
