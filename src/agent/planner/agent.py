"""Planner agent: classify intent and extract slots. No I/O side effects."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.core.persona import build_greeting_reply, build_help_menu
from agent.core.session import get_session
from agent.services.llm import invoke_llm

logger = get_logger(__name__)

AGENT_NAME = "planner"

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "router_classify.txt"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")

_GREETING_RE = re.compile(
    r"^(hi|hello|hey|hola|namaste|yo|good\s+(morning|afternoon|evening))"
    r"[\s!.?,]*$",
    re.IGNORECASE,
)
_HELP_RE = re.compile(r"^(help|menu|\?|commands)[\s!.?]*$", re.IGNORECASE)
_EMAILS_RE = re.compile(
    r"^(emails?|check\s+(my\s+)?(emails?|inbox)|inbox)[\s!.?]*$",
    re.IGNORECASE,
)
_REPOS_RE = re.compile(
    r"^(repos?|repositories|check\s+(my\s+)?repos?|browse\s+repos?)[\s!.?]*$",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(
    r"^(status|task\s+status|my\s+tasks?)[\s!.?]*$",
    re.IGNORECASE,
)
_MEETING_RE = re.compile(
    r"(schedule|set\s*up|setup|book).*(meeting|call|calendar)|meeting.*(schedule|set\s*up|book)",
    re.IGNORECASE,
)
_RELEASE_NOTES_RE = re.compile(
    r"(release\s*notes|write\s+(?:the\s+)?docs?|publish\s+(?:to\s+)?sharepoint|"
    r"documentation\s+agent|create\s+(?:a\s+)?(?:release\s+)?document)",
    re.IGNORECASE,
)
_RUN_QA_RE = re.compile(
    r"(run\s+qa|start\s+qa|qa\s+agent|playwright|test\s+(?:this\s+)?(?:release|deploy))",
    re.IGNORECASE,
)
# Document Knowledge Fabric — must beat coding/repo session like Docs/QA
# Informal phrasing: "list of my document", "show my files", filename search
_LIST_DOCS_RE = re.compile(
    r"(list\s+(?:of\s+)?(?:my\s+)?(?:documents?|docs|files)|"
    r"list\s+(?:sharepoint|onedrive|onenote)|"
    r"(?:show|get|fetch|display|give|pull)\s+(?:me\s+)?(?:all\s+)?(?:my\s+)?(?:the\s+)?"
    r"(?:sharepoint\s+|onedrive\s+|onenote\s+)?(?:documents?|docs)|"
    r"browse\s+(?:documents?|docs|sharepoint)|"
    r"(?:my|all)\s+(?:sharepoint\s+)?(?:documents?|docs)\b|"
    r"documents?\s+from\s+sharepoint|"
    r"list\s+ingested|knowledge\s+base|kb\s+status|doc\s+library)",
    re.IGNORECASE,
)
_SEARCH_DOCS_RE = re.compile(
    r"(?:find|search|locate|look\s+up)\s+(?:my\s+)?(?:documents?|docs|files?)\s+"
    r"(?:named|called|like|for|about|with)?\s*\S+|"
    r"search\s+(?:(?:in|on)\s+)?(?:sharepoint|onedrive|onenote)|"
    r"(?:documents?|docs|files|sharepoint)\s+(?:named|called|like|about|with|for)\s+\S+",
    re.IGNORECASE,
)
_DOC_PAGE_RE = re.compile(
    r"^\s*(?:next(?:\s+page)?|more(?:\s+documents?)?|previous|prev(?:ious)?\s*page|"
    r"page\s+\d+)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_INGEST_DOCS_RE = re.compile(
    r"(ingest\s+(?:\d+|all)|vectorize|index\s+(?:these\s+)?docs?|"
    r"add\s+(?:to\s+)?(?:knowledge|kb)|ingest\s+documents?|"
    r"re-?ingest|ingest\s+stale)",
    re.IGNORECASE,
)
_ASK_DOCS_RE = re.compile(
    r"(ask\s+docs?|ask\s+knowledge|doc\s+q(?:na|&a)?|question\s+on\s+docs?|"
    r"search\s+(?:ingested\s+)?docs?|query\s+(?:the\s+)?(?:knowledge|kb))",
    re.IGNORECASE,
)
_SUMMARIZE_DOCS_RE = re.compile(
    r"(summarize\s+docs?|summarise\s+docs?|doc\s+insights?|"
    r"insights?\s+(?:on|from)\s+docs?|document\s+insights?)",
    re.IGNORECASE,
)
_DATA_ANALYST_RE = re.compile(
    r"(convert\s+(?:this\s+|the\s+)?excel|"
    r"analy[sz]e\s+(?:this\s+|the\s+)?(?:excel|spreadsheet|workbook)|"
    r"excel\s+(?:dashboard|analytics|analyst)|"
    r"advanced\s+excel|"
    r"data\s+analyst(?:\s+agent)?|"
    r"transform\s+(?:this\s+|the\s+)?(?:excel|spreadsheet))",
    re.IGNORECASE,
)
_DOC_PICK_NUMS_RE = re.compile(r"^\s*[\d,\s]+(?:\s*(?:and|&)\s*[\d,\s]+)*\s*$")
# Teams productivity — Outlook + Azure Boards (bypass coding session)
_OUTLOOK_RE = re.compile(
    r"(check\s+(?:my\s+)?outlook|outlook\s+(?:inbox|emails?|mail)|"
    r"my\s+outlook(?:\s+inbox)?)",
    re.IGNORECASE,
)
_BOARDS_RE = re.compile(
    r"(my\s+work\s+items?|my\s+tickets?|my\s+action\s+items?|azure\s+boards?|"
    r"boards?\s+assigned\s+to\s+me|check\s+(?:my\s+)?boards?|"
    r"work\s+items?\s+assigned\s+to\s+me|assigned\s+(?:to\s+)?me\s+(?:on\s+)?boards?|"
    r"fetch\s+(?:my\s+)?(?:action\s+)?items?|list\s+(?:my\s+)?(?:action\s+)?items?)",
    re.IGNORECASE,
)
_BOARDS_TICKET_RE = re.compile(
    r"(?:^|\b)(?:#|ticket\s+|work\s*item\s+|wi\s+|start\s+|work\s+on\s+|implement\s+)"
    r"(\d{1,7})\b|^\s*(\d{1,7})\s*$",
    re.IGNORECASE,
)


def is_simple_greeting(message: str) -> bool:
    return bool(_GREETING_RE.match((message or "").strip()))


async def plan(state: AgentState) -> AgentState:
    """Route Sunny's WhatsApp message to exactly one specialist intent."""
    user_msg = (state.get("user_message") or "").strip()
    phone = state.get("whatsapp_phone", "")

    if not user_msg:
        return {**state, "intent": "general", "error": "No message provided", "planned_by": AGENT_NAME}

    # Release Agent Fabric — take priority over in-progress repo wizard / coding session
    # so "write release notes for PR N" always goes to Docs Agent → SharePoint, not a code PR.
    if _RELEASE_NOTES_RE.search(user_msg):
        if phone:
            try:
                from agent.core.session import save_session

                await save_session(phone, awaiting=None, clear_awaiting=True, merge_data=True)
            except Exception:
                logger.exception("%s failed clearing session for docs intent", AGENT_NAME)
        return {**state, "intent": "publish_release_notes", "planned_by": AGENT_NAME}
    if _RUN_QA_RE.search(user_msg):
        if phone:
            try:
                from agent.core.session import save_session

                await save_session(phone, awaiting=None, clear_awaiting=True, merge_data=True)
            except Exception:
                logger.exception("%s failed clearing session for qa intent", AGENT_NAME)
        return {**state, "intent": "run_qa", "planned_by": AGENT_NAME}

    # Document Knowledge Fabric — priority over coding gates / repo wizard
    async def _clear_for_kb(intent_name: str) -> AgentState:
        handoff = ""
        if phone:
            try:
                from agent.core.session import save_session
                from agent.services.workspace_handoff import (
                    WS_KNOWLEDGE,
                    handoff_note_for,
                )

                handoff = await handoff_note_for(phone, next_workspace=WS_KNOWLEDGE)
                # clear coding awaiting; merge keeps data.doc_catalog for ingest picks
                await save_session(phone, awaiting=None, clear_awaiting=True, merge_data=True)
            except Exception:
                logger.exception("%s failed clearing session for %s", AGENT_NAME, intent_name)
        out = {**state, "intent": intent_name, "planned_by": AGENT_NAME}
        if handoff:
            out["handoff_note"] = handoff
        return out

    # Teams chat attachments — upload to SharePoint then ingest (beats list/ingest without files)
    from agent.config import settings as _settings
    from agent.services import doc_upload as _doc_upload

    incoming_atts: list = list(state.get("attachments") or [])
    pending_atts: list = []
    if phone:
        try:
            _sess = await get_session(phone)
            pending_raw = list((_sess.get("data") or {}).get("pending_attachments") or [])
            pending_atts = _doc_upload.attachments_from_session(pending_raw)
        except Exception:
            logger.exception("%s failed reading pending attachments", AGENT_NAME)

    if incoming_atts and phone:
        try:
            from agent.core.session import save_session

            await save_session(
                phone,
                data={
                    "pending_attachments": _doc_upload.attachments_for_session(incoming_atts),
                },
                merge_data=True,
            )
        except Exception:
            logger.exception("%s failed saving pending attachments", AGENT_NAME)

    effective_atts = incoming_atts or pending_atts
    if effective_atts and _settings.enable_doc_knowledge:
        wants_summarize = bool(
            _SUMMARIZE_DOCS_RE.search(user_msg)
            or re.search(r"\bsummar(?:ize|ise)\b", user_msg, re.I)
        )
        wants_ingest = bool(
            _INGEST_DOCS_RE.search(user_msg) or _doc_upload.wants_upload_action(user_msg)
        )
        if wants_ingest or wants_summarize:
            out = await _clear_for_kb("upload_ingest_docs")
            out["attachments"] = effective_atts
            out["upload_summarize"] = wants_summarize
            return out
        if incoming_atts:
            names = ", ".join(
                (a.get("filename") or a.get("name") or "file") for a in incoming_atts[:5]
            )
            return {
                **state,
                "intent": "general",
                "notification_text": (
                    f"*Doc Upload Agent* — received **{len(incoming_atts)}** file(s): {names}\n\n"
                    "Say **ingest this** to upload to SharePoint and vectorize, or "
                    "**summarize this** for upload + ingest + executive summary."
                ),
                "planned_by": AGENT_NAME,
            }

    if (
        _settings.enable_doc_knowledge
        and _doc_upload.message_expects_teams_attachment(user_msg)
        and not effective_atts
    ):
        return {
            **state,
            "intent": "general",
            "notification_text": _doc_upload.attachment_not_received_reply(),
            "planned_by": AGENT_NAME,
        }

    if _LIST_DOCS_RE.search(user_msg):
        return await _clear_for_kb("list_docs")
    if _SEARCH_DOCS_RE.search(user_msg):
        return await _clear_for_kb("list_docs")
    if _INGEST_DOCS_RE.search(user_msg):
        return await _clear_for_kb("ingest_docs")
    if _ASK_DOCS_RE.search(user_msg):
        return await _clear_for_kb("ask_docs")
    if _SUMMARIZE_DOCS_RE.search(user_msg):
        return await _clear_for_kb("summarize_docs")
    if _DATA_ANALYST_RE.search(user_msg):
        return await _clear_for_kb("analyze_excel")

    # Outlook / Boards — Teams productivity lane (priority over coding session)
    async def _clear_for_productivity(intent_name: str) -> AgentState:
        handoff = ""
        if phone:
            try:
                from agent.core.session import save_session
                from agent.services.workspace_handoff import (
                    WS_PRODUCTIVITY,
                    handoff_note_for,
                )

                handoff = await handoff_note_for(phone, next_workspace=WS_PRODUCTIVITY)
                await save_session(phone, awaiting=None, clear_awaiting=True, merge_data=True)
            except Exception:
                logger.exception("%s failed clearing session for %s", AGENT_NAME, intent_name)
        out = {**state, "intent": intent_name, "planned_by": AGENT_NAME}
        if handoff:
            out["handoff_note"] = handoff
        return out

    if _OUTLOOK_RE.search(user_msg):
        return await _clear_for_productivity("check_outlook")
    if _BOARDS_RE.search(user_msg):
        return await _clear_for_productivity("list_boards")

    # Bare number picks while awaiting doc_pick → ingest
    if phone:
        try:
            session = await get_session(phone)
            awaiting_early = session.get("awaiting")
            if awaiting_early == "doc_pick" and _DOC_PAGE_RE.match(user_msg):
                return {
                    **state,
                    "intent": "list_docs",
                    "session_awaiting": "doc_pick",
                    "session_data": session.get("data") or {},
                    "planned_by": AGENT_NAME,
                }
            if awaiting_early == "doc_pick" and _DOC_PICK_NUMS_RE.match(user_msg):
                return {**state, "intent": "ingest_docs", "planned_by": AGENT_NAME}
            # Boards multi-step (project / ticket) — do not steal coding gates
            if awaiting_early == "boards_project_pick":
                return {
                    **state,
                    "intent": "list_boards",
                    "session_awaiting": "boards_project_pick",
                    "session_data": session.get("data") or {},
                    "planned_by": AGENT_NAME,
                }
            if awaiting_early == "boards_ticket_pick" and (
                _BOARDS_TICKET_RE.search(user_msg) or user_msg.strip().isdigit()
            ):
                return {
                    **state,
                    "intent": "boards_start_dev",
                    "session_awaiting": "boards_ticket_pick",
                    "session_data": session.get("data") or {},
                    "planned_by": AGENT_NAME,
                }
            if awaiting_early in ("boards_project_pick", "boards_ticket_pick"):
                return {
                    **state,
                    "intent": "list_boards",
                    "session_awaiting": awaiting_early,
                    "session_data": session.get("data") or {},
                    "planned_by": AGENT_NAME,
                }
            # Seeded AzDO repo pick after Boards ticket → continue repo_wizard
            if (
                awaiting_early == "repo"
                and (session.get("provider") or "").lower() == "azure_devops"
                and (session.get("data") or {}).get("pending_code_instruction")
            ):
                return {
                    **state,
                    "intent": "browse_repos",
                    "session_awaiting": "repo",
                    "repo_provider": "azure_devops",
                    "session_data": session.get("data") or {},
                    "planned_by": AGENT_NAME,
                }
        except Exception:
            logger.exception("%s failed reading boards/doc_pick session", AGENT_NAME)

    if phone:
        session = await get_session(phone)
        awaiting = session.get("awaiting")
        if awaiting == "approval":
            # Bare approve/reject should be handled in the WhatsApp webhook.
            lower = user_msg.lower().strip()
            tid = (session.get("data") or {}).get("pending_task_id") or "unknown"
            if lower.startswith("approve") or lower.startswith("reject"):
                return {
                    **state,
                    "intent": "general",
                    "notification_text": (
                        f"Please reply *APPROVE {tid}* or *REJECT {tid}* "
                        "(or just *Approve* / *Reject*)."
                    ),
                    "planned_by": AGENT_NAME,
                }
            # fall through to CI check below
        elif awaiting == "plan_approval":
            from agent.workflow.resume_context import format_gate_hint

            return {
                **state,
                "intent": "general",
                "notification_text": format_gate_hint(awaiting, session.get("data")),
                "planned_by": AGENT_NAME,
            }
        elif awaiting == "pr_review_mode":
            from agent.workflow.resume_context import format_gate_hint

            return {
                **state,
                "intent": "general",
                "notification_text": format_gate_hint(awaiting, session.get("data")),
                "planned_by": AGENT_NAME,
            }
        elif awaiting == "manual_pr_review":
            from agent.workflow.resume_context import format_gate_hint

            return {
                **state,
                "intent": "general",
                "notification_text": format_gate_hint(awaiting, session.get("data")),
                "planned_by": AGENT_NAME,
            }

        if awaiting == "approval":
            try:
                from agent.services.ci_gate import (
                    check_ci_busy_for_context,
                    context_from_session_data,
                )

                busy = await check_ci_busy_for_context(
                    context_from_session_data(
                        {
                            **(session.get("data") or {}),
                            "repo_provider": session.get("provider")
                            or (session.get("data") or {}).get("repo_provider", ""),
                        }
                    )
                )
                if busy.busy:
                    return {
                        **state,
                        "intent": "general",
                        "notification_text": busy.wait_message()
                        + f"\n\nPending approval task: `{tid}` — try *Approve* after CI finishes.",
                        "planned_by": AGENT_NAME,
                    }
            except Exception:
                logger.exception("%s CI check during approval wait failed", AGENT_NAME)

            return {
                **state,
                "intent": "general",
                "notification_text": (
                    f"You have a coding change waiting.\n"
                    f"Reply *APPROVE {tid}* "
                    f"or *REJECT …* to continue."
                ),
                "planned_by": AGENT_NAME,
            }
        if awaiting:
            logger.info("%s: continue awaiting=%s task=%s", AGENT_NAME, awaiting, state.get("task_id"))
            updates: dict = {
                "session_awaiting": awaiting,
                "repo_provider": session.get("provider") or state.get("repo_provider", ""),
                "session_data": session.get("data") or {},
                "planned_by": AGENT_NAME,
            }
            if str(awaiting).startswith("meeting"):
                updates["intent"] = "schedule_meeting"
            else:
                updates["intent"] = "browse_repos"
            return {**state, **updates}

    if is_simple_greeting(user_msg):
        session_snap = None
        if phone:
            try:
                session_snap = await get_session(phone)
            except Exception:
                session_snap = None
        from agent.core.channel_identity import is_teams_session

        channel = "teams" if is_teams_session(phone) else "whatsapp"
        return {
            **state,
            "intent": "general",
            "notification_text": build_greeting_reply(
                session=session_snap, channel=channel
            ),
            "planned_by": AGENT_NAME,
        }

    if _HELP_RE.match(user_msg):
        from agent.core.channel_identity import is_teams_session

        channel = "teams" if is_teams_session(phone) else "whatsapp"
        return {
            **state,
            "intent": "general",
            "notification_text": build_help_menu(channel=channel),
            "planned_by": AGENT_NAME,
        }

    if user_msg.strip() == "1" or _EMAILS_RE.match(user_msg):
        # Teams + Outlook flag → Outlook for signed-in user; WhatsApp stays Gmail
        from agent.config import settings
        from agent.core.channel_identity import is_teams_session

        if (
            settings.enable_outlook_agent
            and is_teams_session(phone)
            and _EMAILS_RE.match(user_msg)
        ):
            return await _clear_for_productivity("check_outlook")
        # Bare "1" on Teams with Outlook on → still Outlook (menu shortcut)
        if settings.enable_outlook_agent and is_teams_session(phone) and user_msg.strip() == "1":
            return await _clear_for_productivity("check_outlook")
        return {**state, "intent": "check_email", "planned_by": AGENT_NAME}
    if user_msg.strip() == "2" or _MEETING_RE.search(user_msg):
        return {**state, "intent": "schedule_meeting", "planned_by": AGENT_NAME}
    if user_msg.strip() == "3" or _REPOS_RE.match(user_msg):
        out = {**state, "intent": "browse_repos", "planned_by": AGENT_NAME}
        if phone:
            try:
                from agent.services.workspace_handoff import (
                    WS_DEV,
                    handoff_note_for,
                    mark_workspace,
                )

                note = await handoff_note_for(phone, next_workspace=WS_DEV)
                await mark_workspace(phone, WS_DEV)
                if note:
                    out["handoff_note"] = note
            except Exception:
                logger.exception("%s handoff to Dev failed", AGENT_NAME)
        return out
    if user_msg.strip() == "5" or _STATUS_RE.match(user_msg):
        return {**state, "intent": "task_status", "planned_by": AGENT_NAME}

    t0 = time.perf_counter()
    try:
        response = await invoke_llm(
            [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=user_msg),
            ],
            temperature=0,
            role="default",
        )
    except Exception:
        logger.exception("%s LLM classify failed task=%s — using keyword fallback", AGENT_NAME, state.get("task_id"))
        return {**state, "intent": "general", "planned_by": AGENT_NAME}

    logger.info("%s LLM classify %.2fs task=%s", AGENT_NAME, time.perf_counter() - t0, state.get("task_id"))

    try:
        result = json.loads(response.content)
    except json.JSONDecodeError:
        raw = response.content or ""
        start, end = raw.find("{"), raw.rfind("}") + 1
        try:
            result = json.loads(raw[start:end]) if start != -1 and end > 0 else {}
        except json.JSONDecodeError:
            logger.warning("%s parse failed: %s", AGENT_NAME, response.content)
            return {**state, "intent": "general", "planned_by": AGENT_NAME}

    intent = result.get("intent", "general")
    updates: dict = {"intent": intent, "planned_by": AGENT_NAME}

    if result.get("repo_url"):
        updates["repo_url"] = result["repo_url"]
    if intent in ("approval_yes", "approval_no"):
        # Never start an empty deploy from phrases like "please go ahead"
        # unless WhatsApp session actually has a pending gate.
        pending = await get_session(phone) if phone else {"awaiting": None, "data": {}}
        awaiting = pending.get("awaiting")
        if awaiting == "plan_approval":
            tid = result.get("task_id") or (pending.get("data") or {}).get("pending_task_id")
            return {
                **state,
                "intent": "general",
                "notification_text": (
                    "You still have an *implementation plan* waiting.\n"
                    f"Reply *PROCEED {tid or '<task_id>'}* to start development, "
                    f"or *REJECT {tid or '<task_id>'}* to cancel."
                ),
                "planned_by": AGENT_NAME,
            }
        if awaiting != "approval":
            return {
                **state,
                "intent": "general",
                "notification_text": (
                    "I don't have a pending coding change waiting for approval.\n\n"
                    "• After a *plan*, reply *PROCEED <task_id>*\n"
                    "• After review, reply *APPROVE <task_id>* to deploy\n"
                    "• Or say *check my repos* to start a new change"
                ),
                "planned_by": AGENT_NAME,
            }
        updates["approval_status"] = "approved" if intent == "approval_yes" else "rejected"
        tid = result.get("task_id") or (pending.get("data") or {}).get("pending_task_id")
        if tid:
            updates["task_id"] = tid
    if intent == "send_email":
        for key in ("email_to", "email_subject", "email_body"):
            if result.get(key):
                updates[key] = result[key]
    if intent == "schedule_meeting":
        for key in ("meeting_title", "meeting_when", "meeting_attendees"):
            if result.get(key):
                updates[key] = result[key]

    logger.info("%s -> intent=%s task=%s", AGENT_NAME, intent, state.get("task_id"))
    out = {**state, **updates}
    if phone and intent in ("browse_repos", "code_change", "bug_fix"):
        try:
            from agent.services.workspace_handoff import WS_DEV, handoff_note_for, mark_workspace

            note = await handoff_note_for(phone, next_workspace=WS_DEV)
            await mark_workspace(phone, WS_DEV)
            if note:
                out["handoff_note"] = note
        except Exception:
            logger.exception("%s Dev handoff failed", AGENT_NAME)
    return out


# LangGraph node alias
route_input = plan
