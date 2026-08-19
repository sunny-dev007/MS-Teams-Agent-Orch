"""Microsoft Graph client — SharePoint / OneDrive / OneNote (app-only).

Feature: Release Agent Fabric + Document Knowledge Fabric.
Uses httpx + client credentials; no new package dependency.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)

_GRAPH = "https://graph.microsoft.com/v1.0"
_token_cache: dict[str, Any] = {"access_token": "", "expires_at": 0.0}


def graph_configured() -> bool:
    return bool(
        settings.ms_graph_tenant_id
        and settings.ms_graph_client_id
        and settings.ms_graph_client_secret.get_secret_value()
    )


def docs_agent_ready() -> bool:
    return bool(settings.enable_docs_agent and graph_configured())


def doc_knowledge_ready() -> bool:
    return bool(settings.enable_doc_knowledge and graph_configured())


def outlook_agent_ready() -> bool:
    return bool(settings.enable_outlook_agent and graph_configured())


def clear_app_token_cache() -> None:
    """Drop cached client-credentials token (e.g. after new admin consent)."""
    _token_cache["access_token"] = ""
    _token_cache["expires_at"] = 0.0


def decode_token_roles(token: str) -> list[str]:
    """Read `roles` from an app-only JWT (no signature verify — token from our STS)."""
    try:
        parts = (token or "").split(".")
        if len(parts) < 2:
            return []
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
        roles = payload.get("roles") or []
        return sorted(str(r) for r in roles) if isinstance(roles, list) else []
    except Exception:
        return []


async def graph_token_roles() -> list[str]:
    """Roles claim on the current app token (for health / diagnostics)."""
    if not graph_configured():
        return []
    token = await get_app_token()
    return decode_token_roles(token)


def _graph_error_detail(resp: httpx.Response) -> str:
    """Compact Graph error for logs and user-facing messages."""
    text = (resp.text or "")[:800]
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            code = err.get("code") or ""
            msg = err.get("message") or ""
            return f"{resp.status_code} {code}: {msg}".strip()
    except Exception:
        pass
    return f"{resp.status_code}: {text[:400]}"


async def get_user_profile(user_id: str) -> dict[str, Any]:
    """Fetch Entra user profile by OID or UPN (app-only User.Read.All / Directory.Read.All)."""
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required")
    from urllib.parse import quote

    # Prefer mail + UPN for Boards identity matching
    path = (
        f"/users/{quote(uid)}"
        f"?$select=id,displayName,mail,userPrincipalName,givenName,otherMails"
    )
    return await graph_request("GET", path)


async def get_app_token(*, force_refresh: bool = False) -> str:
    """Client-credentials token for application permissions."""
    now = time.time()
    if (
        not force_refresh
        and _token_cache["access_token"]
        and float(_token_cache["expires_at"]) > now + 60
    ):
        return str(_token_cache["access_token"])

    if not graph_configured():
        raise RuntimeError("Microsoft Graph credentials not configured")

    tenant = settings.ms_graph_tenant_id
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = {
        "client_id": settings.ms_graph_client_id,
        "client_secret": settings.ms_graph_client_secret.get_secret_value(),
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, data=data)
        resp.raise_for_status()
        body = resp.json()
    token = body["access_token"]
    _token_cache["access_token"] = token
    _token_cache["expires_at"] = now + float(body.get("expires_in", 3600))
    roles = decode_token_roles(token)
    logger.info(
        "Graph app token acquired roles=%s has_Mail.Read=%s",
        roles,
        "Mail.Read" in roles,
    )
    return token


async def graph_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
) -> dict[str, Any]:
    url = path if path.startswith("http") else f"{_GRAPH}{path}"
    last_detail = ""
    for attempt in range(2):
        token = await get_app_token(force_refresh=(attempt > 0))
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.request(
                method, url, headers=headers, json=json_body, params=params
            )
            if resp.status_code < 400:
                if resp.status_code == 204 or not resp.content:
                    return {}
                return resp.json()
            last_detail = _graph_error_detail(resp)
            logger.error("Graph %s %s -> %s", method, path, last_detail)
            # Stale token after new admin consent is a common 401/403 cause
            if resp.status_code in (401, 403) and attempt == 0:
                clear_app_token_cache()
                logger.warning(
                    "Graph %s — clearing token cache and retrying once",
                    resp.status_code,
                )
                continue
            raise httpx.HTTPStatusError(
                f"Client error '{last_detail}' for url '{url}'",
                request=resp.request,
                response=resp,
            )
    raise RuntimeError(f"Graph request failed: {last_detail}")


async def graph_request_bytes(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    accept: str = "*/*",
    timeout: float = 120.0,
) -> tuple[bytes, str]:
    """Binary/text download (drive content, OneNote HTML). Returns (body, content_type)."""
    url = path if path.startswith("http") else f"{_GRAPH}{path}"
    last_detail = ""
    for attempt in range(2):
        token = await get_app_token(force_refresh=(attempt > 0))
        headers = {"Authorization": f"Bearer {token}", "Accept": accept}
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.request(method, url, headers=headers, params=params)
            if resp.status_code < 400:
                return resp.content, resp.headers.get("content-type", "")
            last_detail = _graph_error_detail(resp)
            logger.error("Graph bytes %s %s -> %s", method, path, last_detail)
            if resp.status_code in (401, 403) and attempt == 0:
                clear_app_token_cache()
                continue
            resp.raise_for_status()
    raise RuntimeError(f"Graph bytes request failed: {last_detail}")


async def graph_paginate(
    path: str,
    *,
    params: dict | None = None,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    """Collect `value` arrays across @odata.nextLink pages."""
    items: list[dict[str, Any]] = []
    next_path: str | None = path
    next_params = params
    for _ in range(max_pages):
        if not next_path:
            break
        body = await graph_request("GET", next_path, params=next_params)
        batch = body.get("value") or []
        if isinstance(batch, list):
            items.extend([x for x in batch if isinstance(x, dict)])
        link = body.get("@odata.nextLink")
        if not link:
            break
        next_path = str(link)
        next_params = None
    return items
