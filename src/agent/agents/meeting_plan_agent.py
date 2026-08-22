"""Meeting Plan Agent — intent-aware plans, SharePoint publish, DevOps project picker."""

from __future__ import annotations

import re

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.logging import get_logger
from agent.core.session import get_session, save_session
from agent.services import (
    meeting_board_bridge,
    meeting_context,
    meeting_intent,
    meeting_plan_builder,
    meeting_publish,
    ms_graph,
)

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


def _client_name(selected: list[dict]) -> str:
    for row in selected:
        name = (row.get("client_name") or "").strip()
        if name and name.lower() != "unknown client":
            return name
    return (selected[0].get("client_name") or "Unknown client") if selected else ""


async def _create_board_with_project(
    state: AgentState,
    *,
    phone: str,
    msg: str,
    session_data: dict,
    plan_json: dict,
    published: dict,
) -> AgentState:
    default_proj = settings.azdo_boards_project or settings.azdo_demo_project or ""
    picked, projects, needs_picker = await meeting_board_bridge.resolve_board_project(
        user_message=msg,
        session_data=session_data,
        default_project=default_proj,
    )

    if needs_picker:
        if phone:
            try:
                await save_session(
                    phone,
                    awaiting=meeting_board_bridge.AWAITING_BOARD_PROJECT,
                    data={
                        "meeting_board_projects": projects,
                        "last_plan": session_data.get("last_plan") or {},
                    },
                    merge_data=True,
                )
            except Exception:
                logger.exception("Failed saving meeting_board_projects")
        body = meeting_board_bridge.format_project_picker(
            projects,
            plan_title=(plan_json.get("title") or ""),
        )
        return {
            **state,
            "status": "completed",
            "handled_by": AGENT_NAME,
            "notification_text": body,
            "session_awaiting": meeting_board_bridge.AWAITING_BOARD_PROJECT,
        }

    project_name = (picked or {}).get("name") or default_proj
    if not project_name:
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*DevOps board handoff* — no Azure DevOps projects found.\n"
                "Check `AZDO_ORG_URL` / `AZDO_PAT`, or set `AZDO_BOARDS_PROJECT`."
            ),
        }

    try:
        board = await meeting_board_bridge.create_board_from_plan(
            project=project_name,
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

    if phone:
        try:
            await save_session(
                phone,
                awaiting="meeting_pick",
                data={"meeting_board_project": project_name},
                merge_data=True,
            )
        except Exception:
            logger.exception("Failed saving meeting_board_project")

    parent = board.get("parent") or {}
    tasks = board.get("tasks") or []
    note = (
        f"*Azure DevOps board prepared* in project **{board.get('project')}**\n\n"
        f"Parent Feature: [{parent.get('title')}]({parent.get('url')})\n"
        f"Created **{len(tasks)}** linked work item(s).\n\n"
        "**Next:** *my work items* (Boards Agent) · *check my repos* (Dev Agent)"
    )
    return {
        **state,
        "status": "completed",
        "handled_by": AGENT_NAME,
        "notification_text": note,
    }


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

    last_plan = session_data.get("last_plan") or {}
    plan_json = last_plan.get("plan_json") or {}
    published = last_plan.get("published") or last_plan
    meeting_intent_data = session_data.get("meeting_intent") or last_plan.get("meeting_intent") or {}

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
        if (meeting_intent_data.get("meeting_category") or "").lower() != meeting_intent.MEETING_DEV:
            if not meeting_intent_data.get("requires_devops_board"):
                return {
                    **state,
                    "status": "completed",
                    "handled_by": AGENT_NAME,
                    "notification_text": (
                        "_This meeting was classified as non-development._\n\n"
                        "Your **action plan DOCX** is already on SharePoint. "
                        "Use *email the plan to …* to share it.\n\n"
                        "For engineering work, run a development-focused meeting first."
                    ),
                }
        if not settings.enable_boards_agent:
            return {
                **state,
                "status": "failed",
                "handled_by": AGENT_NAME,
                "notification_text": (
                    "*DevOps board* requires `ENABLE_BOARDS_AGENT=true` and AzDO PAT."
                ),
            }
        return await _create_board_with_project(
            state,
            phone=phone,
            msg=msg,
            session_data=session_data,
            plan_json=plan_json,
            published=published,
        )

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

    focus = _extract_focus(msg)
    client = _client_name(selected)
    titles = [r.get("title") or "" for r in selected]

    try:
        block = await meeting_context.build_meetings_block(selected, user_focus=focus)
        intent_data = await meeting_intent.classify_meeting_intent(
            meetings_block=block,
            client_name=client,
            titles=titles,
        )
        category = (intent_data.get("meeting_category") or meeting_intent.MEETING_GENERAL).lower()
        is_dev = category == meeting_intent.MEETING_DEV
        plan_kind = "implementation" if is_dev else "action"

        plan_json = await meeting_plan_builder.build_plan_json(
            meetings_block=block,
            user_focus=focus,
            plan_kind=plan_kind,
        )
        plan_json["meeting_category"] = category
        plan_json["meeting_intent"] = intent_data

        markdown = meeting_plan_builder.plan_to_markdown(plan_json, plan_kind=plan_kind)
        folder = meeting_intent.resolve_publish_folder(intent_data, client_name=client)
        published = await meeting_publish.publish_plan(
            plan_json,
            markdown,
            folder=folder,
            plan_kind=plan_kind,
            client_name=client,
        )
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
        "folder": published.get("folder"),
        "plan_kind": published.get("plan_kind"),
        "created_at": published.get("created_at"),
        "plan_json": plan_json,
        "published": published,
        "meeting_intent": intent_data,
        "source_meeting_ids": [r.get("meeting_id") for r in selected],
    }
    if phone:
        try:
            await save_session(
                phone,
                awaiting="meeting_pick",
                data={
                    "last_plan": last_plan,
                    "meeting_selected": selected,
                    "meeting_intent": intent_data,
                },
                merge_data=True,
            )
        except Exception:
            logger.exception("Failed saving last_plan")

    summary = (plan_json.get("executive_summary") or "")[:1200]
    links = []
    if published.get("docx_url"):
        links.append(f"DOCX: {published['docx_url']}")
    if published.get("md_url"):
        links.append(f"MD: {published['md_url']}")
    link_block = "\n".join(links) if links else "_SharePoint upload unavailable — check Graph write permissions._"
    folder_note = published.get("folder") or folder

    cat_label = {
        meeting_intent.MEETING_DEV: "Development",
        meeting_intent.MEETING_BUSINESS: "Business",
        meeting_intent.MEETING_GENERAL: "General",
    }.get(category, category.title())

    if is_dev:
        next_steps = (
            "**Next:** *create devops board from plan* (pick your AzDO project) · "
            "*email the plan to teammate@co.com* · *my work items*"
        )
    else:
        next_steps = (
            "**Next:** *email the plan to teammate@co.com*\n\n"
            "_Non-development meeting — action plan saved to SharePoint. "
            "DevOps board is skipped unless you run a development meeting._"
        )

    note = (
        f"*Plan published* ({cat_label}) — **{last_plan['title']}**\n\n"
        f"_SharePoint folder:_ `{folder_note}`\n\n"
        f"{link_block}\n\n"
        f"**Executive summary**\n{summary}\n\n"
        f"{next_steps}"
    )
    return {
        **state,
        "status": "completed",
        "handled_by": AGENT_NAME,
        "notification_text": note,
        "meeting_plan_url": published.get("md_url") or "",
        "meeting_plan_docx_url": published.get("docx_url") or "",
        "meeting_plan_title": last_plan["title"] or "",
        "meeting_client_name": client,
    }
