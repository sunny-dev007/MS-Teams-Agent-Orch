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
    repo_provider: str  # github | azure_devops
    azdo_project: str
    azdo_repo_id: str

    # Session / multi-turn
    session_awaiting: str
    session_data: dict

    # Meeting
    meeting_title: str
    meeting_when: str
    meeting_attendees: str
    meeting_link: str

    # Developer agent output
    file_changes: list[dict]
    dev_reasoning: str
    workspace_path: str

    # Multi-gate workflow
    implementation_plan: str
    plan_summary: str
    plan_approved: bool
    pr_review_mode: str  # ai | manual
    pr_number: int
    pr_id: int
    pr_review_score: int

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

    # CI test-fixer agent
    ci_build_id: str
    ci_failure_summary: str
    ci_watch_phase: str  # pr_validation | post_merge | ci_fix_validation

    # Evaluation
    evaluation_text: str

    # Multi-agent metadata
    planned_by: str
    handled_by: str

    # Communication
    whatsapp_phone: str
    notification_text: str
    messages: Annotated[list[BaseMessage], add_messages]
    error: str

    # Release Agent Fabric (optional correlation)
    release_id: str
    doc_url: str
    app_url: str

    # Document Knowledge Fabric (optional — flag gated)
    kb_ingest_results: list
    kb_citations: list
    kb_docs_used: list
    kb_related_queries: list
    handoff_note: str

    # Teams chat attachments (Copilot Studio → SharePoint upload path)
    attachments: list
    upload_summarize: bool
