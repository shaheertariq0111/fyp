from __future__ import annotations

from functools import lru_cache
import re
from typing import Any

from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.session import FileSessionManager

from src.agent.context import AgentRequestContext, request_context
from src.agent.system_prompt import RESTAURANT_AGENT_SYSTEM_PROMPT
from src.agent.tools import MVP_TOOLS
from src.infrastructure.config import get_settings


def build_bedrock_model() -> BedrockModel:
    settings = get_settings()
    model_config: dict[str, Any] = {
        "model_id": settings.bedrock_model_id,
        "temperature": 0.2,
        "max_tokens": 1200,
    }
    if settings.bedrock_guardrail_id:
        model_config["guardrail_id"] = settings.bedrock_guardrail_id
    if settings.bedrock_guardrail_version:
        model_config["guardrail_version"] = settings.bedrock_guardrail_version
    return BedrockModel(region_name=settings.aws_region, **model_config)


def build_session_manager(agent_session_id: str) -> FileSessionManager:
    settings = get_settings()
    return FileSessionManager(
        session_id=agent_session_id,
        storage_dir=settings.strands_session_storage_dir,
    )


def build_restaurant_agent(
    model: BedrockModel | str | None = None,
    session_manager: FileSessionManager | None = None,
) -> Agent:
    return Agent(
        model=model or build_bedrock_model(),
        tools=MVP_TOOLS,
        system_prompt=RESTAURANT_AGENT_SYSTEM_PROMPT,
        name="restaurant-ordering-agent",
        description="Single MVP pizza restaurant ordering assistant.",
        session_manager=session_manager,
        callback_handler=None,
        record_direct_tool_call=True,
    )


@lru_cache
def get_restaurant_agent() -> Agent:
    return build_restaurant_agent()


def invoke_restaurant_agent(
    message: str,
    *,
    user_id: str,
    agent_session_id: str,
    branch_id: str | None = None,
    agent: Agent | None = None,
    **kwargs: Any,
):
    context = AgentRequestContext(
        user_id=user_id,
        agent_session_id=agent_session_id,
        branch_id=branch_id,
    )
    runtime_agent = agent or build_restaurant_agent(
        session_manager=build_session_manager(agent_session_id)
    )
    with request_context(context):
        return runtime_agent(message, **kwargs)


def agent_result_text(result: Any) -> str:
    message = getattr(result, "message", None)
    if isinstance(message, dict):
        parts = [
            block.get("text", "").strip()
            for block in message.get("content", [])
            if isinstance(block, dict) and "text" in block
        ]
        return sanitize_agent_text("\n".join(parts))
    return sanitize_agent_text(str(result))


def sanitize_agent_text(text: str) -> str:
    cleaned = re.sub(r"<thinking>.*?</thinking>\s*", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
