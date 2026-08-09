from typing import Any, Literal

from pydantic import BaseModel, Field

from src.agent.order_intent import OrderIntentClassification
from src.agent.response_grounding import AssistantClaimAssessment
from src.agent.whatsapp_turn_intent import WhatsAppTurnInterpretation
from src.models.tool_responses import TransactionalEffect


class RuntimeRequest(BaseModel):
    task: Literal[
        "conversation",
        "classify_order_intent",
        "classify_whatsapp_turn",
    ] = "conversation"
    message: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    agent_session_id: str = Field(min_length=1)
    request_id: str | None = None
    branch_id: str | None = None
    customer_id: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    channel: str = "web"
    state: str | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    available_options: list[dict[str, str]] = Field(default_factory=list)
    expected_write_tool: str | None = None
    required_effect: TransactionalEffect | None = None


class ToolCallResult(BaseModel):
    tool_name: str
    success: bool
    is_write: bool
    result: dict[str, Any] | None = None
    error_code: str | None = None


class RuntimeResponse(BaseModel):
    text: str
    tool_calls: list[ToolCallResult] = Field(default_factory=list)
    memory: dict[str, str] = Field(default_factory=dict)
    intent: OrderIntentClassification | None = None
    turn_intent: WhatsAppTurnInterpretation | None = None
    claim_assessment: AssistantClaimAssessment | None = None
    no_write_authorized: bool | None = None
    informational_turn: bool | None = None
    expected_write_tool: str | None = None
    required_effect: TransactionalEffect | None = None
    available_options: list[dict[str, str]] | None = None
    grounding_source: str | None = None
