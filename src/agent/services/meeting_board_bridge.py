"""Bridge Meeting Intelligence plans to Azure DevOps Boards.

Optional handoff — requires ENABLE_BOARDS_AGENT + AzDO PAT.
Creates a parent Feature and child Tasks from plan action items.
"""

from __future__ import annotations

from typing import Any

from agent.config import settings
from agent.core.logging import get_logger
from agent.services import azure_boards

logger = get_logger(__name__)


async def create_board_from_plan(
    *,
    project: str,
    plan: dict[str, Any],
    published: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create AzDO work items from a meeting plan JSON."""
    if not settings.enable_boards_agent:
        raise RuntimeError("ENABLE_BOARDS_AGENT=false — enable Boards Agent to create work items")
    if not azure_boards.boards_configured():
        raise RuntimeError("Azure DevOps org URL / PAT not configured")

    proj = (project or settings.azdo_boards_project or settings.azdo_demo_project or "").strip()
    if not proj:
        raise ValueError("AzDO project required — set AZDO_BOARDS_PROJECT or pass project name")

    title = (plan.get("title") or "Meeting implementation plan").strip()
    summary = (plan.get("executive_summary") or "").strip()
    links = published or {}
    link_lines = []
    if links.get("md_url"):
        link_lines.append(f"Markdown: {links['md_url']}")
    if links.get("docx_url"):
        link_lines.append(f"DOCX: {links['docx_url']}")
    desc_parts = [summary]
    if link_lines:
        desc_parts.append("\n".join(link_lines))
    description = "\n\n".join(p for p in desc_parts if p)

    parent = await azure_boards.create_work_item(
        project=proj,
        work_item_type="Feature",
        title=title,
        description=description,
        tags="MeetingIntelligence;AutoPlan",
    )

    created_tasks: list[dict[str, Any]] = []
    for ai in plan.get("action_items") or []:
        if not isinstance(ai, dict):
            continue
        task_title = (ai.get("task") or "").strip()
        if not task_title:
            continue
        owner = (ai.get("owner") or "TBD").strip()
        due = (ai.get("due") or "").strip()
        task_desc = f"Owner: {owner}"
        if due:
            task_desc += f"\nDue: {due}"
        if links.get("md_url"):
            task_desc += f"\nPlan: {links['md_url']}"
        wi = await azure_boards.create_work_item(
            project=proj,
            work_item_type="Task",
            title=task_title[:255],
            description=task_desc,
            tags="MeetingIntelligence;ActionItem",
            parent_id=int(parent.get("id") or 0) or None,
        )
        created_tasks.append(wi)

    # Phase deliverables as User Stories when no action items
    if not created_tasks:
        for ph in plan.get("phases") or []:
            if not isinstance(ph, dict):
                continue
            ph_name = (ph.get("name") or "Phase").strip()
            dels = ph.get("deliverables") or []
            body = "\n".join(f"- {d}" for d in dels if str(d).strip())
            wi = await azure_boards.create_work_item(
                project=proj,
                work_item_type="User Story",
                title=ph_name[:255],
                description=body or ph_name,
                tags="MeetingIntelligence;Phase",
                parent_id=int(parent.get("id") or 0) or None,
            )
            created_tasks.append(wi)

    return {
        "project": proj,
        "parent": parent,
        "tasks": created_tasks,
        "board_url": parent.get("url") or "",
    }
