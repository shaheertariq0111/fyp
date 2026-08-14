from types import SimpleNamespace

from src.agent.continuation import (
    TransactionalContinuation,
    continuation_context_block,
    continuation_satisfied_by,
    resolve_transactional_continuation,
)
from src.agent.response_grounding import ground_agent_response
from src.services.cart_service import CartService
from src.services.order_service import OrderService
from fakes import (
    MemoryAgentSessionRepository,
    MemoryCartRepository,
    MemoryMenuRepository,
    MemoryOrderRepository,
)


def build_services():
    """Menu fixture mirrors the shape of real configurable products.

    Option IDs are deliberately unrelated to their display labels so tests can
    prove continuation binds to backend IDs rather than presentation text.
    """
    menu = MemoryMenuRepository(
        items=[
            {
                "product_id": "configurable-pizza",
                "name": "Configurable Pizza",
                "category": "pizza",
                "currency": "PKR",
                "available": True,
                "starting_price": 750,
                "base_prices": {"small": 750, "medium": 1500, "large": 2100},
                "customization_group_ids": ["pizza-size", "pizza-crust"],
                "upsell_group_ids": [],
            },
        ],
        groups=[
            {
                "option_group_id": "pizza-size",
                "name": "Pizza Size",
                "type": "single_select",
                "required": True,
                "question": "Which size would you like?",
                "options": [
                    {"option_id": "small", "name": "Small", "price_key": "small"},
                    {"option_id": "medium", "name": "Medium", "price_key": "medium"},
                    {"option_id": "large", "name": "Large", "price_key": "large"},
                ],
            },
            {
                "option_group_id": "pizza-crust",
                "name": "Pizza Crust",
                "type": "single_select",
                "required": True,
                "question": "Choose a crust.",
                "options": [
                    {"option_id": "regular", "name": "Regular Crust", "price_delta": 0},
                    {"option_id": "thin", "name": "Crunchy Thin Crust", "price_delta": 0},
                    {"option_id": "stuffed", "name": "Stuffed Crust", "price_delta": 350},
                ],
            },
        ],
        upsells=[],
    )
    carts, orders = MemoryCartRepository(), MemoryOrderRepository()
    order_service = OrderService(orders, menu)
    cart_service = CartService(
        carts,
        menu,
        order_service,
        SimpleNamespace(restaurant_id="restaurant", branch_id="branch"),
    )
    return SimpleNamespace(carts=cart_service, orders=order_service)


def start_customizing_cart(
    services,
    *,
    user_id="user",
    session_id="session",
    channel="whatsapp",
):
    return services.carts.start_item_customization(
        user_id,
        session_id,
        "configurable-pizza",
        1,
        channel=channel,
    )


def reach_awaiting_fulfillment(
    services,
    *,
    user_id="user",
    session_id="session",
    channel="whatsapp",
):
    started = start_customizing_cart(
        services,
        user_id=user_id,
        session_id=session_id,
        channel=channel,
    )
    cart_id = started.data["cart_id"]
    cart_item_id = started.data["cart_item_id"]
    services.carts.save_choice(user_id, cart_item_id, "pizza-size", "small")
    services.carts.save_choice(user_id, cart_item_id, "pizza-crust", "regular")
    created = services.carts.create_pending_order(user_id, cart_id)
    return created.data["order_id"]


def tool_call(
    *,
    tool_name="save_customization_choice",
    is_write=True,
    success=True,
    effects=(),
    user_message="Done.",
):
    return {
        "tool_name": tool_name,
        "is_write": is_write,
        "success": success,
        "result": {
            "success": success,
            "user_message": user_message,
            "grounding": {"transactional_effects": list(effects)},
        },
    }


def test_customizing_cart_exposes_required_customization_effect():
    services = build_services()
    start_customizing_cart(services)

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation is not None
    assert continuation.scope == "cart"
    assert continuation.state == "customizing_item"
    assert continuation.required_effect == "customization_saved"
    assert continuation.required_input == "customization_choice"
    assert continuation.is_outstanding is True
    assert "save_customization_choice" in continuation.valid_next_actions
    assert "discard_active_cart" in continuation.valid_next_actions


def test_customizing_cart_offers_authoritative_backend_option_ids():
    services = build_services()
    start_customizing_cart(services)

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert [option.id for option in continuation.offered_options] == [
        "small",
        "medium",
        "large",
    ]
    assert continuation.field_name == "pizza-size"
    # The pending prompt is backend-authored text, not model prose.
    assert "Which size would you like?" in continuation.pending_prompt


def test_continuation_advances_to_next_backend_step_after_successful_choice():
    services = build_services()
    started = start_customizing_cart(services)
    services.carts.save_choice(
        "user",
        started.data["cart_item_id"],
        "pizza-size",
        "small",
    )

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation.field_name == "pizza-crust"
    assert [option.id for option in continuation.offered_options] == [
        "regular",
        "thin",
        "stuffed",
    ]


def test_invalid_option_does_not_advance_continuation():
    services = build_services()
    started = start_customizing_cart(services)
    rejected = services.carts.save_choice(
        "user",
        started.data["cart_item_id"],
        "pizza-size",
        "not-a-real-option",
    )

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert rejected.success is False
    assert continuation.field_name == "pizza-size"
    assert continuation.required_effect == "customization_saved"


def test_awaiting_fulfillment_order_exposes_fulfillment_continuation():
    services = build_services()
    order_id = reach_awaiting_fulfillment(services)

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation is not None
    assert continuation.scope == "order"
    assert continuation.resource_id == order_id
    assert continuation.state == "awaiting_fulfillment_method"
    assert continuation.required_effect == "fulfillment_saved"
    assert continuation.required_input == "fulfillment_method"
    assert continuation.is_outstanding is True
    # Advertised as the tools that perform them, not as low-level action strings.
    assert "choose_takeaway" in continuation.valid_next_actions
    assert "choose_delivery" in continuation.valid_next_actions
    assert continuation.pending_prompt


def test_takeaway_advances_state_into_the_whatsapp_customer_name_step():
    services = build_services()
    order_id = reach_awaiting_fulfillment(services)
    services.orders.update_order_flow("user", order_id, "set_takeaway")

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    # Takeaway cleared the fulfillment requirement; the existing WhatsApp
    # name-before-confirmation step is preserved unchanged.
    assert continuation.state == "awaiting_customer_name"
    assert continuation.required_effect == "customer_name_updated"
    assert continuation.pending_prompt


def test_pending_confirmation_requires_a_settling_effect():
    services = build_services()
    # The web channel already carries a confirmed customer name, so takeaway
    # lands directly on pending_confirmation.
    order_id = reach_awaiting_fulfillment(services, channel="web")
    services.orders.update_order_flow("user", order_id, "set_takeaway")

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation.state == "pending_confirmation"
    assert continuation.required_effect == "order_submitted"
    assert continuation.is_outstanding is True
    # Cancelling is an equally valid way out of this state.
    assert "order_cancelled" in continuation.accepted_effects
    # Saving a name leaves the order pending_confirmation, so it changes
    # something without settling what this state is waiting for.
    assert "customer_name_updated" not in continuation.accepted_effects


def test_cancelling_a_pending_order_counts_as_progress():
    services = build_services()
    order_id = reach_awaiting_fulfillment(services, channel="web")
    services.orders.update_order_flow("user", order_id, "set_takeaway")
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )
    cancelled = services.orders.update_order_flow("user", order_id, "cancel")

    grounded = ground_agent_response(
        text="No problem, I've cancelled that order.",
        tool_calls=[
            {
                "tool_name": "cancel_order",
                "is_write": True,
                "success": True,
                "result": cancelled.model_dump(exclude_none=True),
            }
        ],
        continuation=continuation,
    )

    assert cancelled.success is True
    # A cancellation must not be told it still needs confirming.
    assert "Should I confirm this order?" not in grounded.text


def test_discarding_an_active_cart_counts_as_progress():
    services = build_services()
    start_customizing_cart(services)
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )
    discarded = services.carts.discard_active_cart("user", "session")

    grounded = ground_agent_response(
        text="No problem, I discarded the cart.",
        tool_calls=[
            {
                "tool_name": "discard_active_cart",
                "is_write": True,
                "success": True,
                "result": discarded.model_dump(exclude_none=True),
            }
        ],
        continuation=continuation,
    )

    assert discarded.success is True
    assert "Which size" not in grounded.text


def test_no_tool_turn_cannot_narrate_submission_at_pending_confirmation():
    """A confirmation claim needs a successful confirm_order, not prose."""
    services = build_services()
    order_id = reach_awaiting_fulfillment(services, channel="web")
    services.orders.update_order_flow("user", order_id, "set_takeaway")
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    for claim in (
        "All set! Your order is on its way to the kitchen.",
        "Done, the restaurant has received it.",
        "Confirmed! We are preparing your food now.",
    ):
        grounded = ground_agent_response(
            text=claim,
            tool_calls=[],
            continuation=continuation,
        )

        assert grounded.source == "authoritative_continuation"
        assert claim not in grounded.text
        assert "Should I confirm this order?" in grounded.text


def test_real_confirmation_is_allowed_through_at_pending_confirmation():
    services = build_services()
    order_id = reach_awaiting_fulfillment(services, channel="web")
    services.orders.update_order_flow("user", order_id, "set_takeaway")
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )
    confirmed = services.orders.update_order_flow("user", order_id, "confirm")

    grounded = ground_agent_response(
        text="Your order has been submitted.",
        tool_calls=[
            {
                "tool_name": "confirm_order",
                "is_write": True,
                "success": True,
                "result": confirmed.model_dump(exclude_none=True),
            }
        ],
        continuation=continuation,
    )

    assert confirmed.success is True
    assert grounded.source != "authoritative_continuation"
    assert "Should I confirm this order?" not in grounded.text


def test_delivery_flow_requires_address_before_confirmation():
    services = build_services()
    order_id = reach_awaiting_fulfillment(services)
    services.orders.update_order_flow("user", order_id, "set_delivery")

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation.state == "awaiting_delivery_address"
    assert continuation.required_effect == "address_saved"
    assert continuation.is_outstanding is True


def test_no_active_resource_yields_no_continuation():
    services = build_services()

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation is None


def test_unfinished_order_in_another_session_does_not_bind_new_session():
    services = build_services()
    reach_awaiting_fulfillment(services, session_id="old-session")

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="new-session",
    )

    assert continuation is None


def test_active_cart_takes_precedence_over_unfinished_order_and_keeps_identity():
    services = build_services()
    stale_order_id = reach_awaiting_fulfillment(services)
    start_customizing_cart(services, session_id="second-session")

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="second-session",
    )

    assert continuation.scope == "cart"
    assert continuation.required_effect == "customization_saved"
    assert continuation.resource_id != stale_order_id


def test_continuation_satisfied_only_by_matching_successful_effect():
    continuation = TransactionalContinuation(
        scope="order",
        resource_id="ORD-1",
        state="awaiting_fulfillment_method",
        required_effect="fulfillment_saved",
        required_input="fulfillment_method",
        valid_next_actions=("update_order_flow:set_takeaway",),
        offered_options=(),
        pending_prompt="Would you like delivery or takeaway?",
    )

    assert continuation_satisfied_by(continuation, []) is False
    assert (
        continuation_satisfied_by(
            continuation,
            [tool_call(effects=["fulfillment_saved"])],
        )
        is True
    )
    assert (
        continuation_satisfied_by(
            continuation,
            [tool_call(effects=["customization_saved"])],
        )
        is False
    )
    assert (
        continuation_satisfied_by(
            continuation,
            [tool_call(success=False, effects=["fulfillment_saved"])],
        )
        is False
    )
    assert (
        continuation_satisfied_by(
            continuation,
            [tool_call(tool_name="get_order_status", is_write=False, effects=[])],
        )
        is False
    )


def test_continuation_without_required_effect_is_always_satisfied():
    continuation = TransactionalContinuation(
        scope="order",
        resource_id="ORD-1",
        state="pending_confirmation",
        required_effect=None,
        required_input="confirm_or_cancel",
        valid_next_actions=("update_order_flow:confirm",),
        offered_options=(),
        pending_prompt=None,
    )

    assert continuation_satisfied_by(continuation, []) is True


def test_context_block_exposes_authoritative_state_and_option_ids():
    services = build_services()
    start_customizing_cart(services)
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    block = continuation_context_block(continuation)

    assert "customizing_item" in block
    assert "customization_saved" in block
    assert "save_customization_choice" in block
    assert "small" in block and "medium" in block and "large" in block


def test_context_block_is_empty_without_continuation():
    assert continuation_context_block(None) == ""


def test_resolution_failure_is_contained_but_reported_as_unavailable():
    """The turn still completes, but the failure is never read as 'nothing pending'."""
    from src.agent.continuation import continuation_unavailable

    class ExplodingCarts:
        def get_active_cart(self, user_id, session_id):
            raise RuntimeError("backend unavailable")

    services = SimpleNamespace(carts=ExplodingCarts(), orders=None)

    resolved = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation_unavailable(resolved) is True
    assert resolved is not None


# End-to-end regressions: the two production failures, driven through the real
# cart/order services and the real grounding path.


def test_production_takeaway_regression_is_blocked_end_to_end():
    """Failure 1: 'takeaway' turn claimed pickup with no choose_takeaway call."""
    services = build_services()
    reach_awaiting_fulfillment(services)
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    grounded = ground_agent_response(
        text=(
            "Fulfillment Method: Takeaway\n"
            "Your order is now ready for pickup."
        ),
        tool_calls=[],
        continuation=continuation,
    )

    assert "ready for pickup" not in grounded.text
    assert grounded.source == "authoritative_continuation"
    assert grounded.rejection_reason == "required_effect_not_satisfied"


def test_real_takeaway_tool_call_is_allowed_through_end_to_end():
    services = build_services()
    order_id = reach_awaiting_fulfillment(services)
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )
    response = services.orders.update_order_flow("user", order_id, "set_takeaway")

    grounded = ground_agent_response(
        text="Takeaway it is!",
        tool_calls=[
            {
                "tool_name": "choose_takeaway",
                "is_write": True,
                "success": True,
                "result": response.model_dump(exclude_none=True),
            }
        ],
        continuation=continuation,
    )

    assert response.success is True
    assert grounded.source != "authoritative_continuation"


def test_production_customization_regression_is_blocked_end_to_end():
    """Failure 2: crust turns claimed progress while the cart never advanced."""
    services = build_services()
    started = start_customizing_cart(services)
    services.carts.save_choice(
        "user",
        started.data["cart_item_id"],
        "pizza-size",
        "small",
    )
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    grounded = ground_agent_response(
        text="Classic Crust selected. Your pizza is fully customized!",
        tool_calls=[],
        continuation=continuation,
    )

    # The invented crust name never reaches the customer, and the authoritative
    # backend options are restated instead.
    assert "Classic Crust" not in grounded.text
    assert "Regular Crust" in grounded.text
    assert "Crunchy Thin Crust" in grounded.text
    assert grounded.source == "authoritative_continuation"


def test_cart_not_ready_turn_restates_the_real_missing_step():
    """Checkout attempted too early must surface the genuine pending step."""
    services = build_services()
    started = start_customizing_cart(services)
    failure = services.carts.create_pending_order("user", started.data["cart_id"])
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    grounded = ground_agent_response(
        text="Your order is being placed!",
        tool_calls=[
            {
                "tool_name": "create_pending_order_from_cart",
                "is_write": True,
                "success": False,
                "result": failure.model_dump(exclude_none=True),
            }
        ],
        continuation=continuation,
    )

    assert failure.error_code == "CART_NOT_READY"
    assert "Your order is being placed!" not in grounded.text
    assert "Which size would you like?" in grounded.text


# Cross-turn menu offer binding: a numbered/shorthand reply must resolve against
# the exact offer the backend issued, not against conversational memory.


def build_session_service(clock=None):
    from src.services.agent_session_service import AgentSessionService

    repo = MemoryAgentSessionRepository()
    repo.create({
        "PK": "CUSTOMER#user",
        "SK": "SESSION#session",
        "agent_session_id": "session",
        "customer_id": "user",
    })
    service = AgentSessionService(
        repo,
        customer_service=None,
        settings=SimpleNamespace(agent_session_ttl_hours=12),
        clock=clock,
    )
    return service, repo


def test_menu_offer_is_persisted_and_bound_to_backend_ids():
    sessions, _ = build_session_service()
    services = build_services()
    services.agent_sessions = sessions
    sessions.save_menu_offer_context(
        "user",
        "session",
        options=[
            {"id": "product-a", "label": "Product A"},
            {"id": "product-b", "label": "Product B"},
            {"id": "product-c", "label": "Product C"},
        ],
    )

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation.scope == "menu_offer"
    # The second offered option resolves to its authoritative product ID.
    assert continuation.offered_options[1].id == "product-b"
    assert [option.id for option in continuation.offered_options] == [
        "product-a",
        "product-b",
        "product-c",
    ]
    # Browsing must never block a response.
    assert continuation.is_outstanding is False


def test_menu_offer_context_block_exposes_ids_for_ordinal_resolution():
    sessions, _ = build_session_service()
    services = build_services()
    services.agent_sessions = sessions
    sessions.save_menu_offer_context(
        "user",
        "session",
        options=[{"id": "lava-cake-2-pcs", "label": "Lava Cake - 2 Pcs"}],
    )

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )
    block = continuation_context_block(continuation)

    assert "id=lava-cake-2-pcs" in block
    assert "never a quantity" in block


def test_newer_offer_replaces_the_previous_one():
    sessions, _ = build_session_service()
    services = build_services()
    services.agent_sessions = sessions
    sessions.save_menu_offer_context(
        "user",
        "session",
        options=[{"id": "old-product", "label": "Old"}],
    )
    sessions.save_menu_offer_context(
        "user",
        "session",
        options=[{"id": "new-product", "label": "New"}],
    )

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert [option.id for option in continuation.offered_options] == ["new-product"]


def test_expired_offer_is_dropped_and_cannot_be_selected():
    from datetime import datetime, timedelta, timezone
    from src.services.agent_session_service import MENU_OFFER_TTL

    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    current = {"value": now}
    sessions, repo = build_session_service(clock=lambda: current["value"])
    services = build_services()
    services.agent_sessions = sessions
    sessions.save_menu_offer_context(
        "user",
        "session",
        options=[{"id": "stale-product", "label": "Stale"}],
    )
    current["value"] = now + MENU_OFFER_TTL + timedelta(seconds=1)

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation is None
    # The stale offer is cleared, not merely hidden.
    assert repo.get_menu_offer_context("user", "session") == {}


def test_empty_offer_clears_previous_options():
    sessions, _ = build_session_service()
    services = build_services()
    services.agent_sessions = sessions
    sessions.save_menu_offer_context(
        "user",
        "session",
        options=[{"id": "product-a", "label": "Product A"}],
    )
    sessions.save_menu_offer_context("user", "session", options=[])

    assert (
        resolve_transactional_continuation(
            services,
            user_id="user",
            agent_session_id="session",
        )
        is None
    )


def test_malformed_offer_entries_are_rejected():
    sessions, _ = build_session_service()

    saved = sessions.save_menu_offer_context(
        "user",
        "session",
        options=[
            {"id": "", "label": "Empty"},
            {"label": "No id"},
            "not-a-dict",
            {"id": "good-product", "label": "Good"},
        ],
    )

    assert saved["menu_offer_options"] == [
        {"id": "good-product", "label": "Good"}
    ]


def test_active_cart_outranks_a_lingering_menu_offer():
    sessions, _ = build_session_service()
    services = build_services()
    services.agent_sessions = sessions
    sessions.save_menu_offer_context(
        "user",
        "session",
        options=[{"id": "product-a", "label": "Product A"}],
    )
    start_customizing_cart(services)

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation.scope == "cart"
    assert continuation.required_effect == "customization_saved"


# Access-pattern guarantees for the normal WhatsApp message path.


class ScanDetectingTable:
    """Fails loudly if continuation resolution ever triggers a table scan."""

    def __init__(self):
        self.queries = 0

    def query(self, **kwargs):
        self.queries += 1
        return {"Items": []}

    def scan(self, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("continuation resolution must not scan a table")

    def get_item(self, **kwargs):
        return {}


def test_continuation_resolution_never_scans_a_table():
    from src.repositories.cart_repository import CartRepository
    from src.repositories.order_repository import OrderRepository

    cart_table, order_table = ScanDetectingTable(), ScanDetectingTable()
    carts = CartRepository.__new__(CartRepository)
    carts.table = cart_table
    orders = OrderRepository.__new__(OrderRepository)
    orders.table = order_table

    order_service = OrderService(orders, None)
    cart_service = CartService(
        carts,
        None,
        order_service,
        SimpleNamespace(restaurant_id="restaurant", branch_id="branch"),
    )
    services = SimpleNamespace(carts=cart_service, orders=order_service)

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation is None
    assert cart_table.queries >= 1
    assert order_table.queries >= 1


def test_continuation_resolution_makes_no_model_calls():
    """Continuation must never invoke Bedrock or any other model."""

    class ForbiddenKnowledge:
        def retrieve(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("continuation must not call the model")

    services = build_services()
    services.knowledge = ForbiddenKnowledge()
    sessions, _ = build_session_service()
    services.agent_sessions = sessions
    start_customizing_cart(services)

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation is not None


# Continuation satisfaction must follow authoritative post-write state, not the
# mere fact that some valid write succeeded.


def build_pending_confirmation(with_customer_service=False):
    from src.services.customer_service import CustomerService
    from test_order_service import MemoryCustomerRepository

    services = build_services()
    if with_customer_service:
        services.orders.customers = CustomerService(MemoryCustomerRepository())
    order_id = reach_awaiting_fulfillment(services, channel="web")
    services.orders.update_order_flow("user", order_id, "set_takeaway")
    return services, order_id


def write_call(response, tool_name):
    return {
        "tool_name": tool_name,
        "is_write": True,
        "success": response.success,
        "result": response.model_dump(exclude_none=True),
    }


def test_self_transition_write_does_not_resolve_the_continuation():
    """A name change at pending_confirmation leaves confirmation outstanding."""
    services, order_id = build_pending_confirmation(with_customer_service=True)
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )
    saved = services.orders.update_order_flow(
        "user",
        order_id,
        "save_customer_name",
        "Ava",
    )
    post_state = services.orders.get_order_status("user", order_id).data["order"][
        "status"
    ]

    assert saved.success is True
    assert post_state == "pending_confirmation"
    assert continuation_satisfied_by(continuation, [write_call(saved, "update_order_flow")]) is False
    # Re-derived from post-write state, confirmation is still required.
    assert resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    ).is_outstanding is True


def test_self_transition_write_cannot_carry_a_submission_claim():
    services, order_id = build_pending_confirmation(with_customer_service=True)
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )
    saved = services.orders.update_order_flow(
        "user",
        order_id,
        "save_customer_name",
        "Ava",
    )

    grounded = ground_agent_response(
        text="All done, order submitted!",
        tool_calls=[write_call(saved, "update_order_flow")],
        continuation=continuation,
    )

    assert "submitted" not in grounded.text
    assert "Should I confirm this order?" in grounded.text


def test_state_changing_writes_do_resolve_the_continuation():
    for action, tool_name in (("confirm", "confirm_order"), ("cancel", "cancel_order")):
        services, order_id = build_pending_confirmation()
        continuation = resolve_transactional_continuation(
            services,
            user_id="user",
            agent_session_id="session",
        )
        response = services.orders.update_order_flow("user", order_id, action)
        post_state = services.orders.get_order_status("user", order_id).data["order"][
            "status"
        ]

        assert response.success is True
        assert post_state != "pending_confirmation"
        assert continuation_satisfied_by(continuation, [write_call(response, tool_name)]) is True


def test_failed_confirmation_leaves_the_continuation_outstanding():
    services, _ = build_pending_confirmation()
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )
    failure = {
        "tool_name": "confirm_order",
        "is_write": True,
        "success": False,
        "result": {
            "success": False,
            "error_code": "INVALID_ORDER_STATE",
            "user_message": "That order cannot be confirmed right now.",
            "grounding": {"transactional_effects": ["order_submitted"]},
        },
    }

    assert continuation_satisfied_by(continuation, [failure]) is False

    grounded = ground_agent_response(
        text="Your order has been submitted successfully.",
        tool_calls=[failure],
        continuation=continuation,
    )

    assert "submitted successfully" not in grounded.text
    assert "That order cannot be confirmed right now." in grounded.text
    assert "Should I confirm this order?" in grounded.text


def test_rejecting_a_name_suggestion_keeps_the_name_step_outstanding():
    services = build_services()
    order_id = reach_awaiting_fulfillment(services, channel="whatsapp")
    services.orders.update_order_flow("user", order_id, "set_takeaway")

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation.state == "awaiting_customer_name"
    assert continuation.is_outstanding is True


# Repeated-read identity stability against the real production tool payloads.


def test_real_cart_reads_are_byte_stable_without_state_change():
    """No timestamps/request IDs/trace IDs leak into these tool payloads.

    If they did, the repeated-read guard could never recognise the production
    loop, so this pins the property rather than assuming it.
    """
    services = build_services()
    start_customizing_cart(services)

    first = services.carts.get_active_cart("user", "session").model_dump(
        exclude_none=True
    )
    second = services.carts.get_active_cart("user", "session").model_dump(
        exclude_none=True
    )

    assert first == second


def test_real_order_reads_are_byte_stable_without_state_change():
    services = build_services()
    order_id = reach_awaiting_fulfillment(services)

    first = services.orders.get_order_status("user", order_id).model_dump(
        exclude_none=True
    )
    second = services.orders.get_order_status("user", order_id).model_dump(
        exclude_none=True
    )

    assert first == second


def test_authoritative_state_change_produces_a_new_read_identity():
    services = build_services()
    started = start_customizing_cart(services)
    before = services.carts.get_active_cart("user", "session").model_dump(
        exclude_none=True
    )
    services.carts.save_choice(
        "user",
        started.data["cart_item_id"],
        "pizza-size",
        "small",
    )
    after = services.carts.get_active_cart("user", "session").model_dump(
        exclude_none=True
    )

    assert before != after


def test_production_repeated_get_active_cart_loop_is_detected_with_real_services(
    monkeypatch,
):
    """Replays the exact production pattern through the real cart service."""
    from src.agent import tools
    from src.agent.context import AgentRequestContext, request_context

    services = build_services()
    start_customizing_cart(services)
    monkeypatch.setattr(tools, "get_services", lambda: services)

    with request_context(AgentRequestContext("user", "session")):
        results = [tools.get_active_cart() for _ in range(36)]

    blocked = [
        item
        for item in results
        if item.get("error_code") == tools.REPEATED_READ_ERROR_CODE
    ]
    assert len(blocked) == 36 - tools.REPEATED_READ_LIMIT
    # The cart itself is untouched by the guard.
    assert services.carts.get_active_cart("user", "session").success is True


# Fail-safe behavior when authoritative state cannot be read at all.


def test_resolver_failure_is_distinguishable_from_no_continuation():
    from src.agent.continuation import continuation_unavailable

    class Broken:
        def get_active_cart(self, *args):
            raise RuntimeError("dynamo unavailable")

    failed = resolve_transactional_continuation(
        SimpleNamespace(carts=Broken(), orders=None),
        user_id="user",
        agent_session_id="session",
    )
    empty = resolve_transactional_continuation(
        build_services(),
        user_id="user",
        agent_session_id="session",
    )

    assert continuation_unavailable(failed) is True
    assert empty is None
    assert failed is not None


def test_resolver_failure_blocks_raw_transactional_prose():
    from src.agent.continuation import CONTINUATION_UNAVAILABLE
    from src.agent.response_grounding import FAILED_READ_FALLBACK

    grounded = ground_agent_response(
        text="Takeaway selected. Your order is ready for pickup!",
        tool_calls=[],
        continuation=CONTINUATION_UNAVAILABLE,
    )

    assert "ready for pickup" not in grounded.text
    assert grounded.text == FAILED_READ_FALLBACK
    assert grounded.source == "continuation_unavailable"
    assert grounded.rejection_reason == "continuation_resolution_failed"


def test_resolver_failure_keeps_backend_authored_tool_text():
    """A write that really happened this turn is still authoritative."""
    from src.agent.continuation import CONTINUATION_UNAVAILABLE

    services = build_services()
    order_id = reach_awaiting_fulfillment(services)
    response = services.orders.update_order_flow("user", order_id, "set_takeaway")

    grounded = ground_agent_response(
        text="Anything else?",
        tool_calls=[
            {
                "tool_name": "choose_takeaway",
                "is_write": True,
                "success": True,
                "result": response.model_dump(exclude_none=True),
            }
        ],
        continuation=CONTINUATION_UNAVAILABLE,
    )

    assert grounded.source != "continuation_unavailable"


def test_resolver_failure_injects_no_trusted_context():
    from src.agent.continuation import CONTINUATION_UNAVAILABLE

    assert continuation_context_block(CONTINUATION_UNAVAILABLE) == ""


def test_resolver_failure_mutates_no_backend_state():
    class Broken:
        def __init__(self):
            self.calls = 0

        def get_active_cart(self, *args):
            self.calls += 1
            raise RuntimeError("dynamo unavailable")

    services = build_services()
    order_id = reach_awaiting_fulfillment(services)
    before = services.orders.get_order_status("user", order_id).data["order"]
    broken = Broken()

    resolve_transactional_continuation(
        SimpleNamespace(carts=broken, orders=services.orders),
        user_id="user",
        agent_session_id="session",
    )
    after = services.orders.get_order_status("user", order_id).data["order"]

    assert broken.calls == 1
    assert before == after


def test_resolver_failure_stays_observable(caplog):
    import logging as _logging

    class Broken:
        def get_active_cart(self, *args):
            raise RuntimeError("dynamo unavailable")

    with caplog.at_level(_logging.ERROR):
        resolve_transactional_continuation(
            SimpleNamespace(carts=Broken(), orders=None),
            user_id="user",
            agent_session_id="session",
        )

    records = [
        record
        for record in caplog.records
        if record.__dict__.get("event") == "continuation_resolution_failed"
    ]
    assert records, "resolution failure must not be swallowed silently"
    assert records[0].exc_info is not None


def test_successful_resolution_with_no_continuation_is_unchanged():
    services = build_services()
    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    grounded = ground_agent_response(
        text="Hello! How can I help with your order today?",
        tool_calls=[],
        continuation=continuation,
    )

    assert continuation is None
    assert grounded.text == "Hello! How can I help with your order today?"
    assert grounded.source == "conversation"


# Stale/absent offer context must not present options as authoritative.


def test_failed_offer_persistence_leaves_no_authoritative_options():
    """Scenario A: the offer never persisted, so nothing authorizes an ordinal."""
    services = build_services()
    sessions, _ = build_session_service()
    services.agent_sessions = sessions

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation is None
    assert continuation_context_block(continuation) == ""


def test_expired_offer_leaves_no_authoritative_options():
    """Scenario B: a TTL-expired offer is cleared, not presented as current."""
    from datetime import datetime, timedelta, timezone
    from src.services.agent_session_service import MENU_OFFER_TTL

    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    clock = {"value": now}
    sessions, repo = build_session_service(clock=lambda: clock["value"])
    services = build_services()
    services.agent_sessions = sessions
    sessions.save_menu_offer_context(
        "user",
        "session",
        options=[{"id": "product-a", "label": "A"}, {"id": "product-b", "label": "B"}],
    )
    clock["value"] = now + MENU_OFFER_TTL + timedelta(seconds=1)

    continuation = resolve_transactional_continuation(
        services,
        user_id="user",
        agent_session_id="session",
    )

    assert continuation is None
    assert continuation_context_block(continuation) == ""
    assert repo.get_menu_offer_context("user", "session") == {}


def test_directly_named_product_still_works_without_any_offer_context():
    """The invariant must not block a customer naming a product outright."""
    services = build_services()

    started = services.carts.start_item_customization(
        "user",
        "session",
        "configurable-pizza",
        1,
        channel="whatsapp",
    )

    assert started.success is True
