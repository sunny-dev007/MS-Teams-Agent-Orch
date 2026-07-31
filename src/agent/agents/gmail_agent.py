import json

from langchain_core.messages import HumanMessage, SystemMessage

from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.services.gmail import get_message, list_recent_messages, send_email
from agent.services.llm import get_llm

logger = get_logger(__name__)

CLASSIFY_PROMPT = """You are an email classification agent. Analyze the email and determine:

1. Is this email related to a code repository (GitHub, Azure DevOps, GitLab)?
2. Is it actionable (requires code changes, bug fix, feature request, PR review)?
3. Extract the repository URL if mentioned.

Email senders from GitHub typically end with @github.com or notifications@github.com.
Azure DevOps notifications come from azuredevops@microsoft.com.

Respond in JSON:
{
  "is_repo_related": true/false,
  "is_actionable": true/false,
  "action_type": "code_change" | "bug_fix" | "pr_review" | "feature_request" | "info_only",
  "repo_url": "https://github.com/owner/repo" or null,
  "summary": "brief summary of what needs to be done",
  "priority": "high" | "medium" | "low"
}"""


async def read_gmail(state: AgentState) -> AgentState:
    task_id = state.get("task_id", "unknown")

    try:
        messages = list_recent_messages(max_results=5)

        if not messages:
            return {
                **state,
                "status": "email_summary",
                "notification_text": "No new emails found in your inbox.",
            }

        actionable_emails = []
        summaries = []

        for msg_ref in messages:
            email = get_message(msg_ref["id"])
            classification = await _classify_email(email)

            subject = email.get("subject", "(no subject)")
            summaries.append(f"• *{subject}*\n  From: {email.get('from', '?')}")

            if classification.get("is_actionable"):
                actionable_emails.append({
                    "email": email,
                    "classification": classification,
                })

        if actionable_emails:
            top = actionable_emails[0]
            cls = top["classification"]
            email = top["email"]

            logger.info("Found actionable email: %s → %s", email["subject"], cls.get("action_type"))

            return {
                **state,
                "intent": "code_change",
                "email_subject": email["subject"],
                "email_body": email["body"][:2000],
                "email_sender": email.get("sender_email", ""),
                "repo_url": cls.get("repo_url", state.get("repo_url", "")),
                "user_message": cls.get("summary", email["subject"]),
                "status": "task_started",
                "notification_text": (
                    f"Found actionable email:\n"
                    f"*Subject:* {email['subject']}\n"
                    f"*Action:* {cls.get('summary', 'N/A')}\n"
                    f"*Repo:* {cls.get('repo_url', 'N/A')}\n\n"
                    f"Starting development..."
                ),
            }

        summary_text = "\n".join(summaries[:5])
        return {
            **state,
            "status": "email_summary",
            "notification_text": f"Found {len(messages)} recent emails, none require action:\n\n{summary_text}",
        }

    except Exception as e:
        logger.exception("Gmail agent failed for task %s", task_id)
        return {
            **state,
            "status": "failed",
            "error": str(e),
            "notification_text": f"Failed to read emails: {e}",
        }


async def _classify_email(email: dict) -> dict:
    llm = get_llm(temperature=0)
    content = f"Subject: {email.get('subject', '')}\nFrom: {email.get('from', '')}\n\n{email.get('body', '')[:2000]}"

    response = await llm.ainvoke([
        SystemMessage(content=CLASSIFY_PROMPT),
        HumanMessage(content=content),
    ])

    try:
        start = response.content.find("{")
        end = response.content.rfind("}") + 1
        if start != -1 and end > 0:
            return json.loads(response.content[start:end])
    except json.JSONDecodeError:
        pass

    return {"is_repo_related": False, "is_actionable": False, "action_type": "info_only"}


async def compose_and_send_email(state: AgentState) -> AgentState:
    task_id = state.get("task_id", "unknown")
    to = state.get("email_to", "")
    subject = state.get("email_subject", "")
    body = state.get("email_body", "")
    user_msg = state.get("user_message", "")

    to = to.strip().strip("<>").strip()
    logger.info("Send email: to=%r, subject=%r, task=%s", to, subject, task_id)

    if not to:
        return {
            **state,
            "status": "failed",
            "notification_text": "No recipient email address provided. Please specify who to send the email to.",
        }

    if not subject or not body:
        llm = get_llm(temperature=0.3)
        response = await llm.ainvoke([
            SystemMessage(content="You are an email composition assistant. Based on the user's request, generate an appropriate email subject and body. Respond in JSON: {\"subject\": \"...\", \"body\": \"...\"}"),
            HumanMessage(content=f"User request: {user_msg}\nRecipient: {to}\nSubject hint: {subject or 'none'}\nBody hint: {body or 'none'}"),
        ])
        try:
            import json
            parsed = json.loads(response.content[response.content.find("{"):response.content.rfind("}") + 1])
            subject = subject or parsed.get("subject", "Message from AI Agent")
            body = body or parsed.get("body", user_msg)
        except Exception:
            subject = subject or "Message from AI Agent"
            body = body or user_msg

    try:
        send_email(to, subject, body)
        logger.info("Email sent to %s for task %s", to, task_id)
        return {
            **state,
            "status": "general_response",
            "notification_text": f"Email sent successfully to {to}\n*Subject:* {subject}",
        }
    except Exception as e:
        logger.exception("Failed to send email for task %s", task_id)
        return {
            **state,
            "status": "failed",
            "notification_text": f"Failed to send email: {e}",
        }
