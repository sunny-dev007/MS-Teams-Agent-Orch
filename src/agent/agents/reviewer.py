import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.services.llm import get_llm

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
        return {
            **state,
            "review_result": "approved",
            "review_comments": "No changes to review.",
            "status": "review_complete",
        }

    changes_summary = "\n".join(
        f"- {c.get('action', 'modify')} `{c['path']}`" for c in file_changes
    )
    file_diffs = "\n\n".join(
        f"### {c['path']} ({c.get('action', 'modify')})\n```\n{c.get('content', '')[:3000]}\n```"
        for c in file_changes
    )

    prompt = CHECKLIST_TEMPLATE.format(
        task_description=user_msg,
        changes_summary=changes_summary,
        file_diffs=file_diffs,
    )

    llm = get_llm(temperature=0)
    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])

    review = _parse_review(response.content)

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
