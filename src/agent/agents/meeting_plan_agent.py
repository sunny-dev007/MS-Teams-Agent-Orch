"""Meeting Plan Agent — synthesize plans and optional DevOps board handoff."""

from __future__ import annotations

import re

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.core.session import get_session, save_session
from agent.services import meeting_board_bridge, meeting_context, meeting_plan_builder, meeting_publish, ms_graph

logger = get_logger(__name__)

AGENT_NAME = "meeting_plan_agent"

_DISABLED = (
    "*Meeting Plan Agent* is disabled (ENABLE_MEETING_INTELLIGENCE=false).\n"
    "Calendar scheduling is unchanged."
)

_FOCUS_RE = re.compile(
    r"(?:focus(?:\s+on)?|about|for)\s+(.+)$",
    re.I,
)


def _extract_focus(message: str) -> str:
    m = _FOCUS_RE.search(message or "")
    return m.group(1).strip() if m else ""


async def run_meeting_plan(state: AgentState) -> AgentState:
    if not settings.enable_meeting_intelligence:
        return {
            **state,
            "status": "skipped",
            "handled_by": AGENT_NAME,
            "notification_text": _DISABLED,
        }

    if not ms_graph.graph_configured():
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": "*Meeting Plan Agent* — Graph credentials missing.",
        }

    msg = state.get("user_message") or ""
    phone = state.get("whatsapp_phone") or ""
    intent = (state.get("intent") or "").lower()

    session_data: dict = {}
    if phone:
        try:
            session_data = (await get_session(phone)).get("data") or {}
        except Exception:
            session_data = {}

    selected = list(session_data.get("meeting_selected") or [])
    if not selected and len(session_data.get("meeting_catalog") or []) == 1:
        selected = list(session_data.get("meeting_catalog") or [])[:1]

    if not selected:
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Meeting Plan Agent* — no meetings selected.\n"
                "Say *list my recent meetings* then *select meetings 1*."
            ),
        }

    last_plan = session_data.get("last_plan") or {}
    plan_json = last_plan.get("plan_json") or {}
    published = last_plan.get("published") or {}

    # DevOps board handoff from existing plan
    if intent == "create_board_from_plan":
        if not plan_json:
            return {
                **state,
                "status": "failed",
                "handled_by": AGENT_NAME,
                "notification_text": (
                    "*Meeting Intelligence* — no plan in session.\n"
                    "Run *make a plan* first, then *create devops board from plan*."
                ),
            }
        try:
            board = await meeting_board_bridge.create_board_from_plan(
                project=(settings.azdo_boards_project or settings.azdo_demo_project or ""),
                plan=plan_json,
                published=published,
            )
        except Exception as exc:
            logger.exception("Board creation from plan failed")
            return {
                **state,
                "status": "failed",
                "handled_by": AGENT_NAME,
                "notification_text": f"*DevOps board handoff failed:* {exc}",
                "error": str(exc),
            }
        parent = board.get("parent") or {}
        tasks = board.get("tasks") or []
        note = (
            f"*Azure DevOps board prepared* in **{board.get('project')}**\n\n"
            f"Parent: [{parent.get('title')}]({parent.get('url')})\n"
            f"Created **{len(tasks)}** linked work item(s).\n\n"
            "Say *my work items* to continue in Boards Agent, or *check my repos* for Dev."
        )
        return {
            **state,
            "status": "completed",
            "handled_by": AGENT_NAME,
            "notification_text": note,
        }

    focus = _extract_focus(msg)
    try:
        block = await meeting_context.build_meetings_block(selected, user_focus=focus)
        plan_json = await meeting_plan_builder.build_plan_json(
            meetings_block=block,
            user_focus=focus,
        )
        markdown = meeting_plan_builder.plan_to_markdown(plan_json)
        published = await meeting_publish.publish_plan(plan_json, markdown)
    except Exception as exc:
        logger.exception("Meeting plan generation failed")
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": f"*Meeting Plan Agent* failed: {exc}",
            "error": str(exc),
        }

    last_plan = {
        "title": published.get("title") or plan_json.get("title"),
        "md_url": published.get("md_url"),
        "docx_url": published.get("docx_url"),
        "created_at": published.get("created_at"),
        "plan_json": plan_json,
        "published": published,
        "source_meeting_ids": [r.get("meeting_id") for r in selected],
    }
    if phone:
        try:
            await save_session(
                phone,
                awaiting="meeting_pick",
                data={"last_plan": last_plan, "meeting_selected": selected},
                merge_data=True,
            )
        except Exception:
            logger.exception("Failed saving last_plan")

    summary = (plan_json.get("executive_summary") or "")[:1200]
    links = []
    if published.get("md_url"):
        links.append(f"MD: {published['md_url']}")
    if published.get("docx_url"):
        links.append(f"DOCX: {published['docx_url']}")
    link_block = "\n".join(links) if links else "_SharePoint upload unavailable — check Graph write permissions._"

    note = (
        f"*Plan published:* **{last_plan['title']}**\n\n"
        f"{link_block}\n\n"
        f"**Executive summary**\n{summary}\n\n"
        "**Next:** *email the plan to teammate@co.com* · "
        "*create devops board from plan* · *my work items*"
    )
    return {
        **state,
        "status": "completed",
        "handled_by": AGENT_NAME,
        "notification_text": note,
        "meeting_plan_url": published.get("md_url") or "",
        "meeting_plan_docx_url": published.get("docx_url") or "",
        "meeting_plan_title": last_plan["title"] or "",
        "meeting_client_name": selected[0].get("client_name") or "",
    }
