from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from src.agent.restaurant_agent import agent_result_text, build_restaurant_agent, invoke_restaurant_agent
from src.agent.dependencies import get_services
from src.infrastructure.config import get_settings

from agent_runtime.config import get_agentcore_runtime_settings, get_secret_value
from agent_runtime.logging import configure_logging
from agent_runtime.memory import agentcore_actor_id, require_agentcore_memory_id
from agent_runtime.schemas import RuntimeRequest, RuntimeResponse, ToolCallResult


logger = logging.getLogger(__name__)


def load_agentcore_memory_integration() -> tuple[type[Any], type[Any]]:
    from bedrock_agentcore.memory.integrations.strands.config import (
        AgentCoreMemoryConfig,
    )
    from bedrock_agentcore.memory.integrations.strands.session_manager import (
        AgentCoreMemorySessionManager,
    )

    return AgentCoreMemoryConfig, AgentCoreMemorySessionManager


def ensure_session_token_secret(settings: Any) -> None:
    if os.getenv("SESSION_TOKEN_SECRET") or not settings.session_token_secret_arn:
        return
    os.environ["SESSION_TOKEN_SECRET"] = get_secret_value(
        settings.session_token_secret_arn,
        settings.aws_region,
    )
    get_settings.cache_clear()
    get_services.cache_clear()


def invoke(event: dict[str, Any], context: Any | None = None) -> dict[str, Any]:
    request = RuntimeRequest.model_validate(event)
    settings = get_agentcore_runtime_settings()
    configure_logging(settings.log_level)
    ensure_session_token_secret(settings)
    memory_id = require_agentcore_memory_id(settings)
    actor_id = agentcore_actor_id(customer_id=request.customer_id, user_id=request.user_id)
    memory_config_cls, session_manager_cls = load_agentcore_memory_integration()
    memory_config = memory_config_cls(
        memory_id=memory_id,
        actor_id=actor_id,
        session_id=request.agent_session_id,
        batch_size=1,
    )
    logger.info(
        "Invoking restaurant agent",
        extra={
            "event": "agentcore_invocation_started",
            "actor_id": actor_id,
            "agent_session_id": request.agent_session_id,
            "channel": request.channel,
            "agentcore_invocation_status": "started",
        },
    )
    started = time.perf_counter()
    try:
        with session_manager_cls(
            agentcore_memory_config=memory_config,
            region_name=settings.aws_region,
        ) as session_manager:
            runtime_agent = build_restaurant_agent(session_manager=session_manager)
            result = invoke_restaurant_agent(
                request.message,
                user_id=request.user_id,
                agent_session_id=request.agent_session_id,
                branch_id=request.branch_id,
                customer_id=request.customer_id,
                customer_name=request.customer_name,
                customer_phone=request.customer_phone,
                channel=request.channel,
                agent=runtime_agent,
            )
    except Exception:
        logger.exception(
            "Restaurant agent invocation failed",
            extra={
                "event": "agentcore_invocation_failed",
                "actor_id": actor_id,
                "agent_session_id": request.agent_session_id,
                "channel": request.channel,
                "agentcore_invocation_status": "failed",
                "error_code": "AGENT_INVOCATION_FAILED",
                "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        raise
    tool_calls = [
        ToolCallResult.model_validate(call)
        for call in (getattr(result, "tool_calls", []) or [])
    ]
    for call in tool_calls:
        logger.info(
            "AgentCore tool call completed",
            extra={
                "event": "agent_tool_completed",
                "actor_id": actor_id,
                "agent_session_id": request.agent_session_id,
                "channel": request.channel,
                "tool_name": call.tool_name,
                "tool_success": call.success,
                "is_write": call.is_write,
                "error_code": call.error_code,
            },
        )
    logger.info(
        "Restaurant agent invocation completed",
        extra={
            "event": "agentcore_invocation_completed",
            "actor_id": actor_id,
            "agent_session_id": request.agent_session_id,
            "channel": request.channel,
            "agentcore_invocation_status": "completed",
            "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
    response = RuntimeResponse(
        text=agent_result_text(result),
        tool_calls=tool_calls,
        memory={
            "memory_id": memory_id,
            "actor_id": actor_id,
            "session_id": request.agent_session_id,
        },
    )
    return response.model_dump(exclude_none=True)


if __name__ == "__main__":
    import sys

    payload = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(invoke(payload), separators=(",", ":")))
