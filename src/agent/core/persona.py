from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


@lru_cache(maxsize=1)
def get_persona_prompt() -> str:
    path = PROMPTS_DIR / "persona_sunny.txt"
    return path.read_text(encoding="utf-8")


# Full agent / tool / quick-prompt catalog. Additive only — say *help*.
# Flag-gated features are labeled; disabled agents reply with a skip message
# and never alter Dev / WhatsApp coding paths.
HELP_MENU = (
    "*Sunny's Personal AI Agent — Help*\n"
    "_All tools, agents, and quick prompts_\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "*1. Session & navigation*\n"
    "• `help` / `menu` / `?` / `commands` — this catalog\n"
    "• `status` / `resume` — pending step / gate\n"
    "• `stop` / `new task` — clear stuck session\n"
    "• `hi` / `hello` — greeting\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "*2. Productivity*\n"
    "• `check my emails` / `inbox` — Email Agent\n"
    "• `schedule a meeting tomorrow 3pm with a@b.com` — Meeting Agent\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "*3. Dev Agent (repos & coding)*\n"
    "• `check my repos` / `browse repos` — Repo Wizard (GitHub / Azure DevOps)\n"
    "• Describe a change after picking a repo — Coding Architect → plan\n"
    "• `PROCEED <task_id>` — start implementation\n"
    "• `1` / `2` — PR review mode (agent vs manual)\n"
    "• `PR READY` — after manual review\n"
    "• `APPROVE <task_id>` / `REJECT <task_id>` — deploy gate\n"
    "_Gates stay open across Knowledge switches unless you say *stop*._\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "*4. Document Knowledge Fabric* _(ENABLE_DOC_KNOWLEDGE)_\n"
    "_Source tags: [SP]=SharePoint · [OD]=OneDrive · [ON]=OneNote_\n"
    "• `list my documents` — Doc Library (tagged SP / OD / ON)\n"
    "• `list sharepoint docs` / `list onedrive` / `list onenote`\n"
    "• `list ingested documents` / `kb status` — already vectorized\n"
    "• `ingest 1,3` / `ingest all` — Doc Ingest → Qdrant\n"
    "• `ask docs <question>` — Doc RAG (citations + related prompts)\n"
    "• `summarize docs <focus>` / `doc insights` — Doc Insights\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "*5. Release Agent Fabric* _(flag-gated)_\n"
    "• `write release notes for PR <n>` — Docs Agent → SharePoint "
    "_(ENABLE_DOCS_AGENT)_\n"
    "• `run QA` / `run QA for <release_id>` — QA Agent smoke + report "
    "_(ENABLE_QA_AGENT)_\n"
    "• Post-deploy handoff prompts when ENABLE_RELEASE_HANDOFF=true\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "*Typical flows*\n"
    "1. Knowledge: `list my documents` → `ingest 1,2` → `ask docs …`\n"
    "2. Coding: `check my repos` → plan → `PROCEED` → review → `APPROVE`\n"
    "3. Release: deploy → `run QA …` → `write release notes for PR …`\n\n"
    "Reply with a prompt above, or just tell me what you need."
)

GREETING_REPLY = (
    "Hi Sunny — I'm your Personal AI Agent.\n\n"
    "I can check emails, schedule meetings, browse GitHub/Azure DevOps repos, "
    "ship code after your approval, and (when enabled) list/ingest/ask "
    "SharePoint · OneDrive · OneNote documents.\n\n"
    "Say *help* for the full tools & prompts catalog, or tell me what to do."
)
