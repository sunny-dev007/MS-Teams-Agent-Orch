"""Azure Bot → Microsoft Teams channel (parallel to Copilot Studio).

Additive. Does not modify WhatsApp or the Copilot Studio connector path.
Reuses the same `_handle_copilot_message` pipeline so coding / docs / RAG /
FinOps replies keep the same markdown + Adaptive Card formats.

Delivery contract (match AI Dev Agent / Copilot Studio UX):
- Every successful pipeline turn must surface a user-visible Teams activity.
- Stop/cancel must always return a clear confirmation (no dangling "working on it").
- Never NameError / 500 into silence.
"""

from __future__ import annotations

import base64
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from agent.config import settings
from agent.core.logging import get_logger
from agent.core.security import is_teams_user_allowed

logger = get_logger(__name__)

router = APIRouter(prefix="/api/channels/teamsbot", tags=["teams-bot"])

WORKING_ACK = "Got it, Sunny — working on it…"

# In-memory ring buffer for production diagnosis (no secrets / no message bodies).
_MAX_TURN_EVENTS = 30
_turn_events: list[dict[str, Any]] = []


def _record_turn(*, ok: bool, detail: str = "", error: str = "") -> None:
    """Record a compact Orbit turn outcome for /diagnostics (never raises)."""
    try:
        _turn_events.append(
            {
                "ts": int(time.time()),
                "ok": bool(ok),
                "detail": (detail or "")[:160],
                "error": (error or "")[:200],
            }
        )
        if len(_turn_events) > _MAX_TURN_EVENTS:
            del _turn_events[: len(_turn_events) - _MAX_TURN_EVENTS]
    except Exception:
        pass


def teams_bot_enabled() -> bool:
    return bool(getattr(settings, "enable_teams_bot_channel", False)) and bool(
        (getattr(settings, "microsoft_app_id", None) or "").strip()
    )


def _adapter():
    from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings

    app_id = (settings.microsoft_app_id or "").strip()
    password = settings.microsoft_app_password.get_secret_value()
    tenant = (getattr(settings, "microsoft_app_tenant_id", None) or "").strip()
    kwargs: dict[str, Any] = {}
    if tenant:
        kwargs["channel_auth_tenant"] = tenant
    return BotFrameworkAdapter(BotFrameworkAdapterSettings(app_id, password, **kwargs))


class OrbitTeamsBot:
    """Thin Teams activity handler → existing Copilot message pipeline."""

    async def on_turn(self, turn_context) -> None:
        from botbuilder.core import CardFactory, MessageFactory
        from botbuilder.schema import ActivityTypes

        activity = turn_context.activity
        act_type = getattr(activity, "type", None)
        logger.info(
            "Orbit Teams activity type=%s channel=%s text_len=%s",
            act_type,
            getattr(activity, "channel_id", None),
            len((getattr(activity, "text", None) or "")),
        )

        if act_type != ActivityTypes.message:
            return

        try:
            await self._handle_message(turn_context, MessageFactory, CardFactory)
        except Exception:
            logger.exception("Orbit on_turn unhandled crash")
            try:
                await turn_context.send_activity(
                    MessageFactory.text(
                        "*Orbit* hit a temporary error. Please retry — say **hello**, "
                        "**status**, or **stop**."
                    )
                )
            except Exception:
                logger.exception("Orbit failed sending crash fallback")

    async def _handle_message(self, turn_context, MessageFactory, CardFactory) -> None:
        activity = turn_context.activity
        text = (activity.text or "").strip()
        if activity.entities:
            for ent in activity.entities:
                if getattr(ent, "type", None) == "mention":
                    mentioned = (getattr(ent, "text", None) or "").strip()
                    if mentioned and mentioned in text:
                        text = text.replace(mentioned, "").strip()

        aad_id = ""
        try:
            fp = activity.from_property
            aad_id = (
                (getattr(fp, "aad_object_id", None) if fp else None)
                or (getattr(fp, "id", None) if fp else None)
                or ""
            )
        except Exception:
            aad_id = ""
        aad_id = str(aad_id).strip()

        if not aad_id:
            await turn_context.send_activity(
                MessageFactory.text(
                    "I couldn't resolve your Teams user id. Please retry from a 1:1 Orbit chat."
                )
            )
            return

        if not is_teams_user_allowed(aad_id):
            await turn_context.send_activity(
                MessageFactory.text(
                    "You're not on the Teams allowlist for this agent. "
                    "Ask the admin to add your user id to ALLOWED_TEAMS_USER_IDS."
                )
            )
            return

        display_name = ""
        try:
            display_name = activity.from_property.name or ""
        except Exception:
            display_name = ""

        conversation_id = ""
        try:
            conversation_id = activity.conversation.id if activity.conversation else ""
        except Exception:
            conversation_id = ""

        try:
            attachments = _map_teams_attachments(activity)
        except Exception:
            logger.exception("Orbit attachment map failed — continuing without attachments")
            attachments = []

        from agent.api.copilot import (
            CopilotAttachment,
            CopilotMessageRequest,
            _handle_copilot_message,
        )
        from agent.core.channel_identity import teams_session_id
        from agent.workflow.resume_context import is_cancel_session_message

        if not text and not attachments:
            text = "hello"

        body = CopilotMessageRequest(
            user_id=aad_id,
            message=text,
            conversation_id=conversation_id or None,
            display_name=display_name or None,
            action="message",
            attachments=[
                CopilotAttachment(**a) if isinstance(a, dict) else a for a in attachments
            ],
        )
        session_id = teams_session_id(aad_id)
        is_cancel = is_cancel_session_message(text)

        try:
            from agent.services.teams_proactive import save_conversation_reference

            await save_conversation_reference(session_id, activity)
        except Exception:
            logger.exception(
                "Orbit failed storing conversation reference session=%s", session_id
            )

        # Match AI Dev Agent: Stop/cancel → confirmation only (no "working on it").
        sent_ack = False
        if not is_cancel:
            try:
                # Teams "Orbit is typing…" indicator while the pipeline runs.
                from botbuilder.schema import Activity, ActivityTypes

                await turn_context.send_activity(Activity(type=ActivityTypes.typing))
            except Exception:
                logger.exception("Orbit failed sending typing indicator session=%s", session_id)
            try:
                await turn_context.send_activity(MessageFactory.text(WORKING_ACK))
                sent_ack = True
            except Exception:
                logger.exception("Orbit failed sending instant ack session=%s", session_id)

        try:
            from agent.services.orbit_turn import orbit_turn

            with orbit_turn():
                result = await _handle_copilot_message(body, session_id)
        except Exception:
            logger.exception("Orbit pipeline failed session=%s", session_id)
            await turn_context.send_activity(
                MessageFactory.text(
                    "*Orbit* — temporary error while processing. Please retry in a few seconds "
                    "(say **hello**)."
                )
            )
            return

        if result.adaptive_card:
            try:
                card_attachment = CardFactory.adaptive_card(result.adaptive_card)
                reply_activity = MessageFactory.attachment(card_attachment)
                if result.reply:
                    reply_activity.text = result.reply
                    reply_activity.text_format = "markdown"
                await turn_context.send_activity(reply_activity)
                logger.info("Orbit replied with Adaptive Card session=%s", session_id)
                return
            except Exception:
                logger.exception("Failed sending Adaptive Card — falling back to markdown")

        # Copilot Studio always returns `reply`. Orbit must send it in-turn.
        # Root cause of production silence: after WORKING_ACK we used to drop
        # status-style fallbacks / empty replies, so Teams showed nothing useful
        # (and often looked completely dead if the ack was missed).
        reply = _strip_leading_working_ack((result.reply or "").strip())

        # Catch late outbox bubbles that landed after the copilot handler returned.
        try:
            from agent.core.channel_outbox import drain_outbox

            extra = await drain_outbox(session_id)
            if extra:
                chunk = "\n\n---\n\n".join(extra)
                reply = f"{reply}\n\n---\n\n{chunk}".strip() if reply else chunk
                reply = _strip_leading_working_ack(reply)
        except Exception:
            logger.exception(
                "Orbit post-pipeline outbox drain failed session=%s", session_id
            )

        if not reply and is_cancel:
            reply = (
                "*Session cleared.*\n\n"
                "You can start fresh:\n"
                "• *check my repos* — browse GitHub / Azure DevOps\n"
                "• *list my documents* — Knowledge (SP / OD / ON)\n"
                "• *help* — full tools & prompts catalog"
            )
        if reply and _is_status_poll_hint(reply):
            # Orbit pushes completions proactively — don't train users to poll.
            reply = (
                "Still working on that — I'll message you here automatically when the "
                "next step is ready.\n\n"
                "_No need to say status — Orbit will push updates._"
            )
        if not reply:
            # Never stay silent after a user message — ack-only feels like a dead bot.
            reply = (
                "Still working on that — I'll post the result here when it's ready.\n\n"
                "_Orbit pushes updates automatically — no need to say status._"
            )

        try:
            from botbuilder.schema import Activity, ActivityTypes

            await turn_context.send_activity(Activity(type=ActivityTypes.typing))
        except Exception:
            pass

        msg = MessageFactory.text(reply)
        msg.text_format = "markdown"
        await turn_context.send_activity(msg)
        _record_turn(ok=True, detail=f"markdown chars={len(reply)} cancel={is_cancel}")
        logger.info(
            "Orbit replied markdown session=%s chars=%s cancel=%s",
            session_id,
            len(reply),
            is_cancel,
        )


# Singleton used by /messages — MUST exist or every Teams turn 500s.
_BOT = OrbitTeamsBot()



def _map_teams_attachments(activity) -> list[dict[str, Any]]:
    """Map Bot Framework / Teams attachments into CopilotAttachment-shaped dicts.

    Teams often attaches non-file payloads (HTML snippets, client info). Those must
    NOT become fake ``upload.bin`` rows — that caused Doc Upload Agent to steal
    repo-wizard picks like ``2`` (Azure DevOps).
    """
    raw = getattr(activity, "attachments", None) or []
    out: list[dict[str, Any]] = []
    skip_types = (
        "adaptivecard",
        "application/vnd.microsoft",
        "text/html",
        "text/plain",
        "application/smil",
        "application/xml",
        "text/xml",
    )
    for att in raw:
        try:
            content_type = (
                getattr(att, "content_type", None)
                or getattr(att, "contentType", None)
                or ""
            ).lower()
            if any(s in content_type for s in skip_types):
                continue
            content = getattr(att, "content", None)
            name = getattr(att, "name", None)
            if not name and isinstance(content, dict):
                name = content.get("name")
            content_url = getattr(att, "content_url", None) or getattr(
                att, "contentUrl", None
            )
            content_b64 = None
            if isinstance(content, dict):
                content_b64 = content.get("contentBytes") or content.get(
                    "content_base64"
                )
            elif isinstance(content, (bytes, bytearray)) and content:
                content_b64 = base64.b64encode(bytes(content)).decode("ascii")
            elif isinstance(content, str) and content.strip():
                candidate = "".join(content.split())
                if len(candidate) >= 64 and re.fullmatch(r"[A-Za-z0-9+/=]+", candidate or ""):
                    content_b64 = candidate
            if not content_url and not content_b64:
                continue
            item: dict[str, Any] = {
                "filename": str(name or "upload.bin"),
                "content_type": content_type or None,
            }
            if content_b64:
                item["content_base64"] = content_b64
            if content_url:
                item["content_url"] = str(content_url)
                item["share_url"] = str(content_url)
            out.append(item)
        except Exception:
            logger.exception("Skipping malformed Teams attachment")
    return out


def _is_working_ack_blob(text: str) -> bool:
    t = (text or "").strip().lower()
    prefixes = (
        "got it, sunny — working on it",
        "got it, sunny - working on it",
    )
    for prefix in prefixes:
        if t.startswith(prefix):
            rest = t[len(prefix) :].strip(" .…")
            return rest == ""
    return False


def _strip_leading_working_ack(reply: str) -> str:
    """Remove leading working-ack segments; keep real agent content after them."""
    raw = (reply or "").strip()
    if not raw:
        return ""
    parts = [p.strip() for p in re.split(r"\n\s*-{3,}\s*\n", raw) if p.strip()]
    if not parts:
        return raw
    while parts and _is_working_ack_blob(parts[0]):
        parts.pop(0)
    if not parts:
        return ""
    return "\n\n---\n\n".join(parts)


def _is_status_poll_hint(reply: str) -> bool:
    t = (reply or "").strip().lower()
    if not t or _is_working_ack_blob(t):
        return False
    markers = (
        "say **status**",
        "say status",
        "action=poll",
        "call action=poll",
        "if you need the latest update",
        "to pull later updates",
        "in a moment if you need the latest update",
    )
    if not any(m in t for m in markers):
        return False
    if len(t) > 450 or "---" in t:
        return False
    return True


@router.get("/health")
async def teams_bot_health() -> dict[str, Any]:
    password_configured = bool(
        (settings.microsoft_app_password.get_secret_value() or "").strip()
    )
    return {
        "status": "healthy" if teams_bot_enabled() and password_configured else "degraded",
        "enabled": teams_bot_enabled(),
        "microsoft_app_id_configured": bool((settings.microsoft_app_id or "").strip()),
        "microsoft_app_password_configured": password_configured,
        "microsoft_app_tenant_configured": bool(
            (getattr(settings, "microsoft_app_tenant_id", None) or "").strip()
        ),
        "endpoint": "/api/channels/teamsbot/messages",
        "diagnostics_endpoint": "/api/channels/teamsbot/diagnostics",
        "agent_name": getattr(settings, "teams_bot_display_name", None) or "Orbit",
        "bot_singleton_loaded": globals().get("_BOT") is not None,
        "message": (
            "Orbit Teams Bot ready — sideload the Teams app / open chat"
            if teams_bot_enabled() and password_configured
            else "Set ENABLE_TEAMS_BOT_CHANNEL=true and MICROSOFT_APP_ID / MICROSOFT_APP_PASSWORD"
        ),
        "isolates_copilot_studio": True,
        "reuses_copilot_pipeline": True,
        "never_silent_after_ack": True,
    }


@router.get("/diagnostics")
async def teams_bot_diagnostics() -> dict[str, Any]:
    """Safe production probe — no secrets, no user message bodies."""
    password_configured = bool(
        (settings.microsoft_app_password.get_secret_value() or "").strip()
    )
    adapter_ok = False
    adapter_error = ""
    try:
        _ = _adapter()
        adapter_ok = True
    except Exception as exc:
        adapter_error = f"{type(exc).__name__}: {exc}"[:200]

    recent = list(_turn_events[-10:])
    failures = sum(1 for e in recent if not e.get("ok"))
    return {
        "status": "ok" if teams_bot_enabled() and password_configured and adapter_ok else "error",
        "enabled": teams_bot_enabled(),
        "microsoft_app_id_configured": bool((settings.microsoft_app_id or "").strip()),
        "microsoft_app_password_configured": password_configured,
        "microsoft_app_tenant_configured": bool(
            (getattr(settings, "microsoft_app_tenant_id", None) or "").strip()
        ),
        "adapter_construct_ok": adapter_ok,
        "adapter_error": adapter_error or None,
        "bot_singleton_loaded": globals().get("_BOT") is not None,
        "messaging_endpoint": "/api/channels/teamsbot/messages",
        "orbit_outbox_flusher": bool(
            getattr(settings, "enable_orbit_outbox_flusher", True)
        ),
        "orbit_progress_ticker": bool(
            getattr(settings, "enable_orbit_progress_ticker", True)
        ),
        "orbit_outbox_flush_seconds": int(
            getattr(settings, "orbit_outbox_flush_seconds", 20) or 20
        ),
        "recent_turns": recent,
        "recent_failure_count": failures,
        "hint": (
            "If Teams is silent: (1) confirm this health is healthy, "
            "(2) check recent_turns for auth/pipeline errors, "
            "(3) confirm Azure Bot messaging endpoint matches messaging_endpoint, "
            "(4) App Service Always On must be On (B1+); Orbit flushes outbox every "
            "~20s so long tasks push updates without typing status."
        ),
    }


@router.post("/messages")
async def teams_bot_messages(request: Request) -> Response:
    """Bot Framework messaging endpoint for the Orbit Azure Bot (Teams channel)."""
    t0 = time.perf_counter()
    if not teams_bot_enabled():
        raise HTTPException(
            status_code=503,
            detail="Teams Bot channel disabled — set ENABLE_TEAMS_BOT_CHANNEL=true",
        )

    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    auth_header = request.headers.get("Authorization", "") or request.headers.get(
        "authorization", ""
    )
    logger.info(
        "Orbit /messages hit type=%s channel=%s auth=%s",
        body.get("type"),
        body.get("channelId"),
        "yes" if auth_header else "no",
    )

    from botbuilder.schema import Activity

    activity = Activity().deserialize(body)
    adapter = _adapter()

    async def _logic(turn_context):
        bot = globals().get("_BOT") or OrbitTeamsBot()
        await bot.on_turn(turn_context)

    try:
        invoke_response = await adapter.process_activity(activity, auth_header, _logic)
        ms = int((time.perf_counter() - t0) * 1000)
        logger.info("Orbit /messages done ms=%s", ms)
        _record_turn(ok=True, detail=f"process_activity ms={ms} type={body.get('type')}")
        if invoke_response:
            return JSONResponse(
                status_code=invoke_response.status,
                content=invoke_response.body,
            )
        return Response(status_code=201)
    except PermissionError as exc:
        logger.warning("Orbit bot auth failed: %s", exc)
        _record_turn(ok=False, error=f"auth: {exc}")
        return PlainTextResponse(str(exc), status_code=401)
    except Exception as exc:
        err = str(exc)
        logger.exception("Teams bot process_activity failed: %s", err)
        _record_turn(ok=False, error=err[:200])
        low = err.lower()
        if any(t in low for t in ("unauthorized", "401", "authentication", "jwt", "token")):
            return PlainTextResponse(err[:500], status_code=401)
        return PlainTextResponse("temporary_failure", status_code=500)
