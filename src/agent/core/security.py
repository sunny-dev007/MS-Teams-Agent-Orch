import hashlib
import hmac

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)


def verify_whatsapp_signature(payload: bytes, signature: str) -> bool:
    expected = hmac.new(
        settings.whatsapp_app_secret.get_secret_value().encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


def is_phone_allowed(phone: str) -> bool:
    if not settings.allowed_phone_numbers:
        return True
    return phone in settings.allowed_phone_numbers


def is_teams_user_allowed(user_id: str) -> bool:
    """Allowlist for Copilot/Teams user ids (AAD oid or UPN). Empty list = allow all."""
    allowed = settings.allowed_teams_user_ids or []
    if not allowed:
        return True
    uid = (user_id or "").strip()
    if not uid:
        return False
    lowered = {a.strip().lower() for a in allowed if a and str(a).strip()}
    return uid.lower() in lowered or uid.removeprefix("teams:").lower() in lowered


def verify_copilot_api_key(header_value: str | None) -> bool:
    expected = settings.copilot_api_key.get_secret_value()
    if not expected:
        return False
    return hmac.compare_digest((header_value or "").strip(), expected)
