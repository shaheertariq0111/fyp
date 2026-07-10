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
    service, _, orders = build_services()
    started = service.start_item_customization("user", "session", "configurable")
    ready = service.save_choice(started.data["cart_item_id"], "dynamic-choice", "choice-a")
    cart_id = ready.data["cart_id"]
    options = service.handle_upsell(cart_id, "get_options")
    assert options.data["items"][0]["product_id"] == "addon"
    service.handle_upsell(cart_id, "add_item", "addon")
    service.handle_upsell(cart_id, "skip")
    pending = service.create_pending_order(cart_id)
    assert pending.success
    assert next(iter(orders.data.values()))["total"] == 19


def test_configurable_upsell_is_customized_before_pending_order():
    service, _, orders = build_services()
    started = service.start_item_customization("user", "session", "configurable")
    ready = service.save_choice(started.data["cart_item_id"], "dynamic-choice", "choice-a")
    options = service.handle_upsell(ready.data["cart_id"], "get_options")

    assert [item["product_id"] for item in options.data["items"]] == ["addon", "addon-configurable"]

    upsell_choice = service.handle_upsell(ready.data["cart_id"], "add_item", "addon-configurable")
    assert upsell_choice.next_action == "ask_customization_choice"
    assert upsell_choice.data["field_name"] == "dynamic-choice"

    upsell_ready = service.save_choice(
        upsell_choice.data["cart_item_id"], "dynamic-choice", "choice-a"
    )
    assert upsell_ready.next_action == "offer_upsell"

    service.handle_upsell(ready.data["cart_id"], "skip")
    pending = service.create_pending_order(ready.data["cart_id"])
    assert pending.success
    assert next(iter(orders.data.values()))["total"] == 20
