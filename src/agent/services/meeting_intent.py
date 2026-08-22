"""Meeting intent classification and SharePoint folder routing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.config import settings
from agent.core.logging import get_logger
from agent.services.llm import invoke_llm

logger = get_logger(__name__)

_CLASSIFY_PROMPT = (
    Path(__file__).resolve().parent.parent / "prompts" / "meeting_intent_classify.txt"
).read_text(encoding="utf-8")

MEETING_DEV = "development"
MEETING_BUSINESS = "business"
MEETING_GENERAL = "general"


def _slug_folder(text: str, *, max_len: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:max_len] or "meeting-output").strip("-")


def _parse_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _heuristic_classify(excerpt: str, *, client_name: str = "") -> dict[str, Any]:
    lower = (excerpt or "").lower()
    dev_kw = (
        "api",
        "architecture",
        "azure devops",
        "sprint",
        "deploy",
        "implementation",
        "code",
        "developer",
        "backend",
        "frontend",
        "integration",
        "mvp",
        "agent",
        "software",
    )
    score = sum(1 for k in dev_kw if k in lower)
    category = MEETING_DEV if score >= 3 else MEETING_BUSINESS if score >= 1 else MEETING_GENERAL
    slug = _slug_folder(client_name or excerpt[:40])
    return {
        "meeting_category": category,
        "confidence": 0.5,
        "requires_devops_board": category == MEETING_DEV,
        "sharepoint_folder_slug": slug,
        "topic_label": (client_name or "Meeting")[:40],
        "rationale": "Heuristic keyword classification",
    }


async def classify_meeting_intent(
    *,
    meetings_block: str,
    client_name: str = "",
    titles: list[str] | None = None,
) -> dict[str, Any]:
    """Classify transcript intent for dev board vs action-plan DOCX path."""
    excerpt = (meetings_block or "")[:6000]
    payload = (
        f"Client: {client_name or 'Unknown'}\n"
        f"Titles: {', '.join(titles or [])}\n\n"
        f"Transcript excerpt:\n{excerpt}"
    )
    try:
        resp = await invoke_llm(
            [SystemMessage(content=_CLASSIFY_PROMPT), HumanMessage(content=payload)],
            temperature=0.1,
            role="planning",
        )
        data = _parse_json(resp.content or "")
        cat = (data.get("meeting_category") or MEETING_GENERAL).lower().strip()
        if cat not in (MEETING_DEV, MEETING_BUSINESS, MEETING_GENERAL):
            cat = MEETING_GENERAL
        data["meeting_category"] = cat
        data["sharepoint_folder_slug"] = _slug_folder(
            data.get("sharepoint_folder_slug") or client_name or cat
        )
        data["requires_devops_board"] = bool(
            data.get("requires_devops_board") and cat == MEETING_DEV
        )
        return data
    except Exception:
        logger.exception("Meeting intent LLM classify failed — using heuristic")
        return _heuristic_classify(excerpt, client_name=client_name)


def resolve_publish_folder(
    intent: dict[str, Any],
    *,
    client_name: str = "",
) -> str:
    """SharePoint folder path for published artifacts."""
    cat = (intent.get("meeting_category") or MEETING_GENERAL).lower()
    slug = _slug_folder(intent.get("sharepoint_folder_slug") or client_name or cat)
    if cat == MEETING_DEV:
        base = (settings.meeting_dev_plans_folder or "MeetingPlans/Development").strip().strip("/")
        return f"{base}/{slug}"
    base = (settings.meeting_action_plans_folder or "MeetingActionPlans").strip().strip("/")
    return f"{base}/{slug}"
