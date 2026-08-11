"""Channel-aware outbound notify — WhatsApp Graph API or Teams outbox."""

from __future__ import annotations

from agent.core.channel_identity import is_teams_session
from agent.core.logging import get_logger

logger = get_logger(__name__)


async def send_channel_message(session_id: str, text: str) -> None:
    """Deliver a user-facing message on the correct channel.

    WhatsApp phone numbers keep using Meta Graph API (unchanged path).
    Teams session ids (`teams:…`) are stored in the outbox for Copilot poll/drain.
    """
    if not session_id or not text:
        return
    if is_teams_session(session_id):
        from agent.core.channel_outbox import enqueue_outbox

        await enqueue_outbox(session_id, text)
        return

    from agent.services.whatsapp import send_message

    await send_message(session_id, text)
