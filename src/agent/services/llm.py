"""Azure OpenAI helpers — multi-deployment fallback with rate-limit retries."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Sequence

from langchain_core.messages import BaseMessage
from langchain_openai import AzureChatOpenAI

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)

# Sensible defaults aligned with typical Azure AI Foundry deployments.
_DEFAULT_PLANNING_FALLBACKS = ("gpt-4.1", "gpt-4o", "gpt-4", "gpt-5")
_DEFAULT_REVIEW_FALLBACKS = ("gpt-4.1", "gpt-4o", "gpt-4", "gpt-5")
_DEFAULT_RAG_FALLBACKS = ("gpt-4.1", "gpt-5", "gpt-4o", "gpt-4", "gpt-4.1-mini")
_DEFAULT_FALLBACKS = ("gpt-4o", "gpt-4.1", "gpt-4", "gpt-5", "gpt-4.1-mini")
# Authored DOCX / FinOps reports: gpt-4.1 first, then gpt-4o (matches AI Dev Agent quality).
_DEFAULT_DOC_AUTHOR_FALLBACKS = ("gpt-4.1", "gpt-4o", "gpt-4", "gpt-5", "gpt-4o-mini")

_RATE_LIMIT_RE = re.compile(
    r"(rate.?limit|too_many_requests|429|quota|capacity|throttl)",
    re.IGNORECASE,
)
_TRANSIENT_RE = re.compile(
    r"(timeout|timed out|503|502|500|overloaded|server.?error|connection)",
    re.IGNORECASE,
)


def _parse_deployments(raw: str) -> list[str]:
    return [d.strip() for d in (raw or "").split(",") if d.strip()]


def deployment_chain(*, role: str = "default") -> list[str]:
    """Ordered deployments to try for a role (primary first, then fallbacks).

    Orbit Teams Bot can use ORBIT_CHAT_DEPLOYMENT / ORBIT_DOC_DEPLOYMENT without
    changing Copilot Studio (AI Dev Agent) shared AZURE_OPENAI_* defaults.
    """
    from agent.services.orbit_turn import is_orbit_surface

    orbit = is_orbit_surface()
    primary = settings.azure_openai_deployment

    if orbit and role in ("doc_author", "planning", "rag", "review"):
        primary = (
            (settings.orbit_doc_deployment or "").strip()
            or settings.azure_openai_planning_deployment
            or settings.azure_openai_rag_deployment
            or "gpt-4.1"
        )
        fallbacks = _parse_deployments(settings.azure_openai_planning_fallbacks)
        if not fallbacks:
            fallbacks = list(_DEFAULT_DOC_AUTHOR_FALLBACKS)
    elif orbit and role == "default":
        primary = (settings.orbit_chat_deployment or "").strip() or "gpt-4o"
        fallbacks = _parse_deployments(settings.azure_openai_fallback_deployments)
        if not fallbacks:
            fallbacks = list(_DEFAULT_FALLBACKS)
    elif role == "planning" and settings.azure_openai_planning_deployment:
        primary = settings.azure_openai_planning_deployment
        fallbacks = _parse_deployments(settings.azure_openai_planning_fallbacks)
        if not fallbacks:
            fallbacks = list(_DEFAULT_PLANNING_FALLBACKS)
    elif role == "review" and settings.azure_openai_review_deployment:
        primary = settings.azure_openai_review_deployment
        fallbacks = _parse_deployments(settings.azure_openai_review_fallbacks)
        if not fallbacks:
            fallbacks = list(_DEFAULT_REVIEW_FALLBACKS)
    elif role == "rag" and settings.azure_openai_rag_deployment:
        primary = settings.azure_openai_rag_deployment
        fallbacks = _parse_deployments(settings.azure_openai_rag_fallbacks)
        if not fallbacks:
            fallbacks = list(_DEFAULT_RAG_FALLBACKS)
    elif role == "rag":
        primary = (
            settings.azure_openai_rag_deployment
            or settings.azure_openai_planning_deployment
            or settings.azure_openai_deployment
        )
        fallbacks = _parse_deployments(settings.azure_openai_rag_fallbacks)
        if not fallbacks:
            fallbacks = list(_DEFAULT_RAG_FALLBACKS)
    elif role == "review":
        fallbacks = _parse_deployments(settings.azure_openai_review_fallbacks)
        if not fallbacks:
            fallbacks = list(_DEFAULT_REVIEW_FALLBACKS)
    elif role == "planning":
        fallbacks = _parse_deployments(settings.azure_openai_planning_fallbacks)
        if not fallbacks:
            fallbacks = list(_DEFAULT_PLANNING_FALLBACKS)
    elif role == "doc_author":
        # Non-Orbit Doc Author: keep shared default deployment (no forced gpt-4.1).
        primary = (
            settings.azure_openai_planning_deployment
            or settings.azure_openai_rag_deployment
            or settings.azure_openai_deployment
        )
        fallbacks = _parse_deployments(settings.azure_openai_fallback_deployments)
        if not fallbacks:
            fallbacks = list(_DEFAULT_FALLBACKS)
    else:
        fallbacks = _parse_deployments(settings.azure_openai_fallback_deployments)
        if not fallbacks:
            fallbacks = list(_DEFAULT_FALLBACKS)

    global_fb = _parse_deployments(settings.azure_openai_fallback_deployments)
    chain: list[str] = []
    for name in [primary, *fallbacks, *global_fb]:
        if name and name not in chain:
            chain.append(name)
    return chain


def get_llm(
    temperature: float = 0.1,
    *,
    role: str = "default",
    deployment: str | None = None,
) -> AzureChatOpenAI:
    """Return Azure OpenAI client for a specific deployment."""
    dep = deployment or deployment_chain(role=role)[0]
    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.effective_api_key,
        azure_deployment=dep,
        api_version=settings.azure_openai_api_version,
        temperature=temperature,
    )


def is_rate_limit_error(exc: BaseException) -> bool:
    if getattr(exc, "status_code", None) == 429:
        return True
    code = getattr(exc, "code", None) or ""
    if str(code).lower() in ("rate_limit_exceeded", "too_many_requests"):
        return True
    return bool(_RATE_LIMIT_RE.search(str(exc)))


def is_transient_error(exc: BaseException) -> bool:
    if is_rate_limit_error(exc):
        return True
    status = getattr(exc, "status_code", None)
    if status in (429, 500, 502, 503, 504):
        return True
    return bool(_TRANSIENT_RE.search(str(exc)))


def user_facing_llm_error(context: str = "request") -> str:
    """WhatsApp-safe message — never expose raw API errors."""
    return (
        f"I hit a temporary AI capacity limit while handling your {context}. "
        "Please wait a minute and try again, or say *check my repos* to restart.\n\n"
        "Your task was not changed."
    )


async def invoke_llm(
    messages: Sequence[BaseMessage],
    *,
    temperature: float = 0.1,
    role: str = "default",
) -> Any:
    """
    Invoke LLM with per-deployment retries and automatic fallback across deployments.
    Raises the last exception if every deployment fails.
    """
    chain = deployment_chain(role=role)
    last_exc: BaseException | None = None
    max_retries = max(1, settings.llm_max_retries_per_deployment)
    base_delay = max(0.5, settings.llm_retry_base_delay_sec)

    for dep in chain:
        llm = get_llm(temperature=temperature, role=role, deployment=dep)
        for attempt in range(max_retries):
            try:
                response = await llm.ainvoke(messages)
                if dep != chain[0]:
                    logger.info("LLM succeeded on fallback deployment %s (role=%s)", dep, role)
                return response
            except Exception as exc:
                last_exc = exc
                if not is_transient_error(exc):
                    logger.warning(
                        "LLM non-transient error on %s (role=%s): %s",
                        dep,
                        role,
                        exc,
                    )
                    break
                delay = base_delay * (2**attempt)
                logger.warning(
                    "LLM transient error on %s (role=%s) attempt %d/%d — retry in %.1fs: %s",
                    dep,
                    role,
                    attempt + 1,
                    max_retries,
                    delay,
                    exc,
                )
                if attempt + 1 < max_retries:
                    await asyncio.sleep(delay)
        logger.info("LLM exhausted retries for deployment %s (role=%s), trying next", dep, role)

    assert last_exc is not None
    raise last_exc
