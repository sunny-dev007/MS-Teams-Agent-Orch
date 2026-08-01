from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
import base64

from googleapiclient.discovery import build

from agent.config import settings
from agent.core.logging import get_logger
from agent.core.oauth import get_google_credentials

logger = get_logger(__name__)


def _calendar_service():
    creds = get_google_credentials()
    return build("calendar", "v3", credentials=creds)


def create_event(
    title: str,
    start: datetime,
    end: datetime,
    attendees: list[str] | None = None,
    description: str = "",
    timezone_name: str = "Asia/Kolkata",
) -> dict:
    service = _calendar_service()
    body = {
        "summary": title,
        "description": description or "Scheduled by Sunny's Personal AI Agent",
        "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
        "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
        "attendees": [{"email": e} for e in (attendees or []) if e],
        "conferenceData": {
            "createRequest": {
                "requestId": f"sunny-agent-{int(start.timestamp())}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }
    event = (
        service.events()
        .insert(
            calendarId="primary",
            body=body,
            conferenceDataVersion=1,
            sendUpdates="all" if attendees else "none",
        )
        .execute()
    )
    logger.info("Created calendar event %s", event.get("id"))
    return event


def parse_when_heuristic(text: str) -> tuple[datetime, datetime] | None:
    """Very light natural-language when parser for common WhatsApp phrases."""
    import re

    now = datetime.now(timezone.utc)
    local_now = now.astimezone()
    text_l = (text or "").lower()

    # tomorrow HH:MM or tomorrow Hpm
    m = re.search(
        r"tomorrow\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        text_l,
    )
    day = None
    hour = None
    minute = 0
    if m:
        day = (local_now + timedelta(days=1)).date()
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
    else:
        m2 = re.search(r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text_l)
        if m2:
            day = local_now.date()
            hour = int(m2.group(1))
            minute = int(m2.group(2) or 0)
            ampm = m2.group(3)
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0

    if day is None or hour is None:
        return None

    start = datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=local_now.tzinfo
    )
    end = start + timedelta(hours=1)
    return start, end


def send_calendar_invite_email(
    to_emails: list[str],
    title: str,
    start: datetime,
    end: datetime,
    description: str = "",
) -> dict:
    """Fallback when Calendar API fails: email an invite-style message via Gmail."""
    from agent.core.oauth import get_gmail_credentials
    from googleapiclient.discovery import build as gbuild

    creds = get_gmail_credentials()
    service = gbuild("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()
    sender = profile.get("emailAddress", "")
    to = ", ".join(to_emails) if to_emails else sender

    body = (
        f"Meeting: {title}\n"
        f"When: {start.isoformat()} → {end.isoformat()}\n"
        f"{description}\n\n"
        f"— Sunny's Personal AI Agent"
    )
    msg = MIMEMultipart("alternative")
    msg["to"] = to
    msg["from"] = formataddr((settings.sender_display_name, sender))
    msg["subject"] = f"Meeting invite: {title}"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()
