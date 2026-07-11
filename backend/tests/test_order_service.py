from src.services.order_service import OrderService
from fakes import MemoryMenuRepository, MemoryOrderRepository


def test_delivery_flow_and_duplicate_idempotency():
    menu = MemoryMenuRepository(
        [{"product_id": "item", "name": "Item", "available": True,
          "starting_price": 10, "customization_group_ids": []}], []
    )
    repository = MemoryOrderRepository()
    service = OrderService(repository, menu)
    cart = {"user_id": "user", "agent_session_id": "session", "restaurant_id": "restaurant",
            "branch_id": "branch", "cart_id": "cart", "subtotal": 10, "currency": "CUR",
            "items": [{"item_id": "item", "name": "Item", "quantity": 1,
                       "selected_options": {}, "current_price": 10}]}
    pending = service.create_pending_from_cart(cart)
    order_id = pending.data["order_id"]
    assert pending.data["status"] == "awaiting_fulfillment_method"
    assert pending.agent["required_input"] == "fulfillment_method"
    assert pending.agent["valid_next_actions"] == [
        "update_order_flow:set_delivery",
        "update_order_flow:set_takeaway",
        "update_order_flow:cancel",
    ]
    delivery = service.update_order_flow(order_id, "set_delivery")
    assert delivery.agent["required_input"] == "delivery_address"
    addressed = service.update_order_flow(order_id, "save_address", "Configured address")
    assert addressed.data["status"] == "pending_confirmation"
    assert addressed.agent["required_input"] == "confirm_or_cancel"
    submitted = service.update_order_flow(order_id, "confirm", idempotency_key="key")
    duplicate = service.update_order_flow(order_id, "confirm", idempotency_key="key")
    assert submitted.data["status"] == "submitted_to_restaurant"
    assert submitted.agent["next_action"] == "await_restaurant_update"
    assert duplicate.success and duplicate.data["version"] == submitted.data["version"]


def test_takeaway_skips_address():
    menu = MemoryMenuRepository(
        [{"product_id": "item", "name": "Item", "available": True,
          "starting_price": 10, "customization_group_ids": []}], []
    )
    repository = MemoryOrderRepository()
    service = OrderService(repository, menu)
    order_id = service.create_pending_from_cart(
        {"user_id": "user", "agent_session_id": "session", "restaurant_id": "restaurant",
         "branch_id": "branch", "cart_id": "cart", "subtotal": 10, "currency": "CUR",
         "items": [{"item_id": "item", "name": "Item", "quantity": 1,
                    "selected_options": {}, "current_price": 10}]}
    ).data["order_id"]
    response = service.update_order_flow(order_id, "set_takeaway")
    assert response.data["status"] == "pending_confirmation"
    assert response.agent["required_input"] == "confirm_or_cancel"


def test_legacy_submit_action_is_not_supported():
    menu = MemoryMenuRepository(
        [{"product_id": "item", "name": "Item", "available": True,
          "starting_price": 10, "customization_group_ids": []}], []
    )
    repository = MemoryOrderRepository()
    service = OrderService(repository, menu)
    order_id = service.create_pending_from_cart(
        {"user_id": "user", "agent_session_id": "session", "restaurant_id": "restaurant",
         "branch_id": "branch", "cart_id": "cart", "subtotal": 10, "currency": "CUR",
         "items": [{"item_id": "item", "name": "Item", "quantity": 1,
                    "selected_options": {}, "current_price": 10}]}
    ).data["order_id"]
    service.update_order_flow(order_id, "set_takeaway")

    response = service.update_order_flow(order_id, "submit")

    assert not response.success
    assert response.error_code == "INVALID_ORDER_STATE"
    assert repository.get_by_order_id(order_id)["status"] == "pending_confirmation"


def test_order_status_response_includes_agent_active_order_guidance():
    menu = MemoryMenuRepository(
        [{"product_id": "item", "name": "Item", "available": True,
          "starting_price": 10, "customization_group_ids": []}], []
    )
    repository = MemoryOrderRepository()
    service = OrderService(repository, menu)
    order_id = service.create_pending_from_cart(
        {"user_id": "user", "agent_session_id": "session", "restaurant_id": "restaurant",
         "branch_id": "branch", "cart_id": "cart", "subtotal": 10, "currency": "CUR",
         "items": [{"item_id": "item", "name": "Item", "quantity": 1,
                    "selected_options": {}, "current_price": 10}]}
    ).data["order_id"]

    response = service.get_order_status("user")

    assert response.agent["entity"] == "orders"
    assert response.agent["orders"] == [{
        "order_id": order_id,
        "status": "awaiting_fulfillment_method",
        "next_action": "ask_fulfillment_method",
        "required_input": "fulfillment_method",
    }]
