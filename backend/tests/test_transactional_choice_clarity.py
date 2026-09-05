"""Regressions for two semantic gaps left after PR #103.

A. A ready cart announced itself with a terse authoritative line and no next
   step, because successful-write grounding replaced the model's wording.
B. An ambiguous acknowledgement at ``awaiting_fulfillment_method`` produced a
   real ``set_delivery`` write, because the mutually exclusive fulfillment
   choices were never modelled as authoritative offered options.

These tests assert machine contracts only. They never assert customer-facing
English, and they never parse customer language.
"""

from src.agent.continuation import (
    continuation_context_block,
    resolve_transactional_continuation,
)
from src.agent.response_grounding import ground_agent_response
from src.agent.system_prompt import RESTAURANT_AGENT_SYSTEM_PROMPT
from test_agent_continuation import (
    build_services,
    reach_awaiting_fulfillment,
    start_customizing_cart,
)


def reach_cart_ready(services, *, user_id="user", session_id="session"):
    started = start_customizing_cart(
        services,
        user_id=user_id,
        session_id=session_id,
        channel="whatsapp",
    )
    cart_item_id = started.data["cart_item_id"]
    services.carts.save_choice(user_id, cart_item_id, "pizza-size", "small")
    saved = services.carts.save_choice(user_id, cart_item_id, "pizza-crust", "regular")
    return started.data["cart_id"], saved


def write_call(response, tool_name):
    return {
        "tool_name": tool_name,
        "is_write": True,
        "success": response.success,
        "result": response.model_dump(exclude_none=True),
    }


# ---------------------------------------------------------------- A. cart ready


def test_cart_ready_exposes_checkout_as_a_machine_next_action():
    services = build_services()
    cart_id, _ = reach_cart_ready(services)
    skipped = services.carts.handle_upsell("user", cart_id, "skip")

    agent = skipped.agent

    assert skipped.success is True
    assert agent["cart_status"] == "cart_ready"
    assert agent["next_action"] == "create_pending_order"
    assert "create_pending_order_from_cart" in agent["valid_next_actions"]
    assert "discard_active_cart" in agent["valid_next_actions"]


def test_cart_ready_continuation_is_not_a_dead_end():
    services = build_services()
    cart_id, _ = reach_cart_ready(services)
    services.carts.handle_upsell("user", cart_id, "skip")

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )
    block = continuation_context_block(continuation)

    assert continuation.state == "cart_ready"
    # A ready cart is not waiting on an effect, but it must still advertise
    # where the conversation can go next.
    assert continuation.is_outstanding is False
    assert "create_pending_order_from_cart" in continuation.valid_next_actions
    assert "discard_active_cart" in continuation.valid_next_actions
    assert "create_pending_order_from_cart" in block


def test_cart_ready_write_lets_the_model_phrase_the_next_step():
    """The terse backend line must not overwrite a complete natural reply."""
    services = build_services()
    cart_id, _ = reach_cart_ready(services)
    skipped = services.carts.handle_upsell("user", cart_id, "skip")
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )
    natural = "Your cart is ready. Want to check out, or change something first?"

    grounded = ground_agent_response(
        text=natural,
        tool_calls=[write_call(skipped, "handle_cart_upsell")],
        continuation=continuation,
    )

    assert grounded.text == natural


def test_cart_ready_does_not_check_out_without_a_successful_checkout_write():
    services = build_services()
    cart_id, _ = reach_cart_ready(services)
    services.carts.handle_upsell("user", cart_id, "skip")

    cart = services.carts.get_active_cart("user", "session").data["cart"]

    assert cart["status"] == "cart_ready"


def test_responses_carrying_exact_artifacts_still_force_substitution():
    """Natural phrasing must never leak into fact-bearing responses."""
    services = build_services()
    started = start_customizing_cart(services)
    choice = services.carts.save_choice(
        "user",
        started.data["cart_item_id"],
        "pizza-size",
        "small",
    )

    grounded = ground_agent_response(
        text="I picked a crust for you.",
        tool_calls=[write_call(choice, "save_customization_choice")],
        continuation=None,
    )

    assert grounded.source == "exact_artifact"
    assert grounded.text == choice.grounding.exact_customer_text
    assert "I picked a crust for you." not in grounded.text


def test_writes_without_the_natural_phrasing_flag_are_unchanged():
    """Default behaviour stays strict for every response that does not opt in."""
    services = build_services()
    order_id = reach_awaiting_fulfillment(services)
    takeaway = services.orders.update_order_flow("user", order_id, "set_takeaway")

    grounded = ground_agent_response(
        text="Anything else I can add?",
        tool_calls=[write_call(takeaway, "choose_takeaway")],
        continuation=None,
    )

    assert grounded.text != "Anything else I can add?"


# -------------------------------------------------------------- B. fulfillment


def test_awaiting_fulfillment_exposes_both_exclusive_choices():
    services = build_services()
    reach_awaiting_fulfillment(services)

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )
    labels = {option.label for option in continuation.offered_options}

    assert continuation.state == "awaiting_fulfillment_method"
    assert continuation.required_effect == "fulfillment_saved"
    assert labels == {"set_delivery", "set_takeaway"}


def test_fulfillment_choices_require_a_unique_selection():
    services = build_services()
    reach_awaiting_fulfillment(services)

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation.requires_unique_choice is True


def test_fulfillment_choices_point_at_the_specialized_tools():
    """The generic tool must not be the advertised handle when a specific one exists."""
    services = build_services()
    reach_awaiting_fulfillment(services)

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )
    ids = {option.id for option in continuation.offered_options}

    assert ids == {"choose_delivery", "choose_takeaway"}


def test_fulfillment_context_block_presents_the_exclusive_choice():
    services = build_services()
    reach_awaiting_fulfillment(services)

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )
    block = continuation_context_block(continuation)

    assert "choose_takeaway" in block
    assert "choose_delivery" in block
    assert "exactly one" in block


def test_ambiguous_turn_leaves_fulfillment_state_untouched():
    """A turn that writes nothing must not be reported as a fulfillment choice."""
    services = build_services()
    order_id = reach_awaiting_fulfillment(services)
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    grounded = ground_agent_response(
        text="Great, delivery it is!",
        tool_calls=[],
        continuation=continuation,
    )
    after = services.orders.get_order_status("user", order_id).data["order"]

    assert after["status"] == "awaiting_fulfillment_method"
    assert after["fulfillment_method"] is None
    assert "delivery it is" not in grounded.text
    assert grounded.source == "authoritative_continuation"


def test_single_option_states_do_not_demand_a_choice():
    """Only genuinely exclusive option sets require unique identification."""
    services = build_services()
    order_id = reach_awaiting_fulfillment(services)
    services.orders.update_order_flow("user", order_id, "set_delivery")

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation.state == "awaiting_delivery_address"
    assert continuation.requires_unique_choice is False


def test_takeaway_and_delivery_writes_both_still_advance():
    for action, expected in (
        ("set_takeaway", "awaiting_customer_name"),
        ("set_delivery", "awaiting_delivery_address"),
    ):
        services = build_services()
        order_id = reach_awaiting_fulfillment(services)
        response = services.orders.update_order_flow("user", order_id, action)
        after = services.orders.get_order_status("user", order_id).data["order"]

        assert response.success is True
        assert after["status"] == expected


# ------------------------------------------- C. semantic contract (prompt-side)


def test_prompt_forbids_acting_on_an_acknowledgement_alone():
    """Interpretation stays with the LLM; this pins the contract it must follow."""
    normalized = " ".join(RESTAURANT_AGENT_SYSTEM_PROMPT.split())

    assert "uniquely identifies one" in normalized
    assert "acknowledgement" in normalized
    assert "ask which one" in normalized


def test_prompt_keeps_short_replies_context_dependent():
    normalized = " ".join(RESTAURANT_AGENT_SYSTEM_PROMPT.split())

    assert "no fixed meaning" in normalized


# ------------------------------------------- D. semantic tool surface (no
# low-level backend action strings exposed to the agent)


from src.agent.tools import MVP_TOOLS
from src.services.order_service import ORDER_TRANSITIONS, OrderService


def exposed_tool_names():
    return {tool.tool_name for tool in MVP_TOOLS}


def test_low_level_order_action_tool_is_not_exposed_to_the_agent():
    """The agent must choose semantic actions, not compose action strings."""
    assert "update_order_flow" not in exposed_tool_names()


def test_customer_name_actions_have_semantic_tools():
    names = exposed_tool_names()

    assert "save_customer_name" in names
    assert "confirm_customer_name" in names
    assert "reject_customer_name" in names


def test_every_order_transition_action_is_reachable_through_a_semantic_tool():
    """No legitimate transition may be lost by removing the generic tool."""
    names = exposed_tool_names()
    actions = {action for (_state, action) in ORDER_TRANSITIONS}

    for action in actions:
        tool = OrderService.SPECIALIZED_ORDER_TOOLS.get(action)
        assert tool is not None, f"{action} has no semantic tool"
        assert tool in names, f"{action} maps to unexposed tool {tool}"


def test_customer_name_wrappers_delegate_to_the_validated_transition(monkeypatch):
    from types import SimpleNamespace
    from src.agent import tools as agent_tools
    from src.agent.context import AgentRequestContext, request_context

    calls = []

    class Orders:
        def update_order_flow(self, user_id, order_id, action, value=None,
                              idempotency_key=None):
            calls.append((user_id, order_id, action, value))
            from src.models.tool_responses import ToolResponse

            return ToolResponse.ok(data={}, user_message="ok")

    monkeypatch.setattr(
        agent_tools,
        "get_services",
        lambda: SimpleNamespace(orders=Orders()),
    )

    with request_context(AgentRequestContext("user-1", "session-1")):
        agent_tools.save_customer_name("ORD-1", "Ava")
        agent_tools.confirm_customer_name("ORD-1")
        agent_tools.reject_customer_name("ORD-1")

    assert calls == [
        ("user-1", "ORD-1", "save_customer_name", "Ava"),
        ("user-1", "ORD-1", "confirm_customer_name", None),
        ("user-1", "ORD-1", "reject_customer_name", None),
    ]


def test_customer_name_flow_still_advances_end_to_end():
    from src.services.customer_service import CustomerService
    from test_order_service import MemoryCustomerRepository

    services = build_services()
    services.orders.customers = CustomerService(MemoryCustomerRepository())
    order_id = reach_awaiting_fulfillment(services)
    services.orders.update_order_flow("user", order_id, "set_takeaway")

    saved = services.orders.update_order_flow(
        "user",
        order_id,
        "save_customer_name",
        "Ava",
    )
    after = services.orders.get_order_status("user", order_id).data["order"]

    assert saved.success is True
    assert after["status"] == "pending_confirmation"


def test_fulfillment_continuation_advertises_only_semantic_tools():
    services = build_services()
    reach_awaiting_fulfillment(services)

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )
    block = continuation_context_block(continuation)

    assert "update_order_flow" not in block
    assert "choose_delivery" in block
    assert "choose_takeaway" in block


# ------------------------------------- E. cart-ready phrasing cannot drop facts


def test_cart_ready_response_always_carries_the_authoritative_statement():
    """Natural wording may extend the backend fact, never replace it."""
    services = build_services()
    cart_id, _ = reach_cart_ready(services)
    skipped = services.carts.handle_upsell("user", cart_id, "skip")
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    grounded = ground_agent_response(
        text="Shall we head to checkout, or would you like to change something?",
        tool_calls=[write_call(skipped, "handle_cart_upsell")],
        continuation=continuation,
    )

    assert skipped.user_message in grounded.text
    assert "checkout" in grounded.text


def test_cart_ready_does_not_duplicate_the_statement_when_model_restates_it():
    services = build_services()
    cart_id, _ = reach_cart_ready(services)
    skipped = services.carts.handle_upsell("user", cart_id, "skip")

    grounded = ground_agent_response(
        text=f"{skipped.user_message} Want to check out now?",
        tool_calls=[write_call(skipped, "handle_cart_upsell")],
        continuation=None,
    )

    assert grounded.text.count(skipped.user_message) == 1


def test_cart_ready_prose_cannot_suppress_the_backend_outcome():
    """Even an empty or evasive model reply still states the authoritative fact."""
    services = build_services()
    cart_id, _ = reach_cart_ready(services)
    skipped = services.carts.handle_upsell("user", cart_id, "skip")

    grounded = ground_agent_response(
        text="",
        tool_calls=[write_call(skipped, "handle_cart_upsell")],
        continuation=None,
    )

    assert skipped.user_message in grounded.text


def test_added_upsell_response_keeps_checkout_prompt():
    grounded = ground_agent_response(
        text="The add-on was added. Want to check out now?",
        tool_calls=[
            {
                "tool_name": "handle_cart_upsell",
                "is_write": True,
                "success": True,
                "result": {
                    "success": True,
                    "user_message": "The add-on was added.",
                    "grounding": {
                        "transactional_effects": ["item_added"],
                        "allows_natural_phrasing": True,
                    },
                },
            }
        ],
        continuation=None,
    )

    assert grounded.text == "The add-on was added. Want to check out now?"
