import httpx

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)

GRAPH_API_URL = "https://graph.facebook.com/v21.0"


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
        resp.raise_for_status()
        result = resp.json()
        logger.info("WhatsApp message sent to %s", to_phone)
        return result


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
