from __future__ import annotations

import logging
import time

from src.agent.restaurant_agent import agent_result_text, invoke_restaurant_agent
from src.agent_client.schemas import AgentInvocationRequest, AgentInvocationResult


logger = logging.getLogger(__name__)


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
        try:
            raw_result = invoke_restaurant_agent(
                request.message,
                user_id=request.user_id,
                agent_session_id=request.agent_session_id,
                branch_id=request.branch_id,
                customer_id=request.customer_id,
                customer_name=request.customer_name,
                customer_phone=request.customer_phone,
                channel=request.channel,
            )
        except Exception:
            logger.exception(
                "Agent runtime invocation failed",
                extra={
                    "event": "agentcore_invocation_failed",
                    "actor_id": request.user_id,
                    "agent_session_id": request.agent_session_id,
                    "channel": request.channel,
                    "agentcore_invocation_status": "failed",
                    "error_code": "AGENT_INVOCATION_FAILED",
                    "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
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
                "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return AgentInvocationResult(
            text=agent_result_text(raw_result),
            raw_result=raw_result,
        )

    async def start_request(self, request: AgentInvocationRequest) -> dict:
        raise NotImplementedError("Durable AgentCore async requests are implemented in a later phase")

    async def get_request_status(self, request_id: str) -> dict:
        raise NotImplementedError("Durable AgentCore request status is implemented in a later phase")
