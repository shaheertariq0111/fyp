from __future__ import annotations

from typing import Protocol

from src.agent_client.schemas import AgentInvocationRequest, AgentInvocationResult


class AgentRuntimeClient(Protocol):
    def invoke(self, request: AgentInvocationRequest) -> AgentInvocationResult:
        """Run the agent synchronously for the temporary local deployment path."""

    async def start_request(self, request: AgentInvocationRequest) -> dict:
        """Start async agent processing when AgentCore request persistence is added."""

    async def get_request_status(self, request_id: str) -> dict:
        """Fetch async request status when Phase 3 adds persisted request state."""
