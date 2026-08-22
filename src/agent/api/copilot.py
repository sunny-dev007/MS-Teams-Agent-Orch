"""Copilot Studio / Teams channel API — additive; WhatsApp webhooks unchanged."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent.config import settings
from agent.core.channel_identity import teams_session_id
from agent.core.logging import get_logger
from agent.core.security import is_teams_user_allowed, verify_copilot_api_key

logger = get_logger(__name__)

router = APIRouter(prefix="/api/channels/copilot", tags=["copilot"])


class CopilotAttachment(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    filename: str = Field(default="upload.bin", min_length=1, description="Original file name")
    content_base64: str | None = Field(
        default=None,
        description="Base64-encoded file bytes from Teams/Copilot Studio",
    )
    content_bytes: str | None = Field(
        default=None,
        alias="contentBytes",
        description="Power Platform alias for base64 content",
    )
    content_url: str | None = Field(
        default=None,
        description="Optional HTTPS URL to download attachment bytes",
    )
    content_type: str | None = Field(default=None, description="MIME type when known")


class CopilotMessageRequest(BaseModel):
    user_id: str = Field(..., min_length=1, description="AAD object id or stable Copilot user id")
    message: str = ""
    conversation_id: str | None = None
    display_name: str | None = None
    action: Literal["message", "poll", "status"] = "message"
    attachments: list[CopilotAttachment] = Field(default_factory=list)


class CopilotMessageResponse(BaseModel):
    reply: str
    awaiting: str | None = None
    task_id: str | None = None
    pending_updates: list[str] = Field(default_factory=list)
    channel: str = "teams"
    session_id: str = ""
    # Optional Adaptive Card for Copilot Studio rich rendering (Hello / Help).
    # Studio: bind this JSON to an Adaptive Card message when present; else show reply.
    adaptive_card: dict[str, Any] | None = None


def _require_channel_enabled() -> None:
    if not settings.enable_teams_copilot_channel:
        raise HTTPException(status_code=503, detail="Teams Copilot channel is disabled")


def _require_api_key(x_copilot_api_key: str | None) -> None:
    if not verify_copilot_api_key(x_copilot_api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Copilot-Api-Key")


async def _safe_job(func, args, kwargs) -> None:
    try:
        result = func(*args, **kwargs)
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.exception("Copilot scheduled job failed: %s", getattr(func, "__name__", func))


async def _run_scheduled_jobs(jobs: list[tuple], session_id: str) -> list[str]:
    """Fire background work; wait briefly and collect outbox so Teams gets real content."""
    from agent.core.channel_outbox import drain_outbox, peek_outbox_count

    for func, args, kwargs in jobs:
        asyncio.create_task(_safe_job(func, args, kwargs))

    pending: list[str] = []
    # Repo picker / gate hints usually land within a few seconds.
    for _ in range(16):
        await asyncio.sleep(0.4)
        batch = await drain_outbox(session_id)
        if batch:
            pending.extend(batch)
            # Keep waiting a bit if more is still arriving
            if await peek_outbox_count(session_id) == 0 and pending:
                break
        elif pending:
            break
    return pending


@router.post("/message", response_model=CopilotMessageResponse)
async def copilot_message(
    body: CopilotMessageRequest,
    x_copilot_api_key: str | None = Header(default=None, alias="X-Copilot-Api-Key"),
) -> CopilotMessageResponse:
    """Entry point for Copilot Studio HTTP tool / custom connector."""
    _require_channel_enabled()
    _require_api_key(x_copilot_api_key)

    if not is_teams_user_allowed(body.user_id):
        raise HTTPException(status_code=403, detail="Teams user not allowlisted")

    session_id = teams_session_id(body.user_id)
    try:
        return await _handle_copilot_message(body, session_id)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Copilot message failed session=%s", session_id)
        # Never return raw 500 to Copilot Studio — it surfaces as tool failure.
        return CopilotMessageResponse(
            reply=(
                "*Sunny's AI Agent* — temporary error while processing your request.\n\n"
                "Please retry in a few seconds (say *status* or *check my repos* again).\n"
                "If it keeps failing, the App Service logs will show the details."
            ),
            awaiting=None,
            task_id=None,
            pending_updates=[],
            session_id=session_id,
        )


async def _handle_copilot_message(
    body: CopilotMessageRequest, session_id: str
) -> CopilotMessageResponse:
    from agent.core.channel_outbox import drain_outbox
    from agent.core.session import get_session, save_session
    from agent.workflow.gates import effective_awaiting
    from agent.workflow.resume_context import format_no_pending_task, format_session_status

    try:
        await save_session(
            session_id,
            data={
                "channel": "teams",
                "teams_user_id": body.user_id,
                "teams_conversation_id": body.conversation_id or "",
                "display_name": body.display_name or "",
            },
            merge_data=True,
        )
    except Exception:
        logger.exception("Failed saving Teams session metadata for %s", session_id)

    action = (body.action or "message").lower()
    if action in ("poll", "status") or not (body.message or "").strip():
        pending = await drain_outbox(session_id)
        session = await get_session(session_id)
        gate = effective_awaiting(session)
        data = session.get("data") or {}
        tid = data.get("pending_task_id")
        if pending:
            reply = "\n\n---\n\n".join(pending)
        elif gate:
            reply = format_session_status({**session, "awaiting": gate})
        else:
            reply = format_no_pending_task()
        return CopilotMessageResponse(
            reply=reply,
            awaiting=gate,
            task_id=str(tid) if tid else None,
            pending_updates=pending,
            session_id=session_id,
        )

    message = body.message.strip()
    jobs: list[tuple[Any, tuple, dict]] = []

    def schedule(func, *args, **kwargs):
        jobs.append((func, args, kwargs))

    graph_attachments = _attachments_for_graph(body)

    from agent.api.channel_gates import route_inbound_message

    await route_inbound_message(
        session_id,
        message,
        schedule=schedule,
        graph_payload={
            "phone": session_id,
            "message": message,
            "message_id": body.conversation_id or "",
            "name": body.display_name or "",
            "attachments": graph_attachments,
        },
        source="teams",
    )
    pending = await _run_scheduled_jobs(jobs, session_id)
    # One more drain for late messages
    pending.extend(await drain_outbox(session_id))

    session = await get_session(session_id)
    gate = effective_awaiting(session)
    data = session.get("data") or {}
    tid = data.get("pending_task_id")

    if pending:
        reply = "\n\n---\n\n".join(pending)
    elif gate:
        reply = (
            "Got it — I'm working on that in the background.\n\n"
            + format_session_status({**session, "awaiting": gate})
            + "\n\n_Say **status** or call action=poll to pull later updates._"
        )
    else:
        reply = (
            "Got it — I'm working on that.\n"
            "Say **status** in a moment if you need the latest update."
        )

    adaptive_card = _teams_adaptive_card_for_message(message, session)

    logger.info(
        "Copilot message session=%s awaiting=%s pending=%s card=%s",
        session_id,
        gate,
        len(pending),
        bool(adaptive_card),
    )
    return CopilotMessageResponse(
        reply=reply,
        awaiting=gate,
        task_id=str(tid) if tid else None,
        pending_updates=pending,
        session_id=session_id,
        adaptive_card=adaptive_card,
    )


def _attachments_for_graph(body: CopilotMessageRequest) -> list[dict[str, Any]]:
    """Normalize Copilot attachment payloads for the Doc Upload Agent."""
    from agent.services import doc_upload

    out: list[dict[str, Any]] = []
    for att in body.attachments or []:
        raw = att.model_dump(exclude_none=True, by_alias=True)
        norm = doc_upload.normalize_attachment(raw)
        if norm:
            out.append(norm)
        else:
            logger.warning(
                "Copilot attachment skipped (no decodable content) filename=%s keys=%s",
                raw.get("filename") or raw.get("name"),
                list(raw.keys()),
            )
    if body.message and doc_upload.message_expects_teams_attachment(body.message):
        logger.info(
            "Copilot attachment-oriented message attachments_in=%s normalized=%s",
            len(body.attachments or []),
            len(out),
        )
    return out


def _teams_adaptive_card_for_message(
    message: str, session: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Attach Adaptive Cards for Hello / Help so Studio can render rich UI."""
    import re

    from agent.core.persona import (
        build_greeting_adaptive_card,
        build_help_adaptive_card,
    )
    from agent.planner.agent import is_simple_greeting

    raw = (message or "").strip()
    if re.match(r"^(help|menu|\?|commands)[\s!.?]*$", raw, re.I):
        return build_help_adaptive_card()
    if is_simple_greeting(raw):
        return build_greeting_adaptive_card(session=session)
    return None


@router.get("/health")
async def copilot_channel_health() -> dict[str, Any]:
    """Liveness + light auth/config probes for Copilot / ops checks."""
    azdo_configured = bool(settings.azdo_org_url and settings.azdo_pat.get_secret_value())
    github_configured = bool(settings.github_token.get_secret_value())
    from agent.services import azure_boards, ms_graph, qdrant_store

    graph_roles: list[str] = []
    graph_has_mail_read = False
    if ms_graph.graph_configured() and settings.enable_outlook_agent:
        try:
            graph_roles = await ms_graph.graph_token_roles()
            graph_has_mail_read = "Mail.Read" in graph_roles
        except Exception:
            graph_roles = []

    return {
        "enabled": bool(settings.enable_teams_copilot_channel),
        "api_key_configured": bool(settings.copilot_api_key.get_secret_value()),
        "allowlist_size": len(settings.allowed_teams_user_ids or []),
        "azdo_configured": azdo_configured,
        "github_configured": github_configured,
        "openai_configured": bool(settings.effective_api_key),
        # Release Agent Fabric — flags default false on prod
        "fabric": {
            "docs_agent_enabled": bool(settings.enable_docs_agent),
            "qa_agent_enabled": bool(settings.enable_qa_agent),
            "release_handoff_enabled": bool(settings.enable_release_handoff),
            "doc_knowledge_enabled": bool(settings.enable_doc_knowledge),
            "data_analyst_agent_enabled": bool(settings.enable_data_analyst_agent),
            "outlook_agent_enabled": bool(settings.enable_outlook_agent),
            "boards_agent_enabled": bool(settings.enable_boards_agent),
            "graph_configured": ms_graph.graph_configured(),
            "graph_has_mail_read_role": graph_has_mail_read,
            "graph_token_roles": graph_roles,
            "docs_agent_ready": ms_graph.docs_agent_ready(),
            "doc_knowledge_ready": ms_graph.doc_knowledge_ready(),
            "outlook_agent_ready": ms_graph.outlook_agent_ready(),
            "boards_agent_ready": azure_boards.boards_agent_ready(),
            "qdrant_configured": qdrant_store.qdrant_configured(),
            "qdrant_ready": qdrant_store.qdrant_ready(),
            "qdrant_collection": (settings.qdrant_collection or "") if qdrant_store.qdrant_configured() else "",
        },
    }
