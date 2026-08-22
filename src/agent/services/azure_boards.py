"""Azure Boards work items — assigned to a specific user (never @Me).

Feature: ENABLE_BOARDS_AGENT (default false).
Uses existing AZDO_ORG_URL + AZDO_PAT. Does not modify git/PR/pipeline APIs.
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

import httpx

from agent.config import settings
from agent.core.logging import get_logger
from agent.services import azure_devops as azdo
from agent.services import ms_graph

logger = get_logger(__name__)

# Lower number = higher priority in Azure DevOps
_PRIORITY_RANK = {1: 0, 2: 1, 3: 2, 4: 3}


def boards_configured() -> bool:
    return bool(settings.azdo_org_url and settings.azdo_pat.get_secret_value())


def boards_agent_ready() -> bool:
    return bool(settings.enable_boards_agent and boards_configured())


def _headers() -> dict[str, str]:
    pat = settings.azdo_pat.get_secret_value()
    encoded = base64.b64encode(f":{pat}".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }


def _org_url() -> str:
    return settings.azdo_org_url.rstrip("/")


async def resolve_identity_email(teams_user_id: str) -> dict[str, Any]:
    """Resolve Entra OID/UPN → mail + display name via Graph."""
    uid = (teams_user_id or "").strip()
    if not uid:
        raise ValueError("teams_user_id is required")
    profile = await ms_graph.get_user_profile(uid)
    mail = (profile.get("mail") or "").strip()
    upn = (profile.get("userPrincipalName") or "").strip()
    other = profile.get("otherMails") or []
    extra = [str(x).strip() for x in other if str(x).strip()] if isinstance(other, list) else []
    email = mail or upn
    if not email:
        raise RuntimeError(
            f"No mail/UPN on Graph user {uid[:12]}… — cannot filter Boards Assigned To"
        )
    return {
        "email": email,
        "upn": upn,
        "mail": mail,
        "other_mails": extra,
        "display_name": (profile.get("displayName") or "").strip(),
        "id": str(profile.get("id") or uid),
    }


def guest_upn_to_mail(upn: str) -> str | None:
    """Decode Entra guest UPN ``local_domain.com#EXT#@tenant`` → ``local@domain.com``."""
    raw = (upn or "").strip()
    if "#ext#" not in raw.lower():
        return None
    prefix = raw.split("#", 1)[0]
    if "_" not in prefix:
        return None
    local, _, domain = prefix.rpartition("_")
    if local and domain and "." in domain:
        return f"{local}@{domain}"
    return None


def assigned_to_aliases(
    identity: dict[str, Any] | None = None,
    *,
    email: str = "",
) -> list[str]:
    """Unique Graph/AzDO identity strings to match System.AssignedTo (never @Me)."""
    ident = identity or {}
    raw: list[str] = []
    for v in (
        email,
        ident.get("email"),
        ident.get("mail"),
        ident.get("upn"),
        *(ident.get("other_mails") or []),
        *(ident.get("azdo_unique_names") or []),
    ):
        s = str(v or "").strip()
        if s:
            raw.append(s)
        decoded = guest_upn_to_mail(s)
        if decoded:
            raw.append(decoded)
    out: list[str] = []
    seen: set[str] = set()
    for s in raw:
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


async def lookup_azdo_unique_names(queries: list[str]) -> list[str]:
    """Resolve Graph aliases to AzDO uniqueName values (MSA vs guest UPN)."""
    found: list[str] = []
    seen: set[str] = set()
    if not boards_configured():
        return []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for q in queries:
            q = (q or "").strip()
            if not q or len(q) < 3:
                continue
            url = (
                f"{_org_url().replace('https://dev.azure.com', 'https://vssps.dev.azure.com')}"
                f"/_apis/identities?searchFilter=General&filterValue={quote(q)}"
                f"&queryMembership=None&api-version=7.1"
            )
            # Some orgs only accept org-scoped identities on dev.azure.com
            try:
                resp = await client.get(url, headers=_headers())
                if resp.status_code >= 400:
                    resp = await client.get(
                        f"{_org_url()}/_apis/identities?searchFilter=General"
                        f"&filterValue={quote(q)}&queryMembership=None&api-version=7.1",
                        headers=_headers(),
                    )
                if resp.status_code >= 400:
                    logger.info("AzDO identity search skipped for %s: %s", q, resp.status_code)
                    continue
                data = resp.json()
            except Exception:
                logger.exception("AzDO identity search failed for %s", q)
                continue
            rows = data if isinstance(data, list) else (data.get("value") or [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                props = row.get("properties") or {}
                for key in ("Account", "Mail", "DirectoryAlias"):
                    block = props.get(key) if isinstance(props, dict) else None
                    val = ""
                    if isinstance(block, dict):
                        val = str(block.get("$value") or "").strip()
                    if val:
                        lk = val.lower()
                        if lk not in seen:
                            seen.add(lk)
                            found.append(val)
                for key in ("uniqueName", "providerDisplayName", "customDisplayName"):
                    val = str(row.get(key) or "").strip()
                    if val and "@" in val:
                        lk = val.lower()
                        if lk not in seen:
                            seen.add(lk)
                            found.append(val)
    return found


async def list_org_projects() -> list[dict[str, str]]:
    """List AzDO projects for the Boards project picker (name + id)."""
    raw = await azdo.list_projects()
    out: list[dict[str, str]] = []
    for p in raw:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        out.append({"id": str(p.get("id") or ""), "name": name})
    out.sort(key=lambda x: x["name"].lower())
    return out


def build_assigned_wiql(
    email: str,
    *,
    project: str = "",
    emails: list[str] | None = None,
) -> str:
    """WIQL for work items assigned to email(s) — never uses @Me (PAT owner)."""
    aliases = [e for e in (emails or [email]) if (e or "").strip()]
    if not aliases and (email or "").strip():
        aliases = [email]
    if not aliases:
        raise ValueError("email is required for Boards WIQL")
    clauses = []
    for raw in aliases:
        safe = (raw or "").replace("'", "''").strip()
        if safe:
            clauses.append(f"[System.AssignedTo] = '{safe}'")
    if not clauses:
        raise ValueError("email is required for Boards WIQL")
    assigned = clauses[0] if len(clauses) == 1 else "(" + " OR ".join(clauses) + ")"
    states = (
        "'New', 'Active', 'Committed', 'To Do', 'Doing', "
        "'Approved', 'In Progress', 'Proposed'"
    )
    where = (
        f"{assigned} "
        f"AND [System.State] IN ({states}) "
        f"AND [System.WorkItemType] <> ''"
    )
    proj = (project or "").strip().replace("'", "''")
    select = (
        "[System.Id], [System.Title], [System.State], "
        "[System.WorkItemType], [System.ChangedDate], "
        "[Microsoft.VSTS.Common.Priority]"
    )
    if proj:
        return (
            f"SELECT {select} FROM WorkItems "
            f"WHERE [System.TeamProject] = '{proj}' AND {where} "
            f"ORDER BY [Microsoft.VSTS.Common.Priority] ASC, [System.ChangedDate] DESC"
        )
    return (
        f"SELECT {select} FROM WorkItems WHERE {where} "
        f"ORDER BY [Microsoft.VSTS.Common.Priority] ASC, [System.ChangedDate] DESC"
    )


def _priority_value(raw: Any) -> int | None:
    try:
        if raw is None or raw == "":
            return None
        return int(raw)
    except (TypeError, ValueError):
        return None


def sort_items_by_priority(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort: Priority 1 first; missing priority last; then newest changed first."""

    def key(it: dict[str, Any]) -> tuple:
        pri = _priority_value(it.get("priority"))
        rank = _PRIORITY_RANK.get(pri, 50) if pri is not None else 40
        changed = str(it.get("changed") or "")
        return (rank, changed)

    # Sort by rank asc, then changed desc (negate by reverse on secondary via two-pass)
    by_date = sorted(items, key=lambda x: str(x.get("changed") or ""), reverse=True)
    return sorted(by_date, key=lambda x: key(x)[0])


def summarize_metrics(items: list[dict[str, Any]]) -> dict[str, int]:
    metrics = {
        "open": len(items),
        "bugs": 0,
        "stories": 0,
        "tasks": 0,
        "features": 0,
        "high_priority": 0,
        "other": 0,
    }
    for it in items:
        t = (it.get("type") or "").lower()
        if "bug" in t:
            metrics["bugs"] += 1
        elif "story" in t or "product backlog" in t:
            metrics["stories"] += 1
        elif "task" in t:
            metrics["tasks"] += 1
        elif "feature" in t or "epic" in t:
            metrics["features"] += 1
        else:
            metrics["other"] += 1
        pri = _priority_value(it.get("priority"))
        if pri is not None and pri <= 2:
            metrics["high_priority"] += 1
    return metrics


async def list_assigned_work_items(
    email: str,
    *,
    project: str | None = None,
    top: int | None = None,
    identity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Query work items assigned to Graph/AzDO identity aliases via WIQL + batch get."""
    if not boards_configured():
        raise RuntimeError("Azure DevOps org URL / PAT not configured")

    limit = int(top if top is not None else settings.boards_work_item_top or 15)
    limit = max(1, min(limit, 50))
    proj = (project if project is not None else "").strip()
    aliases = assigned_to_aliases(identity, email=email)
    try:
        extra = await lookup_azdo_unique_names(aliases[:6])
        aliases = assigned_to_aliases(
            {**(identity or {}), "azdo_unique_names": extra},
            email=email,
        )
    except Exception:
        logger.exception("AzDO identity expansion failed — using Graph aliases only")
    if not aliases:
        raise ValueError("email is required for Boards WIQL")

    wiql = build_assigned_wiql(aliases[0], project=proj, emails=aliases)
    if proj:
        url = f"{_org_url()}/{quote(proj)}/_apis/wit/wiql?api-version=7.1&$top={limit}"
    else:
        url = f"{_org_url()}/_apis/wit/wiql?api-version=7.1&$top={limit}"

    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(url, headers=_headers(), json={"query": wiql})
        if resp.status_code >= 400:
            logger.error("AzDO WIQL failed: %s %s", resp.status_code, resp.text[:400])
            resp.raise_for_status()
        data = resp.json()

    refs = data.get("workItems") or []
    ids = [int(r["id"]) for r in refs if isinstance(r, dict) and r.get("id")][:limit]
    if not ids:
        return []

    ids_csv = ",".join(str(i) for i in ids)
    fields = (
        "System.Id,System.Title,System.State,System.WorkItemType,"
        "System.AssignedTo,System.ChangedDate,System.TeamProject,"
        "Microsoft.VSTS.Common.Priority,System.Description,System.Tags"
    )
    get_url = (
        f"{_org_url()}/_apis/wit/workitems"
        f"?ids={ids_csv}&fields={quote(fields)}&api-version=7.1"
    )
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.get(get_url, headers=_headers())
        if resp.status_code >= 400:
            logger.error("AzDO workitems get failed: %s %s", resp.status_code, resp.text[:400])
            resp.raise_for_status()
        batch = resp.json()

    items = [_normalize_work_item(row, fallback_project=proj) for row in (batch.get("value") or [])]
    items = [x for x in items if x.get("id")]
    items = sort_items_by_priority(items)
    logger.info(
        "Boards assigned aliases=%s count=%s project=%s",
        aliases[:8],
        len(items),
        proj or "*",
    )
    return items


async def get_work_item(work_item_id: int) -> dict[str, Any]:
    """Fetch one work item with description for coding handoff."""
    if not boards_configured():
        raise RuntimeError("Azure DevOps org URL / PAT not configured")
    wid = int(work_item_id)
    fields = (
        "System.Id,System.Title,System.State,System.WorkItemType,"
        "System.AssignedTo,System.ChangedDate,System.TeamProject,"
        "Microsoft.VSTS.Common.Priority,System.Description,System.Tags"
    )
    url = (
        f"{_org_url()}/_apis/wit/workitems/{wid}"
        f"?fields={quote(fields)}&api-version=7.1"
    )
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code >= 400:
            logger.error("AzDO workitem get failed: %s %s", resp.status_code, resp.text[:400])
            resp.raise_for_status()
        row = resp.json()
    return _normalize_work_item(row, fallback_project="")


def _strip_html(text: str) -> str:
    import re

    t = re.sub(r"<[^>]+>", " ", text or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _normalize_work_item(row: dict[str, Any], *, fallback_project: str) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    f = row.get("fields") or {}
    wid = int(row.get("id") or f.get("System.Id") or 0)
    team_project = (f.get("System.TeamProject") or fallback_project or "").strip()
    url = (
        f"{_org_url()}/{quote(team_project)}/_workitems/edit/{wid}"
        if team_project and wid
        else ""
    )
    assigned = f.get("System.AssignedTo") or {}
    assigned_name = ""
    if isinstance(assigned, dict):
        assigned_name = (assigned.get("displayName") or assigned.get("uniqueName") or "")
    desc = f.get("System.Description") or ""
    if isinstance(desc, str):
        desc_plain = _strip_html(desc)[:4000]
    else:
        desc_plain = ""
    return {
        "id": wid,
        "title": (f.get("System.Title") or "").strip(),
        "state": (f.get("System.State") or "").strip(),
        "type": (f.get("System.WorkItemType") or "").strip(),
        "project": team_project,
        "changed": f.get("System.ChangedDate") or "",
        "assigned_to": assigned_name,
        "priority": _priority_value(f.get("Microsoft.VSTS.Common.Priority")),
        "tags": (f.get("System.Tags") or "").strip(),
        "description": desc_plain,
        "url": url,
    }


def build_coding_instruction(item: dict[str, Any]) -> str:
    """Compose a coding task from a Boards work item (for architect/developer)."""
    wid = item.get("id")
    title = item.get("title") or ""
    wtype = item.get("type") or "Work Item"
    state = item.get("state") or ""
    pri = item.get("priority")
    url = item.get("url") or ""
    desc = (item.get("description") or "").strip()
    lines = [
        f"Implement Azure Boards {wtype} #{wid}: {title}",
        f"State: {state}" + (f" · Priority: {pri}" if pri is not None else ""),
    ]
    if url:
        lines.append(f"Work item: {url}")
    if desc:
        lines.append("")
        lines.append("Requirements / description:")
        lines.append(desc)
    else:
        lines.append("")
        lines.append(
            "No detailed description on the work item — "
            "implement based on the title and typical best practices for this type."
        )
    return "\n".join(lines)


async def create_work_item(
    *,
    project: str,
    work_item_type: str,
    title: str,
    description: str = "",
    tags: str = "",
    parent_id: int | None = None,
) -> dict[str, Any]:
    """Create one AzDO work item (Feature / Task / User Story)."""
    if not boards_configured():
        raise RuntimeError("Azure DevOps org URL / PAT not configured")
    proj = (project or "").strip()
    if not proj:
        raise ValueError("project is required")
    wtype = (work_item_type or "Task").strip()
    patch: list[dict[str, Any]] = [
        {"op": "add", "path": "/fields/System.Title", "value": title[:255]},
    ]
    if description:
        patch.append(
            {
                "op": "add",
                "path": "/fields/System.Description",
                "value": f"<div>{description.replace(chr(10), '<br/>')}</div>",
            }
        )
    if tags:
        patch.append({"op": "add", "path": "/fields/System.Tags", "value": tags})
    if parent_id:
        patch.append(
            {
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "System.LinkTypes.Hierarchy-Reverse",
                    "url": f"{_org_url()}/_apis/wit/workitems/{int(parent_id)}",
                },
            }
        )

    url = (
        f"{_org_url()}/{quote(proj)}/_apis/wit/workitems/${quote(wtype)}"
        f"?api-version=7.1"
    )
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(
            url,
            headers={**_headers(), "Content-Type": "application/json-patch+json"},
            json=patch,
        )
        if resp.status_code >= 400:
            logger.error("AzDO create WI failed: %s %s", resp.status_code, resp.text[:400])
            resp.raise_for_status()
        row = resp.json()
    item = _normalize_work_item(row, fallback_project=proj)
    logger.info("Created AzDO %s #%s in %s", wtype, item.get("id"), proj)
    return item
