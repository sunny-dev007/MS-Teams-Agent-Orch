"""Azure FinOps Agent — portal-less Azure inventory, cost, and recommendations.

Feature: ENABLE_AZURE_FINOPS_AGENT (default false).
Optional SKU mutations: ENABLE_AZURE_FINOPS_MUTATIONS (default false) + APPLY PLAN HITL.
Does not alter Dev coding gates, Outlook, Boards, or WhatsApp Gmail paths.
"""

from __future__ import annotations

import re

from agent.agents.state import AgentState
from agent.config import settings
from agent.core.agent_availability import azure_finops_offline
from agent.core.logging import get_logger
from agent.core.session import get_session, save_session
from agent.services import azure_finops
from agent.services import azure_finops_mutations as mut

logger = get_logger(__name__)

AGENT_NAME = "azure_finops_agent"

AWAITING_SUB = "azure_finops_sub_pick"
AWAITING_ACTION = "azure_finops_action_pick"
AWAITING_RG = "azure_finops_rg_pick"
AWAITING_MUTATE = "azure_finops_mutate_plan"

_DISABLED = azure_finops_offline()

_SUB_RE = re.compile(
    r"(?:azure\s+)?(?:subscriptions?|subs?\b)|cloud\s+portal|azure\s+portal|"
    r"(?:list|show)\s+(?:my\s+)?(?:azure\s+)?subscriptions?",
    re.I,
)
_COST_RE = re.compile(
    r"(?:azure\s+)?\bcosts?\b(?!\s+sav)(?:\s+summary)?|"
    r"\bcost\s+(?:summary|report|breakdown|analysis)\b|"
    r"\bspending\b|billing\s+summary|how\s+much\s+(?:am\s+i\s+)?spend|"
    r"show\s+(?:me\s+)?(?:the\s+)?\bcosts?\b(?!\s+sav)|"
    r"what(?:'?s|\s+is)\s+(?:my\s+)?(?:azure\s+)?(?:bill|spend)",
    re.I,
)
_REC_RE = re.compile(
    r"recommend|optimiz|idle|waste|right-?siz|expensive|"
    r"high(?:ly)?\s+cost|cost\s+saving|savings?\s+tips?|"
    r"finops\s+recommend|what\s+should\s+i\s+(?:cut|downgrade|optimize)",
    re.I,
)
_RG_RE = re.compile(
    r"resource\s+groups?|\brgs?\b|inventory|list\s+(?:my\s+)?resources?|"
    r"show\s+(?:me\s+)?(?:my\s+)?resources?",
    re.I,
)
_DEVOPS_RE = re.compile(
    r"(?:azure\s+)?devops\s+overview|azdo\s+overview|devops\s+portal|"
    r"list\s+(?:my\s+)?(?:azdo|azure\s+devops)\s+projects",
    re.I,
)
_MENU_RE = re.compile(r"^(?:menu|actions?|options?|help|what\s+can\s+i\s+do)\s*$", re.I)
_CHANGE_SUB_RE = re.compile(
    r"^(?:back|subscriptions?|change\s+sub(?:scription)?|switch\s+sub(?:scription)?)\s*$",
    re.I,
)
_BACK_RE = _CHANGE_SUB_RE  # legacy alias; menu uses _MENU_RE
_RESIZE_PICK_RE = re.compile(
    r"(?i)\b(?:downgrade|upgrade|resize|change\s+sku|set\s+sku)\s+"
    r"(?:of\s+)?(?:#?\s*)?(\d+)\s+(?:to\s+)?([a-z0-9]+)\b"
)
_RESIZE_NAME_RE = re.compile(
    r"(?i)\b(?:downgrade|upgrade|resize|change\s+sku|set\s+sku)\s+"
    r"(?:of\s+)?([a-z0-9][a-z0-9\-_.]*)\s+to\s+([a-z0-9]+)\b"
)


async def _mark_cloud(phone: str) -> None:
    if not phone:
        return
    try:
        from agent.services.workspace_handoff import WS_PRODUCTIVITY, mark_workspace

        await mark_workspace(phone, WS_PRODUCTIVITY)
    except Exception:
        logger.exception("Failed marking FinOps workspace")


def _pick_index(msg: str, items: list) -> int | None:
    text = (msg or "").strip()
    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(items):
            return idx
    return None


def _pick_by_name(msg: str, items: list[dict], key: str = "name") -> dict | None:
    lower = (msg or "").strip().lower()
    if not lower:
        return None
    for item in items:
        if (item.get(key) or "").lower() == lower:
            return item
    for item in items:
        name = (item.get(key) or "").lower()
        if lower in name:
            return item
    return None


def _money(amount: float, currency: str = "USD") -> str:
    return f"{float(amount):,.2f} {currency}"


def _format_subscriptions(subs: list[dict]) -> str:
    lines = [
        "*Azure FinOps Agent* — choose a subscription",
        "",
        "| # | Subscription | State |",
        "| :---: | :--- | :--- |",
    ]
    for i, s in enumerate(subs, start=1):
        lines.append(f"| {i} | {s.get('name')} | {s.get('state') or '—'} |")
    lines.append("")
    lines.append("Reply with a **number** or the **subscription name**.")
    lines.append(
        "_Also:_ **azure costs** · **finops recommendations** · **deep scan** · "
        "**devops overview**"
    )
    return "\n".join(lines)


def _format_menu(sub: dict, *, days: int) -> str:
    name = sub.get("name") or sub.get("id")
    mut_on = mut.mutations_enabled()
    lines = [
        f"*Azure FinOps Agent* — **{name}**",
        "",
        "What do you want to inspect?",
        "",
        "| # | Action |",
        "| :---: | :--- |",
        "| 1 | Resource groups + inventory |",
        f"| 2 | Cost summary (last {days} days) |",
        "| 3 | FinOps recommendations (idle / high cost) |",
        "| 4 | Azure DevOps overview (org / projects) |",
        "| 5 | Deep cost scan + SKU-mutable resources |",
        "| 6 | Change subscription |",
    ]
    lines.append("")
    if mut_on:
        lines.append(
            "_SKU changes:_ **downgrade 1 to B1** / **change sku <name> to F1** → "
            "plan → **APPLY PLAN <id>** (human-in-the-loop)."
        )
    else:
        lines.append(
            "_SKU resize is OFF_ (`ENABLE_AZURE_FINOPS_MUTATIONS=false`) — "
            "deep scan stays read-only."
        )
    lines.append("")
    lines.append(
        "Reply **1–6**, or *resource groups* / *costs* / *recommendations* / "
        "*deep scan* / *devops overview* / *back*."
    )
    return "\n".join(lines)


def _format_rgs(
    rgs: list[dict],
    *,
    sub_name: str,
    cost_by_rg: list[dict] | None = None,
) -> str:
    cost_map = {c.get("name"): c for c in (cost_by_rg or [])}
    currency = ((cost_by_rg or [{}])[0].get("currency") if cost_by_rg else None) or "USD"
    lines = [
        f"*Azure FinOps* — resource groups in **{sub_name}**",
        "",
        "| # | Resource group | Location | Cost (window) |",
        "| :---: | :--- | :--- | ---: |",
    ]
    for i, rg in enumerate(rgs[:40], start=1):
        name = rg.get("name") or ""
        c = cost_map.get(name)
        cost_s = _money(float(c["cost"]), c.get("currency") or currency) if c else "—"
        lines.append(f"| {i} | {name} | {rg.get('location') or '—'} | {cost_s} |")
    if len(rgs) > 40:
        lines.append(f"\n_Showing 40 of {len(rgs)} groups._")
    lines.append("")
    lines.append("Reply with a **number** for resources in that group, or **menu**.")
    return "\n".join(lines)


def _format_resources(resources: list[dict], *, rg: str, sub_name: str) -> str:
    top_n = int(settings.azure_finops_top_n)
    lines = [
        f"*Azure FinOps* — resources in **{rg}** ({sub_name})",
        "",
        "| # | Name | Type | Location | SKU |",
        "| :---: | :--- | :--- | :--- | :--- |",
    ]
    top = resources[:top_n]
    for i, r in enumerate(top, start=1):
        lines.append(
            f"| {i} | {r.get('name')} | {r.get('type_short') or r.get('type')} | "
            f"{r.get('location') or '—'} | {r.get('sku') or '—'} |"
        )
    if len(resources) > len(top):
        lines.append(f"\n_Showing {len(top)} of {len(resources)} resources._")
    lines.append("")
    lines.append("Say **menu**, **costs**, **deep scan**, or **recommendations**.")
    return "\n".join(lines)


def _format_costs(
    rows: list[dict],
    *,
    title: str,
    days: int,
    ok: bool = True,
    error: str | None = None,
    error_kind: str | None = None,
    user_note: str | None = None,
) -> str:
    if not ok:
        note = (user_note or "").strip() or azure_finops.user_safe_cost_note(
            error_kind or "unknown"
        )
        # Never append raw ARM / URL / status payloads (ignore `error` on purpose).
        return (
            f"*{title}*\n\n"
            f"_{note}_\n\n"
            "Say **menu**, **deep scan**, or try **costs** again in a few minutes."
        )
    if not rows:
        return (
            f"*{title}*\n\n"
            f"_No cost rows for the last {days} days yet._\n"
            "That usually means true zero spend in-window, or costs not published "
            "(can lag). If Portal shows spend, ask again shortly."
        )
    currency = rows[0].get("currency") or "USD"
    total = sum(float(r.get("cost") or 0) for r in rows)
    top_n = int(getattr(settings, "azure_finops_top_n", 15) or 15)
    lines = [
        f"*{title}* — last **{days}** days",
        f"_Total (listed):_ **{_money(total, currency)}**",
        "",
        "| # | Name | Cost |",
        "| :---: | :--- | ---: |",
    ]
    for i, r in enumerate(rows[:top_n], start=1):
        lines.append(
            f"| {i} | {r.get('name')} | "
            f"{_money(float(r.get('cost') or 0), r.get('currency') or currency)} |"
        )
    if user_note:
        lines.append("")
        lines.append(f"_{user_note}_")
    lines.append("")
    lines.append("Say **recommendations**, **deep scan**, **resource groups**, or **menu**.")
    return "\n".join(lines)


def _format_recs(recs: list[dict], *, sub_name: str) -> str:
    lines = [
        f"*Azure FinOps recommendations* — **{sub_name}**",
        "_Heuristic guidance only — review before deleting or resizing._",
        "",
    ]
    for i, r in enumerate(recs, start=1):
        sev = (r.get("severity") or "info").upper()
        lines.append(f"{i}. **[{sev}]** {r.get('title')}")
        lines.append(f"   {r.get('detail')}")
        lines.append("")
    lines.append("Say **menu**, **costs**, **deep scan**, or **resource groups**.")
    return "\n".join(lines)


def _format_devops(snap: dict) -> str:
    if not snap.get("configured"):
        return (
            "*Azure DevOps overview*\n\n"
            "_AZDO org URL / PAT not configured._\n"
            "Classic path **check my repos** is unchanged."
        )
    lines = [
        f"*Azure DevOps overview* — `{snap.get('org')}`",
        "",
        "| Project | Repos | Pipelines |",
        "| :--- | :---: | :---: |",
    ]
    for p in snap.get("projects") or []:
        repos = p.get("repos")
        pipes = p.get("pipelines")
        lines.append(
            f"| {p.get('name')} | {repos if repos is not None else '—'} | "
            f"{pipes if pipes is not None else '—'} |"
        )
    if not snap.get("projects"):
        lines.append("| _(none)_ | — | — |")
    lines.append("")
    lines.append("Boards tickets: **my work items**. Coding: **check my repos**.")
    lines.append("Say **menu** to return to Azure FinOps.")
    return "\n".join(lines)


async def _load_subs(
    state: AgentState, phone: str, *, pending_intent: str = ""
) -> AgentState:
    subs = await azure_finops.list_subscriptions()
    if not subs:
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Azure FinOps Agent* — no subscriptions visible to this app.\n"
                "Grant the app **Reader** + **Cost Management Reader** on target "
                "subscriptions. See `docs/AZURE_FINOPS_AGENT.md`."
            ),
        }
    await save_session(
        phone,
        awaiting=AWAITING_SUB,
        data={"azure_finops_subs": subs, "azure_finops_pending_intent": pending_intent or ""},
        merge_data=True,
    )
    await _mark_cloud(phone)
    return {
        **state,
        "status": "repo_picker",
        "handled_by": AGENT_NAME,
        "session_awaiting": AWAITING_SUB,
        "notification_text": _format_subscriptions(subs),
    }


async def _show_rgs(state: AgentState, *, phone: str, sub: dict) -> AgentState:
    sid = sub.get("id") or ""
    rgs = await azure_finops.list_resource_groups(sid)
    cost_by_rg = await azure_finops.query_costs(sid, group_by="ResourceGroupName")
    await save_session(
        phone,
        awaiting=AWAITING_RG,
        data={"azure_finops_sub": sub, "azure_finops_rgs": rgs},
        merge_data=True,
    )
    return {
        **state,
        "status": "repo_picker",
        "handled_by": AGENT_NAME,
        "session_awaiting": AWAITING_RG,
        "notification_text": _format_rgs(
            rgs, sub_name=sub.get("name") or sid, cost_by_rg=cost_by_rg
        ),
    }


async def _show_costs(state: AgentState, *, phone: str, sub: dict) -> AgentState:
    sid = sub.get("id") or ""
    days = int(settings.azure_finops_cost_days)
    by_rg = await azure_finops.query_costs_result(sid, group_by="ResourceGroupName")
    texts = [
        _format_costs(
            by_rg.get("rows") or [],
            title=f"Cost by resource group — {sub.get('name')}",
            days=days,
            ok=bool(by_rg.get("ok")),
            error_kind=by_rg.get("error_kind"),
            user_note=by_rg.get("user_note"),
        )
    ]
    # Avoid a second Cost Management call when already throttled.
    if by_rg.get("error_kind") != "throttled":
        by_svc = await azure_finops.query_costs_result(sid, group_by="ServiceName")
        texts.append(
            _format_costs(
                by_svc.get("rows") or [],
                title=f"Cost by service — {sub.get('name')}",
                days=days,
                ok=bool(by_svc.get("ok")),
                error_kind=by_svc.get("error_kind"),
                user_note=by_svc.get("user_note"),
            )
        )
    text = "\n\n".join(texts)
    await save_session(
        phone,
        awaiting=AWAITING_ACTION,
        data={"azure_finops_sub": sub},
        merge_data=True,
    )
    return {
        **state,
        "status": "general_response",
        "handled_by": AGENT_NAME,
        "session_awaiting": AWAITING_ACTION,
        "notification_text": text,
    }


async def _show_recommendations(
    state: AgentState, *, phone: str, sub: dict
) -> AgentState:
    sid = sub.get("id") or ""
    resources = await azure_finops.list_resources(sid)
    cost_rg = await azure_finops.query_costs_result(sid, group_by="ResourceGroupName")
    cost_by_rg = list(cost_rg.get("rows") or [])
    cost_by_res: list = []
    if cost_rg.get("error_kind") != "throttled" and cost_rg.get("ok"):
        cost_res = await azure_finops.query_costs_result(sid, group_by="ResourceId")
        cost_by_res = list(cost_res.get("rows") or [])
    recs = azure_finops.build_recommendations(
        resources,
        cost_by_resource=cost_by_res,
        cost_by_rg=cost_by_rg,
    )
    await save_session(
        phone,
        awaiting=AWAITING_ACTION,
        data={"azure_finops_sub": sub},
        merge_data=True,
    )
    return {
        **state,
        "status": "general_response",
        "handled_by": AGENT_NAME,
        "session_awaiting": AWAITING_ACTION,
        "notification_text": _format_recs(recs, sub_name=sub.get("name") or sid),
    }


async def _show_deep_scan(state: AgentState, *, phone: str, sub: dict) -> AgentState:
    sid = sub.get("id") or ""
    scan = await mut.deep_cost_scan(sid)
    await save_session(
        phone,
        awaiting=AWAITING_ACTION,
        data={
            "azure_finops_sub": sub,
            "azure_finops_mutable": scan.get("mutable") or [],
        },
        merge_data=True,
    )
    return {
        **state,
        "status": "general_response",
        "handled_by": AGENT_NAME,
        "session_awaiting": AWAITING_ACTION,
        "notification_text": mut.format_cost_scan(
            scan, sub_name=sub.get("name") or sid
        ),
    }


async def _propose_sku_plan(
    state: AgentState,
    *,
    phone: str,
    sub: dict,
    resource: dict,
    target_token: str,
    user_msg: str,
) -> AgentState:
    if not mut.mutations_enabled():
        return {
            **state,
            "status": "general_response",
            "handled_by": AGENT_NAME,
            "session_awaiting": AWAITING_ACTION,
            "notification_text": (
                "*Azure FinOps* — SKU mutations are **disabled** "
                "(`ENABLE_AZURE_FINOPS_MUTATIONS=false`).\n"
                "Deep scan / inventory stay read-only. Enable the flag and grant "
                "**Contributor** (or scoped write) on the subscription before APPLY PLAN."
            ),
        }
    try:
        plan = await mut.build_sku_change_plan(
            subscription_id=sub.get("id") or "",
            resource=resource,
            target_sku_token=target_token,
            user_msg=user_msg,
        )
    except Exception as exc:
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "session_awaiting": AWAITING_ACTION,
            "notification_text": f"*Azure FinOps* — could not prepare SKU plan: `{exc}`",
            "error": str(exc),
        }
    await save_session(
        phone,
        awaiting=AWAITING_MUTATE,
        data={"azure_finops_sub": sub, "azure_finops_plan": plan},
        merge_data=True,
    )
    return {
        **state,
        "status": "repo_picker",
        "handled_by": AGENT_NAME,
        "session_awaiting": AWAITING_MUTATE,
        "notification_text": mut.format_mutation_plan(plan),
    }


async def _handle_resize_msg(
    state: AgentState,
    *,
    phone: str,
    sub: dict,
    user_msg: str,
    session_data: dict,
) -> AgentState | None:
    """Return AgentState if this message is a resize request; else None."""
    if mut.is_delete_request(user_msg) and not mut.delete_allowed():
        return {
            **state,
            "status": "general_response",
            "handled_by": AGENT_NAME,
            "session_awaiting": AWAITING_ACTION,
            "notification_text": mut.refuse_delete_message(),
        }

    if not (
        mut.is_resize_request(user_msg)
        or _RESIZE_PICK_RE.search(user_msg)
        or _RESIZE_NAME_RE.search(user_msg)
    ):
        return None

    mutable = list(session_data.get("azure_finops_mutable") or [])
    if not mutable:
        resources = await azure_finops.list_resources(sub.get("id") or "")
        mutable = [r for r in resources if mut.is_mutable_type(r.get("type") or "")]

    target_token = mut.extract_sku_token(user_msg)
    resource = None
    m_num = _RESIZE_PICK_RE.search(user_msg)
    m_name = _RESIZE_NAME_RE.search(user_msg)
    if m_num:
        idx = int(m_num.group(1)) - 1
        target_token = (m_num.group(2) or target_token or "").upper()
        resource = mut.find_mutable_resource(mutable, msg=user_msg, pick_index=idx)
    elif m_name:
        name = m_name.group(1)
        target_token = (m_name.group(2) or target_token or "").upper()
        resource = mut.find_mutable_resource(mutable, msg=name)
    else:
        resource = mut.find_mutable_resource(mutable, msg=user_msg)

    if not resource:
        return {
            **state,
            "status": "general_response",
            "handled_by": AGENT_NAME,
            "session_awaiting": AWAITING_ACTION,
            "notification_text": (
                "*Azure FinOps* — no matching SKU-mutable resource.\n"
                "Run **deep scan** first, then e.g. **downgrade 1 to B1** "
                "(App Service plans / SQL databases only)."
            ),
        }

    if not target_token:
        suggested = mut.suggest_downgrade_sku(
            resource.get("type") or "", resource.get("sku") or ""
        )
        if suggested and mut.is_downgrade_request(user_msg):
            target_token = suggested
        else:
            return {
                **state,
                "status": "general_response",
                "handled_by": AGENT_NAME,
                "session_awaiting": AWAITING_ACTION,
                "notification_text": (
                    f"*Azure FinOps* — say the target SKU for `{resource.get('name')}` "
                    f"(current `{resource.get('sku') or '—'}`).\n"
                    "Example: **downgrade to B1** or **change sku to F1**."
                ),
            }

    return await _propose_sku_plan(
        state,
        phone=phone,
        sub=sub,
        resource=resource,
        target_token=target_token,
        user_msg=user_msg,
    )


async def _apply_or_reject_plan(
    state: AgentState,
    *,
    phone: str,
    user_msg: str,
    session_data: dict,
) -> AgentState | None:
    apply_id = mut.parse_apply_plan_id(user_msg)
    if apply_id:
        plan = session_data.get("azure_finops_plan") or {}
        sub = session_data.get("azure_finops_sub") or {}
        if not plan or plan.get("id") != apply_id:
            return {
                **state,
                "status": "failed",
                "handled_by": AGENT_NAME,
                "session_awaiting": AWAITING_MUTATE if plan else AWAITING_ACTION,
                "notification_text": (
                    f"*Azure FinOps* — no pending plan `{apply_id}`.\n"
                    "Prepare a plan first (e.g. **downgrade 1 to B1**), then "
                    "**APPLY PLAN <id>**."
                ),
            }
        if not mut.mutations_enabled():
            return {
                **state,
                "status": "failed",
                "handled_by": AGENT_NAME,
                "notification_text": (
                    "*Azure FinOps* — refusing apply: mutations flag is OFF."
                ),
            }
        try:
            result = await mut.apply_sku_change_plan(plan)
        except Exception as exc:
            logger.exception("SKU apply failed plan=%s", apply_id)
            return {
                **state,
                "status": "failed",
                "handled_by": AGENT_NAME,
                "session_awaiting": AWAITING_MUTATE,
                "notification_text": (
                    f"*Azure FinOps* — APPLY failed for `{apply_id}`: `{exc}`\n"
                    "Plan is still pending — fix RBAC/quota and retry, or **REJECT PLAN**."
                ),
                "error": str(exc),
            }
        await save_session(
            phone,
            awaiting=AWAITING_ACTION,
            data={"azure_finops_sub": sub, "azure_finops_plan": None},
            merge_data=True,
        )
        sku = result.get("result_sku") or plan.get("to_sku")
        return {
            **state,
            "status": "general_response",
            "handled_by": AGENT_NAME,
            "session_awaiting": AWAITING_ACTION,
            "notification_text": (
                f"*Azure FinOps* — **applied** plan `{apply_id}`\n\n"
                f"• Resource: `{plan.get('resource_name')}`\n"
                f"• From: `{plan.get('from_sku_label')}` → "
                f"To: `{plan.get('to_sku_label')}`\n"
                f"• Result SKU: `{sku}`\n\n"
                "Say **deep scan** or **menu** to continue."
            ),
        }

    if mut.is_reject_plan(user_msg):
        sub = session_data.get("azure_finops_sub") or {}
        await save_session(
            phone,
            awaiting=AWAITING_ACTION if sub else AWAITING_SUB,
            data={"azure_finops_plan": None},
            merge_data=True,
        )
        if sub:
            return {
                **state,
                "status": "general_response",
                "handled_by": AGENT_NAME,
                "session_awaiting": AWAITING_ACTION,
                "notification_text": (
                    "*Azure FinOps* — plan **rejected**. Nothing was changed.\n\n"
                    + _format_menu(sub, days=int(settings.azure_finops_cost_days))
                ),
            }
        return await _load_subs(state, phone)

    return None


async def _after_sub(
    state: AgentState, *, phone: str, sub: dict, user_msg: str
) -> AgentState:
    days = int(settings.azure_finops_cost_days)
    session_data = dict(state.get("session_data") or {})
    pending = (session_data.get("azure_finops_pending_intent") or "").strip().lower()
    if not pending:
        try:
            live = await get_session(phone)
            pending = ((live.get("data") or {}).get("azure_finops_pending_intent") or "").strip().lower()
        except Exception:
            pending = ""
    await save_session(
        phone,
        awaiting=AWAITING_ACTION,
        data={"azure_finops_sub": sub, "azure_finops_pending_intent": ""},
        merge_data=True,
    )
    await _mark_cloud(phone)

    # If user only picked a subscription (number/name) after "scan my azure", run that intent.
    pick_only = bool(re.fullmatch(r"\d{1,2}", (user_msg or "").strip())) or (
        len((user_msg or "").split()) <= 4
        and not mut.is_scan_request(user_msg)
        and not mut.is_resize_request(user_msg)
        and not _COST_RE.search(user_msg or "")
        and not _REC_RE.search(user_msg or "")
        and not _RG_RE.search(user_msg or "")
        and not _DEVOPS_RE.search(user_msg or "")
        and not _MENU_RE.match(user_msg or "")
    )
    if pick_only and pending == "scan":
        return await _show_deep_scan(state, phone=phone, sub=sub)
    if pick_only and pending == "costs":
        return await _show_costs(state, phone=phone, sub=sub)
    if pick_only and pending == "recs":
        return await _show_recommendations(state, phone=phone, sub=sub)

    if mut.is_delete_request(user_msg) and not mut.delete_allowed():
        return {
            **state,
            "status": "general_response",
            "handled_by": AGENT_NAME,
            "session_awaiting": AWAITING_ACTION,
            "notification_text": mut.refuse_delete_message()
            + "\n\n"
            + _format_menu(sub, days=days),
        }

    resize = await _handle_resize_msg(
        state, phone=phone, sub=sub, user_msg=user_msg, session_data={}
    )
    if resize is not None:
        return resize

    if mut.is_scan_request(user_msg):
        return await _show_deep_scan(state, phone=phone, sub=sub)
    if _DEVOPS_RE.search(user_msg):
        snap = await azure_finops.devops_org_snapshot()
        return {
            **state,
            "status": "general_response",
            "handled_by": AGENT_NAME,
            "session_awaiting": AWAITING_ACTION,
            "notification_text": _format_devops(snap)
            + "\n\n"
            + _format_menu(sub, days=days),
        }
    if _REC_RE.search(user_msg) and not mut.is_scan_request(user_msg):
        return await _show_recommendations(state, phone=phone, sub=sub)
    if _COST_RE.search(user_msg) and not mut.is_scan_request(user_msg):
        return await _show_costs(state, phone=phone, sub=sub)
    if _RG_RE.search(user_msg):
        return await _show_rgs(state, phone=phone, sub=sub)

    return {
        **state,
        "status": "repo_picker",
        "handled_by": AGENT_NAME,
        "session_awaiting": AWAITING_ACTION,
        "notification_text": _format_menu(sub, days=days),
    }


async def run_azure_finops(state: AgentState) -> AgentState:
    if not settings.enable_azure_finops_agent:
        return {
            **state,
            "status": "skipped",
            "handled_by": AGENT_NAME,
            "notification_text": _DISABLED,
        }

    if not azure_finops.arm_configured():
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": (
                "*Azure FinOps Agent* — ARM credentials missing.\n"
                "Set `AZURE_ARM_TENANT_ID` / `AZURE_ARM_CLIENT_ID` / "
                "`AZURE_ARM_CLIENT_SECRET`, or reuse `MS_GRAPH_*` app credentials "
                "with Azure RBAC. See `docs/AZURE_FINOPS_AGENT.md`."
            ),
        }

    phone = state.get("whatsapp_phone") or ""
    user_msg = (state.get("user_message") or "").strip()
    awaiting = (state.get("session_awaiting") or "").strip()
    session_data = dict(state.get("session_data") or {})

    try:
        # APPLY / REJECT always checked first (HITL — never auto-apply)
        gate = await _apply_or_reject_plan(
            state, phone=phone, user_msg=user_msg, session_data=session_data
        )
        if gate is not None:
            return gate

        if mut.is_delete_request(user_msg) and not mut.delete_allowed():
            return {
                **state,
                "status": "general_response",
                "handled_by": AGENT_NAME,
                "notification_text": mut.refuse_delete_message(),
            }

        if awaiting == AWAITING_MUTATE:
            sub = session_data.get("azure_finops_sub") or {}
            if _MENU_RE.match(user_msg) or _CHANGE_SUB_RE.match(user_msg):
                await save_session(
                    phone,
                    awaiting=AWAITING_ACTION,
                    data={"azure_finops_plan": None},
                    merge_data=True,
                )
                return await _after_sub(state, phone=phone, sub=sub, user_msg="menu")
            plan = session_data.get("azure_finops_plan") or {}
            return {
                **state,
                "status": "repo_picker",
                "handled_by": AGENT_NAME,
                "session_awaiting": AWAITING_MUTATE,
                "notification_text": (
                    "Pending plan still needs approval.\n\n"
                    + (
                        mut.format_mutation_plan(plan)
                        if plan
                        else "No plan in session — say **deep scan**."
                    )
                ),
            }

        if awaiting == AWAITING_SUB:
            subs = session_data.get("azure_finops_subs") or await azure_finops.list_subscriptions()
            idx = _pick_index(user_msg, subs)
            sub = subs[idx] if idx is not None else _pick_by_name(user_msg, subs)
            if not sub:
                return {
                    **state,
                    "status": "repo_picker",
                    "handled_by": AGENT_NAME,
                    "session_awaiting": AWAITING_SUB,
                    "notification_text": (
                        "Please reply with a **subscription number** or name.\n\n"
                        + _format_subscriptions(subs)
                    ),
                }
            return await _after_sub(state, phone=phone, sub=sub, user_msg=user_msg)

        if awaiting == AWAITING_ACTION:
            sub = session_data.get("azure_finops_sub") or {}
            if not sub:
                return await _load_subs(state, phone)

            # "menu" re-shows actions; "back"/change sub returns to subscription picker
            if _MENU_RE.match(user_msg):
                return {
                    **state,
                    "status": "repo_picker",
                    "handled_by": AGENT_NAME,
                    "session_awaiting": AWAITING_ACTION,
                    "notification_text": _format_menu(
                        sub, days=int(settings.azure_finops_cost_days)
                    ),
                }
            if _CHANGE_SUB_RE.match(user_msg) or user_msg.strip() == "6":
                return await _load_subs(state, phone)

            resize = await _handle_resize_msg(
                state,
                phone=phone,
                sub=sub,
                user_msg=user_msg,
                session_data=session_data,
            )
            if resize is not None:
                return resize

            choice = user_msg.strip()
            if choice == "1" or _RG_RE.search(user_msg):
                return await _show_rgs(state, phone=phone, sub=sub)
            # Recommendations before costs — "cost saving" must not steal the rec path
            if choice == "3" or (
                _REC_RE.search(user_msg) and not mut.is_scan_request(user_msg)
            ):
                return await _show_recommendations(state, phone=phone, sub=sub)
            if choice == "2" or (
                _COST_RE.search(user_msg) and not mut.is_scan_request(user_msg)
            ):
                return await _show_costs(state, phone=phone, sub=sub)
            if choice == "4" or _DEVOPS_RE.search(user_msg):
                snap = await azure_finops.devops_org_snapshot()
                return {
                    **state,
                    "status": "general_response",
                    "handled_by": AGENT_NAME,
                    "session_awaiting": AWAITING_ACTION,
                    "notification_text": _format_devops(snap),
                }
            if choice == "5" or mut.is_scan_request(user_msg):
                return await _show_deep_scan(state, phone=phone, sub=sub)

            return {
                **state,
                "status": "repo_picker",
                "handled_by": AGENT_NAME,
                "session_awaiting": AWAITING_ACTION,
                "notification_text": _format_menu(
                    sub, days=int(settings.azure_finops_cost_days)
                ),
            }

        if awaiting == AWAITING_RG:
            sub = session_data.get("azure_finops_sub") or {}
            rgs = session_data.get("azure_finops_rgs") or []
            if _MENU_RE.match(user_msg) or _CHANGE_SUB_RE.match(user_msg):
                return await _after_sub(state, phone=phone, sub=sub, user_msg="menu")
            if mut.is_scan_request(user_msg):
                return await _show_deep_scan(state, phone=phone, sub=sub)
            if _COST_RE.search(user_msg):
                return await _show_costs(state, phone=phone, sub=sub)
            if _REC_RE.search(user_msg):
                return await _show_recommendations(state, phone=phone, sub=sub)

            resize = await _handle_resize_msg(
                state,
                phone=phone,
                sub=sub,
                user_msg=user_msg,
                session_data=session_data,
            )
            if resize is not None:
                return resize

            idx = _pick_index(user_msg, rgs)
            rg = rgs[idx] if idx is not None else _pick_by_name(user_msg, rgs)
            if not rg:
                return {
                    **state,
                    "status": "repo_picker",
                    "handled_by": AGENT_NAME,
                    "session_awaiting": AWAITING_RG,
                    "notification_text": (
                        "Reply with a **resource group number** or name, or **menu**.\n\n"
                        + _format_rgs(rgs, sub_name=sub.get("name") or "")
                    ),
                }
            resources = await azure_finops.list_resources(
                sub.get("id") or "",
                resource_group=rg.get("name") or "",
            )
            mutable = [r for r in resources if mut.is_mutable_type(r.get("type") or "")]
            await save_session(
                phone,
                awaiting=AWAITING_ACTION,
                data={
                    "azure_finops_sub": sub,
                    "azure_finops_rgs": rgs,
                    "azure_finops_mutable": mutable,
                },
                merge_data=True,
            )
            return {
                **state,
                "status": "general_response",
                "handled_by": AGENT_NAME,
                "session_awaiting": AWAITING_ACTION,
                "notification_text": _format_resources(
                    resources,
                    rg=rg.get("name") or "",
                    sub_name=sub.get("name") or "",
                ),
            }

        # Fresh entry — prefer scan/resize if sub already in session
        sub = session_data.get("azure_finops_sub") or {}
        if sub and (
            mut.is_scan_request(user_msg)
            or mut.is_resize_request(user_msg)
            or _RESIZE_PICK_RE.search(user_msg)
        ):
            return await _after_sub(state, phone=phone, sub=sub, user_msg=user_msg)

        if _DEVOPS_RE.search(user_msg) and not _SUB_RE.search(user_msg):
            snap = await azure_finops.devops_org_snapshot()
            seeded = await _load_subs(state, phone)
            return {
                **seeded,
                "notification_text": _format_devops(snap)
                + "\n\n"
                + (seeded.get("notification_text") or ""),
            }

        if mut.is_scan_request(user_msg) or mut.is_resize_request(user_msg):
            # Need a subscription first — remember intent for after pick
            intent = "scan" if mut.is_scan_request(user_msg) else "resize"
            return await _load_subs(state, phone, pending_intent=intent)

        return await _load_subs(state, phone)

    except Exception as exc:
        logger.exception("Azure FinOps Agent failed")
        return {
            **state,
            "status": "failed",
            "handled_by": AGENT_NAME,
            "notification_text": f"*Azure FinOps Agent* failed: `{exc}`",
            "error": str(exc),
        }
