from typing import Any

from pydantic import BaseModel, Field


class CartItem(BaseModel):
    cart_item_id: str
    label: str
    item_id: str
    name: str
    quantity: int = Field(gt=0)
    selected_options: dict[str, Any] = Field(default_factory=dict)
    missing_required_fields: list[str] = Field(default_factory=list)
    current_step: str | None = None
    current_price: int = 0
    is_upsell: bool = False


class Cart(BaseModel):
    cart_id: str
    user_id: str
    agent_session_id: str
    restaurant_id: str
    branch_id: str
    status: str
    customization_mode: str | None = None
    requested_quantity: int = 1
    active_cart_item_id: str | None = None
    items: list[CartItem] = Field(default_factory=list)
    subtotal: int = 0
    currency: str
    version: int = 1
    created_at: str
    updated_at: str
