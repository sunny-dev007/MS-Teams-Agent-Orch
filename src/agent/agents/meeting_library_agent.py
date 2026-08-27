"""Meeting Library Agent — list and select Teams meeting transcripts.

Feature: Meeting Intelligence Fabric. ENABLE_MEETING_INTELLIGENCE default false.
Does NOT handle calendar scheduling (see agents/meeting.py).
"""

from __future__ import annotations

import re

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.agent_availability import meeting_intelligence_offline
from agent.core.channel_identity import is_teams_session
from agent.core.logging import get_logger
from agent.core.session import get_session, save_session
from agent.services import meeting_transcripts, ms_graph

logger = get_logger(__name__)

AGENT_NAME = "meeting_library_agent"

_DISABLED = meeting_intelligence_offline()

_PAGE_NAV_RE = re.compile(
    r"^\s*(next(?:\s+page)?|more(?:\s+meetings?)?|previous|"
    r"prev(?:ious)?\s*page|page\s+\d+)\s*$",
    re.I,
)
_PICK_RE = re.compile(
    r"(?:select\s+meetings?\s+|meetings?\s+|pick\s+meetings?\s+)?"
    r"([\d,\s]+(?:\s*(?:and|&)\s*[\d,\s]+)*)",
    re.I,
)
_NUMS_RE = re.compile(r"\b(\d+)\b")


def _format_catalog_page(
    catalog: list[dict],
    *,
    page: int,
    page_size: int,
    teams: bool = True,
) -> str:
    total = len(catalog)
    if total == 0:
        return (
            "_No meeting transcripts found._\n\n"
            "Teams saves `.vtt` files under SharePoint `Recordings/` or `meeting transcript/`.\n"
            "Check Graph permissions and `MEETING_TRANSCRIPT_FOLDERS`."
        )
    start = page * page_size
    chunk = catalog[start : start + page_size]
    page_count = max(1, (total + page_size - 1) // page_size)
    head = (
        f"Found **{total}** meeting transcript(s) — showing **{start + 1}–{start + len(chunk)}** "
        f"(page **{page + 1}** of **{page_count}**, {page_size} per page)."
    )
    lines = [head, ""]
    if teams:
        lines += [
            "| # | Date | Client | Agenda | Duration | File |",
            "| :---: | :--- | :--- | :--- | :---: | :--- |",
        ]
        for row in chunk:
            title = (row.get("title") or "untitled").replace("|", "/")
            url = (row.get("web_url") or "").strip()
            file_cell = f"[{title}]({url})" if url else f"**{title}**"
            lines.append(
                f"| {row.get('pick')} | {meeting_transcripts.format_meeting_date(row.get('meeting_date') or '')} | "
                f"{(row.get('client_name') or 'Unknown').replace('|', '/')} | "
                f"{(row.get('agenda_summary') or '—').replace('|', '/')} | "
                f"{meeting_transcripts.format_duration(row.get('duration_minutes'))} | {file_cell} |"
            )
    else:
        for row in chunk:
            lines.append(
                f"{row.get('pick')}. *{row.get('title')}* — "
                f"{row.get('client_name') or 'Unknown'} · "
                f"{row.get('agenda_summary') or '—'}"
            )
    return "\n".join(lines)


def _parse_picks(message: str, catalog: list[dict]) -> list[dict]:
    msg = (message or "").strip()
    nums: set[int] = set()
    if _NUMS_RE.fullmatch(re.sub(r"\s*(and|&)\s*", ",", msg, flags=re.I).replace(" ", "")):
        nums = {int(n) for n in _NUMS_RE.findall(msg)}
    else:
        m = _PICK_RE.search(msg)
        if m:
            nums = {int(n) for n in _NUMS_RE.findall(m.group(1))}
    if not nums:
        return []
    return [row for row in catalog if int(row.get("pick") or 0) in nums]


def _selection_summary(selected: list[dict]) -> str:
    bits = []
    for row in selected[:6]:
        bits.append(
            f"**{row.get('client_name') or 'Unknown'}** — "
            f"{(row.get('agenda_summary') or row.get('title') or '')[:60]}"
        )
    extra = len(selected) - 6
    tail = f" (+{extra} more)" if extra > 0 else ""
    return ", ".join(bits) + tail


async def run_meeting_library(state: AgentState) -> AgentState:
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
            "notification_text": (
                "*Meeting Intelligence* — Graph credentials missing.\n"
                "See `docs/MEETING_INTELLIGENCE_FABRIC.md`."
            ),
        }

    raw_msg = state.get("user_message") or ""
    phone = state.get("whatsapp_phone") or ""
    teams = is_teams_session(phone)
    page_size = max(5, min(int(settings.meeting_list_page_size or 10), 25))
    intent = (state.get("intent") or "").lower()

    session_data: dict = {}
    if phone:
        try:
            session_data = (await get_session(phone)).get("data") or {}
        except Exception:
            session_data = {}

    catalog = list(session_data.get("meeting_catalog") or [])
    current_page = int(session_data.get("meeting_page") or 0)

    # Selection path
    if intent == "select_meetings":
        if not catalog:
            return {
                **state,
                "status": "failed",
                "handled_by": AGENT_NAME,
                "notification_text": (
                    "*Meeting Intelligence* — no meeting list in session.\n"
                    "Say *list my recent meetings* first."
                ),
            }
        selected = _parse_picks(raw_msg, catalog)
        if not selected:
            return {
                **state,
                "status": "failed",
                "handled_by": AGENT_NAME,
                "notification_text": (
                    "*Meeting Intelligence* — could not match selection.\n"
                    "Use numbers from the **#** column, e.g. *select meetings 1,3*."
                ),
            }
        if phone:
            try:
                await save_session(
                    phone,
                    awaiting="meeting_pick",
                    data={"meeting_selected": selected},
                    merge_data=True,
                )
            except Exception:
                logger.exception("Failed saving meeting_selected")
        note = (
            f"*Selected {len(selected)} meeting(s):* {_selection_summary(selected)}\n\n"
            "**Next:** *make a plan* · *create implementation plan* · "
            "*email the plan to alice@co.com* · *create devops board from plan*"
        )
        return {
            **state,
            "status": "completed",
            "handled_by": AGENT_NAME,
            "notification_text": note,
            "meeting_selected": selected,
            "session_awaiting": "meeting_pick",
        }

    # List / refresh
    is_page_nav = bool(_PAGE_NAV_RE.match(raw_msg.strip()))
    if is_page_nav and catalog:
        from agent.services import graph_docs

        page_count = max(1, (len(catalog) + page_size - 1) // page_size)
        current_page = graph_docs.parse_library_page(raw_msg, current_page, page_count)
    else:
        try:
            catalog = await meeting_transcripts.build_meeting_catalog(owner_session=phone or None)
        except Exception as exc:
            logger.exception("Meeting catalog list failed")
            return {
                **state,
                "status": "failed",
                "handled_by": AGENT_NAME,
                "notification_text": f"*Meeting Intelligence* failed listing: {exc}",
                "error": str(exc),
            }
        current_page = 0

    page_count = max(1, (len(catalog) + page_size - 1) // page_size)
    current_page = max(0, min(current_page, page_count - 1))

    if phone:
        try:
            from agent.services.workspace_handoff import WS_MEETING, mark_workspace

            await mark_workspace(phone, WS_MEETING)
            await save_session(
                phone,
                awaiting="meeting_pick",
                data={
                    "meeting_catalog": catalog,
                    "meeting_page": current_page,
                    "doc_catalog": [],
                    "doc_page": 0,
                    "channel": "teams" if teams else "whatsapp",
                },
                merge_data=True,
            )
        except Exception:
            logger.exception("Failed saving meeting_catalog session")

    body = _format_catalog_page(catalog, page=current_page, page_size=page_size, teams=teams)
    nav = []
    if current_page + 1 < page_count:
        nav.append(f"*next page* (page {current_page + 2})")
    if current_page > 0:
        nav.append("*previous page*")
    nav.append("*select meetings 1,3*")
    nav.append("*make a plan*")
    note = (
        "*Meeting Intelligence* — recent transcripts\n\n"
        f"{body}\n\n"
        f"**Next:** {' · '.join(nav)}"
    )
    return {
        **state,
        "status": "completed",
        "handled_by": AGENT_NAME,
        "notification_text": note,
        "session_awaiting": "meeting_pick",
    }
