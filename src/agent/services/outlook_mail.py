"""Outlook mail via Microsoft Graph — Teams signed-in user mailbox only.

Feature: ENABLE_OUTLOOK_AGENT (default false).
Uses app-only Mail.Read against /users/{oid}/… — never Gmail OAuth.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from agent.config import settings
from agent.core.logging import get_logger
from agent.services import ms_graph

logger = get_logger(__name__)


def outlook_configured() -> bool:
    return ms_graph.graph_configured()


async def list_user_inbox(
    user_id: str,
    *,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """List recent Inbox messages for one Entra user (OID or UPN)."""
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required for Outlook inbox")
    limit = int(top if top is not None else settings.outlook_mail_top or 10)
    limit = max(1, min(limit, 25))

    path = (
        f"/users/{quote(uid)}/mailFolders/Inbox/messages"
        f"?$select=id,subject,from,receivedDateTime,bodyPreview,isRead,webLink"
        f"&$orderby=receivedDateTime desc"
        f"&$top={limit}"
    )
    body = await ms_graph.graph_request("GET", path)
    rows = body.get("value") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        frm = row.get("from") or {}
        email_addr = (frm.get("emailAddress") or {}) if isinstance(frm, dict) else {}
        out.append(
            {
                "id": row.get("id") or "",
                "subject": (row.get("subject") or "(no subject)").strip(),
                "from_name": (email_addr.get("name") or "").strip(),
                "from_email": (email_addr.get("address") or "").strip(),
                "received": row.get("receivedDateTime") or "",
                "preview": (row.get("bodyPreview") or "").strip()[:280],
                "is_read": bool(row.get("isRead")),
                "web_link": row.get("webLink") or "",
            }
        )
    logger.info("Outlook inbox user=%s count=%s", uid[:12], len(out))
    return out
