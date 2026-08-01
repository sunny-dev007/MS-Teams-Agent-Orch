import re

from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.core.session import clear_session, get_session, save_session
from agent.services.calendar import (
    create_event,
    parse_when_heuristic,
    send_calendar_invite_email,
)

logger = get_logger(__name__)


def _extract_emails(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text or "")


async def schedule_meeting(state: AgentState) -> AgentState:
    phone = state.get("whatsapp_phone", "")
    user_msg = state.get("user_message", "")
    session = await get_session(phone) if phone else {"data": {}, "awaiting": None}
    data = dict(session.get("data") or {})

    title = state.get("meeting_title") or data.get("meeting_title") or ""
    when = state.get("meeting_when") or data.get("meeting_when") or ""
    attendees_raw = state.get("meeting_attendees") or data.get("meeting_attendees") or ""

    # Merge free-text message into fields
    emails = _extract_emails(user_msg) or _extract_emails(attendees_raw)
    if emails:
        attendees_raw = ",".join(emails)

    if not title:
        # crude title: strip schedule verbs
        cleaned = re.sub(
            r"(?i)\b(schedule|set\s*up|setup|book|a|meeting|call|with|tomorrow|today|at|\d{1,2}:?\d{0,2}\s*(am|pm)?)\b",
            " ",
            user_msg,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
        if len(cleaned) > 3 and "@" not in cleaned:
            title = cleaned[:80]

    if not when:
        when = user_msg

    parsed = parse_when_heuristic(when) or parse_when_heuristic(user_msg)

    awaiting = session.get("awaiting")
    if awaiting == "meeting_title" and user_msg:
        title = user_msg.strip()[:80]
    if awaiting == "meeting_when" and user_msg:
        when = user_msg
        parsed = parse_when_heuristic(user_msg)
    if awaiting == "meeting_attendees" and user_msg:
        emails = _extract_emails(user_msg)
        attendees_raw = ",".join(emails) if emails else user_msg

    data.update(
        {
            "meeting_title": title,
            "meeting_when": when,
            "meeting_attendees": attendees_raw,
        }
    )

    if not title:
        await save_session(phone, awaiting="meeting_title", data=data)
        return {
            **state,
            "status": "meeting_scheduled",
            "notification_text": "What should I title this meeting?",
            "meeting_title": title,
        }

    if not parsed:
        await save_session(phone, awaiting="meeting_when", data=data)
        return {
            **state,
            "status": "meeting_scheduled",
            "notification_text": (
                f"Got title: *{title}*\n\n"
                "When should it be? Example: `tomorrow 3pm` or `today 5:30pm`"
            ),
            "meeting_title": title,
        }

    start, end = parsed
    attendees = [e.strip() for e in attendees_raw.split(",") if e.strip() and "@" in e]

    if awaiting == "meeting_attendees" and not attendees and user_msg.lower() not in (
        "none",
        "skip",
        "no",
    ):
        await save_session(phone, awaiting="meeting_attendees", data=data)
        return {
            **state,
            "status": "meeting_scheduled",
            "notification_text": "Please send attendee emails (comma-separated), or reply *skip*.",
        }

    if not attendees and awaiting != "meeting_attendees" and "skip" not in user_msg.lower():
        # Ask once for attendees
        if not state.get("meeting_attendees") and not _extract_emails(user_msg):
            await save_session(phone, awaiting="meeting_attendees", data=data)
            return {
                **state,
                "status": "meeting_scheduled",
                "notification_text": (
                    f"*{title}* at {start.strftime('%d %b %Y %I:%M %p')}\n\n"
                    "Any attendees (emails)? Reply with emails or *skip*."
                ),
                "meeting_title": title,
            }

    try:
        event = create_event(
            title=title,
            start=start,
            end=end,
            attendees=attendees,
            description=f"Created via WhatsApp for Sunny. Request: {user_msg[:200]}",
        )
        link = event.get("htmlLink", "")
        hangout = event.get("hangoutLink") or ""
        conf = ""
        if not hangout:
            for ep in event.get("conferenceData", {}).get("entryPoints", []) or []:
                if ep.get("entryPointType") == "video":
                    hangout = ep.get("uri", "")
                    break
        conf = hangout or "N/A"
        await clear_session(phone)
        detail = (
            f"*Title:* {title}\n"
            f"*When:* {start.strftime('%d %b %Y %I:%M %p')} – {end.strftime('%I:%M %p')}\n"
            f"*Attendees:* {', '.join(attendees) if attendees else 'None'}\n"
            f"*Meet:* {conf}\n"
            f"*Calendar:* {link or 'created'}"
        )
        return {
            **state,
            "status": "meeting_scheduled",
            "meeting_title": title,
            "meeting_link": conf,
            "notification_text": detail,
            "evaluation_text": (
                "1. Parsed meeting request from WhatsApp\n"
                "2. Created Google Calendar event\n"
                f"3. Title: {title}\n"
                f"4. Meet link: {conf}\n"
                "5. Ready — no further action needed"
            ),
        }
    except Exception as e:
        logger.exception("Calendar create failed; trying Gmail invite fallback")
        try:
            send_calendar_invite_email(attendees or [], title, start, end)
            await clear_session(phone)
            return {
                **state,
                "status": "meeting_scheduled",
                "meeting_title": title,
                "notification_text": (
                    f"Calendar API unavailable ({e}). Sent a Gmail invite instead.\n"
                    f"*Title:* {title}\n"
                    f"*When:* {start.isoformat()} → {end.isoformat()}\n"
                    f"*Attendees:* {', '.join(attendees) if attendees else 'you'}"
                ),
                "evaluation_text": (
                    "1. Calendar API failed\n"
                    "2. Fell back to Gmail invite\n"
                    f"3. Title: {title}\n"
                    "4. Done"
                ),
            }
        except Exception as e2:
            logger.exception("Meeting fallback failed")
            return {
                **state,
                "status": "failed",
                "error": str(e2),
                "notification_text": (
                    f"Could not schedule meeting.\nCalendar error: {e}\nInvite error: {e2}\n\n"
                    "Re-run Google OAuth with Calendar scope "
                    "(scripts/setup_gmail_oauth.py) and update GOOGLE_REFRESH_TOKEN."
                ),
            }
