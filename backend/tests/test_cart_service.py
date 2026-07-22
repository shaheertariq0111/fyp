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
        ],
        groups=[{"option_group_id": "dynamic-choice", "name": "Dynamic", "type": "single_select",
                 "required": True, "question": "Choose dynamically", "options": [
                    {"option_id": "choice-a", "label": "Choice A", "price_key": "option-a"}
                 ]}],
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
