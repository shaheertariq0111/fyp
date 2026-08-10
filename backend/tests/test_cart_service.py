from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.agent import tools
from src.agent.context import AgentRequestContext, request_context
from src.agent.response_grounding import (
    _supported_effects,
    ground_authoritative_tool_response,
)
from src.models.conversation_contracts import OptionContract
from src.services.cart_service import CartService
from src.services.agent_request_processor import AgentRequestProcessor
from src.services.order_service import OrderService
from src.repositories.cart_repository import CartCreationConflictError
from fakes import MemoryCartRepository, MemoryMenuRepository, MemoryOrderRepository


def build_services():
    menu = MemoryMenuRepository(
        items=[
            {"product_id": "configurable", "name": "Configured Item", "category": "dynamic",
             "currency": "CUR", "available": True, "starting_price": 10,
             "base_prices": {"option-a": 15}, "customization_group_ids": ["dynamic-choice"],
             "upsell_group_ids": ["dynamic-upsell"]},
            {"product_id": "addon", "name": "Configured Add-on", "category": "addon",
             "currency": "CUR", "available": True, "starting_price": 4,
             "customization_group_ids": [], "upsell_group_ids": []},
            {"product_id": "addon-configurable", "name": "Configurable Add-on", "category": "addon",
             "currency": "CUR", "available": True, "starting_price": 5,
             "customization_group_ids": ["dynamic-choice"], "upsell_group_ids": []},
            {"product_id": "priced-pizza", "name": "Priced Pizza", "category": "pizza",
             "currency": "PKR", "available": True, "starting_price": 850,
             "base_prices": {"small": 850, "medium": 1700, "large": 2400},
             "customization_group_ids": ["pizza-size", "pizza-crust"],
             "upsell_group_ids": []},
        ],
        groups=[
            {"option_group_id": "dynamic-choice", "name": "Dynamic", "type": "single_select",
             "required": True, "question": "Choose dynamically", "options": [
                {"option_id": "choice-a", "label": "Choice A", "price_key": "option-a"}
             ]},
            {"option_group_id": "pizza-size", "name": "Pizza Size", "type": "single_select",
             "required": True, "question": "Which pizza size would you like?", "options": [
                {"option_id": "small", "name": "Small", "price_key": "small"},
                {"option_id": "medium", "name": "Medium", "price_key": "medium"},
                {"option_id": "large", "name": "Large", "price_key": "large"},
             ]},
            {"option_group_id": "pizza-crust", "name": "Pizza Crust", "type": "single_select",
             "required": True, "question": "Choose a crust.", "options": [
                {"option_id": "regular", "name": "Regular Crust", "price_delta": 0},
                {"option_id": "thin", "name": "Crunchy Thin Crust", "price_delta": 0},
                {"option_id": "stuffed", "name": "Mozzarella Stuffed Crust", "price_delta": 350},
                {"option_id": "cheese-burst", "name": "Cheese Burst Crust", "price_delta": 500},
             ]},
        ],
        upsells=[{"upsell_group_id": "dynamic-upsell", "question": "Add?",
                  "items": ["addon", "addon-configurable"]}],
    )
    carts, orders = MemoryCartRepository(), MemoryOrderRepository()
    order_service = OrderService(orders, menu)
    cart_service = CartService(carts, menu, order_service,
                               SimpleNamespace(restaurant_id="restaurant", branch_id="branch"))
    return cart_service, carts, orders


def test_two_identical_items_share_one_cart_item():
    service, carts, _ = build_services()
    started = service.start_item_customization("user", "session", "configurable", 2)
    cart_id = started.data["cart_id"]
    response = service.set_customization_mode("user", cart_id, "same")
    assert len(response.data["items"]) == 1
    assert response.data["items"][0]["quantity"] == 2


def test_two_separate_items_are_labeled_and_advanced():
    service, _, _ = build_services()
    cart_id = service.start_item_customization("user", "session", "configurable", 2).data["cart_id"]
    first = service.set_customization_mode("user", cart_id, "separate")
    first_id = first.data["cart_item_id"]
    second = service.save_choice("user", first_id, "dynamic-choice", "choice-a")
    assert [item["quantity"] for item in second.data["items"]] == [1, 1]
    assert second.data["label"] == "Item 2 of 2"


def test_upsell_then_pending_order_reprices_server_side():
    service, carts, orders = build_services()
    started = service.start_item_customization(
        "user", "session", "configurable",
        customer_id="customer-1", customer_name="Ava", customer_phone="+923001234567",
    )
    ready = service.save_choice("user", started.data["cart_item_id"], "dynamic-choice", "choice-a")
    cart_id = ready.data["cart_id"]
    options = service.handle_upsell("user", cart_id, "get_options")
    assert options.data["upsell_items"][0]["product_id"] == "addon"
    assert options.data["items"][0]["item_id"] == "configurable"
    assert options.agent["next_action"] == "choose_upsell"
    assert options.agent["upsell_items"][0]["product_id"] == "addon"
    added = service.handle_upsell("user", cart_id, "add_item", "addon")
    assert added.next_action == "create_pending_order"
    assert added.data["status"] == "cart_ready"
    pending = service.create_pending_order("user", cart_id)
    assert pending.success
    assert pending.data["status"] == "awaiting_fulfillment_method"
    assert carts.find_by_cart_id("user", cart_id)["status"] == "converted_to_order"
    assert service.get_active_cart("user", "session").data["cart"] is None
    order = next(iter(orders.data.values()))
    assert order["total"] == 19
    assert order["customer_id"] == "customer-1"
    assert order["customer_name"] == "Ava"
    assert order["customer_phone"] == "+923001234567"


def test_checkout_auto_skips_pending_upsell_decision():
    service, carts, orders = build_services()
    started = service.start_item_customization("user", "session", "configurable")
    ready = service.save_choice("user", started.data["cart_item_id"], "dynamic-choice", "choice-a")
    service.handle_upsell("user", ready.data["cart_id"], "get_options")

    pending = service.create_pending_order("user", ready.data["cart_id"])

    assert pending.success
    assert pending.data["status"] == "awaiting_fulfillment_method"
    saved = carts.find_by_cart_id("user", ready.data["cart_id"])
    assert saved["status"] == "converted_to_order"
    assert next(iter(orders.data.values()))["items"][0]["name"] == "Configured Item"


def test_legacy_pending_confirmation_cart_is_not_returned_as_active_cart():
    service, carts, orders = build_services()
    started = service.start_item_customization("user", "session", "configurable")
    ready = service.save_choice("user", started.data["cart_item_id"], "dynamic-choice", "choice-a")
    pending = service.create_pending_order("user", ready.data["cart_id"])
    assert pending.success
    legacy_cart = carts.find_by_cart_id("user", ready.data["cart_id"])
    legacy_cart["status"] = "pending_confirmation"
    carts.data[legacy_cart["cart_id"]] = legacy_cart

    response = service.get_active_cart("user", "session")

    assert response.data["cart"] is None
    assert response.data["orders"][0]["order_id"] == next(iter(orders.data))
    assert response.agent["orders"][0]["status"] == "awaiting_fulfillment_method"
    assert "Do not use a cart_id as an order_id" in response.agent["instruction"]


def test_configurable_upsell_is_customized_before_pending_order():
    service, _, orders = build_services()
    started = service.start_item_customization("user", "session", "configurable")
    ready = service.save_choice("user", started.data["cart_item_id"], "dynamic-choice", "choice-a")
    options = service.handle_upsell("user", ready.data["cart_id"], "get_options")

    assert [item["product_id"] for item in options.data["upsell_items"]] == [
        "addon", "addon-configurable"
    ]

    upsell_choice = service.handle_upsell("user", ready.data["cart_id"], "add_item", "addon-configurable")
    assert upsell_choice.next_action == "ask_customization_choice"
    assert upsell_choice.data["field_name"] == "dynamic-choice"
    assert upsell_choice.agent["active_choice"]["field_name"] == "dynamic-choice"
    assert upsell_choice.agent["valid_next_actions"] == ["save_customization_choice"]

    upsell_ready = service.save_choice(
        "user",
        upsell_choice.data["cart_item_id"], "dynamic-choice", "choice-a"
    )
    assert upsell_ready.next_action == "create_pending_order"
    assert upsell_ready.data["status"] == "cart_ready"

    pending = service.create_pending_order("user", ready.data["cart_id"])
    assert pending.success
    assert next(iter(orders.data.values()))["total"] == 20


def test_add_item_to_active_cart_appends_to_existing_cart():
    service, carts, _ = build_services()
    started = service.start_item_customization("user", "session", "configurable")
    ready = service.save_choice("user", started.data["cart_item_id"], "dynamic-choice", "choice-a")

    added = service.add_item_to_active_cart("user", "session", "addon")

    assert added.success
    saved = carts.find_by_cart_id("user", ready.data["cart_id"])
    assert [item["item_id"] for item in saved["items"]] == ["configurable", "addon"]
    assert saved["status"] == "item_ready"


def test_save_active_choice_matches_current_backend_option_text():
    service, _, _ = build_services()
    service.start_item_customization("user", "session", "configurable")

    response = service.save_active_choice("user", "session", "Choice A")

    assert response.success
    assert response.data["status"] == "item_ready"


def test_get_active_cart_includes_current_customization_prompt():
    service, _, _ = build_services()
    service.start_item_customization("user", "session", "configurable")

    response = service.get_active_cart("user", "session")

    cart = response.data["cart"]
    assert cart["status"] == "customizing_item"
    assert cart["question"] == "Choose dynamically"
    assert cart["options"][0]["label"] == "Choice A"
    assert response.agent["cart_status"] == "customizing_item"
    assert response.agent["active_choice"]["question"] == "Choose dynamically"


def test_cart_tool_response_includes_agent_next_step_packet():
    service, _, _ = build_services()

    response = service.start_item_customization("user", "session", "configurable")

    assert response.agent["entity"] == "cart"
    assert response.agent["cart_id"] == response.data["cart_id"]
    assert response.agent["required_input"] == "customization_choice"
    assert response.agent["active_choice"]["cart_item_id"] == response.data["cart_item_id"]
    assert response.agent["valid_next_actions"] == ["save_customization_choice"]

def test_start_item_customization_does_not_create_second_active_cart():
    service, carts, _ = build_services()

    first = service.start_item_customization(
        "user",
        "session",
        "configurable",
    )
    second = service.start_item_customization(
        "user",
        "session",
        "addon",
    )

    assert len(carts.data) == 1
    assert second.data["cart_id"] == first.data["cart_id"]
    assert second.data["items"][0]["item_id"] == "configurable"
    assert second.agent["active_choice"]["field_name"] == "dynamic-choice"


class StaleActiveLookupCartRepository(MemoryCartRepository):
    """Models two creators that both observed no active cart."""

    def __init__(self):
        super().__init__()
        self.create_attempts = 0

    def find_active_by_session(self, *_args):
        return None

    def create(self, cart):
        self.create_attempts += 1
        if cart["cart_id"] in self.data:
            raise CartCreationConflictError("cart already exists")
        super().create(cart)


def test_same_contract_retry_resumes_one_cart_identity():
    service, carts, _ = build_services()

    first = service.start_item_customization(
        "user", "session", "configurable",
        creation_idempotency_key="contract-1:1",
    )
    retry = service.start_item_customization(
        "user", "session", "configurable",
        creation_idempotency_key="contract-1:1",
    )

    assert first.data["cart_id"] == retry.data["cart_id"]
    assert len(carts.data) == 1
    assert first.grounding.transactional_effects == ["item_selected"]
    assert retry.grounding.transactional_effects == ["item_selected"]


def test_contract_backed_initial_cart_creation_is_idempotent_after_stale_lookup():
    service, _, _ = build_services()
    carts = StaleActiveLookupCartRepository()
    service.carts = carts

    first = service.start_item_customization(
        "user", "session", "configurable",
        creation_idempotency_key="contract-1:1",
    )
    retry = service.start_item_customization(
        "user", "session", "configurable",
        creation_idempotency_key="contract-1:1",
    )

    assert first.success and retry.success
    assert first.data["cart_id"] == retry.data["cart_id"]
    assert len(carts.data) == 1
    assert carts.create_attempts == 2
    assert first.grounding.transactional_effects == ["item_selected"]
    assert retry.grounding.transactional_effects == ["item_selected"]


def _cart_in_status(status):
    service, carts, _ = build_services()
    key = "contract-1:1"
    started = service.start_item_customization(
        "user", "session", "configurable",
        creation_idempotency_key=key,
    )
    if status != "customizing_item":
        progressed = service.save_choice(
            "user", started.data["cart_item_id"],
            "dynamic-choice", "choice-a",
        )
        if status in {"awaiting_upsell_decision", "cart_ready"}:
            progressed = service.handle_upsell(
                "user", progressed.data["cart_id"], "get_options"
            )
        if status == "cart_ready":
            progressed = service.handle_upsell(
                "user", progressed.data["cart_id"], "skip"
            )
        assert progressed.data["status"] == status
    return service, carts, key, started.data["cart_id"]


@pytest.mark.parametrize(
    "status",
    [
        "customizing_item",
        "item_ready",
        "awaiting_upsell_decision",
        "cart_ready",
    ],
)
def test_same_contract_replay_emits_selection_without_mutating_cart(status):
    service, carts, key, cart_id = _cart_in_status(status)
    before = deepcopy(carts.data[cart_id])

    replay = service.start_item_customization(
        "user", "session", "configurable",
        creation_idempotency_key=key,
    )

    assert replay.data["cart_id"] == cart_id
    assert replay.grounding.transactional_effects == ["item_selected"]
    assert carts.data[cart_id] == before
    assert len(carts.data) == 1


def test_cart_created_same_contract_replay_emits_selection_without_duplication():
    service, carts, _ = build_services()
    key = "contract-1:1"
    first = service.start_item_customization(
        "user", "session", "configurable", quantity=2,
        creation_idempotency_key=key,
    )
    before = deepcopy(carts.data[first.data["cart_id"]])

    replay = service.start_item_customization(
        "user", "session", "configurable", quantity=2,
        creation_idempotency_key=key,
    )

    assert replay.data["status"] == "cart_created"
    assert replay.grounding.transactional_effects == ["item_selected"]
    assert carts.data[first.data["cart_id"]] == before
    assert len(carts.data) == 1


def test_unrelated_active_cart_remains_effect_free_for_contract_selection():
    service, carts, _ = build_services()
    unrelated = service.start_item_customization(
        "user", "session", "configurable"
    )

    replay = service.start_item_customization(
        "user", "session", "configurable",
        creation_idempotency_key="contract-1:1",
    )

    assert replay.data["cart_id"] == unrelated.data["cart_id"]
    assert replay.grounding.transactional_effects == []
    assert len(carts.data) == 1


def test_same_deterministic_cart_with_source_item_mismatch_has_no_replay_effect():
    service, carts, _ = build_services()
    key = "contract-1:1"
    started = service.start_item_customization(
        "user", "session", "configurable",
        creation_idempotency_key=key,
    )
    carts.data[started.data["cart_id"]]["source_item_id"] = "addon"

    replay = service.start_item_customization(
        "user", "session", "configurable",
        creation_idempotency_key=key,
    )

    assert replay.grounding.transactional_effects == []


def test_same_deterministic_terminal_cart_has_no_replay_effect():
    service, _, _ = build_services()
    carts = StaleActiveLookupCartRepository()
    service.carts = carts
    key = "contract-1:1"
    first = service.start_item_customization(
        "user", "session", "configurable",
        creation_idempotency_key=key,
    )
    carts.data[first.data["cart_id"]]["status"] = "cancelled"

    replay = service.start_item_customization(
        "user", "session", "configurable",
        creation_idempotency_key=key,
    )

    assert replay.grounding.transactional_effects == []


@pytest.mark.parametrize(
    ("cart_user", "cart_session"),
    [("wrong-user", "session"), ("user", "wrong-session")],
)
def test_deterministic_cart_with_wrong_owner_or_session_has_no_replay_effect(
    cart_user, cart_session,
):
    service, carts, _ = build_services()
    key = "contract-1:1"
    original = service.start_item_customization(
        "user", "session", "configurable",
        creation_idempotency_key=key,
    )
    spoofed = deepcopy(carts.data[original.data["cart_id"]])
    spoofed["user_id"] = cart_user
    spoofed["agent_session_id"] = cart_session

    class SpoofedActiveRepository(MemoryCartRepository):
        def find_active_by_session(self, *_args):
            return deepcopy(spoofed)

    service.carts = SpoofedActiveRepository()
    replay = service.start_item_customization(
        "user", "session", "configurable",
        creation_idempotency_key=key,
    )

    assert replay.grounding.transactional_effects == []


def test_contract_tool_replay_preserves_effect_consumption_and_grounding(
    monkeypatch,
):
    service, carts, _ = build_services()
    now = datetime.now(timezone.utc)
    contract = OptionContract(
        contract_id="contract-1",
        contract_version=1,
        required_effect="item_selected",
        consumer_capability="start_cart_item_customization",
        source_capability="search_menu",
        source_request_id="request-1",
        options=[{"id": "configurable", "label": "Configured Item"}],
        created_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=30)).isoformat(),
    )

    class Sessions:
        def validate_option_contract_consumption(self, *_args, **_kwargs):
            return contract

    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(carts=service, agent_sessions=Sessions()),
    )
    results = []
    recorded_calls = []
    for request_id in ("request-1", "request-2"):
        context = AgentRequestContext(
            "user", "session", customer_id="user", channel="whatsapp",
            request_id=request_id,
        )
        with request_context(context):
            results.append(tools.whatsapp_start_cart_item_customization(
                item_id="configurable",
                contract_id=contract.contract_id,
                contract_version=contract.contract_version,
                selected_option_id="configurable",
            ))
        recorded_calls.append(context.tool_calls[0])

    assert len(carts.data) == 1
    assert len(carts.data[next(iter(carts.data))]["items"]) == 1
    for result, call in zip(results, recorded_calls):
        evidence = result["grounding"]
        assert evidence["transactional_effects"] == ["item_selected"]
        assert evidence["option_contract_consumption"]["effect"] == (
            "item_selected"
        )
        assert call["result"] == result
        assert _supported_effects([call]) == {"item_selected"}
        assert evidence["option_contract_proposal"]["consumer_capability"] == (
            "save_customization_choice"
        )

    grounded = ground_authoritative_tool_response(
        tool_calls=[recorded_calls[-1]],
        expected_write_tool="start_cart_item_customization",
        required_effect="item_selected",
    )
    assert grounded is not None
    assert grounded.required_next_effect == "customization_saved"

    class PersistenceSessions:
        def __init__(self):
            self.active = contract
            self.transitions = []

        def get_active_option_contract(self, *_args):
            return self.active

        def transition_option_contract(self, *_args, **kwargs):
            self.transitions.append(kwargs)
            self.active = kwargs["successor"]

    sessions = PersistenceSessions()
    forbidden = lambda: (_ for _ in ()).throw(AssertionError("not used"))
    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(agent_sessions=sessions),
        agent_client_provider=forbidden,
        identity_resolver=forbidden,
        response_builder=forbidden,
    )
    processor._persist_whatsapp_grounding_state(
        SimpleNamespace(
            channel="whatsapp", customer_id="user", user_id="user",
            agent_session_id="session",
        ),
        SimpleNamespace(raw_result={
            "option_contract_protocol_version": 1,
            "required_effect": grounded.required_next_effect,
            "tool_calls": [recorded_calls[-1]],
        }),
    )

    assert sessions.transitions[-1]["expected"] == contract
    assert sessions.transitions[-1]["successor"].required_effect == (
        "customization_saved"
    )


def test_different_validated_contract_keys_derive_different_cart_identities():
    service, _, _ = build_services()
    carts = StaleActiveLookupCartRepository()
    service.carts = carts

    first = service.start_item_customization(
        "user", "session", "configurable",
        creation_idempotency_key="contract-1:1",
    )
    second = service.start_item_customization(
        "user", "session", "addon",
        creation_idempotency_key="contract-2:1",
    )

    assert first.data["cart_id"] != second.data["cart_id"]
    assert len(carts.data) == 2


def test_web_initial_cart_creation_without_contract_remains_non_idempotent():
    service, _, _ = build_services()
    carts = StaleActiveLookupCartRepository()
    service.carts = carts

    first = service.start_item_customization("user", "session", "addon")
    second = service.start_item_customization("user", "session", "addon")

    assert first.data["cart_id"] != second.data["cart_id"]
    assert len(carts.data) == 2


def test_contract_create_conflict_without_visible_cart_fails_deterministically():
    service, _, _ = build_services()

    class ConflictingRepository(StaleActiveLookupCartRepository):
        def create(self, _cart):
            raise CartCreationConflictError("cart already exists")

    service.carts = ConflictingRepository()
    response = service.start_item_customization(
        "user", "session", "addon",
        creation_idempotency_key="contract-1:1",
    )

    assert response.success is False
    assert response.error_code == "CART_CREATE_CONFLICT"
    assert response.retryable is True


def test_size_choice_includes_authoritative_prices():
    service, _, _ = build_services()

    response = service.start_item_customization(
        "user",
        "session",
        "priced-pizza",
    )

    active_choice = response.agent["active_choice"]

    assert active_choice["options"][0]["display_label"] == "Small - PKR 850"
    assert active_choice["options"][1]["display_label"] == "Medium - PKR 1,700"
    assert active_choice["options"][2]["display_label"] == "Large - PKR 2,400"
    assert active_choice["choice_prompt"] == (
        "Which pizza size would you like?\n"
        "\n"
        "1. Small - PKR 850\n"
        "2. Medium - PKR 1,700\n"
        "3. Large - PKR 2,400"
    )
    assert response.user_message == active_choice["choice_prompt"]
    assert response.grounding.authoritative_domains == ["cart", "menu"]
    assert response.grounding.exact_customer_text == active_choice["choice_prompt"]


def test_crust_choice_includes_authoritative_price_deltas():
    service, _, _ = build_services()

    started = service.start_item_customization(
        "user",
        "session",
        "priced-pizza",
    )

    response = service.save_choice(
        "user",
        started.data["cart_item_id"],
        "pizza-size",
        "medium",
    )

    active_choice = response.agent["active_choice"]

    assert active_choice["options"][0]["display_label"] == (
        "Regular Crust - No additional charge"
    )
    assert active_choice["options"][1]["display_label"] == (
        "Crunchy Thin Crust - No additional charge"
    )
    assert active_choice["options"][2]["display_label"] == (
        "Mozzarella Stuffed Crust - Additional PKR 350"
    )
    assert active_choice["options"][3]["display_label"] == (
        "Cheese Burst Crust - Additional PKR 500"
    )
    assert response.user_message == active_choice["choice_prompt"]


def test_upsell_prompt_lists_backend_items_and_prices():
    service, _, _ = build_services()

    started = service.start_item_customization(
        "user",
        "session",
        "configurable",
    )
    ready = service.save_choice(
        "user",
        started.data["cart_item_id"],
        "dynamic-choice",
        "choice-a",
    )

    response = service.handle_upsell(
        "user",
        ready.data["cart_id"],
        "get_options",
    )

    assert response.agent["upsell_items"][0]["display_label"] == (
        "Configured Add-on - CUR 4"
    )
    assert response.agent["upsell_items"][1]["display_label"] == (
        "Configurable Add-on - From CUR 5"
    )
    assert response.agent["upsell_prompt"] == (
        "Would you like to add anything?\n"
        "\n"
        "1. Configured Add-on - CUR 4\n"
        "2. Configurable Add-on - From CUR 5\n"
        "\n"
        "You can choose one add-on or proceed to checkout."
    )
    assert response.user_message == response.agent["upsell_prompt"]
    assert response.grounding.exact_customer_text == response.agent["upsell_prompt"]


def test_cart_mutations_require_the_cart_owner():
    service, carts, _ = build_services()
    started = service.start_item_customization(
        "customer-a", "session-a", "configurable", 2
    )
    cart_id = started.data["cart_id"]

    mode_response = service.set_customization_mode(
        "customer-b", cart_id, "same"
    )
    item_id = carts.find_by_cart_id("customer-a", cart_id)["cart_item_ids"]
    assert mode_response.error_code == "CART_NOT_FOUND"
    assert item_id == []

    owner_mode = service.set_customization_mode("customer-a", cart_id, "same")
    choice_response = service.save_choice(
        "customer-b",
        owner_mode.data["cart_item_id"],
        "dynamic-choice",
        "choice-a",
    )
    checkout_response = service.create_pending_order("customer-b", cart_id)

    assert choice_response.error_code == "CART_NOT_FOUND"
    assert checkout_response.error_code == "CART_NOT_FOUND"


def test_discard_active_cart_marks_only_owned_session_cart_cancelled():
    service, carts, _ = build_services()
    started = service.start_item_customization(
        "customer-a", "session-a", "configurable"
    )

    wrong_session = service.discard_active_cart("customer-a", "session-b")
    discarded = service.discard_active_cart("customer-a", "session-a")

    assert wrong_session.data["discarded"] is False
    assert discarded.data == {
        "discarded": True,
        "cart_id": started.data["cart_id"],
        "status": "cancelled",
    }
    assert carts.find_by_cart_id(
        "customer-a", started.data["cart_id"]
    )["status"] == "cancelled"


def test_repeated_checkout_for_same_cart_creates_one_order():
    service, _, orders = build_services()
    started = service.start_item_customization("user", "session", "configurable")
    ready = service.save_choice(
        "user", started.data["cart_item_id"], "dynamic-choice", "choice-a"
    )

    first = service.create_pending_order("user", ready.data["cart_id"])
    second = service.create_pending_order("user", ready.data["cart_id"])

    assert first.success and second.success
    assert first.data["order_id"] == second.data["order_id"]
    assert len(orders.data) == 1


def test_repeated_checkout_reports_the_real_advanced_order_state():
    service, _, orders = build_services()
    started = service.start_item_customization("user", "session", "configurable")
    ready = service.save_choice(
        "user", started.data["cart_item_id"], "dynamic-choice", "choice-a"
    )
    pending = service.create_pending_order("user", ready.data["cart_id"])
    order_id = pending.data["order_id"]
    service.order_service.update_order_flow("user", order_id, "set_takeaway")
    service.order_service.update_order_flow("user", order_id, "confirm")

    repeated = service.create_pending_order("user", ready.data["cart_id"])

    assert len(orders.data) == 1
    assert repeated.data["status"] == "submitted_to_restaurant"
    assert repeated.next_action == "await_restaurant_update"
    assert order_id in repeated.user_message


def test_multi_select_choices_are_validated_and_fully_priced():
    service, _, orders = build_services()
    service.menu.groups["toppings"] = {
        "option_group_id": "toppings",
        "name": "Toppings",
        "type": "multi_select",
        "required": True,
        "min_select": 1,
        "max_select": 2,
        "question": "Choose up to two toppings",
        "options": [
            {"option_id": "cheese", "name": "Cheese", "price_delta": 2},
            {"option_id": "olives", "name": "Olives", "price_delta": 3},
            {"option_id": "jalapeno", "name": "Jalapeno", "price_delta": 4},
        ],
    }
    service.menu.items["multi-item"] = {
        "product_id": "multi-item",
        "name": "Multi Item",
        "category": "dynamic",
        "currency": "CUR",
        "available": True,
        "starting_price": 10,
        "customization_group_ids": ["toppings"],
        "upsell_group_ids": [],
    }
    started = service.start_item_customization("user", "session", "multi-item")

    invalid = service.save_choice(
        "user",
        started.data["cart_item_id"],
        "toppings",
        ["cheese", "cheese"],
    )
    ready = service.save_choice(
        "user",
        started.data["cart_item_id"],
        "toppings",
        ["cheese", "olives"],
    )
    pending = service.create_pending_order("user", ready.data["cart_id"])

    assert invalid.error_code == "INVALID_OPTION_COUNT"
    assert ready.data["items"][0]["current_price"] == 15
    assert pending.data["total"] == 15
    assert next(iter(orders.data.values()))["total"] == 15


def test_optional_single_select_can_reach_takeaway_without_a_selection():
    service, carts, orders = build_services()
    service.menu.groups["optional-dip"] = {
        "option_group_id": "optional-dip",
        "name": "Dip Choice",
        "type": "single_select",
        "required": False,
        "question": "Would you like a dip?",
        "options": [
            {"option_id": "garlic", "name": "Garlic Dip", "price_delta": 2},
        ],
    }
    service.menu.items["customizable-roll"] = {
        "product_id": "customizable-roll",
        "name": "Customizable Roll",
        "category": "roll",
        "currency": "CUR",
        "available": True,
        "starting_price": 10,
        "requires_customization": True,
        "customization_group_ids": ["optional-dip"],
        "upsell_group_ids": [],
    }

    started = service.start_item_customization(
        "optional-user",
        "optional-session",
        "customizable-roll",
    )
    cart_id = started.data["cart_id"]
    pending = service.create_pending_order("optional-user", cart_id)
    takeaway = service.order_service.update_order_flow(
        "optional-user",
        pending.data["order_id"],
        "set_takeaway",
    )

    assert started.success
    assert started.data["status"] == "item_ready"
    assert started.data["items"][0]["selected_options"] == {}
    assert started.data["items"][0]["missing_required_fields"] == []
    assert pending.success
    assert pending.data["status"] == "awaiting_fulfillment_method"
    assert carts.find_by_cart_id("optional-user", cart_id)["status"] == (
        "converted_to_order"
    )
    assert takeaway.success
    assert takeaway.data["status"] == "pending_confirmation"
    assert next(iter(orders.data.values()))["total"] == 10
