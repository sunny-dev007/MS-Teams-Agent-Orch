import httpx

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)

GRAPH_API_URL = "https://graph.facebook.com/v21.0"


class WhatsAppAuthError(Exception):
    """Raised when Meta rejects the access token (expired or invalid)."""


async def send_message(to_phone: str, text: str) -> dict:
    url = f"{GRAPH_API_URL}/{settings.whatsapp_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token.get_secret_value()}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text},
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code == 401:
            logger.error(
                "WhatsApp send failed: access token rejected (401). "
                "Temporary Meta tokens expire in ~24h. Generate a System User "
                "permanent token (Business Settings > System users) and update "
                "WHATSAPP_ACCESS_TOKEN in Azure App Settings."
            )
            raise WhatsAppAuthError(
                "WhatsApp access token is invalid or expired (HTTP 401)"
            )
        if resp.is_error:
            logger.error(
                "WhatsApp send failed: HTTP %s — %s",
                resp.status_code,
                resp.text[:500],
            )
        resp.raise_for_status()
        result = resp.json()
        logger.info("WhatsApp message sent to %s", to_phone)
        return result


async def check_access_token() -> dict:
    """Validate the configured token against Meta without sending a message.

    Returns a small status dict suitable for health/diagnostics endpoints.
    Never includes the raw token.
    """
    token = settings.whatsapp_access_token.get_secret_value()
    phone_id = settings.whatsapp_phone_number_id
    if not token or not phone_id:
        return {"ok": False, "error": "WHATSAPP_ACCESS_TOKEN or PHONE_NUMBER_ID missing"}

    url = f"{GRAPH_API_URL}/{phone_id}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers=headers,
                params={"fields": "id,display_phone_number,verified_name"},
                timeout=15,
            )
        if resp.status_code == 401:
            return {
                "ok": False,
                "error": "token_rejected",
                "hint": "Generate a Meta System User permanent token and update WHATSAPP_ACCESS_TOKEN",
            }
        if resp.is_error:
            return {"ok": False, "error": f"http_{resp.status_code}", "detail": resp.text[:200]}
        data = resp.json()
        return {
            "ok": True,
            "phone_number_id": data.get("id"),
            "display_phone_number": data.get("display_phone_number"),
            "verified_name": data.get("verified_name"),
        }
    except Exception as exc:
        logger.exception("WhatsApp token check failed")
        return {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:200]}


def parse_incoming_message(payload: dict) -> dict | None:
    try:
        entry = payload["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        if "messages" not in value:
            return None

        msg = value["messages"][0]
        contact = value["contacts"][0]

        return {
            "phone": msg["from"],
            "message": msg.get("text", {}).get("body", ""),
            "message_id": msg["id"],
            "name": contact.get("profile", {}).get("name", "Unknown"),
            "timestamp": msg.get("timestamp"),
        }
    except (KeyError, IndexError):
        logger.warning("Failed to parse WhatsApp payload")
        return None
