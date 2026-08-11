"""App settings — Pydantic v2: BaseSettings lives in pydantic-settings (never pydantic)."""

import os
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent.core.sqlite_paths import ensure_sqlite_file, to_aiosqlite_url


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
    # Comma-separated fallback deployments when primary hits rate limits (see Azure AI Foundry)
    azure_openai_fallback_deployments: str = ""
    azure_openai_planning_fallbacks: str = ""
    azure_openai_review_fallbacks: str = ""
    llm_max_retries_per_deployment: int = 2
    llm_retry_base_delay_sec: float = 1.5

    # Multi-gate coding workflow: plan approval -> dev -> PR -> review mode -> deploy approval
    enable_multi_gate_workflow: bool = True
    # After CI/test failure, ask on WhatsApp and let the test_fixer agent repair
    enable_ci_test_fix_agent: bool = True

    # Teams / Copilot Studio channel (additive — WhatsApp unchanged)
    enable_teams_copilot_channel: bool = True
    copilot_api_key: SecretStr = SecretStr("")
    allowed_teams_user_ids: list[str] = []

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
        return value

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
