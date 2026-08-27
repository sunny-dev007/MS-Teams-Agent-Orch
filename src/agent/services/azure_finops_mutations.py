"""Azure FinOps mutations — SKU resize with human-in-the-loop apply.

Feature: ENABLE_AZURE_FINOPS_MUTATIONS (default false).
Also requires ENABLE_AZURE_FINOPS_AGENT=true.

Safety:
- No Azure writes unless mutations flag is ON and user sends APPLY PLAN <id>
- Delete/remove refused unless ENABLE_AZURE_FINOPS_ALLOW_DELETE=true
- SKU resize does not move region (called out in the plan)
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from agent.config import settings
from agent.core.logging import get_logger
from agent.services import azure_finops

logger = get_logger(__name__)

ALLOW_TYPES = {
    "microsoft.web/serverfarms",
    "microsoft.sql/servers/databases",
}

APP_SERVICE_SKUS: dict[str, dict[str, Any]] = {
    "F1": {"name": "F1", "tier": "Free", "size": "F1", "family": "F", "capacity": 1},
    "D1": {"name": "D1", "tier": "Shared", "size": "D1", "family": "D", "capacity": 1},
    "B1": {"name": "B1", "tier": "Basic", "size": "B1", "family": "B", "capacity": 1},
    "B2": {"name": "B2", "tier": "Basic", "size": "B2", "family": "B", "capacity": 1},
    "B3": {"name": "B3", "tier": "Basic", "size": "B3", "family": "B", "capacity": 1},
    "S1": {"name": "S1", "tier": "Standard", "size": "S1", "family": "S", "capacity": 1},
    "S2": {"name": "S2", "tier": "Standard", "size": "S2", "family": "S", "capacity": 1},
    "S3": {"name": "S3", "tier": "Standard", "size": "S3", "family": "S", "capacity": 1},
    "P0V3": {"name": "P0v3", "tier": "Premium0V3", "size": "P0v3", "family": "Pv3", "capacity": 1},
    "P1V2": {"name": "P1v2", "tier": "PremiumV2", "size": "P1v2", "family": "Pv2", "capacity": 1},
    "P1V3": {"name": "P1v3", "tier": "PremiumV3", "size": "P1v3", "family": "Pv3", "capacity": 1},
}

SQL_SKUS: dict[str, dict[str, Any]] = {
    "BASIC": {"name": "Basic", "tier": "Basic", "capacity": 5},
    "S0": {"name": "Standard", "tier": "Standard", "capacity": 10},
    "S1": {"name": "Standard", "tier": "Standard", "capacity": 20},
    "S2": {"name": "Standard", "tier": "Standard", "capacity": 50},
    "S3": {"name": "Standard", "tier": "Standard", "capacity": 100},
    "P1": {"name": "Premium", "tier": "Premium", "capacity": 125},
    "P2": {"name": "Premium", "tier": "Premium", "capacity": 250},
}

_DELETE_RE = re.compile(r"\b(delete|remove|destroy|deprovision|drop)\b", re.I)
_SKU_TOKEN_RE = re.compile(
    r"\b(f1|d1|b1|b2|b3|s0|s1|s2|s3|p0v3|p1v2|p1v3|p1|p2|basic)\b",
    re.I,
)
_RESIZE_RE = re.compile(
    r"\b(resize|change\s+sku|set\s+sku|scale|change\s+plan|downgrade|upgrade|"
    r"down\s*size|up\s*size)\b",
    re.I,
)
_DOWN_RE = re.compile(r"\b(downgrade|down\s*size|cheaper|reduce\s+sku|lower\s+sku)\b", re.I)
_UP_RE = re.compile(r"\b(upgrade|up\s*size|higher\s+sku|scale\s+up)\b", re.I)
_SCAN_RE = re.compile(
    r"\bdeep\s+(?:cost\s+)?scan\b|"
    r"(?:deep\s+)?(?:scan|audit)\s+(?:my\s+)?azure|"
    r"high\s+cost\s+alert|sudden\s+(?:cost|spend)|expensive\s+resources?|"
    r"scan\s+(?:all\s+)?(?:my\s+)?resources?",
    re.I,
)
_APPLY_RE = re.compile(r"(?i)^\s*apply\s+plan\s+([a-z0-9\-]+)\s*$")
_REJECT_RE = re.compile(r"(?i)^\s*reject\s+plan(?:\s+[a-z0-9\-]+)?\s*$")


def mutations_enabled() -> bool:
    return bool(
        settings.enable_azure_finops_agent
        and bool(getattr(settings, "enable_azure_finops_mutations", False))
        and azure_finops.arm_configured()
    )


def delete_allowed() -> bool:
    return bool(
        mutations_enabled()
        and bool(getattr(settings, "enable_azure_finops_allow_delete", False))
    )


def is_delete_request(msg: str) -> bool:
    return bool(_DELETE_RE.search(msg or ""))


def is_resize_request(msg: str) -> bool:
    return bool(_RESIZE_RE.search(msg or ""))


def is_downgrade_request(msg: str) -> bool:
    return bool(_DOWN_RE.search(msg or ""))


def is_upgrade_request(msg: str) -> bool:
    return bool(_UP_RE.search(msg or ""))


def is_scan_request(msg: str) -> bool:
    return bool(_SCAN_RE.search(msg or ""))


def parse_apply_plan_id(msg: str) -> str | None:
    m = _APPLY_RE.match((msg or "").strip())
    return m.group(1) if m else None


def is_reject_plan(msg: str) -> bool:
    return bool(_REJECT_RE.match((msg or "").strip()))


def _norm_type(t: str) -> str:
    return (t or "").strip().lower()


def is_mutable_type(resource_type: str) -> bool:
    return _norm_type(resource_type) in ALLOW_TYPES


def extract_sku_token(msg: str) -> str | None:
    m = _SKU_TOKEN_RE.search(msg or "")
    return m.group(1).upper() if m else None


def _app_sku_key(token: str) -> str:
    t = (token or "").upper().replace(" ", "")
    return {"BASIC": "B1", "FREE": "F1"}.get(t, t)


def resolve_target_sku(resource_type: str, token: str) -> dict[str, Any] | None:
    nt = _norm_type(resource_type)
    if nt == "microsoft.web/serverfarms":
        return APP_SERVICE_SKUS.get(_app_sku_key(token))
    if nt == "microsoft.sql/servers/databases":
        return SQL_SKUS.get((token or "").upper())
    return None


def suggest_downgrade_sku(resource_type: str, current_label: str) -> str | None:
    nt = _norm_type(resource_type)
    cur = (current_label or "").upper()
    if nt == "microsoft.web/serverfarms":
        ladder = ["P1V3", "P1V2", "P0V3", "S3", "S2", "S1", "B3", "B2", "B1", "D1", "F1"]
        for i, sku in enumerate(ladder):
            if sku in cur:
                return ladder[i + 1] if i + 1 < len(ladder) else None
        return "B1"
    if nt == "microsoft.sql/servers/databases":
        ladder = ["P2", "P1", "S3", "S2", "S1", "S0", "BASIC"]
        for i, sku in enumerate(ladder):
            if sku == cur or sku in cur:
                return ladder[i + 1] if i + 1 < len(ladder) else None
        return "S0"
    return None


def _sku_label(sku_obj: dict | None) -> str:
    if not sku_obj:
        return "unknown"
    name = sku_obj.get("name") or ""
    tier = sku_obj.get("tier") or ""
    cap = sku_obj.get("capacity")
    if name and tier:
        return f"{name}/{tier}" + (f"x{cap}" if cap is not None else "")
    return name or tier or "unknown"


def _rg_from_id(resource_id: str) -> str:
    parts = (resource_id or "").split("/")
    try:
        i = parts.index("resourceGroups")
        return parts[i + 1]
    except (ValueError, IndexError):
        return ""


async def get_app_service_plan(resource_id: str) -> dict[str, Any]:
    rid = (resource_id or "").rstrip("/")
    return await azure_finops.arm_request("GET", f"{rid}?api-version=2023-12-01")


async def get_sql_database(resource_id: str) -> dict[str, Any]:
    rid = (resource_id or "").rstrip("/")
    return await azure_finops.arm_request(
        "GET", f"{rid}?api-version=2023-08-01-preview"
    )


async def list_sites_on_plan(subscription_id: str, plan_id: str) -> list[dict[str, Any]]:
    sid = (subscription_id or "").strip()
    body = await azure_finops.arm_request(
        "GET",
        f"/subscriptions/{sid}/providers/Microsoft.Web/sites?api-version=2023-12-01",
    )
    plan_l = (plan_id or "").lower()
    out: list[dict[str, Any]] = []
    for site in body.get("value") or []:
        props = site.get("properties") or {}
        if (props.get("serverFarmId") or "").lower() == plan_l:
            out.append(
                {
                    "name": site.get("name"),
                    "id": site.get("id"),
                    "state": props.get("state"),
                    "location": site.get("location"),
                }
            )
    return out


async def check_web_quota(subscription_id: str, location: str) -> dict[str, Any]:
    sid = (subscription_id or "").strip()
    loc = (location or "").strip()
    if not loc:
        return {"ok": False, "note": "No location on resource — skipped quota probe.", "usages": []}
    try:
        body = await azure_finops.arm_request(
            "GET",
            f"/subscriptions/{sid}/providers/Microsoft.Web/locations/{loc}/usages"
            f"?api-version=2023-12-01",
        )
        usages = []
        for u in body.get("value") or []:
            name = u.get("name")
            if isinstance(name, dict):
                label = name.get("localizedValue") or name.get("value") or ""
            else:
                label = str(name or "")
            usages.append(
                {
                    "name": label,
                    "current": u.get("currentValue"),
                    "limit": u.get("limit"),
                    "unit": u.get("unit"),
                }
            )
        return {"ok": True, "location": loc, "usages": usages[:30]}
    except Exception as exc:
        logger.warning("Web quota check failed loc=%s err=%s", loc, exc)
        return {
            "ok": False,
            "location": loc,
            "error": str(exc),
            "note": "Quota API unavailable — plan prepared; apply may fail if SKU is blocked.",
            "usages": [],
        }


async def build_sku_change_plan(
    *,
    subscription_id: str,
    resource: dict[str, Any],
    target_sku_token: str,
    user_msg: str = "",
) -> dict[str, Any]:
    if not mutations_enabled():
        raise RuntimeError("ENABLE_AZURE_FINOPS_MUTATIONS is false")

    rtype = resource.get("type") or ""
    rid = resource.get("id") or ""
    if not is_mutable_type(rtype):
        raise ValueError(
            f"Type `{rtype}` is not in the SKU allowlist "
            "(Microsoft.Web/serverFarms, Microsoft.Sql/servers/databases)."
        )

    target = resolve_target_sku(rtype, target_sku_token)
    if not target:
        raise ValueError(
            f"Unknown target SKU `{target_sku_token}`. "
            "App Service: F1/B1/S1/P1v2/... · SQL: Basic/S0/S1/..."
        )

    nt = _norm_type(rtype)
    location = resource.get("location") or ""
    current_sku: dict[str, Any] = {}
    impact: list[str] = []
    quota: dict[str, Any] = {"ok": True, "usages": []}

    if nt == "microsoft.web/serverfarms":
        current = await get_app_service_plan(rid)
        current_sku = dict(current.get("sku") or {})
        location = current.get("location") or location
        sites = await list_sites_on_plan(subscription_id, rid)
        impact.append(f"Plan hosts **{len(sites)}** web app(s).")
        for site in sites[:8]:
            impact.append(f"- `{site.get('name')}` state={site.get('state') or '?'}")
        if len(sites) > 8:
            impact.append(f"- ... +{len(sites) - 8} more")
        impact.append(
            f"Region stays **{location or 'unchanged'}** — SKU resize does **not** move region."
        )
        if (target.get("name") or "").upper() == "F1" and len(sites) > 1:
            impact.append("Warning: F1 Free has tight app limits — verify before apply.")
        quota = await check_web_quota(subscription_id, location)
    else:
        current = await get_sql_database(rid)
        current_sku = dict(current.get("sku") or {})
        location = current.get("location") or location
        impact.append("SQL tier change may briefly affect performance/connections.")
        impact.append(
            f"Region stays **{location or 'unchanged'}** — tier change is same-region."
        )
        quota = {
            "ok": True,
            "note": "SQL DTU/vCore limits are subscription-scoped; ARM rejects if over quota.",
            "usages": [],
        }

    direction = "change"
    if _DOWN_RE.search(user_msg or ""):
        direction = "downgrade"
    elif _UP_RE.search(user_msg or ""):
        direction = "upgrade"

    return {
        "id": f"sku-{uuid.uuid4().hex[:8]}",
        "created_at": datetime.now(UTC).isoformat(),
        "action": "sku_change",
        "direction": direction,
        "subscription_id": subscription_id,
        "resource_id": rid,
        "resource_name": resource.get("name"),
        "resource_type": rtype,
        "resource_group": resource.get("rg") or _rg_from_id(rid),
        "location": location,
        "region_change": False,
        "from_sku": current_sku,
        "from_sku_label": _sku_label(current_sku),
        "to_sku": target,
        "to_sku_label": _sku_label(target),
        "to_sku_token": target_sku_token,
        "impact": impact,
        "quota": quota,
        "status": "proposed",
        "destructive": False,
    }


async def apply_sku_change_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if not mutations_enabled():
        raise RuntimeError("ENABLE_AZURE_FINOPS_MUTATIONS is false — refusing apply")
    if plan.get("action") != "sku_change" or plan.get("destructive"):
        raise RuntimeError("Plan is not an allowed SKU change")

    rid = plan.get("resource_id") or ""
    rtype = plan.get("resource_type") or ""
    to_sku = plan.get("to_sku") or {}
    nt = _norm_type(rtype)

    if nt == "microsoft.web/serverfarms":
        current = await get_app_service_plan(rid)
        body: dict[str, Any] = {"sku": to_sku}
        if current.get("location"):
            body["location"] = current["location"]
        result = await azure_finops.arm_request(
            "PATCH", f"{rid}?api-version=2023-12-01", json_body=body
        )
        return {"ok": True, "result_sku": result.get("sku") or to_sku}

    if nt == "microsoft.sql/servers/databases":
        current = await get_sql_database(rid)
        body = {"sku": to_sku}
        if current.get("location"):
            body["location"] = current["location"]
        result = await azure_finops.arm_request(
            "PATCH", f"{rid}?api-version=2023-08-01-preview", json_body=body
        )
        return {"ok": True, "result_sku": result.get("sku") or to_sku}

    raise RuntimeError(f"Unsupported type for apply: {rtype}")


async def deep_cost_scan(subscription_id: str) -> dict[str, Any]:
    """Inventory + cost window. Prefer ResourceId costs; fall back to RG totals.

    Azure often returns empty ResourceId rows while RG/Service costs are populated
    (and in billing currency such as INR). Never report a fake 0.00 USD total when
    RG costs exist.
    """
    sid = (subscription_id or "").strip()
    resources = await azure_finops.list_resources(sid)
    cost_res_result = await azure_finops.query_costs_result(sid, group_by="ResourceId")
    cost_rg_result = await azure_finops.query_costs_result(
        sid, group_by="ResourceGroupName"
    )
    cost_by_res = list(cost_res_result.get("rows") or [])
    cost_by_rg = list(cost_rg_result.get("rows") or [])

    # Primary cost series for totals / alerts
    if cost_by_res:
        primary = cost_by_res
        cost_grain = "ResourceId"
    elif cost_by_rg:
        primary = cost_by_rg
        cost_grain = "ResourceGroupName"
    else:
        primary = []
        cost_grain = "none"

    total = sum(float(c.get("cost") or 0) for c in primary) or 0.0
    currency = (
        (primary[0].get("currency") if primary else None)
        or (cost_by_rg[0].get("currency") if cost_by_rg else None)
        or (cost_by_res[0].get("currency") if cost_by_res else None)
        or "USD"
    )
    share_t = float(getattr(settings, "azure_finops_high_cost_share", 0.25))
    abs_t = float(getattr(settings, "azure_finops_high_cost_abs", 50.0))

    alerts: list[dict[str, Any]] = []
    for row in primary[:40]:
        cost = float(row.get("cost") or 0)
        share = (cost / total) if total > 0 else 0.0
        if share >= share_t or cost >= abs_t:
            alerts.append(
                {
                    "severity": "high" if share >= share_t else "medium",
                    "name": row.get("name"),
                    "cost": cost,
                    "currency": row.get("currency") or currency,
                    "share": share,
                    "grain": cost_grain,
                }
            )

    rg_cost_map = {
        str(c.get("name") or "").lower(): c for c in cost_by_rg if c.get("name")
    }
    mutable = [r for r in resources if is_mutable_type(r.get("type") or "")]
    for r in mutable:
        rg_key = str(r.get("rg") or "").lower()
        hit = rg_cost_map.get(rg_key)
        if hit:
            r["rg_window_cost"] = float(hit.get("cost") or 0)
            r["rg_currency"] = hit.get("currency") or currency

    recs = azure_finops.build_recommendations(
        resources, cost_by_resource=cost_by_res, cost_by_rg=cost_by_rg
    )
    cost_ok = bool(cost_res_result.get("ok")) or bool(cost_rg_result.get("ok"))
    cost_error = cost_res_result.get("error") or cost_rg_result.get("error")
    return {
        "resource_count": len(resources),
        "mutable_count": len(mutable),
        "mutable": mutable[:40],
        "total_cost": total,
        "currency": currency,
        "cost_grain": cost_grain,
        "cost_ok": cost_ok,
        "cost_error": cost_error,
        "resource_id_rows": len(cost_by_res),
        "alerts": alerts[:15],
        "recommendations": recs[:10],
        "cost_by_rg": cost_by_rg[:15],
    }


def find_mutable_resource(
    resources: list[dict[str, Any]],
    *,
    msg: str,
    pick_index: int | None = None,
) -> dict[str, Any] | None:
    mutable = [r for r in resources if is_mutable_type(r.get("type") or "")]
    if pick_index is not None and 0 <= pick_index < len(mutable):
        return mutable[pick_index]
    lower = (msg or "").lower()
    for r in mutable:
        name = (r.get("name") or "").lower()
        if name and name in lower:
            return r
    token = (msg or "").strip().lower()
    for r in mutable:
        if (r.get("name") or "").lower() == token:
            return r
    return None


def format_mutation_plan(plan: dict[str, Any]) -> str:
    q = plan.get("quota") or {}
    lines = [
        f"*Azure FinOps — change plan* `{plan.get('id')}`",
        "_Nothing has been changed yet. Human approval required._",
        "",
        f"• **Action:** {plan.get('direction')} SKU",
        f"• **Resource:** `{plan.get('resource_name')}`",
        f"• **Type:** `{plan.get('resource_type')}`",
        f"• **RG:** `{plan.get('resource_group')}`",
        f"• **Region:** `{plan.get('location') or '—'}` (no region move)",
        f"• **From:** `{plan.get('from_sku_label')}`",
        f"• **To:** `{plan.get('to_sku_label')}`",
        "",
        "**Impact / checks**",
    ]
    for item in plan.get("impact") or []:
        lines.append(f"- {item}")
    if q.get("ok") is False:
        lines.append(f"- Quota: {q.get('note') or q.get('error')}")
    elif q.get("usages"):
        lines.append("- Quota sample:")
        for u in (q.get("usages") or [])[:5]:
            lines.append(
                f"  - {u.get('name')}: {u.get('current')}/{u.get('limit')} {u.get('unit') or ''}"
            )
    elif q.get("note"):
        lines.append(f"- Quota: {q.get('note')}")
    lines.extend(
        [
            "",
            "Reply:",
            f"• **APPLY PLAN {plan.get('id')}** — execute",
            "• **REJECT PLAN** — cancel",
            "",
            "_Delete/remove stays blocked unless separately enabled._",
        ]
    )
    return "\n".join(lines)


def format_cost_scan(scan: dict[str, Any], *, sub_name: str) -> str:
    currency = scan.get("currency") or "USD"
    total = float(scan.get("total_cost") or 0)
    grain = scan.get("cost_grain") or "none"
    lines = [
        f"*Azure FinOps deep scan* — **{sub_name}**",
        "",
        f"Resources: **{scan.get('resource_count')}** · "
        f"SKU-mutable: **{scan.get('mutable_count')}**",
        f"Window total: **{total:,.2f} {currency}** "
        f"_(by {grain})_",
        "",
    ]
    if grain == "ResourceGroupName" and int(scan.get("resource_id_rows") or 0) == 0:
        lines.append(
            "_Per-resource Cost Management rows were empty — "
            "totals/alerts use **resource group** costs (same currency as Portal)._"
        )
        lines.append("")
    if scan.get("cost_ok") is False and scan.get("cost_error"):
        lines.append(f"_Cost query issue:_ `{scan.get('cost_error')}`")
        lines.append("")

    alerts = scan.get("alerts") or []
    if alerts:
        lines.append("**High-cost alerts**")
        lines.append("")
        for a in alerts:
            lines.append(
                f"- [{(a.get('severity') or '').upper()}] `{a.get('name')}` — "
                f"{float(a.get('cost') or 0):,.2f} {a.get('currency') or currency} "
                f"({float(a.get('share') or 0):.0%})"
            )
        lines.append("")
    else:
        lines.append("_No row crossed high-cost alert thresholds in this window._")
        lines.append("")

    cost_by_rg = scan.get("cost_by_rg") or []
    if cost_by_rg:
        lines.append("**Cost by resource group** (window)")
        lines.append("")
        lines.append("| # | Resource group | Cost |")
        lines.append("| :---: | :--- | ---: |")
        for i, c in enumerate(cost_by_rg[:10], start=1):
            lines.append(
                f"| {i} | {c.get('name')} | "
                f"{float(c.get('cost') or 0):,.2f} "
                f"{c.get('currency') or currency} |"
            )
        lines.append("")

    mutable = scan.get("mutable") or []
    if mutable:
        lines.append("**SKU-mutable resources**")
        lines.append("")
        lines.append("| # | Name | Type | SKU | RG | RG cost |")
        lines.append("| :---: | :--- | :--- | :--- | :--- | ---: |")
        for i, r in enumerate(mutable[:20], start=1):
            rg_cost = r.get("rg_window_cost")
            rg_cost_s = (
                f"{float(rg_cost):,.2f}"
                if rg_cost is not None
                else "—"
            )
            lines.append(
                f"| {i} | {r.get('name')} | {r.get('type_short') or r.get('type')} | "
                f"{r.get('sku') or '—'} | {r.get('rg') or '—'} | {rg_cost_s} |"
            )
        lines.append("")
        if mutations_enabled():
            lines.extend(
                [
                    "**Mutations ON** — plan then approve (nothing writes until APPLY):",
                    "",
                    "1. **downgrade 1 to B1** or **change sku <name> to F1**",
                    "2. Review the plan (quota / region unchanged)",
                    "3. **APPLY PLAN <id>** — execute · **REJECT PLAN** — cancel",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "Example (when mutations enabled): **downgrade 1 to B1**",
                    "",
                    "_SKU resize is OFF_ (`ENABLE_AZURE_FINOPS_MUTATIONS=false`) — "
                    "this scan is **read-only**.",
                    "",
                ]
            )

    lines.append("Say **menu**, **costs**, or **recommendations**.")
    return "\n".join(lines)


def refuse_delete_message() -> str:
    return (
        "*Azure FinOps* — **delete/remove is blocked** by design.\n\n"
        "For personal infra cost control, use **downgrade SKU** "
        "(plan → **APPLY PLAN <id>**).\n"
        "Deletes require `ENABLE_AZURE_FINOPS_ALLOW_DELETE=true` and a separate "
        "double-confirm flow (not silent)."
    )
