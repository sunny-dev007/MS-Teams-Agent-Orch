"""LLM plan builder for Meeting Intelligence Fabric."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.core.logging import get_logger
from agent.services.llm import invoke_llm, user_facing_llm_error

logger = get_logger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_PLAN_SYSTEM = (_PROMPT_DIR / "meeting_plan_system.txt").read_text(encoding="utf-8")
_PLAN_USER_TMPL = (_PROMPT_DIR / "meeting_plan_user.txt").read_text(encoding="utf-8")
_ACTION_PLAN_TMPL = (_PROMPT_DIR / "meeting_action_plan_user.txt").read_text(encoding="utf-8")


def _parse_json_response(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Plan response must be a JSON object")
    return data


async def summarize_meeting_excerpt(
    *,
    title: str,
    excerpt: str,
    user_focus: str = "",
) -> str:
    focus = f"\nFocus: {user_focus}" if user_focus else ""
    try:
        resp = await invoke_llm(
            [
                SystemMessage(
                    content=(
                        "Summarize this meeting transcript excerpt for planning. "
                        "Include decisions, requirements, risks, and action items."
                    )
                ),
                HumanMessage(content=f"Title: {title}{focus}\n\n{excerpt[:12000]}"),
            ],
            temperature=0.2,
            role="planning",
        )
        return (resp.content or "").strip() or excerpt[:2000]
    except Exception:
        logger.exception("Meeting excerpt summary failed")
        return excerpt[:2000]


async def build_plan_json(
    *,
    meetings_block: str,
    user_focus: str = "",
    plan_kind: str = "implementation",
) -> dict[str, Any]:
    if plan_kind == "action":
        user_prompt = (
            f"User focus: {user_focus or '(none — general action plan)'}\n\n"
            f"Meetings:\n{meetings_block}\n\n"
            f"Template guidance:\n{_ACTION_PLAN_TMPL}"
        )
    else:
        user_prompt = _PLAN_USER_TMPL.format(
            user_focus=user_focus or "(none — general implementation plan)",
            meetings_block=meetings_block,
        )
    try:
        resp = await invoke_llm(
            [SystemMessage(content=_PLAN_SYSTEM), HumanMessage(content=user_prompt)],
            temperature=0.2,
            role="planning",
        )
        return _parse_json_response(resp.content or "")
    except json.JSONDecodeError as exc:
        logger.exception("Plan JSON parse failed")
        raise ValueError(f"Plan LLM returned invalid JSON: {exc}") from exc
    except Exception as exc:
        logger.exception("Plan generation failed")
        raise RuntimeError(user_facing_llm_error("meeting plan")) from exc


def action_plan_to_markdown(plan: dict[str, Any]) -> str:
    title = plan.get("title") or "Meeting Action Plan"
    lines = [f"# {title}", ""]
    for section, key in (("Executive summary", "executive_summary"), ("Context", "context")):
        val = (plan.get(key) or "").strip()
        if val:
            lines += [f"## {section}", "", val, ""]
    for section, key in (
        ("Key decisions", "key_decisions"),
        ("Follow-ups", "follow_ups"),
        ("Risks", "risks"),
        ("Next steps", "next_steps"),
    ):
        items = plan.get(key) or []
        if items:
            lines += [f"## {section}", ""]
            lines += [f"- {x}" for x in items if str(x).strip()]
            lines.append("")
    actions = plan.get("action_items") or []
    if actions:
        lines += [
            "## Action items",
            "",
            "| Priority | Owner | Task | Due |",
            "| --- | --- | --- | --- |",
        ]
        for ai in actions:
            if not isinstance(ai, dict):
                continue
            lines.append(
                f"| {ai.get('priority') or 'Medium'} | {ai.get('owner') or 'TBD'} | "
                f"{ai.get('task') or ''} | {ai.get('due') or 'TBD'} |"
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def plan_to_markdown(plan: dict[str, Any], *, plan_kind: str = "implementation") -> str:
    if plan_kind == "action" or plan.get("meeting_category") in ("business", "general"):
        return action_plan_to_markdown(plan)
    title = plan.get("title") or "Implementation Plan"
    lines = [f"# {title}", ""]
    for section, key in (
        ("Executive summary", "executive_summary"),
        ("Background", "background"),
        ("Architecture overview", "architecture_overview"),
    ):
        val = (plan.get(key) or "").strip()
        if val:
            lines += [f"## {section}", "", val, ""]
    reqs = plan.get("requirements") or []
    if reqs:
        lines += ["## Requirements", ""]
        lines += [f"- {r}" for r in reqs if str(r).strip()]
        lines.append("")
    phases = plan.get("phases") or []
    if phases:
        lines += ["## Phases", ""]
        for ph in phases:
            if not isinstance(ph, dict):
                continue
            name = ph.get("name") or "Phase"
            weeks = ph.get("duration_weeks")
            hdr = f"### {name}"
            if weeks:
                hdr += f" ({weeks} weeks)"
            lines.append(hdr)
            lines.append("")
            for d in ph.get("deliverables") or []:
                lines.append(f"- {d}")
            lines.append("")
    for section, key in (("Risks", "risks"), ("Open questions", "open_questions"), ("Assumptions", "assumptions")):
        items = plan.get(key) or []
        if items:
            lines += [f"## {section}", ""]
            lines += [f"- {x}" for x in items if str(x).strip()]
            lines.append("")
    actions = plan.get("action_items") or []
    if actions:
        lines += ["## Action items", "", "| Owner | Task | Due |", "| --- | --- | --- |"]
        for ai in actions:
            if not isinstance(ai, dict):
                continue
            lines.append(
                f"| {ai.get('owner') or 'TBD'} | {ai.get('task') or ''} | {ai.get('due') or 'TBD'} |"
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"
