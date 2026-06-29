"""LLM builder for the agent.

Supports two auth modes, picked from environment:
  * Azure OpenAI via `az login` (Azure AD) — no API key (preferred locally).
  * Anthropic via ANTHROPIC_API_KEY.

Returns a browser-use ``BaseChatModel`` the runner can hand to the Agent.
"""

from __future__ import annotations

import os


def build_llm():
    """Construct a chat model from env. Azure AAD preferred; Anthropic fallback."""
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").split(",")[0].strip()
    if endpoint:
        from azure.identity import AzureCliCredential, get_bearer_token_provider
        from browser_use.llm.azure.chat import ChatAzureOpenAI

        token_provider = get_bearer_token_provider(
            AzureCliCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
        return ChatAzureOpenAI(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4"),
            azure_endpoint=endpoint,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
            azure_ad_token_provider=token_provider,
        )

    if os.getenv("ANTHROPIC_API_KEY"):
        from browser_use.llm.anthropic.chat import ChatAnthropic

        return ChatAnthropic(model=os.getenv("MODEL", "claude-sonnet-4-20250514"))

    raise RuntimeError(
        "No LLM configured. Set AZURE_OPENAI_ENDPOINT (with `az login`) or ANTHROPIC_API_KEY."
    )
