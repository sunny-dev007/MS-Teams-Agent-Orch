"""Microsoft Graph client — Docs Agent only (SharePoint / OneDrive / OneNote).

Feature: Release Agent Fabric — ENABLE_DOCS_AGENT must be true and credentials set.
Uses httpx + client credentials; no new package dependency.
"""

from __future__ import annotations

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


async def get_app_token() -> str:
    """Client-credentials token for application permissions."""
    now = time.time()
    if _token_cache["access_token"] and float(_token_cache["expires_at"]) > now + 60:
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
    return token


async def graph_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
) -> dict[str, Any]:
    token = await get_app_token()
    url = path if path.startswith("http") else f"{_GRAPH}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.request(method, url, headers=headers, json=json_body, params=params)
        if resp.status_code >= 400:
            logger.error("Graph %s %s -> %s %s", method, path, resp.status_code, resp.text[:500])
            resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()
