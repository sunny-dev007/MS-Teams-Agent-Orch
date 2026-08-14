"""Rich, channel-aware response formatting for WhatsApp + Teams.

WhatsApp: bold sections, key/value rows, ASCII metric bars (no HTML).
Teams/Copilot: same structure plus markdown tables where helpful.
"""

from __future__ import annotations

from typing import Any, Literal, Sequence

from agent.core.channel_identity import is_teams_session

Channel = Literal["whatsapp", "teams"]

_BAR_WIDTH = 10


def detect_channel(session_id: str | None = None) -> Channel:
    return "teams" if is_teams_session(session_id) else "whatsapp"


def outcome_banner(title: str, *, ok: bool | None = None, subtitle: str = "") -> str:
    """Top banner for success / fail / neutral cards."""
    if ok is True:
        mark = "OK"
    elif ok is False:
        mark = "FAILED"
    else:
        mark = "UPDATE"
    lines = [f"*Sunny's AI Agent* — {title}", f"`[{mark}]`"]
    if subtitle:
        lines.append(subtitle)
    lines.append("")
    return "\n".join(lines)


def section_title(title: str) -> str:
    return f"── *{title}* ──"


def metric_bar(label: str, value: float, *, maximum: float = 10.0, unit: str = "") -> str:
    """ASCII progress bar for scores / utilization (WhatsApp + Teams safe)."""
    try:
        maximum = float(maximum) if maximum else 10.0
        value = float(value)
    except (TypeError, ValueError):
        return f"• *{label}:* {value}{unit}"
    if maximum <= 0:
        maximum = 10.0
    ratio = max(0.0, min(1.0, value / maximum))
    filled = int(round(ratio * _BAR_WIDTH))
    empty = _BAR_WIDTH - filled
    bar = "█" * filled + "░" * empty
    suffix = f" {value:g}/{maximum:g}{unit}"
    return f"• *{label}:* `{bar}`{suffix}"


def kv_block(
    rows: Sequence[tuple[str, str]],
    *,
    channel: Channel = "whatsapp",
) -> str:
    """Key/value block — markdown table on Teams when ≥2 rows, else bullets."""
    clean = [(str(k).strip(), str(v).strip()) for k, v in rows if str(k).strip()]
    if not clean:
        return ""
    if channel == "teams" and len(clean) >= 2:
        lines = [
            "| Field | Value |",
            "| --- | --- |",
            *[f"| {k} | {v} |" for k, v in clean],
        ]
        return "\n".join(lines)
    return "\n".join(f"• *{k}:* {v}" for k, v in clean)


def bullet_list(items: Sequence[str]) -> str:
    return "\n".join(f"• {item}" for item in items if str(item).strip())


def next_steps(lines: Sequence[str]) -> str:
    body = bullet_list(lines)
    if not body:
        return ""
    return f"{section_title('Next')}\n{body}"


def format_result_card(
    *,
    title: str,
    ok: bool | None = None,
    subtitle: str = "",
    fields: Sequence[tuple[str, str]] | None = None,
    metrics: Sequence[tuple[str, float, float]] | None = None,
    notes: Sequence[str] | None = None,
    actions: Sequence[str] | None = None,
    channel: Channel = "whatsapp",
) -> str:
    """Standard outcome card used after APPROVE / CI / deploy / evaluation."""
    parts: list[str] = [outcome_banner(title, ok=ok, subtitle=subtitle).rstrip()]

    if fields:
        parts.append(section_title("Summary"))
        parts.append(kv_block(fields, channel=channel))

    if metrics:
        parts.append(section_title("Metrics"))
        for label, value, maximum in metrics:
            parts.append(metric_bar(label, value, maximum=maximum))

    if notes:
        parts.append(section_title("Details"))
        parts.append(bullet_list(notes))

    if actions:
        parts.append(next_steps(actions))

    return "\n\n".join(p for p in parts if p).strip() + "\n"


def format_task_table(
    rows: Sequence[dict[str, Any]],
    *,
    channel: Channel = "whatsapp",
) -> str:
    """Recent tasks / status list with better visibility."""
    if not rows:
        return (
            f"{outcome_banner('Task status', ok=None).rstrip()}\n\n"
            "No persisted task history yet.\n"
            "Active coding tasks use IDs in *APPROVE* / *REJECT* messages.\n"
        )
    if channel == "teams":
        lines = [
            outcome_banner("Recent tasks", ok=None).rstrip(),
            "",
            "| Task | Status | Intent |",
            "| --- | --- | --- |",
        ]
        for r in rows:
            tid = str(r.get("id") or "")[:12]
            status = str(r.get("status") or "n/a")
            intent = str(r.get("intent") or "n/a")
            lines.append(f"| `{tid}` | {status} | {intent} |")
            if r.get("pr_url"):
                lines.append(f"|  | PR | {r['pr_url']} |")
        return "\n".join(lines) + "\n"

    lines = [outcome_banner("Recent tasks", ok=None).rstrip(), "", section_title("History")]
    for r in rows:
        tid = str(r.get("id") or "")
        status = str(r.get("status") or "n/a")
        intent = str(r.get("intent") or "n/a")
        lines.append(f"• `{tid}` — *{status}* — {intent}")
        if r.get("pr_url"):
            lines.append(f"  PR: {r['pr_url']}")
    lines.append("")
    lines.append(next_steps(["Reply *help* for the menu", "Send a new coding request"]))
    return "\n".join(lines)


def format_score_metrics(
    scores: dict[str, float],
    *,
    maximum: float = 10.0,
    title: str = "Scores",
) -> str:
    """Render named scores as ASCII bars (e.g. PR review)."""
    if not scores:
        return ""
    lines = [section_title(title)]
    for label, value in scores.items():
        lines.append(metric_bar(str(label), float(value), maximum=maximum))
    return "\n".join(lines)
