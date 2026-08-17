"""Persona assets + professional Teams/WhatsApp greeting & help catalogs.

Formatting targets Microsoft Teams / Copilot (markdown-friendly):
clear sections, active-agent roster from feature flags, light code ticks.
No behavior change to coding gates — display/copy only.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


@lru_cache(maxsize=1)
def get_persona_prompt() -> str:
    path = PROMPTS_DIR / "persona_sunny.txt"
    return path.read_text(encoding="utf-8")


def _flag_on(enabled: bool) -> str:
    return "`ON`" if enabled else "`OFF`"


def _agent_rows() -> list[tuple[str, bool, str, str]]:
    """(name, enabled, capability, try_prompt)."""
    from agent.config import settings

    # Core agents are always available (not behind fabric flags)
    rows: list[tuple[str, bool, str, str]] = [
        (
            "Dev Agent",
            True,
            "Repos, plans, PRs, deploy gates (GitHub / Azure DevOps)",
            "check my repos",
        ),
        (
            "Email Agent",
            True,
            "Inbox digest and send email",
            "check my emails",
        ),
        (
            "Meeting Agent",
            True,
            "Schedule calendar meetings",
            "schedule a meeting tomorrow 3pm with a@b.com",
        ),
        (
            "Doc Library",
            bool(settings.enable_doc_knowledge),
            "List SharePoint / OneDrive / OneNote  `[SP]` `[OD]` `[ON]`",
            "list my documents",
        ),
        (
            "Doc Ingest",
            bool(settings.enable_doc_knowledge),
            "Vectorize selected docs into Qdrant",
            "ingest 1,3",
        ),
        (
            "Doc RAG",
            bool(settings.enable_doc_knowledge),
            "Ask questions with citations + related prompts",
            "ask docs <your question>",
        ),
        (
            "Doc Insights",
            bool(settings.enable_doc_knowledge),
            "Themes, risks, recommended actions",
            "summarize docs risks",
        ),
        (
            "Docs Agent",
            bool(settings.enable_docs_agent),
            "Publish release notes to SharePoint",
            "write release notes for PR <n>",
        ),
        (
            "QA Agent",
            bool(settings.enable_qa_agent),
            "HTTP smoke checks + SharePoint QA report",
            "run QA",
        ),
    ]
    return rows


def format_active_agents_block(*, show_off: bool = True) -> str:
    """Professional roster of agents with ON/OFF from live settings."""
    lines = ["*Active agents*", ""]
    for name, enabled, capability, try_prompt in _agent_rows():
        if not enabled and not show_off:
            continue
        status = _flag_on(enabled)
        lines.append(f"• *{name}* — {status}")
        lines.append(f"  {capability}")
        if enabled:
            lines.append(f"  Try: *{try_prompt}*")
    return "\n".join(lines)


def build_greeting_reply(
    *,
    session: dict[str, Any] | None = None,
    include_off_agents: bool = False,
) -> str:
    """ChatGPT-style welcome: session line + active agents + suggested next."""
    awaiting = (session or {}).get("awaiting") if session else None
    if awaiting:
        session_line = f"In progress — gate `*{awaiting}*` (say *status* or *stop*)"
    else:
        session_line = "Clear — nothing pending"

    agents = format_active_agents_block(show_off=include_off_agents)

    parts = [
        "*Sunny's Personal AI Agent*",
        "",
        "Hi Sunny — ready when you are.",
        "",
        f"*Session:* {session_line}",
        "",
        agents,
        "",
        "*Suggested next*",
        "1. *check my repos* — start a coding task",
        "2. *list my documents* — knowledge library (`[SP]` / `[OD]` / `[ON]`)",
        "3. *ask docs …* — query ingested documents",
        "4. *help* — full command catalog",
        "",
        "_Or ask me an Azure / GenAI / DevOps architecture question._",
    ]
    return "\n".join(parts)


def build_help_menu(*, include_off_agents: bool = True) -> str:
    """Full tools & prompts catalog — clean sections for Teams readability."""
    from agent.config import settings

    kb = bool(settings.enable_doc_knowledge)
    docs = bool(settings.enable_docs_agent)
    qa = bool(settings.enable_qa_agent)

    parts = [
        "*Sunny's Personal AI Agent*",
        "*Command catalog*",
        "",
        format_active_agents_block(show_off=include_off_agents),
        "",
        "────────────────",
        "*1 · Session*",
        "• *help* / *menu* / *?* — this catalog",
        "• *status* / *resume* — pending step or gate",
        "• *stop* / *new task* — clear stuck session",
        "• *hi* / *hello* — welcome + active agents",
        "",
        "*2 · Productivity*",
        "• *check my emails* / *inbox* — Email Agent",
        "• *schedule a meeting tomorrow 3pm with a@b.com* — Meeting Agent",
        "",
        "*3 · Dev Agent*",
        "• *check my repos* / *browse repos* — pick GitHub or Azure DevOps",
        "• Describe a change after selecting a repo — plan → development",
        "• *PROCEED <task_id>* — start implementation",
        "• *1* (AI review) / *2* (manual review) — after PR opens",
        "• *PR READY* — after manual review",
        "• *APPROVE <task_id>* / *REJECT <task_id>* — deploy gate",
        "_Gates stay open across Knowledge switches unless you say *stop*._",
        "",
        f"*4 · Document Knowledge* — {_flag_on(kb)}",
        "Source tags: `[SP]` SharePoint · `[OD]` OneDrive · `[ON]` OneNote",
        "• *list my documents* — library with `[SP]` / `[OD]` / `[ON]` tags",
        "• *list sharepoint docs* · *list onedrive* · *list onenote*",
        "• *list ingested documents* / *kb status* — already vectorized",
        "• *ingest 1,3* / *ingest all* — Doc Ingest → Qdrant",
        "• *ask docs <question>* — Doc RAG (citations + related prompts)",
        "• *summarize docs <focus>* / *doc insights* — Doc Insights",
        "",
        f"*5 · Release Fabric*",
        f"• Docs Agent {_flag_on(docs)} — *write release notes for PR <n>*",
        f"• QA Agent {_flag_on(qa)} — *run QA* / *run QA for <release_id>*",
        "",
        "*Typical flows*",
        "1. *Knowledge* — list my documents → ingest 1,2 → ask docs …",
        "2. *Coding* — check my repos → plan → PROCEED → review → APPROVE",
        "3. *Release* — deploy → run QA … → write release notes for PR …",
        "",
        "Reply with any prompt above, or just tell me what you need.",
    ]
    return "\n".join(parts)


# Backward-compatible module constants (evaluated at import; prefer builders at runtime).
HELP_MENU = build_help_menu()
GREETING_REPLY = build_greeting_reply()
