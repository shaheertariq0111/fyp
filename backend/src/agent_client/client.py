from __future__ import annotations

from typing import Protocol

from src.agent.order_intent import OrderIntentClassification, OrderIntentRequest
from src.agent.whatsapp_turn_intent import (
    WhatsAppTurnIntentRequest,
    WhatsAppTurnInterpretation,
)
from src.agent_client.schemas import (
    AgentInvocationRequest,
    AgentInvocationResult,
)


class AgentRuntimeClient(Protocol):
    def invoke(self, request: AgentInvocationRequest) -> AgentInvocationResult:
        """Run the agent synchronously for the temporary local deployment path."""

    def classify_order_intent(
        self,
        request: OrderIntentRequest,
    ) -> OrderIntentClassification:
        """Classify one constrained order action without transactional tools."""

    def classify_whatsapp_turn(
        self,
        request: WhatsAppTurnIntentRequest,
    ) -> WhatsAppTurnInterpretation:
        """Classify a WhatsApp turn without tools or transaction authority."""

    async def start_request(self, request: AgentInvocationRequest) -> dict:
        """Start async agent processing when AgentCore request persistence is added."""

    async def get_request_status(self, request_id: str) -> dict:
        """Fetch async request status when Phase 3 adds persisted request state."""
