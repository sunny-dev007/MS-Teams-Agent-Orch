"""Durable workflow-gate mirror on /home/site/data.

App Service recycles containers mid-request. SQLite session rows written by the
dying worker are sometimes invisible to the new worker. An atomic JSON file on
the persistent volume is the source of truth for the active WhatsApp gate.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from agent.core.logging import get_logger

logger = get_logger(__name__)


def _gates_dir() -> Path:
    if os.getenv("WEBSITE_SITE_NAME"):
        path = Path("/home/site/data/workflow_gates")
    else:
        path = Path("tmp/workflow_gates")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _gate_path(phone: str) -> Path:
    safe = "".join(c for c in (phone or "unknown") if c.isalnum() or c in ("+", "-", "_"))
    return _gates_dir() / f"{safe}.json"


def write_gate(
    phone: str,
    *,
    awaiting: str,
    task_id: str = "",
    provider: str = "",
    data: dict[str, Any] | None = None,
) -> None:
    if not phone:
        return
    payload = {
        "awaiting": awaiting,
        "task_id": task_id or (data or {}).get("pending_task_id") or "",
        "provider": provider or "",
        "data": data or {},
        "updated_at": time.time(),
    }
    path = _gate_path(phone)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
        tmp.replace(path)
        logger.info(
            "Durable gate written phone=%s awaiting=%s task=%s",
            phone,
            awaiting,
            payload.get("task_id"),
        )
    except Exception:
        logger.exception("Failed writing durable gate for %s", phone)


def read_gate(phone: str) -> dict[str, Any] | None:
    if not phone:
        return None
    path = _gate_path(phone)
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed reading durable gate for %s", phone)
        return None


def clear_gate(phone: str) -> None:
    if not phone:
        return
    path = _gate_path(phone)
    try:
        path.unlink(missing_ok=True)
    except Exception:
        logger.exception("Failed clearing durable gate for %s", phone)


def merge_session_with_gate(session: dict[str, Any]) -> dict[str, Any]:
    """Prefer durable gate when it is newer / more advanced than SQLite session."""
    phone = session.get("phone") or ""
    gate = read_gate(phone)
    if not gate:
        return session

    out = dict(session)
    data = dict(out.get("data") or {})
    gate_data = dict(gate.get("data") or {})
    # Gate file data wins for PR fields / task id
    for key, val in gate_data.items():
        if val not in (None, "", [], {}):
            data[key] = val
    if gate.get("task_id") and not data.get("pending_task_id"):
        data["pending_task_id"] = gate["task_id"]

    sql_awaiting = out.get("awaiting")
    file_awaiting = gate.get("awaiting")
    # Prefer pr_review_mode / later gates from durable file over stale plan_approval
    rank = {
        None: 0,
        "": 0,
        "plan_approval": 1,
        "pr_review_mode": 2,
        "manual_pr_review": 3,
        "pipeline_watching": 4,
        "approval": 5,
        "ci_fix_approval": 6,
    }
    if rank.get(file_awaiting, 0) >= rank.get(sql_awaiting, 0):
        out["awaiting"] = file_awaiting
        if gate.get("provider"):
            out["provider"] = gate["provider"]
    out["data"] = data
    return out
