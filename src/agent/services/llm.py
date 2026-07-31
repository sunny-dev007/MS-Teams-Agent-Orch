from langchain_openai import AzureChatOpenAI

from agent.config import settings


def get_llm(temperature: float = 0.1) -> AzureChatOpenAI:
    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.effective_api_key,
        azure_deployment=settings.azure_openai_deployment,
        api_version=settings.azure_openai_api_version,
        temperature=temperature,
    )
