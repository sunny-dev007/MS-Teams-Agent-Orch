"""Channel session identity — WhatsApp phones vs Teams Copilot users."""

from __future__ import annotations

TEAMS_PREFIX = "teams:"


def is_teams_session(session_id: str | None) -> bool:
    return str(session_id or "").startswith(TEAMS_PREFIX)


def teams_session_id(user_id: str) -> str:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("Teams user_id is required")
    if uid.startswith(TEAMS_PREFIX):
        return uid
    return f"{TEAMS_PREFIX}{uid}"


def channel_name_for_session(session_id: str | None) -> str:
    return "teams" if is_teams_session(session_id) else "whatsapp"
