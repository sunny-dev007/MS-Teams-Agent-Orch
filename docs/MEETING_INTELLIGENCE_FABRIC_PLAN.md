# Meeting Intelligence Fabric — Implementation Plan

**Status:** Planning only (no code in this document)  
**Audience:** Implementation chatbot / developer with full repo context  
**Author context:** Deep review of `src/agent/` as of Aug 2026  
**Related sample:** `samples/meeting-transcript/Travel Planner AI Agent-20250822_100000-Meeting Transcript.vtt`

---

## 1. Executive summary

You asked for an agent flow:

1. **List recent meetings** → quick summary with **client name** and **agenda**
2. **Pick one or many** meeting transcripts
3. **Understand combined context** across selections
4. **Generate a plan** → publish **Markdown and/or DOCX** to SharePoint with good formatting
5. **Email** the plan to yourself or teammates

**Critical finding:** The codebase today has **no meeting-transcript agent**. What the UI calls **“Meeting Agent”** (`core/persona.py`) is only **Google Calendar scheduling** (`agents/meeting.py` → `specialists/calendar_agent.py`, intent `schedule_meeting`). Transcript handling does not exist; `.vtt` is not supported in `doc_extract.py`.

**Recommendation:** Add a new **Meeting Intelligence Fabric** (feature-flagged, default `false`) modeled after **Document Knowledge Fabric** (`list → select → process → publish`), not an extension of the calendar agent.

---

## 2. Current state (what exists)

### 2.1 Calendar “Meeting Agent” (scheduling only)

| Piece | Path | Role |
|-------|------|------|
| Business logic | `src/agent/agents/meeting.py` | Multi-turn slot fill; `create_event()` via Google Calendar |
| Graph node | `src/agent/specialists/calendar_agent.py` | Wraps `schedule_meeting` |
| Planner | `planner/agent.py` → `schedule_meeting` | Regex + session `meeting_*` awaiting |
| State fields | `state.py`: `meeting_title`, `meeting_when`, `meeting_attendees`, `meeting_link` | Scheduling only |

**Does not:** read Teams transcripts, list past meetings, or generate plans.

### 2.2 Closest reusable patterns

| Pattern | Reference | Reuse for meetings |
|---------|-----------|-------------------|
| List + paginate + session catalog | `doc_library_agent.py` | `meeting_catalog` in session, `awaiting=meeting_pick` |
| Multi-number pick `1,3` | `doc_ingest_agent.py`, `ingest_status.py` | `select meetings 1,3` |
| Graph list/download | `services/graph_docs.py` | List `.vtt` under `Recordings/` / `meeting transcript/` |
| Text extract | `services/doc_extract.py` | **Extend** with WebVTT parser (not implemented today) |
| Ingest + RAG | `services/doc_knowledge.py`, `models/knowledge_doc.py` | Optional: index transcripts; plan agent can also use raw text |
| SharePoint upload (HTML) | `agents/docs_agent.py` → `_upload_html_to_drive` | Pattern for uploading plan artifacts |
| SharePoint upload (binary) | `services/doc_upload.py` | Upload `.md` / `.docx` bytes |
| Teams attachments | `api/copilot.py` + `doc_upload` | Not needed for auto-saved transcripts if sourced from SharePoint |
| Email send | `services/gmail.py` `send_email` | WhatsApp path |
| Outlook send | `services/outlook_mail.py` (read only today) | **New:** Graph `sendMail` for Teams users |
| Planner priority / gate bypass | `planner/agent.py`, `api/channel_gates.py` | New regex block before coding gates |
| Workspace handoff | `services/workspace_handoff.py` | New lane `WS_MEETING` |

### 2.3 Gaps (must build)

| Gap | Impact |
|-----|--------|
| No `.vtt` in `doc_extract.py` | Cannot ingest Teams transcripts via Doc Knowledge today |
| No meeting metadata store | No cached client/agenda for list view |
| No “list meetings” intent or agent | User phrase unmatched |
| No multi-meeting selection session | No `meeting_catalog` |
| No plan generator | No LLM pipeline for cross-meeting synthesis |
| No DOCX writer | Docs agent uploads HTML only; no `python-docx` in `pyproject.toml` |
| Outlook cannot send mail | Only inbox read in `outlook_mail.py` |
| Persona naming collision | “Meeting Agent” = calendar confuses users |

---

## 3. Requirements traceability

| # | User requirement | Proposed capability | Primary agent/service |
|---|------------------|---------------------|------------------------|
| R1 | List recent meetings | Scan SharePoint/OneDrive `Recordings` (+ optional Graph API); sort by `lastModified` desc | `meeting_library_agent` + `meeting_transcripts.py` |
| R2 | Quick summary: client + agenda | LLM metadata extraction on VTT (cached in SQLite); show in table | `meeting_summarizer` (service) |
| R3 | Pick one or many meetings | Session `meeting_catalog` + `meeting_selected` picks | `meeting_select` (planner + session helpers) |
| R4 | Understand multi-meeting context | Load full parsed text for all picks; optional chunk+RAG for long corpora | `meeting_context.py` |
| R5 | “Make a plan for me” | LLM plan with structured sections (exec summary, scope, phases, risks, actions) | `meeting_plan_agent` |
| R6 | Save MD/DOCX to SharePoint | Upload to `{site}/MeetingPlans/` | `meeting_publish.py` |
| R7 | Email plan to team/users | Gmail (WhatsApp) or Graph `sendMail` (Teams); link + optional attachment | `meeting_email_agent` |

---

## 4. Recommended architecture

### 4.1 New fabric: Meeting Intelligence (additive)

```text
ENABLE_MEETING_INTELLIGENCE=false   # master flag (default off)

Teams / WhatsApp message
  → channel_gates (bypass coding gates for meeting phrases)
  → planner (regex priority over coding session)
  → LangGraph specialist
  → notify_result
```

**Rename in persona (follow-up UX task):**

- **Calendar Agent** — `schedule a meeting …` (existing)
- **Meeting Intelligence** — `list my meetings`, `select meetings 1,3`, `create plan from meetings`

### 4.2 Specialist agents (4 nodes)

| Specialist | Intent | Responsibility |
|------------|--------|----------------|
| **Meeting Library** | `list_meetings` | Discover `.vtt` files; show numbered list with client, agenda, date, duration |
| **Meeting Select** | `select_meetings` | Parse `1,3` / `meetings 1 and 2`; persist `meeting_selected` in session |
| **Meeting Plan** | `create_meeting_plan` | Synthesize plan from selected transcripts + user instructions |
| **Meeting Email** | `email_meeting_plan` | Send last published plan (or prompt for recipients) |

**Alternative (fewer nodes):** Merge Select into Library (same as doc ingest picking). Merge Email into Plan (if message contains “email to …”). Start with **3 agents** (Library, Plan, Email) if you want less graph surface.

### 4.3 Services layer (new module)

```text
src/agent/services/
  meeting_transcripts.py    # list, download, parse VTT, extract metadata
  meeting_context.py        # merge N transcripts for LLM context window
  meeting_plan_builder.py   # LLM prompts + structured plan JSON
  meeting_publish.py        # SharePoint upload .md / .docx
  meeting_mail.py           # Gmail + Graph sendMail wrapper
```

### 4.4 Data sources (two phases)

**Phase 1 — SharePoint / OneDrive file discovery (recommended MVP)**

- Teams saves native **`text/vtt`** under:
  - `{site}/Shared Documents/Recordings/`
  - `{site}/Shared Documents/meeting transcript/` (custom folder)
  - Organizer OneDrive: `Recordings/` (if `MS_GRAPH_ONEDRIVE_USER_ID` set)
- Filter: `extension == .vtt` AND name contains `Transcript` or `Meeting Transcript`
- **Do not** convert VTT before storage; parse at read time

**Phase 2 — Microsoft Graph Online Meetings API (optional)**

- `GET /users/{id}/onlineMeetings` + `.../transcripts`
- `GET .../transcripts/{id}/content` with `Accept: text/vtt`
- Requires **OnlineMeetings.Read** / **OnlineMeetingTranscript.Read.All** (application or delegated)
- Correlates calendar subject, attendees, start time (richer than filename parsing)

Start Phase 1 to align with your sample file and existing `graph_docs` investment.

---

## 5. End-to-end user flows

### Flow A — List recent meetings

```text
User: list my recent meetings
  → planner: list_meetings
  → meeting_library_agent
       → graph_docs.list_meeting_transcripts()  [NEW]
       → for each new/changed file: parse VTT header + LLM extract client_name, agenda_one_liner
       → upsert meeting_transcripts table (cache)
       → format Teams table:
            | # | Date | Client | Agenda | Duration | File |
  → save_session(awaiting=meeting_pick, data={meeting_catalog, meeting_page})
```

### Flow B — Select multiple meetings

```text
User: select meetings 1, 3 and 5
  → planner: select_meetings (or bare "1,3,5" when awaiting=meeting_pick)
  → validate picks against meeting_catalog
  → download + parse full VTT for each (if not cached)
  → save_session(data={meeting_selected: [rows...]})
  → reply: "Selected 3 meetings: Global Voyager (Aug 22), … Say create plan …"
```

### Flow C — Create plan

```text
User: create a plan for the travel agent project / make a plan for me
  → planner: create_meeting_plan
  → meeting_plan_agent
       → meeting_context.build(selected meetings + optional user focus)
       → LLM (planning deployment) → structured plan JSON
       → meeting_publish.publish_plan(md + docx) → SharePoint URLs
       → save_session(data={last_plan_url, last_plan_docx_url})
  → reply with summary + links
```

### Flow D — Email plan

```text
User: email the plan to alice@co.com, bob@co.com
  → planner: email_meeting_plan
  → meeting_email_agent
       → load last_plan_* from session
       → Teams: Graph sendMail (HTML body + link to SharePoint)
       → WhatsApp: Gmail send_email
  → confirmation
```

---

## 6. Session state design

Mirror `doc_catalog` pattern in `core/session.py` `data_json`:

```python
{
  "meeting_catalog": [  # list view rows
    {
      "pick": 1,
      "meeting_id": "uuid-or-drive-item-id",
      "title": "Travel Planner AI Agent-20250822_100000-Meeting Transcript",
      "meeting_date": "2026-08-22T10:00:00+05:30",
      "duration_minutes": 30,
      "client_name": "Global Voyager Inc.",
      "agenda_summary": "Discovery for worldwide travel planner AI agent",
      "web_url": "https://...sharepoint.../Transcript.vtt",
      "drive_id": "...",
      "item_id": "...",
      "participants": ["Sunny Kushwaha", "Priya Mehta", ...],
      "vtt_hash": "sha256...",
      "metadata_cached_at": "..."
    }
  ],
  "meeting_page": 0,
  "meeting_query": "",
  "meeting_selected": [ /* subset of catalog rows */ ],
  "last_plan": {
    "title": "...",
    "md_url": "...",
    "docx_url": "...",
    "created_at": "..."
  }
}
```

**Awaiting values:**

| `awaiting` | Meaning |
|------------|---------|
| `meeting_pick` | User should pick numbers or ask for plan |
| `meeting_email_to` | Waiting for recipient list |

Do **not** reuse `meeting_title` / `meeting_when` (calendar fields).

---

## 7. Data model (SQLite)

### Option A — Dedicated table (recommended)

New file: `src/agent/models/meeting_transcript.py`

```text
meeting_transcripts
  id                 VARCHAR PK
  external_id        VARCHAR  # Graph drive item id
  source_type        VARCHAR  # sharepoint | onedrive | graph_api
  title              VARCHAR
  web_url            VARCHAR
  meeting_date       DATETIME nullable
  duration_seconds   INT nullable
  client_name        VARCHAR  # LLM-extracted
  agenda_summary     VARCHAR  # LLM-extracted, 1-2 lines
  participants_json  TEXT
  vtt_hash           VARCHAR  # invalidate cache on file change
  parsed_text        TEXT nullable  # full plain text (optional, can be large)
  metadata_json      TEXT
  owner_session      VARCHAR nullable
  status             VARCHAR  # listed | parsed | failed
  created_at / updated_at
```

### Option B — Reuse `knowledge_documents`

Set `doc_mode = "transcript"` and store client/agenda in `metadata_json`.

**Downside:** Doc Library UI mixes all files; meeting list needs filtered view and different columns. **Prefer Option A** with optional ingest into knowledge base later (`doc_mode=transcript`).

### Plan artifacts (optional table)

```text
meeting_plans
  id, title, source_meeting_ids_json, md_url, docx_url, summary, created_at, owner_session
```

---

## 8. VTT processing (Teams native format)

### 8.1 Input format

Teams / Graph returns **WebVTT** with speaker tags:

```vtt
00:00:05.000 --> 00:00:22.000
<v Priya Mehta>Good morning, everyone...
```

Reference sample: `samples/meeting-transcript/Travel Planner AI Agent-20250822_100000-Meeting Transcript.vtt`

### 8.2 New parser

Add to `doc_extract.py` OR new `services/vtt_parse.py`:

| Function | Output |
|----------|--------|
| `parse_vtt(bytes \| str)` | `list[{start, end, speaker, text}]` |
| `vtt_to_plain_text(cues)` | Speaker-labeled prose for LLM |
| `vtt_metadata_heuristic(filename)` | Date from `20250822_100000` in Teams filename |

Register in `detect_format()` → `"vtt"` and wire `graph_docs.fetch_document_text()` to use VTT parser for `.vtt` files.

### 8.3 Metadata extraction (list view)

**Lightweight path (list 20 meetings):**

1. Parse VTT → plain text
2. If `client_name` / `agenda_summary` missing in DB or `vtt_hash` changed:
3. LLM call with **first ~3,000 tokens** + filename + `meeting_plan_metadata.txt` prompt
4. JSON: `{client_name, agenda_summary, participants[], meeting_date_guess}`

Cache results — do not re-LLM on every list unless stale.

---

## 9. Plan generation

### 9.1 LLM contract

New prompts:

- `prompts/meeting_metadata_extract.txt`
- `prompts/meeting_plan_system.txt`
- `prompts/meeting_plan_user.txt` (template: `{meetings_block}`, `{user_focus}`, `{output_schema}`)

**Output schema (JSON intermediate):**

```json
{
  "title": "Implementation Plan — Global Voyager Travel Agent",
  "executive_summary": "...",
  "background": "...",
  "requirements": [],
  "architecture_overview": "...",
  "phases": [{"name": "", "duration_weeks": 0, "deliverables": []}],
  "risks": [],
  "open_questions": [],
  "action_items": [{"owner": "", "task": "", "due": ""}],
  "assumptions": []
}
```

Use `services/llm.py` `invoke_llm` with **planning deployment** (`azure_openai_planning_deployment`).

### 9.2 Context window strategy

| Selected meetings | Strategy |
|-------------------|----------|
| 1 meeting, &lt; 30 min | Full parsed text in prompt |
| 2–3 meetings | Per-meeting LLM summary → merge → final plan |
| 4+ or very long | Per-meeting summary + RAG over ingested chunks (reuse `doc_knowledge` optional path) |

Store `meeting_selected` transcript text in session only for small sets; spill to DB `parsed_text` for large.

### 9.3 SharePoint publish

New settings:

```env
MEETING_PLANS_FOLDER=MeetingPlans
MEETING_TRANSCRIPT_FOLDERS=Recordings,meeting transcript
MEETING_LIST_MAX=30
```

**Publish steps (`meeting_publish.py`):**

1. Render **Markdown** from plan JSON (templates in code, Teams-friendly headings)
2. Render **DOCX**:
   - **Option 1:** Add `python-docx` dependency (simplest, good formatting)
   - **Option 2:** Minimal OOXML zip (like reverse of `doc_extract`) — no new dep
   - **Option 3:** Upload HTML (reuse `docs_agent` style) + separate `.md` — user converts DOCX manually (**not ideal** for your requirement)
3. Upload via Graph `PUT /sites/{id}/drive/root:/{folder}/{filename}:/content`
4. Return `webUrl` for each file

**Filename pattern:**

```text
MeetingPlans/2026-08-22-global-voyager-travel-agent-plan.md
MeetingPlans/2026-08-22-global-voyager-travel-agent-plan.docx
```

---

## 10. Email delivery

| Channel | Mechanism | Files to touch |
|---------|-----------|----------------|
| WhatsApp | `gmail.send_email(to, subject, body_html)` | `meeting_mail.py` |
| Teams | Graph `POST /users/{id}/sendMail` | Extend `ms_graph.py` or `outlook_mail.py` |

**Email body:**

- Short executive summary (from plan)
- Links to SharePoint MD + DOCX
- Optional: attach DOCX if size &lt; 4 MB (Graph attachment API)

**Permissions:**

- Gmail: existing OAuth `gmail.send`
- Teams: **Mail.Send** (application with access policy, or delegated — product decision)

**Planner phrases:**

- `email plan to alice@co.com, bob@co.com`
- `send the plan to my team`

If no recipients in message → `awaiting=meeting_email_to`.

---

## 11. Planner & routing changes

### 11.1 New regex (priority: after fabric docs, before coding session)

Add to `planner/agent.py`:

| Regex / pattern | Intent |
|-----------------|--------|
| `list (my )?(recent )?meetings`, `show meeting transcripts` | `list_meetings` |
| `select meetings? 1,3`, bare `1,3,5` when `awaiting=meeting_pick` | `select_meetings` |
| `create (a )?plan`, `make a plan`, `implementation plan from meetings` | `create_meeting_plan` |
| `email (the )?plan`, `send plan to` | `email_meeting_plan` |

### 11.2 `channel_gates.py` bypass

Add meeting fabric phrases to the fabric bypass regex (same as doc knowledge) so `PROCEED` / coding gates do not intercept `1,3` picks.

**Important:** Distinguish `meeting_pick` from `doc_pick` via `session.awaiting` — only one catalog active at a time. On `list_meetings`, call `workspace_handoff` → `WS_MEETING` and clear `doc_pick` awaiting.

### 11.3 `graph.py` wiring

```python
# New nodes (when ENABLE_MEETING_INTELLIGENCE)
graph.add_node("meeting_library_agent", run_meeting_library)
graph.add_node("meeting_plan_agent", run_meeting_plan)
graph.add_node("meeting_email_agent", run_meeting_email)

# _route_after_plan additions
if intent == "list_meetings": return "meeting_library_agent"
if intent == "select_meetings": return "meeting_library_agent"  # or dedicated select handler
if intent == "create_meeting_plan": return "meeting_plan_agent"
if intent == "email_meeting_plan": return "meeting_email_agent"

graph.add_edge("meeting_library_agent", "notify_result")
graph.add_edge("meeting_plan_agent", "notify_result")
graph.add_edge("meeting_email_agent", "notify_result")
```

Selection can be handled **inside** `meeting_library_agent` when `awaiting=meeting_pick` and message matches number pick (copy `doc_ingest_agent` logic).

### 11.4 `state.py` additions

```python
# Meeting Intelligence Fabric
meeting_selected: list
meeting_plan_url: str
meeting_plan_docx_url: str
meeting_plan_title: str
meeting_client_name: str
```

---

## 12. Configuration (`config.py`)

```python
enable_meeting_intelligence: bool = False
meeting_transcript_folders: str = "Recordings,meeting transcript"
meeting_plans_folder: str = "MeetingPlans"
meeting_list_max: int = 30
meeting_list_page_size: int = 10
meeting_metadata_cache_hours: int = 168
meeting_plan_default_format: str = "md,docx"  # csv
enable_meeting_plan_email: bool = True
```

Register in `.env.example`, Copilot health (`/api/channels/copilot/health` → `meeting_intelligence_enabled`), and `persona.py` help catalog.

---

## 13. Microsoft Graph permissions

| Permission | Purpose | Phase |
|------------|---------|-------|
| `Sites.Read.All` or `Sites.Selected` | List/download `.vtt` | 1 |
| `Sites.ReadWrite.All` or write on site | Upload plans | 1 |
| `Files.Read.All` | Organizer OneDrive Recordings | 1 |
| `OnlineMeetings.Read` / `OnlineMeetingTranscript.Read.All` | Graph transcript API | 2 |
| `Mail.Send` | Email plan from Teams identity | 1 (if Teams email required) |
| `User.Read.All` | Resolve organizer / attendees | 2 |

Reuse existing Entra app from Doc Knowledge / Docs Agent where possible.

---

## 14. Implementation phases

### Phase 1 — MVP (4–6 weeks)

- [ ] Feature flag + disabled stub agents
- [ ] VTT parser + `fetch_document_text` for `.vtt`
- [ ] `list_meeting_transcripts()` SharePoint folder scan
- [ ] `meeting_transcripts` table + metadata LLM cache
- [ ] Meeting Library agent (list + pick multi)
- [ ] Meeting Plan agent (MD + DOCX publish to SharePoint)
- [ ] Gmail email on WhatsApp path
- [ ] Planner regex + graph nodes + gate bypass
- [ ] Tests: VTT parse, list filter, pick validation, plan JSON schema, disabled flag
- [ ] Docs: `docs/MEETING_INTELLIGENCE_FABRIC.md`
- [ ] Persona rename: Calendar vs Meeting Intelligence

### Phase 2 — Teams email + Graph transcripts

- [ ] Graph `sendMail` for Teams channel
- [ ] Online Meetings transcript API as alternate source
- [ ] Auto-sync webhook / scheduled job for new transcripts (optional)

### Phase 3 — Optional RAG

- [ ] Ingest selected meetings into Qdrant (`doc_mode=transcript`)
- [ ] `ask meetings what did client say about budget?` before plan

---

## 15. File-by-file implementation checklist

| Action | Path |
|--------|------|
| **CREATE** | `src/agent/agents/meeting_library_agent.py` |
| **CREATE** | `src/agent/agents/meeting_plan_agent.py` |
| **CREATE** | `src/agent/agents/meeting_email_agent.py` |
| **CREATE** | `src/agent/specialists/meeting_library_agent.py` |
| **CREATE** | `src/agent/specialists/meeting_plan_agent.py` |
| **CREATE** | `src/agent/specialists/meeting_email_agent.py` |
| **CREATE** | `src/agent/services/meeting_transcripts.py` |
| **CREATE** | `src/agent/services/meeting_context.py` |
| **CREATE** | `src/agent/services/meeting_plan_builder.py` |
| **CREATE** | `src/agent/services/meeting_publish.py` |
| **CREATE** | `src/agent/services/meeting_mail.py` |
| **CREATE** | `src/agent/models/meeting_transcript.py` |
| **CREATE** | `src/agent/prompts/meeting_metadata_extract.txt` |
| **CREATE** | `src/agent/prompts/meeting_plan_system.txt` |
| **CREATE** | `src/agent/prompts/meeting_plan_user.txt` |
| **CREATE** | `tests/test_services/test_vtt_parse.py` |
| **CREATE** | `tests/test_services/test_meeting_transcripts.py` |
| **CREATE** | `tests/test_agents/test_meeting_plan.py` |
| **MODIFY** | `src/agent/config.py` — new settings |
| **MODIFY** | `src/agent/agents/state.py` — meeting plan fields |
| **MODIFY** | `src/agent/agents/graph.py` — nodes + routes |
| **MODIFY** | `src/agent/planner/agent.py` — intents + session pick |
| **MODIFY** | `src/agent/api/channel_gates.py` — bypass phrases |
| **MODIFY** | `src/agent/services/graph_docs.py` — `list_meeting_transcripts`, VTT fetch |
| **MODIFY** | `src/agent/services/doc_extract.py` — VTT format OR import vtt_parse |
| **MODIFY** | `src/agent/models/db.py` — register new models |
| **MODIFY** | `src/agent/core/persona.py` — help rows + rename Calendar |
| **MODIFY** | `src/agent/api/copilot.py` — health fabric flags |
| **MODIFY** | `.env.example` |
| **MODIFY** | `pyproject.toml` — optional `python-docx` |
| **DO NOT MODIFY** | `agents/meeting.py` (calendar) except comments clarifying scope |

---

## 16. Test plan

| Test | Assert |
|------|--------|
| `test_vtt_parse_sample_file` | Parses sample VTT; 113 cues; speakers preserved |
| `test_list_meetings_filters_vtt_only` | Ignores `.mp4`, `.docx` |
| `test_metadata_extract_mock_llm` | client_name + agenda from fixture |
| `test_select_meetings_invalid_pick` | Friendly error on out-of-range |
| `test_plan_agent_disabled_flag` | `skipped` when flag false |
| `test_publish_upload_mock_graph` | PUT called with correct folder |
| `test_planner_list_meetings_beats_repo_wizard` | Intent priority |
| `test_channel_gates_meeting_phrase_bypass` | Does not route to coding |
| `test_email_requires_recipients` | Sets `awaiting=meeting_email_to` |

Use sample: `samples/meeting-transcript/Travel Planner AI Agent-20250822_100000-Meeting Transcript.vtt`

---

## 17. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Transcripts embedded in `.mp4` not visible as `.vtt` file | Document limitation; Phase 2 Graph API; manual upload to `meeting transcript/` |
| LLM invents client name | Prompt: extract only from transcript; show confidence; allow “Unknown client” |
| Large multi-meeting context exceeds token limit | Per-meeting summarization chain |
| Persona “Meeting Agent” confusion | Rename to Calendar Agent in help |
| `doc_pick` vs `meeting_pick` collision | Workspace handoff clears other catalog awaiting |
| App Service disk / memory for large VTT | Stream download; cap `parsed_text` size; store hash only |
| DOCX dependency | Pin `python-docx` in pyproject; fallback MD-only if import fails |

---

## 18. Non-goals (MVP)

- Live meeting join / real-time transcription
- Replacing Teams Copilot meeting recap
- Automatic plan email without user confirmation
- Editing calendar events from transcript agent
- Video (.mp4) processing

---

## 19. Example conversation (acceptance criteria)

```text
User: list my recent meetings

Agent:
| # | Date | Client | Agenda | Duration |
| 1 | 22 Aug 2026 | Global Voyager Inc. | Travel planner AI agent discovery | 30m |
...

User: select meetings 1

Agent: Selected 1 meeting: Global Voyager — Travel planner discovery.
       Say *create plan for travel agent MVP* or *make implementation plan*.

User: make a plan for me — focus on MVP phases and integrations

Agent: Plan published:
       MD: https://tenant.sharepoint.com/.../MeetingPlans/2026-08-22-global-voyager-plan.md
       DOCX: https://tenant.sharepoint.com/.../MeetingPlans/2026-08-22-global-voyager-plan.docx
       [Executive summary in chat...]

User: email the plan to priya@globalvoyager.com, marcus@globalvoyager.com

Agent: Sent plan links to 2 recipients.
```

---

## 20. ChatGPT / implementation bot prompt (copy-paste)

```text
Implement the Meeting Intelligence Fabric per docs/MEETING_INTELLIGENCE_FABRIC_PLAN.md.

Constraints:
- ENABLE_MEETING_INTELLIGENCE=false by default
- Do not change calendar scheduling (agents/meeting.py)
- Reuse doc_library session catalog patterns
- Parse native Teams WebVTT only; sample in samples/meeting-transcript/
- Publish MD + DOCX to SharePoint MeetingPlans folder
- Add tests listed in section 16
- Follow existing two-layer agent pattern (agents/ + specialists/)
```

---

## 21. Related documents

- `samples/meeting-transcript/README.md` — Teams VTT format & SharePoint paths
- `docs/DOCUMENT_KNOWLEDGE_FABRIC.md` — pattern reference
- `docs/RELEASE_AGENT_FABRIC_GRAPH_SETUP.md` — Graph app setup
- `docs/chatgpt-context/COMPLETE-CONTEXT.md` — full platform context

---

*End of implementation plan.*
