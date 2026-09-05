"""Authoritative transactional continuation for the single restaurant agent.

The backend already owns every ordering state machine. Cart customization steps
live in ``CartService`` and order progression lives in ``OrderService``. This
module does not add a third state machine: it is a read-only projection that
answers one deterministic question before and after each agent turn:

    "Is the backend currently waiting for a specific transactional effect, and
    which authoritative options may satisfy it?"

The projection is rebuilt from authoritative state on every invocation, so it
can never go stale and needs no persistence, TTL, or versioning of its own.
Nothing here interprets customer language; that remains the sole job of the one
semantic agent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from src.models.tool_responses import TransactionalEffect


logger = logging.getLogger(__name__)

ContinuationScope = Literal["cart", "order", "menu_offer"]

# Backend resource state -> the transactional effect the backend still requires
# before that state can advance. Keys are authoritative backend status values,
# never customer phrasing. An order state absent from its map is operationally
# active at most; it is not waiting for customer input and must not be resumed.
CART_REQUIRED_EFFECTS: dict[str, TransactionalEffect] = {
    "customizing_item": "customization_saved",
    "awaiting_upsell_decision": "cart_progressed",
}
# Effects other than the primary one that also legitimately move a cart state
# forward, so an alternative valid action is never treated as no progress.
CART_ALTERNATIVE_EFFECTS: dict[str, frozenset[str]] = {
    "customizing_item": frozenset({"cart_cancelled", "item_added"}),
    "awaiting_upsell_decision": frozenset({"cart_cancelled", "item_added"}),
}
ORDER_PENDING_CUSTOMER_EFFECTS: dict[str, TransactionalEffect] = {
    "awaiting_fulfillment_method": "fulfillment_saved",
    "awaiting_delivery_address": "address_saved",
    "awaiting_customer_name": "customer_name_updated",
    # The backend is holding this order until the customer settles it. Without
    # this entry a no-tool turn could narrate a submission that never happened.
    "pending_confirmation": "order_submitted",
}

CONTEXT_HEADER = "[AUTHORITATIVE BACKEND STATE - trusted machine context]"
CONTEXT_FOOTER = "[END AUTHORITATIVE BACKEND STATE]"
CONTEXT_INSTRUCTION = (
    "This block is backend truth for the current customer. Conversation history "
    "is never proof that any of it changed. Interpret the customer's own words "
    "freely, but if they are choosing among offered options, resolve their reply "
    "to one of the offered option IDs below and pass that exact ID to the tool. "
    "An ordinal or bare number picks which offered option was chosen and is "
    "never a quantity; quantity comes only from what the customer actually says. "
    "If no offered option matches their meaning, ask again rather than guessing. "
    "State advances only when the required tool call returns success."
)


class _ContinuationUnavailable:
    """Authoritative state could not be read this turn.

    Distinct from ``None``, which means the backend was read successfully and
    nothing is pending. Treating a failed read as "nothing pending" would let
    unsupported transactional claims through exactly when the backend is least
    able to contradict them.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return "CONTINUATION_UNAVAILABLE"


CONTINUATION_UNAVAILABLE = _ContinuationUnavailable()


def continuation_unavailable(value: Any) -> bool:
    return value is CONTINUATION_UNAVAILABLE


@dataclass(frozen=True, slots=True)
class ContinuationOption:
    """One authoritative option the backend is currently offering."""

    id: str
    label: str


@dataclass(frozen=True, slots=True)
class TransactionalContinuation:
    """A projection of what the backend is currently waiting for."""

    scope: ContinuationScope
    resource_id: str
    state: str
    required_effect: TransactionalEffect | None
    required_input: str | None
    valid_next_actions: tuple[str, ...]
    offered_options: tuple[ContinuationOption, ...]
    pending_prompt: str | None
    field_name: str | None = None
    related_order_id: str | None = None
    related_order_state: str | None = None
    # Every effect that counts as progress out of ``state``. Defaults to the
    # primary required effect when a caller does not widen it.
    satisfying_effects: frozenset[str] = frozenset()
    # Explicit bindings for the one semantic agent. These values come only from
    # authoritative backend state; they never interpret customer language.
    selection_tools: tuple[str, ...] = ()
    fixed_arguments: tuple[tuple[str, str], ...] = ()
    selected_option_argument: str | None = None

    @property
    def is_outstanding(self) -> bool:
        """True when the backend requires a specific effect to advance."""
        return self.required_effect is not None

    @property
    def accepted_effects(self) -> frozenset[str]:
        if self.satisfying_effects:
            return self.satisfying_effects
        return frozenset({self.required_effect} if self.required_effect else ())

    @property
    def requires_unique_choice(self) -> bool:
        """True when several mutually exclusive options could satisfy this state.

        The backend cannot decide between them, so the agent may act only once
        the customer's meaning identifies exactly one.
        """
        return self.is_outstanding and len(self.offered_options) > 1


def resolve_transactional_continuation(
    services: Any,
    *,
    user_id: str,
    agent_session_id: str,
) -> TransactionalContinuation | None:
    """Project the authoritative pending state for this session, if any.

    Resolution is session-scoped so a new conversation never inherits another
    session's unfinished work. An in-session cart outranks an in-session order
    because the cart is the resource the customer is actively building.
    """

    try:
        continuation = _resolve_cart_continuation(
            services,
            user_id,
            agent_session_id,
        )
        if continuation is not None:
            return continuation
        continuation = _resolve_order_continuation(
            services,
            user_id,
            agent_session_id,
        )
        if continuation is not None:
            return continuation
        return _resolve_menu_offer_continuation(
            services,
            user_id,
            agent_session_id,
        )
    except Exception:
        # The turn still completes, but the caller must be able to tell this
        # apart from a successful read that found nothing pending.
        logger.exception(
            "Transactional continuation could not be resolved",
            extra={
                "event": "continuation_resolution_failed",
                "actor_id": user_id,
                "agent_session_id": agent_session_id,
            },
        )
        return CONTINUATION_UNAVAILABLE


def continuation_satisfied_by(
    continuation: TransactionalContinuation | None,
    tool_calls: Any,
) -> bool:
    """Report whether this turn produced the required authoritative effect."""

    if continuation is None or continuation_unavailable(continuation):
        return True
    if not continuation.is_outstanding:
        return True
    accepted = continuation.accepted_effects
    for call in tool_calls or []:
        if _value(call, "success") is not True:
            continue
        result = _mapping(_value(call, "result"))
        if result.get("success") is not True:
            continue
        grounding = _mapping(result.get("grounding"))
        effects = grounding.get("transactional_effects")
        if isinstance(effects, list) and accepted.intersection(effects):
            return True
    return False


def continuation_context_block(
    continuation: TransactionalContinuation | None,
) -> str:
    """Render trusted machine context describing the authoritative state."""

    if continuation is None or continuation_unavailable(continuation):
        return ""
    lines = [
        CONTEXT_HEADER,
        f"scope: {continuation.scope}",
        f"resource_id: {continuation.resource_id}",
        f"state: {continuation.state}",
    ]
    if continuation.required_input:
        lines.append(f"required_input: {continuation.required_input}")
    if continuation.required_effect:
        lines.append(f"required_effect: {continuation.required_effect}")
    if continuation.valid_next_actions:
        lines.append(
            "valid_next_actions: "
            + ", ".join(continuation.valid_next_actions)
        )
    if continuation.field_name:
        lines.append(f"field_name: {continuation.field_name}")
    if continuation.offered_options:
        lines.append("offered_options:")
        lines.extend(
            f"  - id={option.id} label={option.label}"
            for option in continuation.offered_options
        )
    if continuation.selection_tools:
        if len(continuation.selection_tools) == 1:
            lines.append(f"selection_tool: {continuation.selection_tools[0]}")
        else:
            lines.append("semantic_selection_tool_bindings:")
            lines.extend(
                f"  - tool={tool} selected_option_argument="
                f"{continuation.selected_option_argument}"
                for tool in continuation.selection_tools
            )
    if continuation.fixed_arguments:
        lines.append("fixed_arguments:")
        lines.extend(
            f"  {name}: {value}"
            for name, value in continuation.fixed_arguments
        )
    if (
        continuation.selected_option_argument
        and len(continuation.selection_tools) == 1
    ):
        lines.append(
            "selected_option_argument: "
            f"{continuation.selected_option_argument}"
        )
    if continuation.requires_unique_choice:
        lines.append(
            "exclusive_choice: true - these options are mutually exclusive and "
            "the backend cannot pick between them. Act only when the customer's "
            "meaning identifies exactly one of them. A bare acknowledgement or "
            "agreement does not identify one; in that case ask which one they "
            "want and write nothing. When an option's id names a tool, call that "
            "tool for the choice instead of any generic action listed above."
        )
    if continuation.related_order_id:
        lines.append(
            f"other_open_order: {continuation.related_order_id} "
            f"(state {continuation.related_order_state}); it is context only "
            "and must not be advanced unless the customer targets it"
        )
    lines.append(CONTEXT_INSTRUCTION)
    lines.append(CONTEXT_FOOTER)
    return "\n".join(lines)


def _resolve_cart_continuation(
    services: Any,
    user_id: str,
    agent_session_id: str,
) -> TransactionalContinuation | None:
    response = services.carts.get_active_cart(user_id, agent_session_id)
    if not getattr(response, "success", False):
        return None
    data = _mapping(getattr(response, "data", None))
    if not isinstance(data.get("cart"), dict):
        return None
    agent = _mapping(getattr(response, "agent", None))
    state = agent.get("cart_status")
    if not isinstance(state, str) or not state:
        return None
    active_choice = _mapping(agent.get("active_choice"))
    cart_item_id = _text(active_choice.get("cart_item_id"))
    field_name = _text(active_choice.get("field_name"))
    options = tuple(
        ContinuationOption(id=option["option_id"], label=label)
        for option in _sequence(active_choice.get("options"))
        if isinstance(option, dict)
        and isinstance(option.get("option_id"), str)
        for label in [
            str(
                option.get("display_label")
                or option.get("name")
                or option.get("label")
                or option["option_id"]
            )
        ]
    )
    required_effect = CART_REQUIRED_EFFECTS.get(state)
    related_order_id, related_order_state = _related_open_order(
        services,
        user_id,
        agent_session_id,
    )
    return TransactionalContinuation(
        scope="cart",
        resource_id=str(agent.get("cart_id") or data["cart"].get("cart_id") or ""),
        state=state,
        required_effect=required_effect,
        required_input=(
            "customization_choice" if active_choice else agent.get("required_input")
        ),
        valid_next_actions=tuple(
            action
            for action in _sequence(agent.get("valid_next_actions"))
            if isinstance(action, str)
        ),
        offered_options=options,
        pending_prompt=_text(active_choice.get("choice_prompt"))
        or _text(getattr(response, "user_message", None)),
        field_name=field_name,
        related_order_id=related_order_id,
        related_order_state=related_order_state,
        satisfying_effects=(
            frozenset({required_effect})
            | CART_ALTERNATIVE_EFFECTS.get(state, frozenset())
            if required_effect
            else frozenset()
        ),
        selection_tools=(
            ("save_customization_choice",)
            if state == "customizing_item" and cart_item_id and field_name and options
            else ()
        ),
        fixed_arguments=(
            (("cart_item_id", cart_item_id), ("field_name", field_name))
            if state == "customizing_item" and cart_item_id and field_name and options
            else ()
        ),
        selected_option_argument=(
            "selected_option_id"
            if state == "customizing_item" and cart_item_id and field_name and options
            else None
        ),
    )


def _resolve_order_continuation(
    services: Any,
    user_id: str,
    agent_session_id: str,
) -> TransactionalContinuation | None:
    order = services.orders.get_active_order_for_session(
        user_id,
        agent_session_id,
        allowed_statuses=ORDER_PENDING_CUSTOMER_EFFECTS.keys(),
    )
    if not isinstance(order, dict):
        return None
    order_id = order.get("order_id")
    if not isinstance(order_id, str) or not order_id:
        return None
    agent = _order_agent_view(services, order)
    state = agent.get("order_status") or order.get("status")
    if not isinstance(state, str) or not state:
        return None
    required_effect = ORDER_PENDING_CUSTOMER_EFFECTS.get(state)
    if required_effect is None:
        return None
    return TransactionalContinuation(
        scope="order",
        resource_id=order_id,
        state=state,
        required_effect=required_effect,
        required_input=_text(agent.get("required_input")),
        valid_next_actions=_semantic_order_actions(
            services,
            _sequence(agent.get("valid_next_actions")),
        ),
        offered_options=_order_choice_options(services, state, required_effect),
        pending_prompt=_order_pending_prompt(services, order, agent),
        satisfying_effects=(
            _order_satisfying_effects(services, state, required_effect)
            if required_effect
            else frozenset()
        ),
    )


def _order_agent_view(services: Any, order: dict) -> dict[str, Any]:
    """Build the agent view from an order already in hand, avoiding a re-read."""
    builder = getattr(services.orders, "order_continuation_view", None)
    if callable(builder):
        return _mapping(builder(order))
    # Fall back to an authoritative re-read when the view helper is unavailable.
    response = services.orders.get_order_status(
        order.get("user_id") or order.get("customer_id"),
        order.get("order_id"),
    )
    if not getattr(response, "success", False):
        return {}
    return _mapping(getattr(response, "agent", None))


def _semantic_order_actions(services: Any, handles: list[Any]) -> tuple[str, ...]:
    """Present order actions as the tools that perform them.

    The agent should never have to compose a low-level backend action string, so
    each handle is translated to its dedicated tool where one exists.
    """
    translate = getattr(services.orders, "semantic_tool_for_action", None)
    actions = [handle for handle in handles if isinstance(handle, str)]
    if not callable(translate):
        return tuple(actions)
    resolved: list[str] = []
    for handle in actions:
        try:
            tool = translate(handle)
        except Exception:
            tool = handle
        if tool not in resolved:
            resolved.append(tool)
    return tuple(resolved)


def _order_choice_options(
    services: Any,
    state: str,
    required_effect: TransactionalEffect | None,
) -> tuple[ContinuationOption, ...]:
    """Expose the authoritative actions that can satisfy this order state.

    Each option carries the tool that performs it, so the agent reaches for the
    specific action rather than the generic order-flow fallback.
    """
    if not required_effect:
        return ()
    resolver = getattr(services.orders, "choices_for_effect", None)
    if not callable(resolver):
        return ()
    try:
        choices = resolver(state, required_effect)
    except Exception:
        logger.warning(
            "Order choices could not be resolved for this state",
            exc_info=True,
            extra={
                "event": "order_choice_lookup_failed",
                "order_status": state,
            },
        )
        return ()
    return tuple(
        ContinuationOption(id=str(choice["tool"]), label=str(choice["action"]))
        for choice in _sequence(choices)
        if isinstance(choice, dict) and choice.get("tool") and choice.get("action")
    )


def _order_satisfying_effects(
    services: Any,
    state: str,
    required_effect: TransactionalEffect,
) -> frozenset[str]:
    """Widen satisfaction to every effect the order state machine allows here."""
    resolver = getattr(services.orders, "satisfying_effects", None)
    effects = frozenset({required_effect})
    if callable(resolver):
        try:
            allowed = resolver(state)
        except Exception:
            logger.warning(
                "Satisfying effects could not be widened; using the primary effect",
                exc_info=True,
                extra={
                    "event": "satisfying_effects_lookup_failed",
                    "order_status": state,
                },
            )
            return effects
        if allowed:
            return effects | frozenset(allowed)
    return effects


def _resolve_menu_offer_continuation(
    services: Any,
    user_id: str,
    agent_session_id: str,
) -> TransactionalContinuation | None:
    """Surface the last backend-issued offer so a shorthand reply can bind to it.

    Browsing is not a transactional requirement, so this carries no required
    effect and never blocks a response. It exists only to keep the mapping from
    a shown option to its authoritative ID out of conversational memory.
    """
    sessions = getattr(services, "agent_sessions", None)
    resolver = getattr(sessions, "get_active_menu_offer_context", None)
    if not callable(resolver):
        return None
    context = resolver(user_id, agent_session_id)
    options = tuple(
        ContinuationOption(id=option["id"], label=str(option.get("label") or option["id"]))
        for option in _sequence(_mapping(context).get("menu_offer_options"))
        if isinstance(option, dict) and isinstance(option.get("id"), str)
    )
    if not options:
        return None
    return TransactionalContinuation(
        scope="menu_offer",
        resource_id=str(_mapping(context).get("menu_offer_at") or ""),
        state="menu_options_offered",
        required_effect=None,
        required_input=None,
        valid_next_actions=("start_cart_item_customization", "get_menu_item"),
        offered_options=options,
        pending_prompt=None,
        selection_tools=("get_menu_item", "start_cart_item_customization"),
        selected_option_argument="item_id",
    )


def _related_open_order(
    services: Any,
    user_id: str,
    agent_session_id: str,
) -> tuple[str | None, str | None]:
    """Identify an unfinished order so a new cart is never confused with it."""

    try:
        order = services.orders.get_active_order_for_session(
            user_id,
            agent_session_id,
        )
    except Exception:
        logger.warning(
            "Related open order could not be resolved",
            exc_info=True,
            extra={
                "event": "related_open_order_lookup_failed",
                "actor_id": user_id,
                "agent_session_id": agent_session_id,
            },
        )
        return (None, None)
    if not isinstance(order, dict):
        return (None, None)
    order_id = order.get("order_id")
    if not isinstance(order_id, str) or not order_id:
        return (None, None)
    return (order_id, _text(order.get("status")))


def _order_pending_prompt(services: Any, order: dict, agent: dict) -> str | None:
    prompt_builder = getattr(services.orders, "pending_input_prompt", None)
    if callable(prompt_builder):
        prompt = _text(prompt_builder(order))
        if prompt:
            return prompt
    return _text(agent.get("status_message"))


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _value(call: Any, key: str) -> Any:
    return call.get(key) if isinstance(call, dict) else getattr(call, key, None)


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
