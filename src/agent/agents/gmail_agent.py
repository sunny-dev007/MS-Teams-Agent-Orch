import json

from langchain_core.messages import HumanMessage, SystemMessage

from agent.agents.state import AgentState
from agent.core.logging import get_logger
from agent.core.persona import get_persona_prompt
from agent.services.gmail import get_message, list_recent_messages, send_email
from agent.services.llm import invoke_llm

logger = get_logger(__name__)

CLASSIFY_PROMPT = """You are an email classification agent. Analyze the email and determine:

1. Is this email related to a code repository (GitHub, Azure DevOps, GitLab)?
2. Is it actionable (requires code changes, bug fix, feature request, PR review)?
3. Extract the repository URL if mentioned.

Respond in JSON:
{
  "is_repo_related": true/false,
  "is_actionable": true/false,
  "action_type": "code_change" | "bug_fix" | "pr_review" | "feature_request" | "info_only",
  "repo_url": "https://github.com/owner/repo" or null,
  "summary": "brief summary of what needs to be done",
  "priority": "high" | "medium" | "low"
}"""

DIGEST_PROMPT = """You write WhatsApp-friendly email digests for Sunny Kushwaha.
Given raw email metadata, produce a comprehensive but scannable digest.

Rules:
- Use short sections and bullets
- For each email: From, Subject, Summary (1-2 lines), Suggested action
- Group or highlight anything urgent
- Do NOT invent email content that is not present
- Keep total length suitable for WhatsApp (aim under 3500 chars)
"""


async def read_gmail(state: AgentState) -> AgentState:
    task_id = state.get("task_id", "unknown")

    try:
        messages = list_recent_messages(max_results=12)

        if not messages:
            return {
                **state,
                "status": "email_summary",
                "notification_text": "No emails found in your inbox, Sunny.",
                "evaluation_text": "1. Checked Gmail inbox\n2. No messages found\n3. Done",
            }

        emails: list[dict] = []
        actionable_emails = []

        for msg_ref in messages:
            email = get_message(msg_ref["id"])
            emails.append(email)
            # Only classify a few for actionable coding handoff to save latency
            if len(actionable_emails) < 2 and len(emails) <= 5:
                classification = await _classify_email(email)
                if classification.get("is_actionable") and classification.get("is_repo_related"):
                    actionable_emails.append({"email": email, "classification": classification})

        digest = await _build_digest(emails)

        if actionable_emails:
            top = actionable_emails[0]
            cls = top["classification"]
            email = top["email"]
            logger.info(
                "Found actionable email: %s → %s", email["subject"], cls.get("action_type")
            )
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
                    f"{digest}\n\n"
                    "---\n"
                    "*Actionable coding item detected*\n"
                    f"*Subject:* {email['subject']}\n"
                    f"*Action:* {cls.get('summary', 'N/A')}\n"
                    f"*Repo:* {cls.get('repo_url') or 'not found — tell me the repo'}\n\n"
                    "Starting development if repo is known…"
                ),
                "evaluation_text": (
                    f"1. Fetched {len(emails)} recent emails\n"
                    "2. Built digest for WhatsApp\n"
                    f"3. Detected actionable item: {email['subject']}\n"
                    "4. Handing off to developer agent"
                ),
            }

        return {
            **state,
            "status": "email_summary",
            "notification_text": digest,
            "evaluation_text": (
                f"1. Fetched {len(emails)} recent emails\n"
                "2. Built comprehensive WhatsApp digest\n"
                "3. No coding handoff required\n"
                "4. Done"
            ),
        }

    except Exception as e:
        logger.exception("Gmail agent failed for task %s", task_id)
        return {
            **state,
            "status": "failed",
            "error": str(e),
            "notification_text": f"Failed to read emails: {e}",
        }


async def _build_digest(emails: list[dict]) -> str:
    payload = []
    for e in emails[:12]:
        payload.append(
            {
                "from": e.get("from", ""),
                "subject": e.get("subject", ""),
                "snippet": (e.get("snippet") or e.get("body", "")[:400]),
            }
        )
    try:
        response = await invoke_llm(
            [
                SystemMessage(content=get_persona_prompt() + "\n\n" + DIGEST_PROMPT),
                HumanMessage(content=json.dumps(payload, indent=2)),
            ],
            temperature=0.2,
            role="default",
        )
        text = (response.content or "").strip()
    except Exception:
        logger.exception("Digest LLM unavailable — using plain list")
        lines = [f"• *{e.get('subject', '(no subject)')}* — {e.get('from', '')}" for e in emails[:12]]
        text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3900] + "\n…(truncated)"
    return text


async def _classify_email(email: dict) -> dict:
    content = (
        f"Subject: {email.get('subject', '')}\n"
        f"From: {email.get('from', '')}\n\n"
        f"{email.get('body', '')[:2000]}"
    )
    try:
        response = await invoke_llm(
            [
                SystemMessage(content=CLASSIFY_PROMPT),
                HumanMessage(content=content),
            ],
            temperature=0,
            role="default",
        )
    except Exception:
        logger.exception("Email classify LLM unavailable")
        return {"is_repo_related": False, "is_actionable": False, "action_type": "info_only"}
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
        try:
            response = await invoke_llm(
                [
                    SystemMessage(
                        content=(
                            get_persona_prompt()
                            + "\n\nCompose email JSON: {\"subject\": \"...\", \"body\": \"...\"}"
                        )
                    ),
                    HumanMessage(
                        content=(
                            f"User request: {user_msg}\nRecipient: {to}\n"
                            f"Subject hint: {subject or 'none'}\nBody hint: {body or 'none'}"
                        )
                    ),
                ],
                temperature=0.3,
                role="default",
            )
            parsed = json.loads(
                response.content[response.content.find("{") : response.content.rfind("}") + 1]
            )
            subject = subject or parsed.get("subject", "Message from Sunny's AI Agent")
            body = body or parsed.get("body", user_msg)
        except Exception:
            subject = subject or "Message from Sunny's AI Agent"
            body = body or user_msg

    try:
        send_email(to, subject, body)
        logger.info("Email sent to %s for task %s", to, task_id)
        return {
            **state,
            "status": "general_response",
            "notification_text": f"Email sent to {to}\n*Subject:* {subject}",
            "evaluation_text": f"1. Composed email\n2. Sent to {to}\n3. Subject: {subject}\n4. Done",
        }
    except Exception as e:
        logger.exception("Failed to send email for task %s", task_id)
        return {
            **state,
            "status": "failed",
            "notification_text": f"Failed to send email: {e}",
        }
