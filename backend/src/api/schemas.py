from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None
    user_id: str = "anonymous"
    customer_id: str | None = None
    channel: str = "web"
    force_new_session: bool = False
    branch_id: str | None = None


class ChatResponse(BaseModel):
    text: str
    session_id: str
    user_id: str
    customer_id: str | None = None
    customer: dict[str, Any] | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list["ToolCallResult"] = Field(default_factory=list)
    write_succeeded: bool = False
    state: dict[str, Any] = Field(default_factory=dict)
    buttons: list[dict[str, Any]] = Field(default_factory=list)


class ChatSubmitResponse(BaseModel):
    request_id: str
    status: str
    session_id: str
    user_id: str
    customer_id: str | None = None
    customer: dict[str, Any] | None = None


class ChatRequestStatusResponse(BaseModel):
    request_id: str
    status: str
    session_id: str | None = None
    user_id: str | None = None
    customer_id: str | None = None
    customer: dict[str, Any] | None = None
    response: str | None = None
    text: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list["ToolCallResult"] = Field(default_factory=list)
    write_succeeded: bool = False
    state: dict[str, Any] = Field(default_factory=dict)
    buttons: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = None
    message: str | None = None


class ToolCallResult(BaseModel):
    tool_name: str
    success: bool
    is_write: bool
    result: dict[str, Any] | None = None
    error_code: str | None = None


class ActionRequest(BaseModel):
    action: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    user_id: str = "anonymous"
    customer_id: str | None = None
    channel: str = "web"
    force_new_session: bool = False
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
    customer_id: str | None = None
    channel: str = "web"


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class AdminStatusUpdateRequest(BaseModel):
    action: str = Field(min_length=1)
    reason: str | None = None


class AdminMenuItemRequest(BaseModel):
    product_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    currency: str = Field(min_length=1)
    description: str = ""
    available: bool = True
    price: int | float | None = None
    starting_price: int | float | None = None
    base_prices: dict[str, int | float] = Field(default_factory=dict)
    requires_customization: bool = False
    customization_group_ids: list[str] = Field(default_factory=list)
    upsell_group_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    image_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdminAvailabilityRequest(BaseModel):
    available: bool


class AdminCategoryRequest(BaseModel):
    category_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    sort_order: int = 999


class AdminOptionGroupRequest(BaseModel):
    option_group_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    question: str = Field(min_length=1)
    options: list[dict[str, Any]] = Field(default_factory=list)
    required: bool = False
    min_select: int | None = None
    max_select: int | None = None


class AdminUpsellGroupRequest(BaseModel):
    upsell_group_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    items: list[str] = Field(default_factory=list)
    trigger_categories: list[str] = Field(default_factory=list)
    max_suggestions: int = Field(default=3, gt=0)
