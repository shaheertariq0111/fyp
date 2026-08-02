from typing import Any, Literal

from pydantic import BaseModel, Field

from src.agent.order_intent import OrderIntentClassification
from src.agent.whatsapp_turn_intent import WhatsAppTurnInterpretation


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
