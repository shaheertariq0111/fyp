from __future__ import annotations

from functools import lru_cache
import json
import re
from typing import Any

from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.session import FileSessionManager

from src.agent.context import AgentRequestContext, request_context
from src.agent.system_prompt import restaurant_prompt_for_channel
from src.agent.tools import tools_for_channel
from src.infrastructure.config import get_bedrock_model_settings, get_settings


def build_bedrock_model() -> BedrockModel:
    settings = get_bedrock_model_settings()
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


def build_session_manager(agent_session_id: str) -> FileSessionManager | None:
    settings = get_settings()
    if not settings.strands_session_storage_dir:
        return None
    return FileSessionManager(
        session_id=agent_session_id,
        storage_dir=settings.strands_session_storage_dir,
    )


def build_restaurant_agent(
    model: BedrockModel | str | None = None,
    session_manager: Any | None = None,
    channel: str = "web",
) -> Agent:
    return Agent(
        model=model or build_bedrock_model(),
        tools=tools_for_channel(channel),
        system_prompt=restaurant_prompt_for_channel(channel),
        name="restaurant-ordering-agent",
        description="Single MVP pizza restaurant ordering assistant.",
        session_manager=session_manager,
        callback_handler=None,
        record_direct_tool_call=True,
    )


@lru_cache
def get_restaurant_agent() -> Agent:
    return build_restaurant_agent(channel="web")


def invoke_restaurant_agent(
    message: str,
    *,
    user_id: str,
    agent_session_id: str,
    request_id: str | None = None,
    branch_id: str | None = None,
    customer_id: str | None = None,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    channel: str = "web",
    agent: Agent | None = None,
    option_contract: dict[str, Any] | None = None,
    **kwargs: Any,
):
    context = AgentRequestContext(
        user_id=user_id,
        agent_session_id=agent_session_id,
        request_id=request_id,
        branch_id=branch_id,
        customer_id=customer_id or user_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        channel=channel,
        current_message=message,
    )
    if agent is not None:
        runtime_agent = agent
    else:
        build_kwargs = {"session_manager": build_session_manager(agent_session_id)}
        if channel != "web":
            build_kwargs["channel"] = channel
        runtime_agent = build_restaurant_agent(**build_kwargs)
    with request_context(context):
        agent_input = message
        if channel == "whatsapp" and option_contract:
            agent_input = json.dumps(
                {
                    "trusted_active_option_contract": option_contract,
                    "customer_message": message,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        result = runtime_agent(agent_input, **kwargs)
        try:
            setattr(result, "tool_calls", list(context.tool_calls))
        except Exception:
            pass
        return result


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
