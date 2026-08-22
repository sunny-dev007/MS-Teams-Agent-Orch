# Document Knowledge Fabric

Additive feature for Teams (and WhatsApp): **list → select → ingest/vectorize → RAG QnA → insights**.

**Default off.** Existing Dev coding, WhatsApp, Docs Agent, and QA Agent stay unchanged until you set `ENABLE_DOC_KNOWLEDGE=true`.

## Architecture (five specialists)

| Agent | Intent | Teams phrases |
|---|---|---|
| **Doc Library** | `list_docs` | `list my documents`, `list of my document`, `show me my documents`, `find document &lt;keyword&gt;`, `next page` |
| **Doc Ingest** | `ingest_docs` | `ingest 1,3`, `ingest all`, or bare `1, 3` after a list |
| **Doc Upload** | `upload_ingest_docs` | Attach file in Teams + `ingest this`, `summarize this pdf`, `upload to SharePoint` |
| **Doc RAG** | `ask_docs` | `ask docs what is our release process?` |
| **Doc Insights** | `summarize_docs` | `summarize docs risks`, `doc insights` |

### Teams chat upload (Doc Upload Agent)

When a user attaches a local file in Teams and asks to **ingest** or **summarize**, the agent:

1. Accepts optional `attachments[]` on `POST /api/channels/copilot/message` (base64 or URL — see swagger `1.0.7`).
2. Uploads to SharePoint folder **`UploadedDocs`** (override: `DOC_UPLOAD_FOLDER`).
3. Runs the same ingest pipeline as Doc Ingest (extract → chunk → embed → Qdrant).
4. If the user asked to summarize, runs Doc Insights on the freshly ingested file(s).

Copilot Studio must pass attachment bytes in the HTTP tool body (Power Automate can base64-encode Teams attachments). Without `attachments[]`, the agent can still use files stored in session from a prior turn (`pending_attachments`).

Max upload size default **25 MB** (`DOC_UPLOAD_MAX_BYTES`).

### Source tags in lists

| Tag | Source |
|---|---|
| **SP** | SharePoint |
| **OD** | OneDrive |
| **ON** | OneNote |

Example Teams row: `1. [SP] [developer-guide.md](https://…)` with type, size, site/folder, and **Ingestion** (`Raw / Not ingested`, `Ingested`, or `Ready for re-ingest` when Graph `lastModified`/size is newer than the last index). Lists paginate **10 per page** (`next page`). Filename search: `find document guide`. Extra SharePoint sites: `DOC_KNOWLEDGE_ALL_SITES=true` (falls back to the configured site).

**Graceful reindex:** `ingest 1,3` always replaces chunks/vectors for that file id (Qdrant delete-then-upsert). `ingest all` skips unchanged ingested files. `reingest stale` only refreshes **Ready for re-ingest** rows.

Say `help` for the full tools & quick-prompts catalog (all agents with `ON`/`OFF`).
Say `hello` for the welcome card: session status, *Active agents*, and suggested next steps.

```
Teams message
    → channel_gates (bypass coding gates)
    → planner keywords (beat repo/coding session)
    → LangGraph specialist → notify_result
         Library lists Graph files + stores catalog in session
         Upload saves Teams attachments → SharePoint UploadedDocs → ingest
         Ingest downloads text, chunks, embeds → Qdrant (SQLite catalog)
         RAG embeds question → Qdrant top-k (SQLite cosine fallback)
         Insights synthesizes themes/risks/actions from corpus
```

## Safety

- `ENABLE_DOC_KNOWLEDGE=false` by default (App Service / CI must not auto-enable).
- Agents registered always; flag off → `status: skipped` message (no Graph/LLM side effects).
- Routing never enters `coding_architect` / `repo_wizard` for knowledge intents.
- Binary Office/PDF: extracted via `doc_extract` (DOCX/PPTX/XLSX/PDF/CSV/MD). Legacy `.doc/.ppt/.xls` are rejected gracefully — re-save as modern formats. Never UTF-8-decode ZIP bytes into Qdrant.

## App Service settings

```bash
az webapp config appsettings set -g ai-agent-rg -n whatsapp-ai-agent-sunny --settings \
  ENABLE_DOC_KNOWLEDGE=false \
  AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large \
  DOC_KNOWLEDGE_SOURCES=sharepoint,onedrive,onenote \
  DOC_KNOWLEDGE_MAX_LIST=100 \
  DOC_KNOWLEDGE_PAGE_SIZE=10 \
  DOC_KNOWLEDGE_ALL_SITES=true \
  DOC_UPLOAD_FOLDER=UploadedDocs \
  DOC_UPLOAD_MAX_BYTES=26214400 \
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

Also ensure Azure OpenAI **embedding** `text-embedding-3-large` and RAG chat `gpt-4.1` (or `gpt-5`) are deployed in Foundry.

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

## Storage / vector backend

| Layer | Role |
|---|---|
| **SQLite** `knowledge_documents` + `knowledge_chunks` | Catalog, metadata (`doc_mode`, source), chunk **text** |
| **Qdrant Cloud** (preferred when configured) | Vector upsert + semantic search for RAG |
| **SQLite cosine** | Automatic fallback if Qdrant URL/key missing or search fails |

### Quality stack (Foundry + Qdrant)

| Piece | Recommended (on `suchi-m5s861xi-eastus`) |
|---|---|
| Embedding | `text-embedding-3-large` (3072-d) |
| RAG / Insights LLM | `gpt-4.1` with fallbacks `gpt-5,gpt-4o` |
| Chunking | Structure-aware (headings → paragraphs → sentences), ~1800 chars, ~20% overlap |
| Retrieval | Multi-query expansion + over-fetch (`fetch_k=24`) + MMR diversify (`top_k=8`) |
| Qdrant collection | `doc_knowledge_te3_large` (auto-created; Cosine) |

Re-run **ingest** after changing embedding model or collection name.

### Qdrant setup (important)

For ingest/RAG you need the **cluster REST endpoint** + a **Database API key**:

1. Qdrant Cloud → **Clusters** → open your cluster  
2. Copy **Cluster URL** (append `:6333` if missing)  
3. **Data Access Control** → Database API key  
4. Set App Service (never commit secrets to git):

```bash
az webapp config appsettings set -g ai-agent-rg -n whatsapp-ai-agent-sunny --settings \
  ENABLE_DOC_KNOWLEDGE=true \
  ENABLE_QDRANT=true \
  QDRANT_URL="https://YOUR-CLUSTER.aws.cloud.qdrant.io:6333" \
  QDRANT_API_KEY="***" \
  QDRANT_COLLECTION=doc_knowledge_te3_large \
  AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large \
  AZURE_OPENAI_RAG_DEPLOYMENT=gpt-4.1 \
  AZURE_OPENAI_RAG_FALLBACKS=gpt-5,gpt-4o,gpt-4.1-mini \
  AZURE_OPENAI_PLANNING_DEPLOYMENT=gpt-4.1
```

On first ingest the agent auto-creates the Qdrant collection (Cosine, size = embedding dims).

## Enterprise orchestration notes

- **Workspaces:** Knowledge vs Dev soft-switch with a banner; never auto-clear PROCEED/APPROVE gates.
- **Related prompts:** After RAG, suggest follow-ups derived from retrieved section locators only.
- **Sources:** `DOC_KNOWLEDGE_SOURCES=sharepoint,onedrive,onenote`. OneDrive also needs `MS_GRAPH_ONEDRIVE_USER_ID`.
- **List tags:** `[SP]` / `[OD]` / `[ON]` on every library + ingested + citation line.
- **Help catalog:** `help` / `menu` lists all agents and quick prompts (flag-gated features labeled).
- **Isolation:** Knowledge intents bypass coding gates; `stop` clears stuck sessions intentionally.
