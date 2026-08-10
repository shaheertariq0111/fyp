from typing import Any, Literal

from pydantic import BaseModel, Field


class ActionButton(BaseModel):
    label: str
    action: str
    metadata: dict[str, Any] = Field(default_factory=dict)


AuthoritativeDomain = Literal[
    "menu",
    "cart",
    "order",
    "customer",
    "restaurant_policy",
    "support",
]
TransactionalEffect = Literal[
    "item_selected",
    "item_added",
    "customization_saved",
    "quantity_changed",
    "cart_progressed",
    "checkout_started",
    "fulfillment_saved",
    "address_saved",
    "order_cancelled",
    "order_submitted",
    "customer_profile_updated",
    "customer_name_updated",
    "cart_cancelled",
    "menu_session_created",
    "support_ticket_created",
    "support_request_cancelled",
    "other_transactional_progression",
]

LEGACY_ITEM_SELECTION_TOOL = "start_cart_item_customization"
LEGACY_ITEM_SELECTION_EFFECT: TransactionalEffect = "item_selected"


def apply_legacy_item_selection_compatibility(
    required_effect: TransactionalEffect | None,
    *,
    expected_write_tool: str | None = None,
    has_legacy_offered_options: bool = False,
) -> tuple[TransactionalEffect | None, str | None]:
    """Bridge legacy menu-selection state; remove after old callers/rows retire."""
    if required_effect is None and (
        expected_write_tool == LEGACY_ITEM_SELECTION_TOOL
        or has_legacy_offered_options
    ):
        required_effect = LEGACY_ITEM_SELECTION_EFFECT
    if required_effect == LEGACY_ITEM_SELECTION_EFFECT:
        expected_write_tool = LEGACY_ITEM_SELECTION_TOOL
    elif expected_write_tool == LEGACY_ITEM_SELECTION_TOOL:
        expected_write_tool = None
    return required_effect, expected_write_tool


class PresentationConstraints(BaseModel):
    max_items: int | None = Field(default=None, ge=1, le=50)
    role: Literal["selection_offer", "informational_reference"] | None = None


class GroundingOption(BaseModel):
    id: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=256)


class ImmutableFact(BaseModel):
    path: str = Field(min_length=1, max_length=256)
    value: Any


class GroundingEvidence(BaseModel):
    authoritative_domains: list[AuthoritativeDomain] = Field(default_factory=list)
    transactional_effects: list[TransactionalEffect] = Field(default_factory=list)
    required_next_effect: TransactionalEffect | None = None
    offered_options: list[GroundingOption] = Field(default_factory=list, max_length=50)
    immutable_facts: list[ImmutableFact] = Field(default_factory=list, max_length=50)
    exact_customer_text: str | None = None
    presentation: PresentationConstraints | None = None
    option_contract_proposal: dict[str, Any] | None = None
    option_contract_consumption: dict[str, Any] | None = None


class ToolResponse(BaseModel):
    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    user_message: str
    next_action: str | None = None
    agent: dict[str, Any] = Field(default_factory=dict)
    buttons: list[ActionButton] = Field(default_factory=list)
    error_code: str | None = None
    retryable: bool | None = None
    grounding: GroundingEvidence | None = None

    @classmethod
    def ok(cls, *, data: dict[str, Any] | None = None, user_message: str,
           next_action: str | None = None, buttons: list[dict] | None = None,
           agent: dict[str, Any] | None = None,
           grounding: GroundingEvidence | dict[str, Any] | None = None) -> "ToolResponse":
        return cls(success=True, data=data or {}, user_message=user_message,
                   next_action=next_action, agent=agent or {}, buttons=buttons or [],
                   grounding=grounding)

    @classmethod
    def error(cls, *, error_code: str, user_message: str,
              retryable: bool = False, agent: dict[str, Any] | None = None) -> "ToolResponse":
        return cls(success=False, error_code=error_code,
                   user_message=user_message, retryable=retryable, agent=agent or {})
