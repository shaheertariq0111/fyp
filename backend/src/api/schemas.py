from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer


def _public_tool_result(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    public = dict(value)
    public.pop("grounding", None)
    return public


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

    @field_serializer("result")
    def serialize_public_result(self, result: dict[str, Any] | None):
        return _public_tool_result(result)


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


class StrictAdminTicketSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AdminTicketListItem(StrictAdminTicketSchema):
    ticket_id: str
    user_id: str
    customer_id: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    ticket_type: str
    category: str
    priority: str
    status: str
    order_id: str | None = None
    source: str
    created_at: str
    updated_at: str
    version: int = Field(gt=0)


class AdminTicketListResponse(StrictAdminTicketSchema):
    tickets: list[AdminTicketListItem]
    next_cursor: str | None = None


class AdminTicketStatusHistoryEntry(StrictAdminTicketSchema):
    previous_status: str
    new_status: str
    timestamp: str
    actor: str | None = None
    reason: str | None = None


class AdminTicketPriorityHistoryEntry(StrictAdminTicketSchema):
    previous_priority: str
    new_priority: str
    timestamp: str
    actor: str | None = None
    reason: str | None = None


class AdminTicketNote(StrictAdminTicketSchema):
    note_id: str | None = None
    actor: str | None = None
    timestamp: str
    text: str


class AdminTicketLinkedOrder(StrictAdminTicketSchema):
    order_id: str
    status: str
    fulfillment_method: str | None = None
    total: int
    currency: str
    created_at: str
    updated_at: str


class AdminTicketDetail(StrictAdminTicketSchema):
    ticket_id: str
    user_id: str
    customer_id: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    ticket_type: str
    category: str
    description: str | None = None
    priority: str
    status: str
    order_id: str | None = None
    order_status_snapshot: str | None = None
    source: str
    created_at: str
    updated_at: str
    status_history: list[AdminTicketStatusHistoryEntry]
    priority_history: list[AdminTicketPriorityHistoryEntry]
    admin_notes: list[AdminTicketNote]
    version: int = Field(gt=0)
    linked_order: AdminTicketLinkedOrder | None = None


class AdminTicketDetailResponse(StrictAdminTicketSchema):
    ticket: AdminTicketDetail


class AdminTicketStatusUpdateRequest(StrictAdminTicketSchema):
    status: str
    reason: str | None = None
    expected_version: int = Field(gt=0)


class AdminTicketPriorityUpdateRequest(StrictAdminTicketSchema):
    priority: str
    reason: str | None = None
    expected_version: int = Field(gt=0)


class AdminTicketNoteCreateRequest(StrictAdminTicketSchema):
    text: str
    expected_version: int = Field(gt=0)


class AdminTicketReopenRequest(StrictAdminTicketSchema):
    target_status: str
    reason: str
    expected_version: int = Field(gt=0)
