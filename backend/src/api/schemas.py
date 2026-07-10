from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    user_id: str = "anonymous"
    branch_id: str | None = None


class ChatResponse(BaseModel):
    text: str
    session_id: str
    user_id: str
    data: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseModel):
    action: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(min_length=1)
    user_id: str = "anonymous"
    branch_id: str | None = None


class MenuOrderItem(BaseModel):
    item_id: str = Field(min_length=1)
    quantity: int = Field(default=1, gt=0)
    selected_options: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None
    is_upsell: bool = False


class MenuOrderRequest(BaseModel):
    items: list[MenuOrderItem] = Field(min_length=1)
    session_token: str | None = None
    user_id: str | None = None
    session_id: str | None = None

