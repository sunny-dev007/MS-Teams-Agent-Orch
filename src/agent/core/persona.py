"""Persona assets + channel-aware greeting & help catalogs.

Teams: rich markdown (tables, bold prompts, minimal code ticks) + Adaptive Cards.
WhatsApp: compact *bold* bullets (Meta formatting).
No coding-gate behavior changes — display only.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

Channel = Literal["whatsapp", "teams"]


@lru_cache(maxsize=1)
def get_persona_prompt() -> str:
    path = PROMPTS_DIR / "persona_sunny.txt"
    return path.read_text(encoding="utf-8")


def _b(text: str, *, channel: Channel) -> str:
    """Bold — Teams prefers **, WhatsApp uses *."""
    t = (text or "").strip()
    if not t:
        return ""
    return f"**{t}**" if channel == "teams" else f"*{t}*"


def _agent_rows() -> list[tuple[str, bool, str, str]]:
    """(name, enabled, capability, try_prompt)."""
    from agent.config import settings

    return [
        (
            "Dev Agent",
            True,
            "Repos, plans, PRs, deploy gates (GitHub / Azure DevOps)",
            "check my repos",
        ),
        (
            "Email Agent",
            True,
            "Gmail inbox digest (WhatsApp)",
            "check my emails",
        ),
        (
            "Outlook Agent",
            bool(settings.enable_outlook_agent),
            "Teams signed-in user Outlook inbox",
            "check my outlook",
        ),
        (
            "Boards Agent",
            bool(settings.enable_boards_agent),
            "Pick project → action items by priority → ticket → Dev",
            "my work items",
        ),
        (
            "Calendar Agent",
            True,
            "Schedule Google Calendar meetings (not transcript plans)",
            "schedule a meeting tomorrow 3pm with a@b.com",
        ),
        (
            "Meeting Intelligence",
            bool(settings.enable_meeting_intelligence),
            "Teams transcripts → plan → SharePoint / email / DevOps board",
            "list my recent meetings",
        ),
        (
            "Doc Library",
            bool(settings.enable_doc_knowledge),
            "List SharePoint / OneDrive / OneNote — tags SP · OD · ON",
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
            "Data Analyst",
            bool(settings.enable_data_analyst_agent),
            "Excel → executive dashboard workbook (SharePoint Analytics)",
            "convert excel 6",
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


def format_active_agents_block(
    *,
    channel: Channel = "whatsapp",
    show_off: bool = True,
) -> str:
    """Professional roster — markdown table on Teams, bullets on WhatsApp."""
    rows = _agent_rows()
    if channel == "teams":
        lines = [
            _b("Active agents", channel=channel),
            "",
            "| Agent | Status | What it does | Try |",
            "| :--- | :---: | :--- | :--- |",
        ]
        for name, enabled, capability, try_prompt in rows:
            if not enabled and not show_off:
                continue
            status = "ON" if enabled else "OFF"
            try_col = try_prompt if enabled else "—"
            lines.append(f"| {name} | {status} | {capability} | {try_col} |")
        return "\n".join(lines)

    lines = [_b("Active agents", channel=channel), ""]
    for name, enabled, capability, try_prompt in rows:
        if not enabled and not show_off:
            continue
        status = "ON" if enabled else "OFF"
        lines.append(f"• {_b(name, channel=channel)} — {status}")
        lines.append(f"  {capability}")
        if enabled:
            lines.append(f"  Try: {_b(try_prompt, channel=channel)}")
    return "\n".join(lines)


def build_greeting_reply(
    *,
    session: dict[str, Any] | None = None,
    include_off_agents: bool = False,
    channel: Channel = "whatsapp",
) -> str:
    """Welcome card: session + active agents + suggested next."""
    awaiting = (session or {}).get("awaiting") if session else None
    if awaiting:
        session_line = (
            f"In progress — gate {awaiting} "
            f"(say {_b('status', channel=channel)} or {_b('stop', channel=channel)})"
        )
    else:
        session_line = "Clear — nothing pending"

    agents = format_active_agents_block(
        channel=channel, show_off=include_off_agents
    )
    parts = [
        _b("Sunny's Personal AI Agent", channel=channel),
        "",
        "Hi Sunny — ready when you are.",
        "",
        f"{_b('Session', channel=channel)}: {session_line}",
        "",
        agents,
        "",
        _b("Suggested next", channel=channel),
        f"1. {_b('check my repos', channel=channel)} — start a coding task",
        f"2. {_b('list my documents', channel=channel)} — knowledge library (SP / OD / ON)",
        f"3. {_b('check my outlook', channel=channel)} / {_b('my work items', channel=channel)} — mail & boards",
        f"4. {_b('ask docs …', channel=channel)} — query ingested documents",
        f"5. {_b('help', channel=channel)} — full command catalog",
        "",
        "_Or ask me an Azure / GenAI / DevOps architecture question._",
    ]
    return "\n".join(parts)


def build_help_menu(
    *,
    include_off_agents: bool = True,
    channel: Channel = "whatsapp",
) -> str:
    """Full tools catalog — Teams uses tables + spacing; WhatsApp stays compact."""
    from agent.config import settings

    kb = "ON" if settings.enable_doc_knowledge else "OFF"
    docs = "ON" if settings.enable_docs_agent else "OFF"
    qa = "ON" if settings.enable_qa_agent else "OFF"
    b = lambda t: _b(t, channel=channel)  # noqa: E731

    if channel == "teams":
        parts = [
            b("Sunny's Personal AI Agent"),
            b("Command catalog"),
            "",
            "_Tip: copy any prompt in the Try column and send it as a message._",
            "",
            format_active_agents_block(channel=channel, show_off=include_off_agents),
            "",
            "---",
            "",
            b("1 · Session"),
            f"- {b('help')} / {b('menu')} — this catalog",
            f"- {b('status')} / {b('resume')} — pending step or gate",
            f"- {b('stop')} / {b('new task')} — clear stuck session",
            f"- {b('hi')} / {b('hello')} — welcome + active agents",
            "",
            b("2 · Productivity"),
            f"- {b('check my emails')} — Gmail (WhatsApp) / Outlook (Teams when enabled)",
            f"- {b('check my outlook')} — Outlook Agent (Teams signed-in user)",
            f"- {b('my work items')} / {b('my action items')} — Boards: pick project → tickets",
            f"- Then **42** or **#42** — start Dev Agent from that work item",
            f"- {b('schedule a meeting tomorrow 3pm with a@b.com')} — Meeting Agent",
            "",
            b("3 · Dev Agent"),
            f"- {b('check my repos')} — pick GitHub or Azure DevOps",
            "- Describe a change after selecting a repo — plan then development",
            f"- {b('PROCEED <task_id>')} — start implementation",
            f"- {b('1')} AI review · {b('2')} manual review — after PR opens",
            f"- {b('PR READY')} — after manual review",
            f"- {b('APPROVE <task_id>')} / {b('REJECT <task_id>')} — deploy gate",
            "",
            f"{b('4 · Document Knowledge')} — {kb}",
            "Source tags: **SP** SharePoint · **OD** OneDrive · **ON** OneNote",
            f"- {b('list my documents')} — library with SP / OD / ON tags",
            f"- {b('list ingested documents')} — already vectorized",
            f"- {b('ingest 1,3')} / {b('ingest all')} — Doc Ingest → Qdrant",
            f"- Attach a file in Teams + {b('ingest this')} / {b('summarize this')} — Doc Upload → SharePoint",
            f"- {b('ask docs <question>')} — Doc RAG (citations + related)",
            f"- {b('summarize docs <focus>')} — Doc Insights",
            f"- {b('convert excel <n>')} — Data Analyst "
            f"({'ON' if settings.enable_data_analyst_agent else 'OFF'})",
            "",
            b("5 · Release Fabric"),
            f"- Docs Agent ({docs}) — {b('write release notes for PR <n>')}",
            f"- QA Agent ({qa}) — {b('run QA')} / {b('run QA for <release_id>')}",
            "",
            b("Typical flows"),
            "1. **Knowledge** — list my documents → ingest 1,2 → ask docs …",
            "2. **Coding** — check my repos → plan → PROCEED → review → APPROVE",
            "3. **Release** — deploy → run QA … → write release notes for PR …",
            "",
            "Reply with any prompt above, or just tell me what you need.",
        ]
        return "\n".join(parts)

    parts = [
        b("Sunny's Personal AI Agent"),
        b("Command catalog"),
        "",
        format_active_agents_block(channel=channel, show_off=include_off_agents),
        "",
        b("1 · Session"),
        f"• {b('help')} / {b('menu')} — this catalog",
        f"• {b('status')} / {b('resume')} — pending step or gate",
        f"• {b('stop')} / {b('new task')} — clear stuck session",
        "",
        b("2 · Productivity"),
        f"• {b('check my emails')} — Gmail (WhatsApp)",
        f"• {b('check my outlook')} / {b('my work items')} — Teams Outlook / Boards",
        f"• {b('schedule a meeting …')} — Meeting Agent",
        "",
        b("3 · Dev Agent"),
        f"• {b('check my repos')} — GitHub / Azure DevOps",
        f"• {b('PROCEED <task_id>')} → review → {b('APPROVE <task_id>')}",
        "",
        f"{b('4 · Document Knowledge')} — {kb}",
        "Tags: SP · OD · ON",
        f"• {b('list my documents')} → {b('ingest 1,3')} → {b('ask docs …')}",
        f"• {b('convert excel <n>')} — Data Analyst "
        f"({'ON' if settings.enable_data_analyst_agent else 'OFF'})",
        "",
        f"{b('5 · Release Fabric')} — Docs {docs} · QA {qa}",
        f"• {b('write release notes for PR <n>')} · {b('run QA')}",
        "",
        "Say any prompt above, or tell me what you need.",
    ]
    return "\n".join(parts)


def build_help_adaptive_card(*, include_off_agents: bool = True) -> dict[str, Any]:
    """Adaptive Card JSON for Copilot Studio / Teams rich rendering."""
    from agent.config import settings

    facts = []
    for name, enabled, capability, try_prompt in _agent_rows():
        if not enabled and not include_off_agents:
            continue
        status = "ON" if enabled else "OFF"
        value = f"{status} — {try_prompt}" if enabled else f"{status} — {capability}"
        facts.append({"title": name, "value": value})

    kb = "ON" if settings.enable_doc_knowledge else "OFF"
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": "Sunny's Personal AI Agent",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "size": "Medium",
            "weight": "Bolder",
            "text": "Command catalog",
            "spacing": "Small",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": "Copy any **Try** prompt below and send it as a chat message.",
            "isSubtle": True,
            "wrap": True,
            "spacing": "Small",
        },
        {
            "type": "TextBlock",
            "text": "Active agents",
            "weight": "Bolder",
            "spacing": "Medium",
            "wrap": True,
        },
        {"type": "FactSet", "facts": facts, "spacing": "Small"},
        {
            "type": "TextBlock",
            "text": "Quick prompts",
            "weight": "Bolder",
            "spacing": "Medium",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": (
                "• **help** · **status** · **stop** · **back**\n"
                "• **check my emails** · **check my repos**\n"
                f"• **list my documents** (Knowledge {kb}) — tags SP / OD / ON\n"
                "• **ask docs &lt;question&gt;** · **summarize docs &lt;focus&gt;**\n"
                "• **write release notes for PR &lt;n&gt;** · **run QA**"
            ),
            "wrap": True,
            "spacing": "Small",
        },
        {
            "type": "TextBlock",
            "text": (
                "**Flows:** Knowledge = list → ingest → ask docs  ·  "
                "Coding = repos → PROCEED → APPROVE  ·  "
                "Release = run QA → write release notes"
            ),
            "wrap": True,
            "spacing": "Medium",
            "isSubtle": True,
        },
    ]
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }


def build_greeting_adaptive_card(
    *,
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Adaptive Card for Hello — active agents only (ON)."""
    awaiting = (session or {}).get("awaiting") if session else None
    session_line = (
        f"In progress — {awaiting}" if awaiting else "Clear — nothing pending"
    )
    facts = []
    for name, enabled, capability, try_prompt in _agent_rows():
        if not enabled:
            continue
        facts.append({"title": name, "value": f"ON — {try_prompt}"})

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": "Sunny's Personal AI Agent",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": "Hi Sunny — ready when you are.",
                "wrap": True,
                "spacing": "Small",
            },
            {
                "type": "FactSet",
                "facts": [{"title": "Session", "value": session_line}],
                "spacing": "Medium",
            },
            {
                "type": "TextBlock",
                "text": "Active agents",
                "weight": "Bolder",
                "spacing": "Medium",
                "wrap": True,
            },
            {"type": "FactSet", "facts": facts, "spacing": "Small"},
            {
                "type": "TextBlock",
                "text": (
                    "Suggested: **check my repos** · **list my documents** · "
                    "**ask docs …** · **help**"
                ),
                "wrap": True,
                "spacing": "Medium",
            },
        ],
    }


# Backward-compatible defaults (WhatsApp-style at import).
HELP_MENU = build_help_menu(channel="whatsapp")
GREETING_REPLY = build_greeting_reply(channel="whatsapp")
