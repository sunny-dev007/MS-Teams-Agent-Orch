"""App settings — Pydantic v2: BaseSettings lives in pydantic-settings (never pydantic)."""

import os
from pathlib import Path
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from agent.core.sqlite_paths import ensure_sqlite_file, to_aiosqlite_url


def _parse_csv_or_json_list(value) -> list[str]:
    """Accept App Service plain CSV/UUID or JSON array env values."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            import json

            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(v).strip() for v in parsed if str(v).strip()]
            except json.JSONDecodeError:
                pass
        return [part.strip() for part in text.split(",") if part.strip()]
    return []


def _azure_data_dir() -> Path | None:
    if os.getenv("WEBSITE_SITE_NAME"):
        path = Path("/home/site/data")
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return path
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Azure OpenAI
    azure_openai_endpoint: str = ""
    azure_openai_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_api_version: str = "2024-10-21"
    # Optional stronger models for planning / PR review (e.g. gpt-4.1); falls back to deployment above
    azure_openai_planning_deployment: str = ""
    azure_openai_review_deployment: str = ""
    # Doc Knowledge RAG / Insights — prefer high-quality Foundry chat deployment
    azure_openai_rag_deployment: str = ""
    # Comma-separated fallback deployments when primary hits rate limits (see Azure AI Foundry)
    azure_openai_fallback_deployments: str = ""
    azure_openai_planning_fallbacks: str = ""
    azure_openai_review_fallbacks: str = ""
    azure_openai_rag_fallbacks: str = ""
    llm_max_retries_per_deployment: int = 2
    llm_retry_base_delay_sec: float = 1.5

    # Multi-gate coding workflow: plan approval -> dev -> PR -> review mode -> deploy approval
    enable_multi_gate_workflow: bool = True
    # After CI/test failure, ask on WhatsApp and let the test_fixer agent repair
    enable_ci_test_fix_agent: bool = True

    # Release Agent Fabric (additive — defaults OFF so prod Dev/WhatsApp stay unchanged)
    enable_docs_agent: bool = False
    enable_qa_agent: bool = False
    enable_release_handoff: bool = False

    # Teams productivity agents (Outlook mail + Azure Boards) — defaults OFF
    # Scoped to Teams signed-in user (Entra OID). WhatsApp Gmail path unchanged.
    enable_outlook_agent: bool = False
    enable_boards_agent: bool = False
    outlook_mail_top: int = 10
    boards_work_item_top: int = 15
    azdo_boards_project: str = ""  # optional project filter for WIQL

    # Document Knowledge Fabric (SharePoint/OneDrive/OneNote → ingest → RAG/Insights)
    # Additive — defaults OFF; never affects WhatsApp/Dev coding path when false.
    enable_doc_knowledge: bool = False
    # Prefer text-embedding-3-large (3072-d) when deployed in Foundry — highest RAG recall
    azure_openai_embedding_deployment: str = "text-embedding-3-large"
    ms_graph_onedrive_user_id: str = ""  # UPN or AAD OID for OneDrive listing (optional)
    doc_knowledge_sources: str = "sharepoint,onedrive,onenote"  # csv of sources to list
    # Catalog cap in session (pagination displays page_size at a time)
    doc_knowledge_max_list: int = 100
    doc_knowledge_page_size: int = 10
    # Also list other Graph-visible site collections (falls back to configured site)
    doc_knowledge_all_sites: bool = True
    doc_knowledge_max_sites: int = 8
    doc_knowledge_folder_depth: int = 3
    # Structure-aware chunking defaults (~450–600 tokens with ~20% overlap)
    doc_knowledge_chunk_chars: int = 1800
    doc_knowledge_chunk_overlap: int = 360
    doc_knowledge_top_k: int = 8
    doc_knowledge_fetch_k: int = 24  # over-fetch before MMR diversify
    doc_knowledge_multi_query: bool = True
    doc_knowledge_min_score: float = 0.15
    # Teams chat attachments → SharePoint folder before ingest (Doc Upload Agent)
    doc_upload_folder: str = "UploadedDocs"
    doc_upload_max_bytes: int = 26_214_400  # 25 MB

    # Qdrant Cloud (optional vector backend for Doc Knowledge — defaults empty = SQLite cosine)
    # Use the *cluster* REST URL + Database API key (not only the Cloud Management key).
    qdrant_url: str = ""  # e.g. https://xxxx.aws.cloud.qdrant.io:6333
    qdrant_api_key: SecretStr = SecretStr("")
    # Separate collection for 3-large dims so older small-embedding indexes stay untouched
    qdrant_collection: str = "doc_knowledge_te3_large"
    # Prefer Qdrant when URL+key set; set false to force local SQLite vectors
    enable_qdrant: bool = True

    # Data Analyst Agent — Excel → executive workbook (SharePoint Analytics folder)
    # Additive — default OFF; does not change Doc RAG, Dev, or WhatsApp coding.
    enable_data_analyst_agent: bool = False

    # Meeting Intelligence Fabric (Teams transcripts → plan → SharePoint / email / Boards)
    # Additive — default OFF; calendar scheduling (agents/meeting.py) unchanged.
    enable_meeting_intelligence: bool = False
    meeting_transcript_folders: str = "Recordings,meeting transcript"
    meeting_plans_folder: str = "MeetingPlans"
    meeting_list_max: int = 30
    meeting_list_page_size: int = 10
    meeting_metadata_cache_hours: int = 168
    meeting_plan_default_format: str = "md,docx"
    enable_meeting_plan_email: bool = True

    # Microsoft Graph (Docs Agent + Doc Knowledge) — app-only client credentials
    ms_graph_tenant_id: str = ""
    ms_graph_client_id: str = ""
    ms_graph_client_secret: SecretStr = SecretStr("")
    ms_graph_sharepoint_site_id: str = ""
    ms_graph_sharepoint_hostname: str = ""  # e.g. contoso.sharepoint.com
    ms_graph_sharepoint_site_path: str = ""  # e.g. /sites/Engineering

    # Teams / Copilot Studio channel (additive — WhatsApp unchanged)
    enable_teams_copilot_channel: bool = True
    copilot_api_key: SecretStr = SecretStr("")
    # NoDecode: pydantic-settings otherwise JSON-parses list env vars and crashes on a plain UUID/CSV
    # (that crash took down the whole App Service, including WhatsApp).
    allowed_teams_user_ids: Annotated[list[str], NoDecode] = []

    @property
    def effective_api_key(self) -> str:
        key = self.azure_openai_api_key.get_secret_value()
        if not key:
            key = self.openai_api_key.get_secret_value()
        return key

    # WhatsApp (Meta Cloud API)
    whatsapp_verify_token: str = ""
    whatsapp_access_token: SecretStr = SecretStr("")
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: SecretStr = SecretStr("")

    # Gmail / Google
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_refresh_token: SecretStr = SecretStr("")
    gmail_watch_label: str = "INBOX"
    sender_display_name: str = "Sunny Kushwaha"

    # GitHub
    github_token: SecretStr = SecretStr("")
    github_default_owner: str = ""

    # Azure DevOps
    azdo_org_url: str = ""
    azdo_pat: SecretStr = SecretStr("")

    # Demo sample app (GitHub phone-browser live preview after WhatsApp APPROVE)
    sample_app_name: str = "personal-task-api-sunny"
    sample_app_url: str = "https://personal-task-api-sunny.azurewebsites.net"
    sample_app_resource_group: str = "ai-agent-rg"
    sample_app_github_repo: str = "personal-task-api"
    sample_app_publish_user: str = ""
    sample_app_publish_password: SecretStr = SecretStr("")

    # Azure DevOps demo target — same agent App Service + portal page E2E
    azdo_demo_project: str = "Project-NIT"
    azdo_demo_repo: str = "web.Whatsapp-AI-Agent"
    agent_app_name: str = "whatsapp-ai-agent-sunny"
    agent_app_url: str = "https://whatsapp-ai-agent-sunny.azurewebsites.net"
    agent_app_publish_user: str = ""
    agent_app_publish_password: SecretStr = SecretStr("")

    # App — on Azure App Service use /home/site/data so CI watches survive zip deploy
    database_url: str = "sqlite+aiosqlite:///./agent.db"
    workspace_dir: str = "./workspaces"
    log_level: str = "INFO"
    allowed_phone_numbers: list[str] = []

    @field_validator("allowed_teams_user_ids", mode="before")
    @classmethod
    def _parse_teams_allowlist(cls, value):
        return _parse_csv_or_json_list(value)

    @field_validator("allowed_phone_numbers", mode="before")
    @classmethod
    def _parse_phone_allowlist(cls, value):
        return _parse_csv_or_json_list(value)

    @field_validator("database_url", mode="before")
    @classmethod
    def _default_persistent_db(cls, value: str | None) -> str:
        explicit = (value or "").strip()
        data = _azure_data_dir()

        # Azure App Service — always land on a writable absolute path under /home/site/data.
        if data is not None:
            if explicit and "/home/site/data" in explicit:
                path = ensure_sqlite_file(
                    explicit if "://" in explicit else f"sqlite+aiosqlite:///{explicit}"
                )
                return to_aiosqlite_url(path)
            if not explicit or explicit in (
                "sqlite+aiosqlite:///./agent.db",
                "sqlite:///./agent.db",
                "sqlite+aiosqlite:///agent.db",
            ):
                return to_aiosqlite_url(ensure_sqlite_file(to_aiosqlite_url(data / "agent.db")))
            # Explicit non-default URL on Azure — still ensure parent exists.
            return to_aiosqlite_url(ensure_sqlite_file(explicit))

        return explicit or "sqlite+aiosqlite:///./agent.db"

    @field_validator("workspace_dir", mode="before")
    @classmethod
    def _default_persistent_workspace(cls, value: str | None) -> str:
        explicit = (value or "").strip()
        if explicit and explicit.startswith("/home/site/data"):
            Path(explicit).mkdir(parents=True, exist_ok=True)
            return explicit
        data = _azure_data_dir()
        if data is not None and explicit in ("", "./workspaces", "workspaces"):
            ws = data / "workspaces"
            ws.mkdir(parents=True, exist_ok=True)
            return str(ws)
        return explicit or "./workspaces"


settings = Settings()
