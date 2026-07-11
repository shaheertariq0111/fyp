from __future__ import annotations

import json
import logging
from typing import Any

from src.agent.restaurant_agent import agent_result_text, invoke_restaurant_agent

from agent_runtime.config import get_agentcore_runtime_settings
from agent_runtime.logging import configure_logging
from agent_runtime.memory import AgentCoreMemoryConfig
from agent_runtime.schemas import RuntimeRequest, RuntimeResponse, ToolCallResult


logger = logging.getLogger(__name__)


def invoke(event: dict[str, Any], context: Any | None = None) -> dict[str, Any]:
    request = RuntimeRequest.model_validate(event)
    settings = get_agentcore_runtime_settings()
    configure_logging(settings.log_level)
    memory = AgentCoreMemoryConfig(
        memory_id=settings.agentcore_memory_id,
        actor_id=request.customer_id or request.user_id,
        session_id=request.agent_session_id,
    )
    logger.info(
        "Invoking restaurant agent",
        extra={
            "actor_id": memory.actor_id,
            "agent_session_id": memory.session_id,
            "channel": request.channel,
        },
    )
    result = invoke_restaurant_agent(
        request.message,
        user_id=request.user_id,
        agent_session_id=request.agent_session_id,
        branch_id=request.branch_id,
        customer_id=request.customer_id,
        customer_name=request.customer_name,
        customer_phone=request.customer_phone,
        channel=request.channel,
    )
    response = RuntimeResponse(
        text=agent_result_text(result),
        tool_calls=[
            ToolCallResult.model_validate(call)
            for call in (getattr(result, "tool_calls", []) or [])
        ],
        memory={
            "memory_id": memory.memory_id,
            "actor_id": memory.actor_id,
            "session_id": memory.session_id,
        },
    )
    return response.model_dump(exclude_none=True)


if __name__ == "__main__":
    import sys

    payload = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(invoke(payload), separators=(",", ":")))
