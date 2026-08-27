"""Graceful replies when a specialist is OFF in Azure App Settings.

Additive helper — does not change routing or coding gates.
"""

from __future__ import annotations

from typing import Sequence


def format_agent_offline(
    agent_name: str,
    *,
    env_flag: str,
    try_again: str | Sequence[str] = (),
    meanwhile: str | Sequence[str] = (),
) -> str:
    """User-facing copy when ENABLE_* is false for a requested agent.

    Tone: clear that the agent is off, what to flip in Azure, then ask again.
    Avoid sounding like a crash or a coding-gate block.
    """
    name = (agent_name or "That agent").strip()
    flag = (env_flag or "").strip() or "the matching ENABLE_* setting"

    examples: list[str] = []
    if isinstance(try_again, str) and try_again.strip():
        examples = [try_again.strip()]
    else:
        examples = [e.strip() for e in (try_again or []) if str(e).strip()]

    alts: list[str] = []
    if isinstance(meanwhile, str) and meanwhile.strip():
        alts = [meanwhile.strip()]
    else:
        alts = [a.strip() for a in (meanwhile or []) if str(a).strip()]
    if not alts:
        alts = ["help", "check my repos"]

    lines = [
        f"*{name}* is currently **turned off** in your Azure app configuration.",
        "",
        "I can’t run that request until the agent is enabled.",
        "",
        f"1. In Azure App Settings, set `{flag}=true`",
        "2. Save / restart the app (if your host needs a restart)",
        "3. Ask me the **same question** again",
    ]
    if examples:
        lines.append("")
        lines.append("Examples once it’s on:")
        for ex in examples[:4]:
            lines.append(f"• *{ex}*")
    lines.append("")
    lines.append("Meanwhile you can still use:")
    lines.append(" · ".join(f"*{a}*" for a in alts[:5]))
    return "\n".join(lines)


# Convenience presets used by specialists (keep wording consistent).
def outlook_offline() -> str:
    return format_agent_offline(
        "Outlook Agent",
        env_flag="ENABLE_OUTLOOK_AGENT",
        try_again=("check my outlook", "check my emails"),
        meanwhile=("check my repos", "my work items", "help"),
    )


def boards_offline() -> str:
    return format_agent_offline(
        "Boards Agent",
        env_flag="ENABLE_BOARDS_AGENT",
        try_again=("my work items", "my tickets", "my action items"),
        meanwhile=("check my repos", "check my outlook", "help"),
    )


def azure_finops_offline() -> str:
    return format_agent_offline(
        "Azure FinOps Agent",
        env_flag="ENABLE_AZURE_FINOPS_AGENT",
        try_again=(
            "azure subscriptions",
            "azure costs",
            "deep scan",
            "finops recommendations",
        ),
        meanwhile=("check my repos", "my work items", "help"),
    )


def doc_knowledge_offline(*, agent_label: str = "Document Knowledge") -> str:
    return format_agent_offline(
        agent_label,
        env_flag="ENABLE_DOC_KNOWLEDGE",
        try_again=("list my documents", "ingest 1,3", "ask docs <question>"),
        meanwhile=("check my repos", "help"),
    )


def meeting_intelligence_offline(*, agent_label: str = "Meeting Intelligence") -> str:
    return format_agent_offline(
        agent_label,
        env_flag="ENABLE_MEETING_INTELLIGENCE",
        try_again=("list my recent meetings", "create a plan from this meeting"),
        meanwhile=("check my repos", "schedule a meeting", "help"),
    )


def data_analyst_offline() -> str:
    return format_agent_offline(
        "Data Analyst Agent",
        env_flag="ENABLE_DATA_ANALYST_AGENT",
        try_again=("convert excel 6", "analyze this workbook"),
        meanwhile=("list my documents", "check my repos", "help"),
    )


def docs_agent_offline() -> str:
    return format_agent_offline(
        "Documentation Agent",
        env_flag="ENABLE_DOCS_AGENT",
        try_again=("write release notes for PR <n>",),
        meanwhile=("check my repos", "help"),
    )


def qa_agent_offline() -> str:
    return format_agent_offline(
        "QA Agent",
        env_flag="ENABLE_QA_AGENT",
        try_again=("run QA",),
        meanwhile=("check my repos", "help"),
    )
