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
    "upsell_offered",
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

class PresentationConstraints(BaseModel):
    max_items: int | None = Field(default=None, ge=1, le=50)


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
    # Opt-in, set only by responses whose user_message states an outcome and
    # carries no customer-facing figures, IDs, or contents. Those may be phrased
    # naturally by the agent so it can add the next step. Any response that
    # carries facts leaves this False and is still substituted verbatim.
    allows_natural_phrasing: bool = False
    # Opt-in for simple backend-required next-step prompts where the required
    # state is authoritative, but the customer-facing wording can vary.
    allows_semantic_rephrasing: bool = False


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
