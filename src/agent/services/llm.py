from langchain_openai import AzureChatOpenAI

from agent.config import settings


def get_llm(temperature: float = 0.1, *, role: str = "default") -> AzureChatOpenAI:
    """Return Azure OpenAI client. role=planning|review uses dedicated deployments when set."""
    deployment = settings.azure_openai_deployment
    if role == "planning" and settings.azure_openai_planning_deployment:
        deployment = settings.azure_openai_planning_deployment
    elif role == "review" and settings.azure_openai_review_deployment:
        deployment = settings.azure_openai_review_deployment

    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.effective_api_key,
        azure_deployment=deployment,
        api_version=settings.azure_openai_api_version,
        temperature=temperature,
    )
