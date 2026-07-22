from types import SimpleNamespace

from src.services.cart_service import CartService
from src.services.order_service import OrderService
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
    response = service.set_customization_mode(cart_id, "same")
    assert len(response.data["items"]) == 1
    assert response.data["items"][0]["quantity"] == 2


def test_two_separate_items_are_labeled_and_advanced():
    service, _, _ = build_services()
    cart_id = service.start_item_customization("user", "session", "configurable", 2).data["cart_id"]
    first = service.set_customization_mode(cart_id, "separate")
    first_id = first.data["cart_item_id"]
    second = service.save_choice(first_id, "dynamic-choice", "choice-a")
    assert [item["quantity"] for item in second.data["items"]] == [1, 1]
    assert second.data["label"] == "Item 2 of 2"


def test_upsell_then_pending_order_reprices_server_side():
    service, carts, orders = build_services()
    started = service.start_item_customization(
        "user", "session", "configurable",
        customer_id="customer-1", customer_name="Ava", customer_phone="+923001234567",
    )
    ready = service.save_choice(started.data["cart_item_id"], "dynamic-choice", "choice-a")
    cart_id = ready.data["cart_id"]
    options = service.handle_upsell(cart_id, "get_options")
    assert options.data["upsell_items"][0]["product_id"] == "addon"
    assert options.data["items"][0]["item_id"] == "configurable"
    assert options.agent["next_action"] == "choose_upsell"
    assert options.agent["upsell_items"][0]["product_id"] == "addon"
    added = service.handle_upsell(cart_id, "add_item", "addon")
    assert added.next_action == "create_pending_order"
    assert added.data["status"] == "cart_ready"
    pending = service.create_pending_order(cart_id)
    assert pending.success
    assert pending.data["status"] == "awaiting_fulfillment_method"
    assert carts.find_by_cart_id(cart_id)["status"] == "converted_to_order"
    assert service.get_active_cart("user", "session").data["cart"] is None
    order = next(iter(orders.data.values()))
    assert order["total"] == 19
    assert order["customer_id"] == "customer-1"
    assert order["customer_name"] == "Ava"
    assert order["customer_phone"] == "+923001234567"


def test_checkout_auto_skips_pending_upsell_decision():
    service, carts, orders = build_services()
    started = service.start_item_customization("user", "session", "configurable")
    ready = service.save_choice(started.data["cart_item_id"], "dynamic-choice", "choice-a")
    service.handle_upsell(ready.data["cart_id"], "get_options")

    pending = service.create_pending_order(ready.data["cart_id"])

    assert pending.success
    assert pending.data["status"] == "awaiting_fulfillment_method"
    saved = carts.find_by_cart_id(ready.data["cart_id"])
    assert saved["status"] == "converted_to_order"
    assert next(iter(orders.data.values()))["items"][0]["name"] == "Configured Item"


def test_legacy_pending_confirmation_cart_is_not_returned_as_active_cart():
    service, carts, orders = build_services()
    started = service.start_item_customization("user", "session", "configurable")
    ready = service.save_choice(started.data["cart_item_id"], "dynamic-choice", "choice-a")
    pending = service.create_pending_order(ready.data["cart_id"])
    assert pending.success
    legacy_cart = carts.find_by_cart_id(ready.data["cart_id"])
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
    ready = service.save_choice(started.data["cart_item_id"], "dynamic-choice", "choice-a")
    options = service.handle_upsell(ready.data["cart_id"], "get_options")

    assert [item["product_id"] for item in options.data["upsell_items"]] == [
        "addon", "addon-configurable"
    ]

    upsell_choice = service.handle_upsell(ready.data["cart_id"], "add_item", "addon-configurable")
    assert upsell_choice.next_action == "ask_customization_choice"
    assert upsell_choice.data["field_name"] == "dynamic-choice"
    assert upsell_choice.agent["active_choice"]["field_name"] == "dynamic-choice"
    assert upsell_choice.agent["valid_next_actions"] == ["save_customization_choice"]

    upsell_ready = service.save_choice(
        upsell_choice.data["cart_item_id"], "dynamic-choice", "choice-a"
    )
    assert upsell_ready.next_action == "create_pending_order"
    assert upsell_ready.data["status"] == "cart_ready"

    pending = service.create_pending_order(ready.data["cart_id"])
    assert pending.success
    assert next(iter(orders.data.values()))["total"] == 20


def test_add_item_to_active_cart_appends_to_existing_cart():
    service, carts, _ = build_services()
    started = service.start_item_customization("user", "session", "configurable")
    ready = service.save_choice(started.data["cart_item_id"], "dynamic-choice", "choice-a")

    added = service.add_item_to_active_cart("user", "session", "addon")

    assert added.success
    saved = carts.find_by_cart_id(ready.data["cart_id"])
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


def test_crust_choice_includes_authoritative_price_deltas():
    service, _, _ = build_services()

    started = service.start_item_customization(
        "user",
        "session",
        "priced-pizza",
    )

    response = service.save_choice(
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
        started.data["cart_item_id"],
        "dynamic-choice",
        "choice-a",
    )

    response = service.handle_upsell(
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
