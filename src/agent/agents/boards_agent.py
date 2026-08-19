"""Azure Boards Agent — project picker → priority action items → ticket → Dev handoff.

Feature: ENABLE_BOARDS_AGENT default false.
Does not replace repo wizard / coding gates — seeds them when a ticket is chosen.
"""

from __future__ import annotations

import re

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.channel_identity import is_teams_session
from agent.core.logging import get_logger
from agent.core.session import save_session
from agent.services import azure_boards
from agent.specialists.azdo_agent import azdo_agent

logger = get_logger(__name__)

AGENT_NAME = "boards_agent"

AWAITING_PROJECT = "boards_project_pick"
AWAITING_TICKET = "boards_ticket_pick"

_DISABLED = (
    "*Boards Agent* is installed but **disabled** (ENABLE_BOARDS_AGENT=false).\n\n"
    "When enabled: *my work items* / *my action items* / *my tickets*.\n"
    "Dev coding path (*check my repos*) is unchanged."
)

_TICKET_RE = re.compile(
    r"(?:^|\b)(?:#|ticket\s+|work\s*item\s+|wi\s+|start\s+|work\s+on\s+|implement\s+)?"
    r"(\d{1,7})\b",
    re.I,
)


def _teams_user_id(state: AgentState) -> str:
    phone = state.get("whatsapp_phone") or ""
    if is_teams_session(phone):
        return str(phone).removeprefix("teams:").strip()
    data = state.get("session_data") or {}
    return str(data.get("teams_user_id") or "").strip()


def _pick_project(user_msg: str, projects: list[dict]) -> dict | None:
    if not projects:
        return None
    msg = (user_msg or "").strip()
    if msg.isdigit():
        idx = int(msg) - 1
        if 0 <= idx < len(projects):
            return projects[idx]
    lower = msg.lower()
    for p in projects:
        if (p.get("name") or "").lower() == lower:
            return p
    for p in projects:
        name = (p.get("name") or "").lower()
        if lower and lower in name:
            return p
    return None


def _extract_ticket_id(user_msg: str, catalog: list[dict]) -> int | None:
    msg = (user_msg or "").strip()
    ids = {int(x["id"]) for x in catalog if x.get("id")}
    if msg.isdigit():
        n = int(msg)
        # Prefer catalog match; bare number OK if in catalog OR catalog empty
        if n in ids or (not ids and 1 <= n <= 9_999_999):
            return n if (n in ids or not ids) else None
        # Also allow pick index 1..N from catalog
        if 1 <= n <= len(catalog):
            return int(catalog[n - 1]["id"])
        return None
    m = _TICKET_RE.search(msg)
    if not m:
        return None
    n = int(m.group(1))
    if ids and n not in ids:
        # Still allow explicit #id even if not in last page
        if msg.startswith("#") or "ticket" in msg.lower() or "work" in msg.lower():
            return n
        return None
    return n


def _format_project_picker(projects: list[dict], *, display: str = "") -> str:
    who = f" for **{display}**" if display else ""
    lines = [
        "*Boards Agent* — choose a project",
        f"_Assigned work will be filtered to your identity{who}_",
        "",
        "| # | Project |",
        "| :---: | :--- |",
    ]
    for i, p in enumerate(projects, start=1):
        lines.append(f"| {i} | {p.get('name')} |")
    lines.append("")
    lines.append("Reply with a **number** or the **project name**.")
    lines.append("_Classic path:_ **check my repos** (unchanged).")
    return "\n".join(lines)


def _format_items(
    items: list[dict],
    *,
    project: str,
    display: str = "",
) -> str:
    who = display or "you"
    metrics = azure_boards.summarize_metrics(items)
    if not items:
        return (
            f"*Boards Agent* — **{project}**\n"
            f"Assigned to: **{who}**\n\n"
            "_No open action items matched._\n\n"
            "Items must be assigned to **your Teams identity** (mail or guest UPN) "
            "and not Done/Closed.\n"
            "Try another project, or **check my repos**."
        )

    lines = [
        "*Boards Agent* — Action items",
        f"**Project:** {project}",
        f"**Assigned to:** {who}",
        "",
        "| Metric | Count |",
        "| :--- | :---: |",
        f"| Open items | {metrics['open']} |",
        f"| Bugs | {metrics['bugs']} |",
        f"| Stories | {metrics['stories']} |",
        f"| Tasks | {metrics['tasks']} |",
        f"| Features / Epics | {metrics['features']} |",
        f"| High priority (1–2) | {metrics['high_priority']} |",
        "",
        "_Sorted by priority (1 = highest), then recent activity._",
        "",
        "| # | ID | Pri | Type | State | Title |",
        "| :---: | :---: | :---: | :--- | :--- | :--- |",
    ]
    for i, it in enumerate(items, start=1):
        title = (it.get("title") or "").replace("|", "/")[:70]
        pri = it.get("priority")
        pri_s = str(pri) if pri is not None else "—"
        lines.append(
            f"| {i} | {it.get('id')} | {pri_s} | {it.get('type') or '?'} | "
            f"{it.get('state') or '?'} | {title} |"
        )
    lines.append("")
    lines.append("**Links**")
    for it in items[:10]:
        if it.get("url"):
            lines.append(f"- #{it.get('id')}: {it['url']}")
    lines.append("")
    lines.append("── *Start development* ──")
    lines.append(
        "Reply with a ticket ID (e.g. **42** or **#42** or **work on 42**) "
        "to hand off to the Dev Agent for this project."
    )
    lines.append("Or say **check my repos** for the classic provider → repo flow.")
    return "\n".join(lines)


async def _mark_productivity(phone: str) -> None:
    if not phone:
        return
    try:
        from agent.services.workspace_handoff import WS_PRODUCTIVITY, mark_workspace

        await mark_workspace(phone, WS_PRODUCTIVITY)
    except Exception:
        logger.exception("Failed marking productivity workspace")


async def list_my_boards(state: AgentState) -> AgentState:
    if not settings.enable_boards_agent:
        return {
            **state,
            "status": "skipped",
            "handled_by": AGENT_NAME,
            "notification_text": _DISABLED,
        }

    phone = state.get("whatsapp_phone") or ""
    if not is_teams_session(phone):
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Boards Agent* is for **Teams signed-in users** only."
            ),
        }

    if not azure_boards.boards_configured():
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Boards Agent* — Azure DevOps org URL / PAT missing.\n"
                "See `docs/OUTLOOK_BOARDS_AGENTS.md`."
            ),
        }

    user_id = _teams_user_id(state)
    if not user_id:
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": "*Boards Agent* — could not resolve Teams user id.",
        }

    user_msg = (state.get("user_message") or "").strip()
    session_data = dict(state.get("session_data") or {})
    awaiting = (state.get("session_awaiting") or "").strip()

    try:
        identity = await azure_boards.resolve_identity_email(user_id)
        display = identity.get("display_name") or identity["email"]

        # --- Start Dev from ticket id ---
        if awaiting == AWAITING_TICKET or state.get("intent") == "boards_start_dev":
            catalog = session_data.get("boards_catalog") or []
            tid = _extract_ticket_id(user_msg, catalog)
            if not tid:
                return {
                    **state,
                    "status": "repo_picker",
                    "handled_by": AGENT_NAME,
                    "session_awaiting": AWAITING_TICKET,
                    "notification_text": (
                        "Reply with a **ticket ID** from the list "
                        "(e.g. **42** or **#42**), or *my work items* to refresh."
                    ),
                }
            return await _start_dev_from_ticket(
                state,
                phone=phone,
                work_item_id=tid,
                identity=identity,
                session_data=session_data,
            )

        # --- Project pick ---
        if awaiting == AWAITING_PROJECT:
            projects = session_data.get("boards_projects") or []
            selected = _pick_project(user_msg, projects)
            if not selected:
                return {
                    **state,
                    "status": "repo_picker",
                    "handled_by": AGENT_NAME,
                    "session_awaiting": AWAITING_PROJECT,
                    "notification_text": (
                        "Please reply with the **project number** or exact name.\n\n"
                        + _format_project_picker(projects, display=display)
                    ),
                }
            return await _fetch_and_list(
                state,
                phone=phone,
                identity=identity,
                display=display,
                project=selected["name"],
            )

        # --- Fresh request: resolve project ---
        configured = (settings.azdo_boards_project or "").strip()
        # Explicit "project X" in message
        m_proj = re.search(
            r"(?:in|for|project)\s+[\"']?([A-Za-z0-9._\- ]{2,80})[\"']?\s*$",
            user_msg,
            re.I,
        )
        if m_proj and not re.search(r"action|work|ticket|board|item", m_proj.group(1), re.I):
            return await _fetch_and_list(
                state,
                phone=phone,
                identity=identity,
                display=display,
                project=m_proj.group(1).strip(),
            )

        projects = await azure_boards.list_org_projects()
        if configured and any(p["name"] == configured for p in projects):
            # Still ask if multiple — unless only one project in org
            if len(projects) <= 1:
                return await _fetch_and_list(
                    state,
                    phone=phone,
                    identity=identity,
                    display=display,
                    project=configured,
                )

        if len(projects) == 1:
            return await _fetch_and_list(
                state,
                phone=phone,
                identity=identity,
                display=display,
                project=projects[0]["name"],
            )

        if not projects:
            # Fall back to configured / demo project name
            fallback = configured or (settings.azdo_demo_project or "").strip()
            if fallback:
                return await _fetch_and_list(
                    state,
                    phone=phone,
                    identity=identity,
                    display=display,
                    project=fallback,
                )
            return {
                **state,
                "status": "failed",
                "handled_by": AGENT_NAME,
                "notification_text": "*Boards Agent* — no Azure DevOps projects found.",
            }

        # Multiple projects → ask
        await save_session(
            phone,
            awaiting=AWAITING_PROJECT,
            data={
                "boards_projects": projects,
                "boards_identity": identity,
                "channel": "teams",
            },
            merge_data=True,
        )
        await _mark_productivity(phone)
        return {
            **state,
            "status": "repo_picker",
            "handled_by": AGENT_NAME,
            "session_awaiting": AWAITING_PROJECT,
            "notification_text": _format_project_picker(projects, display=display),
        }

    except Exception as exc:
        logger.exception("Boards Agent failed")
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                f"*Boards Agent* failed: {exc}\n\n"
                "Confirm Graph **User.Read.All** and AzDO PAT **Work Items (Read)**."
            ),
            "error": str(exc),
        }


async def _fetch_and_list(
    state: AgentState,
    *,
    phone: str,
    identity: dict,
    display: str,
    project: str,
) -> AgentState:
    items = await azure_boards.list_assigned_work_items(
        identity.get("email") or "",
        project=project,
        identity=identity,
    )
    catalog = [
        {
            "id": it["id"],
            "title": it.get("title"),
            "type": it.get("type"),
            "state": it.get("state"),
            "priority": it.get("priority"),
            "url": it.get("url"),
            "project": it.get("project") or project,
        }
        for it in items
    ]
    session_data = dict(state.get("session_data") or {})
    awaiting = AWAITING_TICKET if catalog else AWAITING_PROJECT
    save_data: dict = {
        "boards_catalog": catalog,
        "boards_project": project,
        "boards_identity": identity,
        "channel": "teams",
    }
    if session_data.get("boards_projects"):
        save_data["boards_projects"] = session_data["boards_projects"]
    await save_session(
        phone,
        awaiting=awaiting,
        data=save_data,
        merge_data=True,
    )
    await _mark_productivity(phone)
    note = _format_items(items, project=project, display=display)
    if not catalog and session_data.get("boards_projects"):
        note += "\n\nReply with another **project number**, or **stop** then *my work items*."
    return {
        **state,
        "status": "completed",
        "handled_by": AGENT_NAME,
        "session_awaiting": awaiting,
        "notification_text": note,
    }


async def _start_dev_from_ticket(
    state: AgentState,
    *,
    phone: str,
    work_item_id: int,
    identity: dict,
    session_data: dict,
) -> AgentState:
    """Load work item, list repos for its project, seed repo_wizard (AzDO)."""
    item = await azure_boards.get_work_item(work_item_id)
    project = (
        item.get("project")
        or session_data.get("boards_project")
        or (settings.azdo_boards_project or "").strip()
        or (settings.azdo_demo_project or "").strip()
    )
    if not project:
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                f"Work item **#{work_item_id}** has no project. "
                "Say *my work items* and pick a project first."
            ),
        }

    instruction = azure_boards.build_coding_instruction(item)
    try:
        repos_raw = await azdo_agent.list_repositories(project)
    except Exception as exc:
        logger.exception("Boards→repos list failed")
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                f"Loaded **#{work_item_id}** but could not list repos in *{project}*: {exc}\n\n"
                "Say **check my repos** to continue the classic way."
            ),
        }

    slim = [
        {
            "id": r.get("id"),
            "name": r.get("name"),
            "remoteUrl": r.get("remoteUrl") or r.get("webUrl"),
        }
        for r in repos_raw
    ]
    if not slim:
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                f"**#{work_item_id}** *{item.get('title')}* ready, "
                f"but no repos found in *{project}*.\n\n"
                "Say **check my repos** to pick another source."
            ),
        }

    await save_session(
        phone,
        awaiting="repo",
        provider="azure_devops",
        data={
            "azdo_project": project,
            "repos": slim,
            "pending_code_instruction": instruction,
            "boards_work_item_id": work_item_id,
            "boards_work_item_url": item.get("url") or "",
            "boards_catalog": session_data.get("boards_catalog") or [],
            "channel": "teams",
        },
        merge_data=True,
    )

    lines = [
        f"*Boards → Dev handoff*",
        f"**Work item:** #{work_item_id} — {item.get('title')}",
        f"**Type:** {item.get('type')} · **State:** {item.get('state')}"
        + (
            f" · **Priority:** {item.get('priority')}"
            if item.get("priority") is not None
            else ""
        ),
        f"**Project:** {project}",
    ]
    if item.get("url"):
        lines.append(f"**URL:** {item['url']}")
    lines.extend(
        [
            "",
            "_Coding instruction is ready from the work item._",
            "Pick the **repository** to implement it (same Dev Agent flow as usual):",
            "",
            "| # | Repository |",
            "| :---: | :--- |",
        ]
    )
    for i, r in enumerate(slim, start=1):
        lines.append(f"| {i} | {r.get('name')} |")
    lines.append("")
    lines.append("Reply with a **number**. After you pick, planning starts automatically.")
    lines.append("_Or say **check my repos** to start the classic path instead._")

    return {
        **state,
        "status": "repo_picker",
        "handled_by": AGENT_NAME,
        "intent": "browse_repos",
        "repo_provider": "azure_devops",
        "azdo_project": project,
        "session_awaiting": "repo",
        "notification_text": "\n".join(lines),
    }
