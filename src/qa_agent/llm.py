import os

from strands.models.openai import OpenAIModel

from .security import require_env


def build_model() -> OpenAIModel:
    api_key = require_env("OPENROUTER_API_KEY")
    return OpenAIModel(
        client_args={
            "api_key": api_key,
            "base_url": "https://openrouter.ai/api/v1",
            "default_headers": {
                "HTTP-Referer": "https://localhost",
                "X-Title": "Strands QA Agent",
            },
        },
        model_id=os.environ.get("MODEL", "deepseek/deepseek-v3.2"),
        params={"temperature": 0.2},
    )
