from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Multi-gate coding workflow: plan approval -> dev -> PR -> review mode -> deploy approval
    enable_multi_gate_workflow: bool = True

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

    # App
    database_url: str = "sqlite+aiosqlite:///./agent.db"
    workspace_dir: str = "./workspaces"
    log_level: str = "INFO"
    allowed_phone_numbers: list[str] = []


settings = Settings()
