from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
]

# Used only by setup_gmail_oauth.py when minting a new refresh token that covers both.
ALL_SCOPES = GMAIL_SCOPES + CALENDAR_SCOPES


def _refresh(scopes: list[str], label: str) -> Credentials:
    creds = Credentials(
        token=None,
        refresh_token=settings.google_refresh_token.get_secret_value(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        scopes=scopes,
    )
    creds.refresh(Request())
    logger.info("%s credentials refreshed", label)
    return creds


def get_gmail_credentials() -> Credentials:
    """Gmail-only scopes so existing refresh tokens keep working."""
    return _refresh(GMAIL_SCOPES, "Gmail")


def get_calendar_credentials() -> Credentials:
    """Calendar scopes. Requires a refresh token that includes calendar.events."""
    try:
        return _refresh(CALENDAR_SCOPES, "Calendar")
    except Exception:
        # Token minted with combined consent may refresh only with full scope set.
        logger.warning("Calendar-only refresh failed; retrying with combined scopes")
        return _refresh(ALL_SCOPES, "Google(combined)")


# Alias used by calendar service
get_google_credentials = get_calendar_credentials
