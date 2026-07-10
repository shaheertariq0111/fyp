from typing import Any

from pydantic import BaseModel, Field


class Order(BaseModel):
    order_id: str
    user_id: str
    agent_session_id: str
    restaurant_id: str
    branch_id: str
    status: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    subtotal: int
    delivery_fee: int | None = None
    total: int
    currency: str
    fulfillment_method: str | None = None
    delivery_address: str | None = None
    idempotency_keys: list[str] = Field(default_factory=list)
    version: int = 1
    created_at: str
    updated_at: str
