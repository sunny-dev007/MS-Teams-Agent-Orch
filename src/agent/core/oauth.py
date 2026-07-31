from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def get_gmail_credentials() -> Credentials:
    creds = Credentials(
        token=None,
        refresh_token=settings.google_refresh_token.get_secret_value(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        scopes=SCOPES,
    )
    creds.refresh(Request())
    logger.info("Gmail credentials refreshed")
    return creds
