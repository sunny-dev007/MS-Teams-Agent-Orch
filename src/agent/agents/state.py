from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    task_id: str
    status: str
    source: str
    intent: str
    user_message: str

    # Email context
    email_to: str
    email_subject: str
    email_body: str
    email_sender: str

    # Repo context
    repo_url: str
    repo_owner: str
    repo_name: str
    branch_name: str

    # Developer agent output
    file_changes: list[dict]
    dev_reasoning: str
    workspace_path: str

    # Reviewer agent output
    review_result: str
    review_comments: str
    review_iteration: int

    # Human approval
    approval_status: str
    approval_message: str

    # Deployment output
    commit_sha: str
    pr_url: str
    pipeline_url: str
    pipeline_status: str

    # Communication
    whatsapp_phone: str
    notification_text: str
    messages: Annotated[list[BaseMessage], add_messages]
    error: str
