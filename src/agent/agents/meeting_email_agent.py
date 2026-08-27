"""Meeting Email Agent — send published plans via Gmail or Graph sendMail."""

from __future__ import annotations

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.agent_availability import meeting_intelligence_offline
from agent.core.channel_identity import is_teams_session
from agent.core.logging import get_logger
from agent.core.session import get_session, save_session
from agent.services import meeting_mail

logger = get_logger(__name__)

AGENT_NAME = "meeting_email_agent"

_DISABLED = meeting_intelligence_offline(agent_label="Meeting Email Agent")


async def run_meeting_email(state: AgentState) -> AgentState:
    if not settings.enable_meeting_intelligence:
        return {
            **state,
            "status": "skipped",
            "handled_by": AGENT_NAME,
            "notification_text": _DISABLED,
        }

    msg = state.get("user_message") or ""
    phone = state.get("whatsapp_phone") or ""

    session_data: dict = {}
    awaiting = ""
    if phone:
        try:
            sess = await get_session(phone)
            session_data = sess.get("data") or {}
            awaiting = sess.get("awaiting") or ""
        except Exception:
            session_data = {}

    last_plan = session_data.get("last_plan") or {}
    plan_json = last_plan.get("plan_json") or {}
    published = last_plan.get("published") or last_plan

    if not last_plan.get("md_url") and not last_plan.get("docx_url"):
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Meeting Email Agent* — no published plan in session.\n"
                "Run *make a plan* first."
            ),
        }

    recipients = meeting_mail.parse_recipients(msg)
    if not recipients:
        if phone:
            try:
                await save_session(phone, awaiting="meeting_email_to", merge_data=True)
            except Exception:
                logger.exception("Failed setting meeting_email_to awaiting")
        return {
            **state,
            "status": "completed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Meeting Email Agent* — who should receive the plan?\n"
                "Reply with addresses, e.g. *alice@co.com, bob@co.com*"
            ),
            "session_awaiting": "meeting_email_to",
        }

    teams_uid = ""
    if phone and is_teams_session(phone):
        teams_uid = str(phone).removeprefix("teams:").strip()

    subject = f"Meeting plan: {last_plan.get('title') or 'Implementation plan'}"
    body_html = meeting_mail.build_plan_email_html(
        plan=plan_json,
        published=published,
        executive_summary=(plan_json.get("executive_summary") or ""),
    )

    try:
        result = await meeting_mail.send_plan_email(
            recipients=recipients,
            subject=subject,
            body_html=body_html,
            teams_user_id=teams_uid,
        )
    except Exception as exc:
        logger.exception("Meeting plan email failed")
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": f"*Meeting Email Agent* failed: {exc}",
            "error": str(exc),
        }

    if phone and awaiting == "meeting_email_to":
        try:
            await save_session(phone, awaiting="meeting_pick", merge_data=True)
        except Exception:
            logger.exception("Failed clearing meeting_email_to awaiting")

    sent = result.get("recipients") or recipients
    note = (
        f"*Plan emailed* to **{len(sent)}** recipient(s): "
        f"{', '.join(sent)}\n\n"
        "**Next:** *create devops board from plan* · *my work items* · *check my repos*"
    )
    return {
        **state,
        "status": "completed",
        "handled_by": AGENT_NAME,
        "notification_text": note,
    }
