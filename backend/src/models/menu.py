from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


Number = int | float | Decimal


class MenuItemMetadata(BaseModel):
    recommendation_score: Number = 0
    is_popular: bool = False
    best_for: list[str] = Field(default_factory=list)
    serves: str | None = None
    display_reason: str | None = None
    spice_level: Any | None = None
    dietary_labels: list[str] = Field(default_factory=list)
    marketing_badge: str | None = None
    sort_order: Number | None = None


class MenuItem(BaseModel):
    product_id: str
    name: str
    description: str = ""
    category: str
    currency: str
    available: bool
    price: Number | None = None
    starting_price: Number | None = None
    base_prices: dict[str, Number] = Field(default_factory=dict)
    requires_customization: bool = False
    customization_group_ids: list[str] = Field(default_factory=list)
    upsell_group_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    image_url: str | None = None
    metadata: MenuItemMetadata = Field(default_factory=MenuItemMetadata)


class OptionGroup(BaseModel):
    option_group_id: str
    name: str
    type: str
    required: bool = False
    question: str
    options: list[dict[str, Any]] = Field(default_factory=list)
    min_select: int | None = None
    max_select: int | None = None
