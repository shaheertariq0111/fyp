from typing import Any

from pydantic import BaseModel, Field


class RuntimeRequest(BaseModel):
    message: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    agent_session_id: str = Field(min_length=1)
    branch_id: str | None = None
    customer_id: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    channel: str = "web"


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
