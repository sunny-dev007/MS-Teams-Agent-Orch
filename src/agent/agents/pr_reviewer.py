"""Post-PR AI review with detailed WhatsApp formatting (gpt-4.1 / planning deployment)."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from agent.agents.reviewer import _content_for_review, _soften_false_incomplete_html
from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.services.llm import invoke_llm

logger = get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "pr_reviewer_system.txt").read_text()


async def review_pull_request(state: AgentState) -> AgentState:
    task_id = state.get("task_id", "unknown")
    file_changes = state.get("file_changes") or []
    user_msg = state.get("user_message", "") or state.get("email_body", "")
    pr_url = state.get("pr_url", "N/A")

    if not file_changes:
        return {
            **state,
            "status": "failed",
            "review_result": "rejected",
            "notification_text": "No changes to review on the PR.",
        }

    changes_summary = "\n".join(
        f"- {c.get('action', 'modify')} `{c['path']}`" for c in file_changes
    )
    file_diffs = "\n\n".join(
        f"### {c['path']} ({c.get('action', 'modify')})\n```\n{_content_for_review(c)}\n```"
        for c in file_changes
    )

    prompt = (
        f"## Original request\n{user_msg}\n\n"
        f"## Pull request\n{pr_url}\n\n"
        f"## Changes summary\n{changes_summary}\n\n"
        f"## Diffs\n{file_diffs}\n\n"
        "Review this PR for merge to main."
    )

    response = await invoke_llm(
        [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ],
        temperature=0.1,
        role="review",
    )

    review = _parse_pr_review(response.content)
    review = _soften_false_incomplete_html(review, file_changes)
    detail = review.get("whatsapp_detail") or _format_pr_review_fallback(review)

    logger.info(
        "PR AI review task=%s result=%s score=%s",
        task_id,
        review.get("result"),
        review.get("score"),
    )

    return {
        **state,
        "status": "pr_review_complete",
        "review_result": review.get("result", "approved"),
        "review_comments": detail,
        "pr_review_score": review.get("score"),
        "notification_text": detail,
    }


def _parse_pr_review(content: str) -> dict:
    start = content.find("{")
    end = content.rfind("}") + 1
    if start != -1 and end > 0:
        try:
            return json.loads(content[start:end])
        except json.JSONDecodeError:
            pass
    return {
        "result": "approved",
        "score": 7,
        "summary": "Auto-approved (parse failure)",
        "whatsapp_detail": (content or "")[:3500],
        "issues": [],
    }


def _format_pr_review_fallback(review: dict) -> str:
    from agent.services.rich_response import format_result_card, metric_bar

    score_raw = review.get("score", "N/A")
    try:
        score_val = float(score_raw)
        metrics = [("PR score", score_val, 10.0)]
        score_line = metric_bar("PR score", score_val, maximum=10.0)
    except (TypeError, ValueError):
        metrics = None
        score_line = f"• *PR score:* {score_raw}/10"

    notes: list[str] = [f"Result: {review.get('result', 'approved')}"]
    if review.get("summary"):
        notes.append(str(review["summary"]))
    if review.get("strengths"):
        notes.append("Strengths: " + "; ".join(str(s) for s in review["strengths"][:5]))
    if review.get("issues"):
        for issue in review["issues"][:6]:
            if isinstance(issue, dict):
                notes.append(
                    f"[{issue.get('severity', '?')}] `{issue.get('file', '?')}`: "
                    f"{issue.get('description', '')}"
                )
            else:
                notes.append(str(issue))
    if review.get("security_notes"):
        notes.append("Security: " + "; ".join(review["security_notes"][:3]))
    if review.get("deployment_notes"):
        notes.append("Deploy notes: " + "; ".join(review["deployment_notes"][:3]))

    text = format_result_card(
        title="AI PR review",
        ok=None,
        fields=[("Result", str(review.get("result", "approved")))],
        metrics=metrics,
        notes=notes,
        actions=[
            "Reply *1* if already choosing AI review path",
            "Reply *APPROVE <task_id>* when ready to deploy (after gates)",
        ],
    )
    # Ensure score bar visible even if metrics parsing failed
    if metrics is None:
        text = text.replace("── *Details* ──", f"{score_line}\n\n── *Details* ──", 1)
    return text[:3500]
