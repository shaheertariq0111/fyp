from __future__ import annotations

from src.agent.restaurant_agent import agent_result_text, invoke_restaurant_agent
from src.agent_client.schemas import AgentInvocationRequest, AgentInvocationResult


class LocalStrandsAgentRuntimeClient:
    """Local adapter used until the Strands agent moves behind AgentCore Runtime."""

    def invoke(self, request: AgentInvocationRequest) -> AgentInvocationResult:
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
        return AgentInvocationResult(
            text=agent_result_text(raw_result),
            raw_result=raw_result,
        )

    async def start_request(self, request: AgentInvocationRequest) -> dict:
        raise NotImplementedError("Durable AgentCore async requests are implemented in a later phase")

    async def get_request_status(self, request_id: str) -> dict:
        raise NotImplementedError("Durable AgentCore request status is implemented in a later phase")
