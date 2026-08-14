from agent.agents.state import AgentState
from agent.core.logging import get_logger

logger = get_logger(__name__)


async def evaluate_result(state: AgentState) -> AgentState:
    """Produce a step-by-step final evaluation for WhatsApp / Teams."""
    from agent.services.rich_response import detect_channel, format_result_card

    channel = detect_channel(state.get("whatsapp_phone"))

    if state.get("evaluation_text"):
        text = state["evaluation_text"]
    else:
        fields: list[tuple[str, str]] = [
            ("Intent", f"`{state.get('intent') or 'n/a'}`"),
            ("Final status", f"`{state.get('status') or 'n/a'}`"),
        ]
        notes: list[str] = []
        metrics: list[tuple[str, float, float]] = []

        if state.get("repo_url"):
            fields.append(("Repository", state["repo_url"]))
        if state.get("file_changes"):
            changes = ", ".join(
                f"`{c.get('path')}`" for c in state["file_changes"][:8] if c.get("path")
            )
            notes.append(f"Code changes: {changes or 'see diff in PR'}")
        if state.get("review_result"):
            fields.append(("Review", str(state["review_result"])))
            # Best-effort score bar when review looks like "8/10" or "Score: 8"
            raw = str(state.get("review_result") or "")
            for token in raw.replace(",", " ").split():
                if "/" in token:
                    left, _, right = token.partition("/")
                    try:
                        metrics.append(("Review score", float(left), float(right)))
                        break
                    except ValueError:
                        pass
            if state.get("review_comments"):
                notes.append(f"Comments: {str(state['review_comments'])[:500]}")
        if state.get("approval_status"):
            fields.append(("Human approval", str(state["approval_status"])))
        if state.get("commit_sha"):
            fields.append(("Commit", f"`{str(state['commit_sha'])[:8]}`"))
        if state.get("pr_url"):
            fields.append(("Pull request", state["pr_url"]))
        if state.get("pipeline_url"):
            fields.append(("Pipeline", state["pipeline_url"]))
        if state.get("meeting_title"):
            fields.append(("Meeting", state["meeting_title"]))
            if state.get("meeting_link"):
                fields.append(("Meet link", state["meeting_link"]))
        if state.get("error"):
            notes.append(f"Error: {state['error']}")

        ok = None
        status = (state.get("status") or "").lower()
        if "fail" in status or state.get("error"):
            ok = False
        elif "complete" in status or state.get("approval_status") == "approved":
            ok = True

        text = format_result_card(
            title="Final evaluation",
            ok=ok,
            fields=fields,
            metrics=metrics or None,
            notes=notes or None,
            actions=["Reply *help* for the menu", "Send another task"],
            channel=channel,
        )
        # Keep a visible metric line even when only review_comments exist
        if not metrics and state.get("review_result"):
            text = text  # already formatted

    logger.info("Evaluation ready for task %s", state.get("task_id"))
    return {
        **state,
        "status": "evaluation",
        "evaluation_text": text,
        "notification_text": text,
    }


async def task_status(state: AgentState) -> AgentState:
    from agent.core.session import list_recent_task_summaries
    from agent.services.rich_response import detect_channel, format_task_table

    rows = await list_recent_task_summaries(5)
    channel = detect_channel(state.get("whatsapp_phone"))
    text = format_task_table(rows, channel=channel)

    return {
        **state,
        "status": "general_response",
        "notification_text": text,
        "evaluation_text": text,
    }
