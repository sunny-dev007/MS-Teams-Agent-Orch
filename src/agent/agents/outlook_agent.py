"""Outlook Agent — Teams signed-in user inbox digest (Graph Mail.Read).

Feature: ENABLE_OUTLOOK_AGENT default false. WhatsApp Gmail path unchanged.
"""

from __future__ import annotations

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.channel_identity import is_teams_session
from agent.core.logging import get_logger
from agent.services import outlook_mail
from agent.services.ms_graph import graph_configured

logger = get_logger(__name__)

AGENT_NAME = "outlook_agent"

_DISABLED = (
    "*Outlook Agent* is installed but **disabled** (ENABLE_OUTLOOK_AGENT=false).\n\n"
    "When enabled on Teams: *check my outlook* or *check my emails*.\n"
    "WhatsApp continues to use Gmail (*check my emails*)."
)


def _teams_user_id(state: AgentState) -> str:
    phone = state.get("whatsapp_phone") or ""
    if is_teams_session(phone):
        return str(phone).removeprefix("teams:").strip()
    data = state.get("session_data") or {}
    return str(data.get("teams_user_id") or "").strip()


def _format_digest(messages: list[dict], *, display: str = "") -> str:
    who = f" for **{display}**" if display else ""
    if not messages:
        return (
            f"*Outlook Agent*{who}\n\n"
            "_Inbox is empty (or no messages returned)._\n\n"
            "Try: **my work items** · **check my repos** · **help**"
        )
    lines = [
        f"*Outlook Agent*{who}",
        f"_Showing {len(messages)} recent Inbox message(s)_",
        "",
        "| # | From | Subject | When |",
        "| :---: | :--- | :--- | :--- |",
    ]
    for i, m in enumerate(messages, start=1):
        frm = m.get("from_name") or m.get("from_email") or "?"
        subj = (m.get("subject") or "").replace("|", "/")
        when = (m.get("received") or "")[:16].replace("T", " ")
        unread = "" if m.get("is_read") else "● "
        lines.append(f"| {i} | {frm} | {unread}{subj} | {when} |")
    lines.append("")
    lines.append("**Previews**")
    for i, m in enumerate(messages[:5], start=1):
        prev = (m.get("preview") or "").replace("\n", " ")
        link = m.get("web_link") or ""
        lines.append(f"{i}. {prev[:160]}{'…' if len(prev) > 160 else ''}")
        if link:
            lines.append(f"   {link}")
    lines.append("")
    lines.append("Next: **my work items** · **check my repos** · **help**")
    return "\n".join(lines)


async def read_outlook(state: AgentState) -> AgentState:
    if not settings.enable_outlook_agent:
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
                "*Outlook Agent* is for **Teams signed-in users** only.\n\n"
                "On WhatsApp, use *check my emails* (Gmail)."
            ),
        }

    if not graph_configured():
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Outlook Agent* — Graph credentials missing.\n"
                "See `docs/OUTLOOK_BOARDS_AGENTS.md`."
            ),
        }

    user_id = _teams_user_id(state)
    if not user_id:
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": "*Outlook Agent* — could not resolve Teams user id.",
        }

    try:
        from agent.services import ms_graph

        display = ""
        mailbox_id = user_id
        try:
            profile = await ms_graph.get_user_profile(user_id)
            display = (
                profile.get("displayName")
                or profile.get("mail")
                or profile.get("userPrincipalName")
                or ""
            ).strip()
            # Prefer Entra OID for mail APIs (more reliable than UPN encoding)
            oid = str(profile.get("id") or "").strip()
            if oid:
                mailbox_id = oid
        except Exception:
            logger.exception("Outlook profile lookup failed (continuing with raw id)")

        # Catch stale tokens minted before Application Mail.Read was consented
        roles = await ms_graph.graph_token_roles()
        if roles and "Mail.Read" not in roles:
            ms_graph.clear_app_token_cache()
            roles = await ms_graph.graph_token_roles()
            if "Mail.Read" not in roles:
                return {
                    **state,
                    "status": "failed",
                    "handled_by": AGENT_NAME,
                    "notification_text": (
                        "*Outlook Agent* — Graph app token is missing role **Mail.Read**.\n\n"
                        f"Token roles: `{', '.join(roles) or '(none)'}`\n\n"
                        "On app **Release-Agent-Fabric-Docs** add **Application** "
                        "**Mail.Read**, grant admin consent, then **restart** the App Service "
                        "and retry."
                    ),
                    "error": "token_missing_Mail.Read",
                }

        messages = await outlook_mail.list_user_inbox(mailbox_id)
        note = _format_digest(messages, display=display)
        if phone:
            try:
                from agent.services.workspace_handoff import (
                    WS_PRODUCTIVITY,
                    mark_workspace,
                )

                await mark_workspace(phone, WS_PRODUCTIVITY)
            except Exception:
                logger.exception("Failed marking productivity workspace")
        return {
            **state,
            "status": "completed",
            "handled_by": AGENT_NAME,
            "notification_text": note,
        }
    except Exception as exc:
        logger.exception("Outlook Agent failed")
        detail = str(exc)
        hint = (
            "Confirm Graph **Application** permission **Mail.Read** + admin consent.\n"
            "(Delegated Mail.Read is not enough — this agent uses app-only client credentials.)"
        )
        if "403" in detail or "AccessDenied" in detail or "ErrorAccessDenied" in detail:
            hint = (
                "*403 Forbidden* with Application **Mail.Read** already granted usually means:\n\n"
                "1. **Stale token** — restart App Service `whatsapp-ai-agent-sunny`, wait 1 min, retry\n"
                "2. **Exchange Application Access Policy** is denying this app for your mailbox:\n"
                "   `Connect-ExchangeOnline` then\n"
                "   `Test-ApplicationAccessPolicy -Identity sunny@aienterpriselabs.com "
                "-AppId 0a98eb76-b5ca-4269-9a47-f64456b2f776`\n"
                "   If **Denied**, create/update an Allow policy for your mailbox "
                "(or remove the blocking policy).\n"
                "3. Confirm mailbox has an Exchange Online license\n\n"
                "App id in use: `0a98eb76-b5ca-4269-9a47-f64456b2f776` "
                "(must match the app where you granted Application Mail.Read)."
            )
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": f"*Outlook Agent* failed: {detail}\n\n{hint}",
            "error": detail,
        }
