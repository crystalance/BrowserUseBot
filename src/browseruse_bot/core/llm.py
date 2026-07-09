"""LLM builder for the agent.

Supports two auth modes, picked from environment:
  * Azure OpenAI via `az login` (Azure AD) — no API key (preferred locally).
  * Anthropic via ANTHROPIC_API_KEY.

Returns a browser-use ``BaseChatModel`` the runner can hand to the Agent.
"""

from __future__ import annotations

import os


def build_llm():
    """Construct a chat model from env. OpenAI > Azure AAD > Anthropic.

    Set LLM_PROVIDER=openai|azure|anthropic to force one explicitly.
    """
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()

    if provider == "openai" or (not provider and os.getenv("OPENAI_API_KEY")):
        from browser_use import ChatOpenAI

        return ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            api_key=os.getenv("OPENAI_API_KEY"),
        )

    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").split(",")[0].strip()
    if provider == "azure" or (not provider and endpoint):
        from azure.identity import AzureCliCredential, get_bearer_token_provider
        from browser_use.llm.azure.chat import ChatAzureOpenAI

        token_provider = get_bearer_token_provider(
            AzureCliCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4")
        # gpt-5.x are reasoning models: on Azure they must use the Responses API
        # for reliable *strict* structured output. Via Chat Completions they emit
        # JSON + trailing text -> browser-use's parser fails ("trailing
        # characters"). Requires api_version >= 2025-03-01-preview.
        use_responses_api = deployment.lower().startswith("gpt-5")
        return ChatAzureOpenAI(
            model=deployment,
            azure_endpoint=endpoint,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
            azure_ad_token_provider=token_provider,
            use_responses_api=use_responses_api,
        )

    if os.getenv("ANTHROPIC_API_KEY"):
        from browser_use.llm.anthropic.chat import ChatAnthropic

        return ChatAnthropic(model=os.getenv("MODEL", "claude-sonnet-4-20250514"))

    raise RuntimeError(
        "No LLM configured. Set AZURE_OPENAI_ENDPOINT (with `az login`) or ANTHROPIC_API_KEY."
    )
