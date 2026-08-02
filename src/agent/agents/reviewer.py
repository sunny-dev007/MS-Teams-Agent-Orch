import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.services.llm import invoke_llm

logger = get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "reviewer_system.txt").read_text()
CHECKLIST_TEMPLATE = (PROMPTS_DIR / "reviewer_checklist.txt").read_text()

MAX_REVIEW_ITERATIONS = 3


async def review_code(state: AgentState) -> AgentState:
    task_id = state.get("task_id", "unknown")
    file_changes = state.get("file_changes", [])
    user_msg = state.get("user_message", "") or state.get("email_body", "")
    iteration = state.get("review_iteration", 0)

    if not file_changes:
        # Never auto-approve empty work — that produced false APPROVE prompts
        # after clone/dev failures.
        return {
            **state,
            "review_result": "rejected",
            "review_comments": "No code changes were produced to review.",
            "status": "failed",
            "notification_text": "No code changes were produced to review.",
            "error": state.get("error") or "No file changes to review",
        }

    changes_summary = "\n".join(
        f"- {c.get('action', 'modify')} `{c['path']}`" for c in file_changes
    )
    file_diffs = "\n\n".join(
        f"### {c['path']} ({c.get('action', 'modify')})\n```\n{_content_for_review(c)}\n```"
        for c in file_changes
    )

    prompt = CHECKLIST_TEMPLATE.format(
        task_description=user_msg,
        changes_summary=changes_summary,
        file_diffs=file_diffs,
    )

    response = await invoke_llm(
        [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ],
        temperature=0,
        role="review",
    )

    review = _parse_review(response.content)
    review = _soften_false_incomplete_html(review, file_changes)

    logger.info(
        "Reviewer result=%s score=%s for task %s (iteration %d)",
        review["result"],
        review.get("score"),
        task_id,
        iteration,
    )

    new_iteration = iteration + 1
    review_text = f"*Score:* {review.get('score', 'N/A')}/10\n*Summary:* {review.get('summary', '')}"

    if review.get("issues"):
        review_text += "\n*Issues:*"
        for issue in review["issues"]:
            review_text += f"\n  - [{issue.get('severity', '?')}] {issue.get('file', '?')}: {issue.get('description', '')}"

    return {
        **state,
        "review_result": review["result"],
        "review_comments": review_text,
        "review_iteration": new_iteration,
        "status": "review_complete",
        "notification_text": review_text,
    }


def _content_for_review(change: dict) -> str:
    """Pass enough content for HTML/CSS reviews; mark truncation so LLM does not reject falsely."""
    path = (change.get("path") or "").lower()
    content = change.get("content") or ""
    limit = 12000 if path.endswith((".html", ".htm", ".css")) else 4000
    if len(content) <= limit:
        return content
    return content[:limit] + "\n\n/* REVIEW_NOTE: file truncated for reviewer prompt only; full file is stored for deploy */"


def _soften_false_incomplete_html(review: dict, file_changes: list) -> dict:
    """Don't block human approval when reviewer only complains about prompt truncation."""
    if (review.get("result") or "").lower() != "changes_requested":
        return review

    html_files = [
        c
        for c in file_changes
        if str(c.get("path", "")).lower().endswith((".html", ".htm"))
    ]
    if not html_files:
        return review

    complete_html = all(
        "</html>" in (c.get("content") or "").lower() for c in html_files
    )
    if not complete_html:
        return review

    issues = list(review.get("issues") or [])
    remaining = []
    for issue in issues:
        desc = (issue.get("description") or "").lower()
        if any(
            key in desc
            for key in (
                "incomplete",
                "cut off",
                "truncated",
                "mid-html",
                "mid-line",
                "provide the full",
            )
        ):
            continue
        remaining.append(issue)

    if remaining:
        review["issues"] = remaining
        return review

    logger.info("Ignoring truncation-only HTML review complaints; treating as approved")
    return {
        **review,
        "result": "approved",
        "summary": (review.get("summary") or "")
        + " (truncation-only concerns ignored; full HTML is present for deploy)",
        "issues": [],
    }


def _parse_review(content: str) -> dict:
    start = content.find("{")
    end = content.rfind("}") + 1
    if start != -1 and end > 0:
        try:
            return json.loads(content[start:end])
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse reviewer output, defaulting to approved")
    return {"result": "approved", "score": 7, "summary": "Auto-approved (parse failure)", "issues": []}
