"""Merge selected meeting transcripts for plan generation."""

from __future__ import annotations

from typing import Any

from agent.core.logging import get_logger
from agent.models import meeting_transcript as mt
from agent.services import meeting_transcripts, vtt_parse

logger = get_logger(__name__)

_MAX_SINGLE_CHARS = 80_000
_MAX_PER_MEETING = 24_000


async def load_meeting_text(row: dict[str, Any]) -> str:
    plain = (row.get("parsed_text") or "").strip()
    if plain:
        return plain
    ext_id = meeting_transcripts._drive_key(row)
    cached = await mt.get_by_external_id(ext_id)
    if cached and cached.parsed_text:
        return cached.parsed_text
    _cues, plain, _h = await meeting_transcripts.download_and_parse(row)
    return plain


async def build_meetings_block(
    selected: list[dict[str, Any]],
    *,
    user_focus: str = "",
) -> str:
    if not selected:
        return ""
    parts: list[str] = []
    for i, row in enumerate(selected, start=1):
        title = row.get("title") or f"Meeting {i}"
        client = row.get("client_name") or "Unknown client"
        agenda = row.get("agenda_summary") or ""
        text = await load_meeting_text(row)
        if len(text) > _MAX_PER_MEETING:
            text = text[:_MAX_PER_MEETING] + "\n…(truncated)"
        parts.append(
            f"### Meeting {i}: {title}\n"
            f"Client: {client}\n"
            f"Agenda: {agenda}\n\n"
            f"{text}"
        )
    block = "\n\n---\n\n".join(parts)
    if len(block) > _MAX_SINGLE_CHARS and len(selected) > 1:
        logger.info("Multi-meeting context large — using per-meeting summaries path")
        return await _summarized_block(selected, user_focus=user_focus)
    return block


async def _summarized_block(
    selected: list[dict[str, Any]],
    *,
    user_focus: str = "",
) -> str:
    from agent.services.meeting_plan_builder import summarize_meeting_excerpt

    parts: list[str] = []
    for i, row in enumerate(selected, start=1):
        text = await load_meeting_text(row)
        excerpt = text[:12_000]
        summary = await summarize_meeting_excerpt(
            title=row.get("title") or f"Meeting {i}",
            excerpt=excerpt,
            user_focus=user_focus,
        )
        parts.append(
            f"### Meeting {i}: {row.get('title')}\n"
            f"Client: {row.get('client_name') or 'Unknown'}\n\n{summary}"
        )
    return "\n\n---\n\n".join(parts)
