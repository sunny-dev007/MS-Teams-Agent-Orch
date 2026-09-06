"""Channel-aware outbound notify — WhatsApp Graph API or Teams outbox/proactive."""

from __future__ import annotations

from agent.core.channel_identity import is_teams_session
from agent.core.logging import get_logger

logger = get_logger(__name__)


def _is_instant_working_ack(text: str) -> bool:
    t = (text or "").strip().lower()
    prefixes = (
        "got it, sunny — working on it",
        "got it, sunny - working on it",
        "got it, sunny — working on it",
        "got it, sunny - working on it",
    )
    return any(t.startswith(p) for p in prefixes)


def _ack_only(text: str) -> bool:
    """True when text is solely the working-ack bubble (no other content)."""
    t = (text or "").strip().lower()
    if not _is_instant_working_ack(t):
        return False
    prefixes = (
        "got it, sunny — working on it",
        "got it, sunny - working on it",
        "got it, sunny — working on it",
        "got it, sunny - working on it",
    )
    rest = t
    for prefix in prefixes:
        if rest.startswith(prefix):
            rest = rest[len(prefix):].strip(" .……")
            break
    return rest == ""


async def send_channel_message(session_id: str, text: str) -> None:
    """Deliver a user-facing message on the correct channel.

    WhatsApp phone numbers keep using Meta Graph API (unchanged path).

    Teams session ids (`teams:…`):
    - During an active Orbit turn: always enqueue outbox so the turn can drain
      and send via turn_context (reliable; no Status needed).
    - Outside a turn (late/background): try proactive push; fall back to outbox.
    - Copilot Studio (no conversation reference): outbox only.
    """
    if not session_id or not text:
        return
    if is_teams_session(session_id):
        from agent.core.channel_outbox import enqueue_outbox
        from agent.services.orbit_turn import is_orbit_turn_active

        # Active Orbit turn: never proactive — drain+turn_context is the contract.
        if is_orbit_turn_active():
            if _ack_only(text):
                # Instant ack already sent by teams_bot.on_turn.
                logger.info("Orbit turn skip duplicate working-ack session=%s", session_id)
                return
            await enqueue_outbox(session_id, text)
            logger.info(
                "Orbit turn outbox enqueue session=%s chars=%s",
                session_id,
                len(text),
            )
            return

        # Background / late delivery path — retry briefly, then outbox (flusher
        # will push without the user typing "status").
        delivered = False
        try:
            import asyncio

            from agent.services.teams_proactive import (
                load_conversation_reference,
                try_send_proactive_teams_message,
            )

            has_orbit_ref = bool(await load_conversation_reference(session_id))
            if has_orbit_ref and _ack_only(text):
                logger.info("Orbit skip duplicate working-ack session=%s", session_id)
                delivered = True
            elif has_orbit_ref:
                for attempt in range(3):
                    delivered = await try_send_proactive_teams_message(
                        session_id, text
                    )
                    if delivered:
                        logger.info(
                            "Orbit proactive delivered session=%s chars=%s attempt=%s",
                            session_id,
                            len(text),
                            attempt + 1,
                        )
                        break
                    await asyncio.sleep(0.6 * (attempt + 1))
        except Exception:
            logger.exception("Orbit proactive path error session=%s", session_id)
            delivered = False

        if not delivered:
            await enqueue_outbox(session_id, text)
            logger.info(
                "Orbit outbox fallback session=%s chars=%s (flusher will push)",
                session_id,
                len(text),
            )
    else:
        from agent.services.whatsapp import send_message

        await send_message(session_id, text)

    try:
        from agent.services import agent_runtime as runtime

        if runtime.orchestration_enabled():
            await runtime.append_bubble(session_id, role="assistant", text=text)
    except Exception:
        logger.exception("append_bubble failed session=%s", session_id)
