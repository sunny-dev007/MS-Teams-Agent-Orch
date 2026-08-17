# Document Knowledge Fabric

Additive feature for Teams (and WhatsApp): **list → select → ingest/vectorize → RAG QnA → insights**.

**Default off.** Existing Dev coding, WhatsApp, Docs Agent, and QA Agent stay unchanged until you set `ENABLE_DOC_KNOWLEDGE=true`.

## Architecture (four specialists)

| Agent | Intent | Teams phrases |
|---|---|---|
| **Doc Library** | `list_docs` | `list my documents`, `list sharepoint docs`, `list ingested documents` |
| **Doc Ingest** | `ingest_docs` | `ingest 1,3`, `ingest all`, or bare `1, 3` after a list |
| **Doc RAG** | `ask_docs` | `ask docs what is our release process?` |
| **Doc Insights** | `summarize_docs` | `summarize docs risks`, `doc insights` |

```
Teams message
    → channel_gates (bypass coding gates)
    → planner keywords (beat repo/coding session)
    → LangGraph specialist → notify_result
         Library lists Graph files + stores catalog in session
         Ingest downloads text, chunks, embeds, stores in SQLite
         RAG embeds question, cosine top-k, LLM answer + citations
         Insights synthesizes themes/risks/actions from corpus
```

## Safety

- `ENABLE_DOC_KNOWLEDGE=false` by default (App Service / CI must not auto-enable).
- Agents registered always; flag off → `status: skipped` message (no Graph/LLM side effects).
- Routing never enters `coding_architect` / `repo_wizard` for knowledge intents.
- Binary Office/PDF: metadata-only stub in v1 (title/mode searchable). Prefer `.md` / `.txt` / `.html` for full-text RAG.

## App Service settings

```bash
az webapp config appsettings set -g ai-agent-rg -n whatsapp-ai-agent-sunny --settings \
  ENABLE_DOC_KNOWLEDGE=false \
  AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small \
  DOC_KNOWLEDGE_SOURCES=sharepoint,onedrive,onenote \
  MS_GRAPH_ONEDRIVE_USER_ID=  # optional UPN/OID for OneDrive
```

Reuse existing Graph app credentials (`MS_GRAPH_*`) from Docs Agent setup.

## Graph permissions (application)

Beyond Docs write scopes, listing/reading needs:

| Source | Typical app permission |
|---|---|
| SharePoint site drive | `Sites.Read.All` or `Sites.Selected` (+ site grant) |
| OneDrive (user) | `Files.Read.All` + set `MS_GRAPH_ONEDRIVE_USER_ID` |
| OneNote pages | `Notes.Read.All` (or Sites-scoped Notes where available) |

Also ensure an Azure OpenAI **embedding** deployment exists (`text-embedding-3-small` or similar).

## Verify

```bash
curl -sS https://whatsapp-ai-agent-sunny.azurewebsites.net/api/channels/copilot/health | jq .fabric
# expect: doc_knowledge_enabled, doc_knowledge_ready
```

## Teams E2E

1. `list my documents`
2. `ingest 1,2` (or `ingest all`)
3. `ask docs <question>`
4. `summarize docs <focus>`
5. `list ingested documents`

## Storage

SQLite tables `knowledge_documents` + `knowledge_chunks` (same App Service DB pattern as `release_events`). Vectors stored as JSON float arrays; similarity is in-process cosine (fits B1 corpora without a separate vector DB).
