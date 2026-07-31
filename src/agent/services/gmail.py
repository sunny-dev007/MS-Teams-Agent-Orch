import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr

from googleapiclient.discovery import build

from agent.config import settings
from agent.core.logging import get_logger
from agent.core.oauth import get_gmail_credentials

logger = get_logger(__name__)


def _get_service():
    creds = get_gmail_credentials()
    return build("gmail", "v1", credentials=creds)


def list_recent_messages(max_results: int = 10, label: str = "INBOX") -> list[dict]:
    service = _get_service()
    result = service.users().messages().list(
        userId="me", labelIds=[label], maxResults=max_results
    ).execute()
    return result.get("messages", [])


def get_message(message_id: str) -> dict:
    service = _get_service()
    msg = service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()
    return _parse_message(msg)


def get_messages_since_history(history_id: str) -> list[dict]:
    service = _get_service()
    try:
        result = service.users().history().list(
            userId="me", startHistoryId=history_id, historyTypes=["messageAdded"]
        ).execute()
    except Exception:
        logger.warning("History list failed, may be expired history_id=%s", history_id)
        return []

    messages = []
    for record in result.get("history", []):
        for added in record.get("messagesAdded", []):
            msg_id = added["message"]["id"]
            messages.append(get_message(msg_id))

    return messages


def setup_watch(topic_name: str) -> dict:
    service = _get_service()
    result = service.users().watch(
        userId="me",
        body={"topicName": topic_name, "labelIds": ["INBOX"]},
    ).execute()
    logger.info("Gmail watch registered, historyId=%s, expiration=%s",
                result.get("historyId"), result.get("expiration"))
    return result


def send_email(to: str, subject: str, body: str) -> dict:
    service = _get_service()

    _, clean_to = parseaddr(to)
    if not clean_to:
        clean_to = to.strip().strip("<>").strip()

    profile = service.users().getProfile(userId="me").execute()
    sender_email = profile.get("emailAddress", "")
    from_header = formataddr((settings.sender_display_name, sender_email))

    msg = MIMEMultipart("alternative")
    msg["to"] = clean_to
    msg["from"] = from_header
    msg["subject"] = subject
    msg["reply-to"] = from_header

    html_body = body.replace("\n", "<br>\n")
    msg.attach(MIMEText(body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    logger.info("Email sent from %s to %s, message_id=%s", from_header, clean_to, result.get("id"))
    return result


def _parse_message(msg: dict) -> dict:
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}

    body = ""
    payload = msg.get("payload", {})

    if "body" in payload and payload["body"].get("data"):
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    elif "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                break

    _, sender_email = parseaddr(headers.get("from", ""))

    return {
        "id": msg["id"],
        "subject": headers.get("subject", "(no subject)"),
        "from": headers.get("from", ""),
        "sender_email": sender_email,
        "date": headers.get("date", ""),
        "body": body[:5000],
        "snippet": msg.get("snippet", ""),
        "labels": msg.get("labelIds", []),
    }
