"""Proactive Teams replies for Orbit (Azure Bot) — additive to Copilot outbox.

Copilot Studio keeps draining the outbox via action=poll/status.
Orbit stores a Bot Framework conversation reference and pushes completions
when the turn has already ended, so users do not need to type Status.
"""

from __future__ import annotations

from typing import Any

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)

_REF_KEY = "bot_conversation_reference"


def _teams_bot_live() -> bool:
    return bool(getattr(settings, "enable_teams_bot_channel", False)) and bool(
        (getattr(settings, "microsoft_app_id", None) or "").strip()
    )


def _reference_to_dict(ref: Any) -> dict[str, Any] | None:
    """Serialize ConversationReference in a form that round-trips with from_dict.

    IMPORTANT: ``deserialize(as_dict())`` drops ``service_url`` in botbuilder.
    Use ``as_dict()`` + ``from_dict()``, or ``serialize()`` + ``deserialize()``.
    """
    if ref is None:
        return None
    try:
        if hasattr(ref, "as_dict"):
            data = ref.as_dict()
            if isinstance(data, dict) and data.get("service_url"):
                return data
        if hasattr(ref, "serialize"):
            data = ref.serialize()
            if isinstance(data, dict) and (
                data.get("serviceUrl") or data.get("service_url")
            ):
                return data
    except Exception:
        logger.exception("ConversationReference serialize failed")
    return None


def _reference_from_dict(data: dict[str, Any]):
    from botbuilder.schema import ConversationReference

    # Prefer from_dict for snake_case as_dict payloads (keeps service_url).
    if "service_url" in data or "channel_id" in data:
        return ConversationReference.from_dict(data)
    # camelCase serialize() payloads
    return ConversationReference().deserialize(data)


async def save_conversation_reference(session_id: str, activity: Any) -> None:
    """Persist Bot Framework conversation reference for later proactive sends."""
    if not session_id or activity is None:
        return
    try:
        from botbuilder.core import TurnContext

        from agent.core.session import save_session

        ref = TurnContext.get_conversation_reference(activity)
        ref_dict = _reference_to_dict(ref)
        if not ref_dict:
            logger.warning(
                "Orbit conversation reference empty/invalid session=%s", session_id
            )
            return
        await save_session(
            session_id,
            data={_REF_KEY: ref_dict},
            merge_data=True,
        )
        logger.info(
            "Orbit conversation reference saved session=%s service_url=%s",
            session_id,
            ref_dict.get("service_url") or ref_dict.get("serviceUrl"),
        )
    except Exception:
        logger.exception(
            "Failed saving Orbit conversation reference session=%s", session_id
        )


async def load_conversation_reference(session_id: str) -> dict[str, Any] | None:
    try:
        from agent.core.session import get_session

        session = await get_session(session_id)
        data = session.get("data") or {}
        ref = data.get(_REF_KEY)
        if not isinstance(ref, dict) or not ref:
            return None
        if not (ref.get("service_url") or ref.get("serviceUrl")):
            logger.warning(
                "Orbit conversation reference missing service_url session=%s keys=%s",
                session_id,
                sorted(ref.keys()),
            )
            return None
        return ref
    except Exception:
        logger.exception(
            "Failed loading Orbit conversation reference session=%s", session_id
        )
        return None


def resolve_microsoft_app_credentials():
    """Resolve MicrosoftAppCredentials across botbuilder package layouts.

    Newer botbuilder builds no longer re-export this from ``botbuilder.core``.
    Prefer ``botframework.connector.auth``, fall back to core for older installs.
    """
    try:
        from botframework.connector.auth import MicrosoftAppCredentials

        return MicrosoftAppCredentials
    except ImportError:
        from botbuilder.core import MicrosoftAppCredentials

        return MicrosoftAppCredentials


def proactive_credentials_probe() -> dict[str, Any]:
    """Safe diagnostics probe — no secrets, no network."""
    try:
        cls = resolve_microsoft_app_credentials()
        return {
            "ok": True,
            "module": getattr(cls, "__module__", ""),
            "has_trust_service_url": hasattr(cls, "trust_service_url"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:200],
            "has_trust_service_url": False,
        }


def _trust_bot_service_url(service_url: str) -> None:
    cls = resolve_microsoft_app_credentials()
    cls.trust_service_url(service_url)


async def try_send_proactive_teams_message(session_id: str, text: str) -> bool:
    """Push text to Teams via continue_conversation. Returns True if delivered."""
    if not session_id or not text or not _teams_bot_live():
        return False

    ref_dict = await load_conversation_reference(session_id)
    if not ref_dict:
        return False

    app_id = (settings.microsoft_app_id or "").strip()
    password = settings.microsoft_app_password.get_secret_value()
    tenant = (getattr(settings, "microsoft_app_tenant_id", None) or "").strip()
    if not app_id or not password:
        return False

    try:
        from botbuilder.core import (
            BotFrameworkAdapter,
            BotFrameworkAdapterSettings,
            MessageFactory,
        )

        kwargs: dict[str, Any] = {}
        if tenant:
            kwargs["channel_auth_tenant"] = tenant
        adapter = BotFrameworkAdapter(
            BotFrameworkAdapterSettings(app_id, password, **kwargs)
        )
        reference = _reference_from_dict(ref_dict)
        if not getattr(reference, "service_url", None):
            logger.error(
                "Orbit proactive aborted — service_url still empty session=%s",
                session_id,
            )
            return False

        async def _logic(turn_context) -> None:
            activity = MessageFactory.text(text)
            activity.text_format = "markdown"
            await turn_context.send_activity(activity)

        service_url = getattr(reference, "service_url", None)
        if service_url:
            _trust_bot_service_url(service_url)
        await adapter.continue_conversation(reference, _logic, app_id)
        logger.info(
            "Orbit proactive send ok session=%s chars=%s",
            session_id,
            len(text),
        )
        return True
    except Exception:
        logger.exception("Orbit proactive send failed session=%s", session_id)
        return False
