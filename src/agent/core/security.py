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
