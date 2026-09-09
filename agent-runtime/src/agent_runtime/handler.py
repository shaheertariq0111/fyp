from __future__ import annotations

from contextlib import nullcontext
import json
import logging
import os
import time
from typing import Any

from src.agent.response_grounding import (
    GroundedAssistantMemoryBuffer,
    ground_agent_response,
    grounding_decision_log_fields,
    retry_message_for_grounding_failure,
    should_retry_grounding,
)
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


def agentcore_memory_session_id(request: RuntimeRequest, settings: Any) -> str:
    if request.channel != "whatsapp":
        return request.agent_session_id
    namespace = str(
        getattr(settings, "whatsapp_agentcore_memory_namespace", "")
        or "whatsapp-agent-v3"
    ).strip()
    ttl_hours = int(getattr(settings, "whatsapp_agentcore_memory_ttl_hours", 6) or 6)
    ttl_seconds = max(ttl_hours, 1) * 60 * 60
    bucket = int(time.time() // ttl_seconds)
    base_session_id = (
        f"{namespace}-{request.agent_session_id}"
        if namespace
        else request.agent_session_id
    )
    return f"{base_session_id}-b{bucket}"


def invoke(event: dict[str, Any], context: Any | None = None) -> dict[str, Any]:
    request = RuntimeRequest.model_validate(event)
    settings = get_agentcore_runtime_settings()
    configure_logging(settings.log_level)
    ensure_session_token_secret(settings)
    memory_enabled = (
        request.channel != "whatsapp" or settings.whatsapp_agentcore_memory_enabled
    )
    actor_id = agentcore_actor_id(customer_id=request.customer_id, user_id=request.user_id)
    memory_id = ""
    memory_session_id = ""
    if memory_enabled:
        memory_id = require_agentcore_memory_id(settings)
        memory_session_id = agentcore_memory_session_id(request, settings)
        memory_config_cls, session_manager_cls = load_agentcore_memory_integration()
        memory_config = memory_config_cls(
            memory_id=memory_id,
            actor_id=actor_id,
            session_id=memory_session_id,
            batch_size=1,
        )
    logger.info(
        "AgentCore memory mode selected",
        extra={
            "event": "agentcore_memory_mode",
            "channel": request.channel,
            "memory_enabled": memory_enabled,
        },
    )
    logger.info(
        "Invoking restaurant agent",
        extra={
            "event": "agentcore_invocation_started",
            "actor_id": actor_id,
            "agent_session_id": request.agent_session_id,
            "memory_session_id": memory_session_id,
            "channel": request.channel,
            "agentcore_invocation_status": "started",
        },
    )
    started = time.perf_counter()
    try:
        with (
            session_manager_cls(
                agentcore_memory_config=memory_config,
                region_name=settings.aws_region,
            )
            if memory_enabled
            else nullcontext()
        ) as session_manager:
            memory_buffer = (
                GroundedAssistantMemoryBuffer(session_manager)
                if request.channel == "whatsapp" and memory_enabled
                else None
            )
            try:
                runtime_agent = build_restaurant_agent(
                    session_manager=memory_buffer or session_manager
                )
                result = _invoke_restaurant(
                    request,
                    message=request.message,
                    agent=runtime_agent,
                )
                tool_calls = [
                    ToolCallResult.model_validate(call)
                    for call in (getattr(result, "tool_calls", []) or [])
                ]
                raw_text = agent_result_text(result)
                grounded_text = raw_text
                grounding_source = None
                grounding_rejection_reason = None
                retry_count = 0
                if request.channel == "whatsapp":
                    grounded = ground_agent_response(
                        text=raw_text,
                        tool_calls=tool_calls,
                        continuation=getattr(result, "continuation", None),
                    )
                    if should_retry_grounding(grounded):
                        retry_count = 1
                        if memory_buffer is not None:
                            memory_buffer.pending_assistant = None
                        retry_agent = build_restaurant_agent(
                            session_manager=None
                        )
                        result = _invoke_restaurant(
                            request,
                            message=retry_message_for_grounding_failure(
                                request.message
                            ),
                            agent=retry_agent,
                        )
                        tool_calls = [
                            ToolCallResult.model_validate(call)
                            for call in (
                                getattr(result, "tool_calls", []) or []
                            )
                        ]
                        raw_text = agent_result_text(result)
                        grounded = ground_agent_response(
                            text=raw_text,
                            tool_calls=tool_calls,
                            continuation=getattr(result, "continuation", None),
                        )
                    logger.info(
                        "WhatsApp response selected",
                        extra={
                            "event": "whatsapp_response_selected",
                            "tool_call_count": len(tool_calls),
                            "tool_names": [call.tool_name for call in tool_calls],
                            "grounding_retry_count": retry_count,
                            **grounding_decision_log_fields(grounded),
                        },
                    )
                    grounded_text = grounded.text
                    grounding_source = grounded.source
                    grounding_rejection_reason = grounded.rejection_reason
                if memory_buffer is not None:
                    memory_buffer.commit(grounded_text, runtime_agent)
            finally:
                if memory_buffer is not None:
                    memory_buffer.pending_assistant = None
    except Exception:
        logger.exception(
            "Restaurant agent invocation failed",
            extra={
                "event": "agentcore_invocation_failed",
                "actor_id": actor_id,
                "agent_session_id": request.agent_session_id,
                "memory_session_id": memory_session_id,
                "channel": request.channel,
                "agentcore_invocation_status": "failed",
                "error_code": "AGENT_INVOCATION_FAILED",
                "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        raise
    for call in tool_calls:
        logger.info(
            "AgentCore tool call completed",
            extra={
                "event": "agent_tool_completed",
                "actor_id": actor_id,
                "agent_session_id": request.agent_session_id,
                "memory_session_id": memory_session_id,
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
            "memory_session_id": memory_session_id,
            "channel": request.channel,
            "agentcore_invocation_status": "completed",
            "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
    response = RuntimeResponse(
        text=grounded_text,
        tool_calls=tool_calls,
        memory={
            "memory_id": memory_id,
            "actor_id": actor_id,
            "session_id": memory_session_id,
        } if memory_enabled else {},
        grounding_source=grounding_source,
        grounding_rejection_reason=grounding_rejection_reason,
    )
    return response.model_dump(exclude_none=True)


def _invoke_restaurant(request: RuntimeRequest, *, message: str, agent: Any):
    return invoke_restaurant_agent(
        message,
        user_id=request.user_id,
        agent_session_id=request.agent_session_id,
        request_id=request.request_id,
        branch_id=request.branch_id,
        customer_id=request.customer_id,
        customer_name=request.customer_name,
        customer_phone=request.customer_phone,
        channel=request.channel,
        agent=agent,
    )


if __name__ == "__main__":
    import sys

    payload = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(invoke(payload), separators=(",", ":")))
