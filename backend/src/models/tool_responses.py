from typing import Any

from pydantic import BaseModel, Field


class ActionButton(BaseModel):
    label: str
    action: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolResponse(BaseModel):
    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    user_message: str
    next_action: str | None = None
    agent: dict[str, Any] = Field(default_factory=dict)
    buttons: list[ActionButton] = Field(default_factory=list)
    error_code: str | None = None
    retryable: bool | None = None

    @classmethod
    def ok(cls, *, data: dict[str, Any] | None = None, user_message: str,
           next_action: str | None = None, buttons: list[dict] | None = None,
           agent: dict[str, Any] | None = None) -> "ToolResponse":
        return cls(success=True, data=data or {}, user_message=user_message,
                   next_action=next_action, agent=agent or {}, buttons=buttons or [])

    @classmethod
    def error(cls, *, error_code: str, user_message: str,
              retryable: bool = False, agent: dict[str, Any] | None = None) -> "ToolResponse":
        return cls(success=False, error_code=error_code,
                   user_message=user_message, retryable=retryable, agent=agent or {})
