from __future__ import annotations

import logging
import time

from src.agent.response_grounding import (
    GroundedAssistantMemoryBuffer,
    ground_agent_response,
    grounding_decision_log_fields,
)
from src.agent.restaurant_agent import (
    agent_result_text,
    build_restaurant_agent,
    build_session_manager,
    invoke_restaurant_agent,
)
from src.agent_client.schemas import AgentInvocationRequest, AgentInvocationResult


logger = logging.getLogger(__name__)


def _safe_tool_names(tool_calls) -> list[str]:
    names: list[str] = []
    for call in tool_calls:
        name = (
            call.get("tool_name")
            if isinstance(call, dict)
            else getattr(call, "tool_name", None)
        )
        if isinstance(name, str) and name:
            names.append(name)
    return names


class LocalStrandsAgentRuntimeClient:
    """Local adapter used until the Strands agent moves behind AgentCore Runtime."""

    def invoke(self, request: AgentInvocationRequest) -> AgentInvocationResult:
        started = time.perf_counter()
        logger.info(
            "Agent runtime invocation started",
            extra={
                "event": "agentcore_invocation_started",
                "actor_id": request.user_id,
                "agent_session_id": request.agent_session_id,
                "channel": request.channel,
                "agentcore_invocation_status": "started",
            },
        )
        memory_buffer = None
        try:
            session_manager = None
            runtime_agent = None
            if request.channel == "whatsapp":
                session_manager = build_session_manager(request.agent_session_id)
                if session_manager is not None:
                    memory_buffer = GroundedAssistantMemoryBuffer(session_manager)
                runtime_agent = build_restaurant_agent(
                    session_manager=memory_buffer or session_manager
                )
            invocation_kwargs = {}
            if runtime_agent is not None:
                invocation_kwargs["agent"] = runtime_agent
            raw_result = invoke_restaurant_agent(
                request.message,
                user_id=request.user_id,
                agent_session_id=request.agent_session_id,
                request_id=request.request_id,
                branch_id=request.branch_id,
                customer_id=request.customer_id,
                customer_name=request.customer_name,
                customer_phone=request.customer_phone,
                channel=request.channel,
                **invocation_kwargs,
            )
            response_text = agent_result_text(raw_result)
            if request.channel == "whatsapp":
                tool_calls = list(getattr(raw_result, "tool_calls", []) or [])
                grounded = ground_agent_response(
                    text=response_text,
                    tool_calls=tool_calls,
                    continuation=getattr(raw_result, "continuation", None),
                )
                logger.info(
                    "Local WhatsApp response selected",
                    extra={
                        "event": "whatsapp_response_selected",
                        "tool_call_count": len(tool_calls),
                        "tool_names": _safe_tool_names(tool_calls),
                        **grounding_decision_log_fields(grounded),
                    },
                )
                response_text = grounded.text
                if memory_buffer is not None:
                    memory_buffer.commit(response_text, runtime_agent)
        except Exception:
            if memory_buffer is not None:
                memory_buffer.pending_assistant = None
            logger.exception(
                "Agent runtime invocation failed",
                extra={
                    "event": "agentcore_invocation_failed",
                    "actor_id": request.user_id,
                    "agent_session_id": request.agent_session_id,
                    "channel": request.channel,
                    "agentcore_invocation_status": "failed",
                    "error_code": "AGENT_INVOCATION_FAILED",
                    "response_time_ms": round(
                        (time.perf_counter() - started) * 1000,
                        2,
                    ),
                },
            )
            raise
        logger.info(
            "Agent runtime invocation finished",
            extra={
                "event": "agentcore_invocation_completed",
                "actor_id": request.user_id,
                "agent_session_id": request.agent_session_id,
                "channel": request.channel,
                "agentcore_invocation_status": "completed",
                "response_time_ms": round(
                    (time.perf_counter() - started) * 1000,
                    2,
                ),
            },
        )
        return AgentInvocationResult(text=response_text, raw_result=raw_result)

    async def start_request(self, request: AgentInvocationRequest) -> dict:
        raise NotImplementedError(
            "Durable AgentCore async requests are implemented in a later phase"
        )

    async def get_request_status(self, request_id: str) -> dict:
        raise NotImplementedError(
            "Durable AgentCore request status is implemented in a later phase"
        )
