"""Azure FinOps / Cloud Portal — ARM inventory + Cost Management (app-only).

Feature: ENABLE_AZURE_FINOPS_AGENT (default false).
Uses httpx + client credentials; no new package dependency.
Separate from azure_devops.py (AzDO PAT) and coding gates.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from agent.config import settings
from agent.core.logging import get_logger

logger = get_logger(__name__)

# Cost Management is tightly rate-limited (~tens of queries/min/sub).
# Backend absorbs 429s: retry + spacing + short cache. Never surface raw ARM errors to chat.
_COST_CACHE: dict[str, dict[str, Any]] = {}
_COST_LOCK = asyncio.Lock()
_LAST_COST_CALL_AT = 0.0
_COST_CACHE_TTL_SEC = 300.0
_COST_MIN_GAP_SEC = 2.5
_COST_RETRY_MAX = 4

_ARM = "https://management.azure.com"
_ARM_SCOPE = "https://management.azure.com/.default"
_token_cache: dict[str, Any] = {"access_token": "", "expires_at": 0.0}


def _arm_tenant() -> str:
    return (settings.azure_arm_tenant_id or settings.ms_graph_tenant_id or "").strip()


def _arm_client_id() -> str:
    return (settings.azure_arm_client_id or settings.ms_graph_client_id or "").strip()


def _arm_client_secret() -> str:
    dedicated = settings.azure_arm_client_secret.get_secret_value()
    if dedicated:
        return dedicated
    return settings.ms_graph_client_secret.get_secret_value()


def arm_configured() -> bool:
    return bool(_arm_tenant() and _arm_client_id() and _arm_client_secret())


def azure_finops_ready() -> bool:
    return bool(settings.enable_azure_finops_agent and arm_configured())


def clear_arm_token_cache() -> None:
    _token_cache["access_token"] = ""
    _token_cache["expires_at"] = 0.0


async def get_arm_token(*, force_refresh: bool = False) -> str:
    now = time.time()
    if (
        not force_refresh
        and _token_cache["access_token"]
        and float(_token_cache["expires_at"]) > now + 60
    ):
        return str(_token_cache["access_token"])

    if not arm_configured():
        raise RuntimeError(
            "Azure ARM credentials not configured "
            "(set AZURE_ARM_* or MS_GRAPH_* app credentials)"
        )

    tenant = _arm_tenant()
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = {
        "client_id": _arm_client_id(),
        "client_secret": _arm_client_secret(),
        "scope": _ARM_SCOPE,
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


async def arm_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    max_retries: int | None = None,
) -> dict[str, Any]:
    """ARM call with auth refresh + 429/503 backoff (Cost Management friendly)."""
    url = path if path.startswith("http") else f"{_ARM}{path}"
    is_cost = "Microsoft.CostManagement" in url or "/providers/Microsoft.CostManagement/" in url
    attempts = int(max_retries) if max_retries is not None else (_COST_RETRY_MAX if is_cost else 3)
    last_detail = ""
    last_resp: httpx.Response | None = None
    for attempt in range(max(1, attempts)):
        token = await get_arm_token(force_refresh=(attempt > 0 and last_resp is not None and last_resp.status_code in (401, 403)))
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.request(
                method, url, headers=headers, json=json_body, params=params
            )
            last_resp = resp
            if resp.status_code < 400:
                if resp.status_code == 204 or not resp.content:
                    return {}
                return resp.json()
            last_detail = f"{resp.status_code}: {(resp.text or '')[:400]}"
            logger.error("ARM %s %s -> %s (attempt %s)", method, path, last_detail, attempt + 1)
            if resp.status_code in (401, 403) and attempt == 0:
                clear_arm_token_cache()
                continue
            if resp.status_code in (429, 503) and attempt < attempts - 1:
                wait = _retry_after_seconds(resp, attempt)
                logger.warning(
                    "ARM throttle/backoff status=%s wait=%.1fs path=%s",
                    resp.status_code,
                    wait,
                    path,
                )
                await asyncio.sleep(wait)
                continue
            raise httpx.HTTPStatusError(
                f"ARM error '{last_detail}' for url '{url}'",
                request=resp.request,
                response=resp,
            )
    raise RuntimeError(f"ARM request failed: {last_detail}")


def _retry_after_seconds(resp: httpx.Response, attempt: int) -> float:
    raw = (resp.headers.get("Retry-After") or "").strip()
    if raw.isdigit():
        return min(30.0, max(1.0, float(raw)))
    # exponential: 2, 4, 8, 16 (cap 20)
    return min(20.0, float(2 ** (attempt + 1)))


def classify_cost_failure(exc: BaseException | str | None) -> str:
    """Map ARM failures to stable kinds: throttled | forbidden | transient | unknown."""
    text = str(exc or "")
    code = None
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        code = exc.response.status_code
    if code is None:
        m = re.search(r"\b(429|503|401|403|500|502|504)\b", text)
        code = int(m.group(1)) if m else None
    if code == 429 or "too many requests" in text.lower():
        return "throttled"
    if code in (401, 403):
        return "forbidden"
    if code in (500, 502, 503, 504):
        return "transient"
    return "unknown"


def user_safe_cost_note(
    kind: str | None,
    *,
    used_cache: bool = False,
) -> str:
    """Chat-safe copy — never includes URLs, JSON, or ARM status payloads."""
    k = (kind or "").strip().lower()
    if used_cache:
        return (
            "Showing the latest saved cost snapshot while Azure Cost Management "
            "catches up. Ask again in a few minutes for a fresh refresh."
        )
    if k == "throttled":
        return (
            "Cost figures are briefly delayed (Azure Cost Management is busy). "
            "Inventory below is current — ask **costs** or **deep scan** again "
            "in a few minutes for amounts."
        )
    if k == "forbidden":
        return (
            "Cost data needs **Cost Management Reader** on this subscription. "
            "Inventory is still available."
        )
    if k == "transient":
        return (
            "Cost figures are temporarily unavailable. "
            "Inventory is ready — try **costs** again shortly."
        )
    return (
        "Cost figures are temporarily unavailable. "
        "Inventory is ready — try **costs** again shortly."
    )


def _cost_cache_key(subscription_id: str, group_by: str, days: int) -> str:
    return f"{subscription_id}|{group_by}|{days}"


def _get_cost_cache(key: str) -> dict[str, Any] | None:
    row = _COST_CACHE.get(key)
    if not row:
        return None
    if time.time() - float(row.get("ts") or 0) > _COST_CACHE_TTL_SEC:
        return None
    return row.get("result")


def _set_cost_cache(key: str, result: dict[str, Any]) -> None:
    if not result.get("ok"):
        return
    _COST_CACHE[key] = {"ts": time.time(), "result": dict(result)}


def clear_cost_query_cache() -> None:
    _COST_CACHE.clear()


async def list_subscriptions() -> list[dict[str, Any]]:
    body = await arm_request("GET", "/subscriptions?api-version=2020-01-01")
    rows = []
    for item in body.get("value") or []:
        rows.append(
            {
                "id": item.get("subscriptionId") or "",
                "name": item.get("displayName") or item.get("subscriptionId") or "",
                "state": (item.get("state") or ""),
                "tenant_id": (item.get("tenantId") or ""),
            }
        )
    rows.sort(key=lambda r: (r.get("state") != "Enabled", (r.get("name") or "").lower()))
    return rows


async def list_resource_groups(subscription_id: str) -> list[dict[str, Any]]:
    sid = (subscription_id or "").strip()
    body = await arm_request(
        "GET",
        f"/subscriptions/{sid}/resourcegroups?api-version=2021-04-01",
    )
    rows = []
    for item in body.get("value") or []:
        rows.append(
            {
                "name": item.get("name") or "",
                "location": item.get("location") or "",
                "id": item.get("id") or "",
            }
        )
    rows.sort(key=lambda r: (r.get("name") or "").lower())
    return rows


async def list_resources(
    subscription_id: str,
    *,
    resource_group: str | None = None,
) -> list[dict[str, Any]]:
    sid = (subscription_id or "").strip()
    rg = (resource_group or "").strip()
    if rg:
        path = (
            f"/subscriptions/{sid}/resourceGroups/{rg}/resources"
            f"?api-version=2021-04-01"
        )
    else:
        path = f"/subscriptions/{sid}/resources?api-version=2021-04-01"
    rows: list[dict[str, Any]] = []
    next_url: str | None = path if path.startswith("http") else f"{_ARM}{path}"
    # Cap pages for chat UX
    for _ in range(8):
        if not next_url:
            break
        body = await arm_request("GET", next_url)
        for item in body.get("value") or []:
            rtype = item.get("type") or ""
            rows.append(
                {
                    "name": item.get("name") or "",
                    "type": rtype,
                    "type_short": rtype.split("/")[-1] if rtype else "",
                    "location": item.get("location") or "",
                    "id": item.get("id") or "",
                    "rg": _rg_from_id(item.get("id") or ""),
                    "sku": ((item.get("sku") or {}) or {}).get("name")
                    or ((item.get("sku") or {}) or {}).get("tier")
                    or "",
                    "kind": item.get("kind") or "",
                }
            )
        next_url = body.get("nextLink")
    rows.sort(key=lambda r: ((r.get("rg") or ""), (r.get("type") or ""), (r.get("name") or "")))
    return rows


def _rg_from_id(resource_id: str) -> str:
    parts = (resource_id or "").split("/")
    try:
        i = parts.index("resourceGroups")
        return parts[i + 1]
    except (ValueError, IndexError):
        return ""


def _cost_period(days: int) -> tuple[str, str]:
    end = datetime.now(UTC).date()
    start = end - timedelta(days=max(1, int(days)))
    return start.isoformat(), end.isoformat()


async def query_costs_result(
    subscription_id: str,
    *,
    group_by: str = "ResourceGroupName",
    days: int | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Cost Management Query with retry spacing, cache, and chat-safe failures.

    Do not conflate API errors with zero spend. Never put raw ARM payloads in `error`
    for UI — use `error_kind` + `user_note` instead (details stay in logs).
    """
    global _LAST_COST_CALL_AT

    sid = (subscription_id or "").strip()
    days_n = int(days if days is not None else settings.azure_finops_cost_days)
    grouping_name = group_by
    cache_key = _cost_cache_key(sid, grouping_name, days_n)

    if use_cache:
        cached = _get_cost_cache(cache_key)
        if cached is not None:
            out = dict(cached)
            out["from_cache"] = True
            out["error_kind"] = None
            out["error"] = None
            out["user_note"] = None
            return out

    start, end = _cost_period(days_n)
    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": start, "to": end},
        "dataset": {
            "granularity": "None",
            "aggregation": {
                "totalCost": {"name": "Cost", "function": "Sum"},
            },
            "grouping": [{"type": "Dimension", "name": grouping_name}],
        },
    }
    path_url = (
        f"/subscriptions/{sid}/providers/Microsoft.CostManagement/query"
        f"?api-version=2023-11-01"
    )

    async with _COST_LOCK:
        gap = _COST_MIN_GAP_SEC - (time.time() - float(_LAST_COST_CALL_AT or 0.0))
        if gap > 0:
            await asyncio.sleep(gap)
        try:
            resp = await arm_request(
                "POST", path_url, json_body=body, max_retries=_COST_RETRY_MAX
            )
            _LAST_COST_CALL_AT = time.time()
        except Exception as exc:
            _LAST_COST_CALL_AT = time.time()
            kind = classify_cost_failure(exc)
            logger.warning(
                "Cost query failed sub=%s group=%s kind=%s err=%s",
                sid,
                grouping_name,
                kind,
                str(exc)[:240],
            )
            cached = _get_cost_cache(cache_key) if use_cache else None
            if cached is not None:
                out = dict(cached)
                out["ok"] = True
                out["from_cache"] = True
                out["error_kind"] = kind
                out["error"] = None
                out["user_note"] = user_safe_cost_note(kind, used_cache=True)
                return out
            return {
                "ok": False,
                "rows": [],
                "error": None,
                "error_kind": kind,
                "from_cache": False,
                "user_note": user_safe_cost_note(kind),
                "group_by": grouping_name,
            }

    columns = [c.get("name") for c in (resp.get("properties") or {}).get("columns") or []]
    rows_out: list[dict[str, Any]] = []
    for row in (resp.get("properties") or {}).get("rows") or []:
        mapped = {columns[i]: row[i] for i in range(min(len(columns), len(row)))}
        name = (
            mapped.get(grouping_name)
            or mapped.get("ResourceId")
            or mapped.get("ServiceName")
            or "unknown"
        )
        cost = mapped.get("Cost") or mapped.get("totalCost") or 0
        try:
            cost_f = float(cost)
        except (TypeError, ValueError):
            cost_f = 0.0
        currency = mapped.get("Currency") or "USD"
        rows_out.append(
            {
                "name": str(name),
                "cost": cost_f,
                "currency": str(currency),
            }
        )
    rows_out.sort(key=lambda r: -float(r.get("cost") or 0))
    result = {
        "ok": True,
        "rows": rows_out,
        "error": None,
        "error_kind": None,
        "from_cache": False,
        "user_note": None,
        "group_by": grouping_name,
    }
    _set_cost_cache(cache_key, result)
    return result


async def query_costs(
    subscription_id: str,
    *,
    group_by: str = "ResourceGroupName",
    days: int | None = None,
) -> list[dict[str, Any]]:
    """Cost Management Query — ActualCost grouped by dimension (rows only)."""
    result = await query_costs_result(
        subscription_id, group_by=group_by, days=days
    )
    return list(result.get("rows") or [])


_FREE_PLAN_SKUS = {
    "F1",
    "D1",
    "FREE",
    "Y1",
    "DYNAMIC",
    "SHARED",
}


def _resource_cost_lookup(
    cost_by_resource: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in cost_by_resource or []:
        raw = str(row.get("name") or "")
        lower = raw.lower()
        out[lower] = row
        tail = lower.rstrip("/").split("/")[-1]
        if tail:
            prev = out.get(tail)
            if prev is None or float(row.get("cost") or 0) >= float(prev.get("cost") or 0):
                out[tail] = row
    return out


def build_recommendations(
    resources: list[dict[str, Any]],
    *,
    cost_by_resource: list[dict[str, Any]] | None = None,
    cost_by_rg: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Cost-first FinOps tips. Free/Y1 plans stay info unless they carry real spend."""
    tips: list[dict[str, Any]] = []
    by_type: dict[str, list[dict]] = {}
    for r in resources:
        by_type.setdefault((r.get("type") or "").lower(), []).append(r)

    cost_rows = cost_by_resource or []
    rg_rows = cost_by_rg or []
    total = sum(float(c.get("cost") or 0) for c in cost_rows) or 0.0
    if total <= 0:
        total = sum(float(c.get("cost") or 0) for c in rg_rows) or 0.0
    currency = (
        (cost_rows[0].get("currency") if cost_rows else None)
        or (rg_rows[0].get("currency") if rg_rows else None)
        or "USD"
    )
    share_t = float(getattr(settings, "azure_finops_high_cost_share", 0.25) or 0.25)
    abs_t = float(getattr(settings, "azure_finops_high_cost_abs", 50.0) or 50.0)
    cost_lookup = _resource_cost_lookup(cost_rows)

    if rg_rows and total > 0:
        for row in rg_rows[:8]:
            cost = float(row.get("cost") or 0)
            if cost <= 0:
                continue
            share = cost / total
            if share < max(0.08, share_t * 0.4) and cost < abs_t:
                continue
            sev = "high" if share >= share_t or cost >= abs_t else "medium"
            tips.append(
                {
                    "severity": sev,
                    "title": f"High-cost RG `{row.get('name')}` ({share:.0%})",
                    "detail": (
                        f"≈ {cost:,.2f} {row.get('currency') or currency} in the window "
                        f"({share:.0%} of listed spend). Right-size or move idle workloads."
                    ),
                    "cost": cost,
                }
            )

    if cost_rows and total > 0:
        for row in cost_rows[:10]:
            cost = float(row.get("cost") or 0)
            if cost <= 0:
                continue
            share = cost / total
            if share < share_t and cost < abs_t:
                continue
            short = str(row.get("name") or "").rstrip("/").split("/")[-1]
            tips.append(
                {
                    "severity": "high",
                    "title": f"Top cost resource `{short}` ({share:.0%})",
                    "detail": (
                        f"≈ {cost:,.2f} {row.get('currency') or currency} "
                        f"({share:.0%} of listed spend). Review SKU / idle hours."
                    ),
                    "cost": cost,
                }
            )

    rgs_with_resources = {r.get("rg") for r in resources if r.get("rg")}
    for row in rg_rows:
        name = row.get("name") or ""
        cost = float(row.get("cost") or 0)
        if name and name not in rgs_with_resources and cost <= 0.01:
            tips.append(
                {
                    "severity": "low",
                    "title": f"Possibly empty RG `{name}`",
                    "detail": "No resources listed and ~0 spend — confirm then delete for hygiene.",
                    "cost": 0.0,
                }
            )

    for r in by_type.get("microsoft.web/serverfarms", []):
        sku = (r.get("sku") or "unknown").upper()
        name = r.get("name") or ""
        # Only use resource-level cost — never attribute whole RG spend to a free plan.
        row = cost_lookup.get(name.lower())
        cost = float((row or {}).get("cost") or 0)
        freeish = sku in _FREE_PLAN_SKUS or sku.startswith("Y1")
        if freeish and cost < 1.0:
            tips.append(
                {
                    "severity": "info",
                    "title": f"Free/consumption plan `{name}` ({sku})",
                    "detail": "Near-zero spend — hygiene only, not a bill driver.",
                    "cost": cost,
                }
            )
            continue
        share = (cost / total) if total > 0 else 0.0
        if share >= 0.15 or cost >= abs_t:
            sev = "high"
        elif cost > 0:
            sev = "medium"
        elif not freeish:
            sev = "medium"  # paid SKU with unknown cost — review capacity
        else:
            sev = "info"
        tips.append(
            {
                "severity": sev,
                "title": f"App Service plan `{name}` ({sku})",
                "detail": (
                    f"Window spend ≈ {cost:,.2f} {currency} ({share:.0%}). "
                    "Verify site count; downgrade idle paid plans after human approval."
                ),
                "cost": cost,
            }
        )

    for r in by_type.get("microsoft.network/publicipaddresses", []):
        name = r.get("name") or ""
        row = cost_lookup.get(name.lower())
        cost = float((row or {}).get("cost") or 0)
        tips.append(
            {
                "severity": "medium" if cost > 0 else "low",
                "title": f"Public IP `{name}`",
                "detail": "Confirm association; unused public IPs still incur charges.",
                "cost": cost,
            }
        )

    for r in by_type.get("microsoft.compute/disks", []):
        name = r.get("name") or ""
        row = cost_lookup.get(name.lower())
        cost = float((row or {}).get("cost") or 0)
        tips.append(
            {
                "severity": "medium" if cost > 0 else "low",
                "title": f"Managed disk `{name}`",
                "detail": "Check if attached; unattached disks are a common idle spend source.",
                "cost": cost,
            }
        )

    for r in by_type.get("microsoft.compute/virtualmachines", []):
        name = r.get("name") or ""
        row = cost_lookup.get(name.lower())
        cost = float((row or {}).get("cost") or 0)
        tips.append(
            {
                "severity": "high" if cost > 0 else "medium",
                "title": f"VM `{name}`",
                "detail": "Prefer deallocate (not stop) when idle; review disk + public IP spend.",
                "cost": cost,
            }
        )

    if not tips:
        tips.append(
            {
                "severity": "info",
                "title": "No strong idle signals from heuristics",
                "detail": "Review top cost rows and right-size SKUs for POC vs production.",
                "cost": 0.0,
            }
        )

    severity_rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
    tips.sort(
        key=lambda t: (
            severity_rank.get(t.get("severity") or "info", 9),
            -float(t.get("cost") or 0),
        )
    )
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for t in tips:
        key = t["title"]
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "severity": str(t.get("severity") or "info"),
                "title": str(t.get("title") or ""),
                "detail": str(t.get("detail") or ""),
            }
        )
    return out[:20]



async def devops_org_snapshot() -> dict[str, Any]:
    """Light AzDO portal summary using existing PAT (optional)."""
    from agent.services import azure_devops as azdo

    if not (settings.azdo_org_url and settings.azdo_pat.get_secret_value()):
        return {"configured": False, "projects": []}
    projects = await azdo.list_projects()
    slim = []
    for p in (projects or [])[:12]:
        name = p.get("name") or ""
        entry: dict[str, Any] = {"name": name, "id": p.get("id") or ""}
        try:
            repos = await azdo.list_repositories(name)
            entry["repos"] = len(repos or [])
        except Exception:
            entry["repos"] = None
        try:
            pipes = await azdo.list_pipelines(name)
            entry["pipelines"] = len(pipes or [])
        except Exception:
            entry["pipelines"] = None
        slim.append(entry)
    return {
        "configured": True,
        "org": settings.azdo_org_url.rstrip("/"),
        "projects": slim,
    }
