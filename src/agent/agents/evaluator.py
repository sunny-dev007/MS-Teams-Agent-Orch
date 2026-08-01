from agent.agents.state import AgentState
from agent.core.logging import get_logger

logger = get_logger(__name__)


async def evaluate_result(state: AgentState) -> AgentState:
    """Produce a step-by-step final evaluation for WhatsApp."""
    if state.get("evaluation_text"):
        text = state["evaluation_text"]
    else:
        steps: list[str] = []
        intent = state.get("intent", "")
        status = state.get("status", "")
        steps.append(f"1. Intent handled: `{intent or 'n/a'}`")
        steps.append(f"2. Final status: `{status}`")

        if state.get("repo_url"):
            steps.append(f"3. Repository: {state['repo_url']}")
        if state.get("file_changes"):
            changes = "\n".join(
                f"   - {c.get('action', 'modify')} `{c.get('path')}`"
                for c in state["file_changes"]
            )
            steps.append(f"4. Code changes:\n{changes}")
        if state.get("review_result"):
            steps.append(f"5. Review: {state['review_result']}")
            if state.get("review_comments"):
                steps.append(f"   Comments: {state['review_comments'][:500]}")
        if state.get("approval_status"):
            steps.append(f"6. Human approval: {state['approval_status']}")
        if state.get("commit_sha"):
            steps.append(f"7. Commit: `{state['commit_sha'][:8]}`")
        if state.get("pr_url"):
            steps.append(f"8. Pull request: {state['pr_url']}")
        if state.get("pipeline_url"):
            steps.append(f"9. Pipeline: {state['pipeline_url']}")
        if state.get("meeting_title"):
            steps.append(f"3. Meeting: {state['meeting_title']}")
            if state.get("meeting_link"):
                steps.append(f"4. Meet link: {state['meeting_link']}")
        if state.get("error"):
            steps.append(f"Error: {state['error']}")

        steps.append("\n*Next:* Reply *help* for the menu, or send another task.")
        text = "\n".join(steps)

    logger.info("Evaluation ready for task %s", state.get("task_id"))
    return {
        **state,
        "status": "evaluation",
        "evaluation_text": text,
        "notification_text": text,
    }


async def task_status(state: AgentState) -> AgentState:
    from agent.core.session import list_recent_task_summaries

    rows = await list_recent_task_summaries(5)
    if not rows:
        text = (
            "No persisted task history yet.\n\n"
            "Active coding tasks use thread IDs in APPROVE/REJECT messages "
            "(e.g. `APPROVE abc12345`)."
        )
    else:
        lines = [
            f"• `{r['id']}` — *{r['status']}* — {r.get('intent') or 'n/a'}"
            + (f"\n  PR: {r['pr_url']}" if r.get("pr_url") else "")
            for r in rows
        ]
        text = "*Recent tasks*\n\n" + "\n".join(lines)

    return {
        **state,
        "status": "general_response",
        "notification_text": text,
        "evaluation_text": text,
    }
