from src.services.order_service import OrderService
from src.services.customer_service import CustomerService
from fakes import MemoryMenuRepository, MemoryOrderRepository


class MemoryCustomerRepository:
    def __init__(self):
        self.data = {}

    def create(self, customer):
        self.data[customer["customer_id"]] = dict(customer)

    def get(self, customer_id):
        customer = self.data.get(customer_id)
        return dict(customer) if customer else None

    def get_by_phone_hash(self, phone_hash):
        return next(
            (dict(customer) for customer in self.data.values()
             if customer.get("phone_hash") == phone_hash),
            None,
        )

    def save(self, customer):
        self.data[customer["customer_id"]] = dict(customer)


def make_order_service():
    menu = MemoryMenuRepository(
        [{"product_id": "item", "name": "Item", "available": True,
          "starting_price": 10, "customization_group_ids": []}], []
    )
    repository = MemoryOrderRepository()
    return OrderService(repository, menu), repository


def make_cart(user_id="user", cart_id="cart"):
    return {
        "user_id": user_id,
        "agent_session_id": f"session-{cart_id}",
        "restaurant_id": "restaurant",
        "branch_id": "branch",
        "cart_id": cart_id,
        "subtotal": 10,
        "currency": "CUR",
        "items": [{
            "item_id": "item",
            "name": "Item",
            "quantity": 1,
            "selected_options": {},
            "current_price": 10,
        }],
    }


def create_order(service, *, user_id="user", cart_id="cart"):
    return service.create_pending_from_cart(make_cart(user_id=user_id, cart_id=cart_id)).data["order_id"]


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
    assert addressed.data["delivery_address"] == "Configured address"
    assert addressed.agent["order_summary"]["delivery_address"] == "Configured address"
    assert addressed.agent["required_input"] == "confirm_or_cancel"
    submitted = service.update_order_flow(order_id, "confirm", idempotency_key="key")
    duplicate = service.update_order_flow(order_id, "confirm", idempotency_key="key")
    assert submitted.data["status"] == "submitted_to_restaurant"
    assert submitted.data["delivery_address"] == "Configured address"
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


def test_check_active_orders_returns_no_active_orders():
    service, _repository = make_order_service()

    response = service.check_active_orders("user")

    assert response.data["has_active_orders"] is False
    assert response.data["active_order_count"] == 0
    assert response.data["unfinished_orders"] == []
    assert response.data["placed_orders"] == []
    assert response.next_action == "begin_menu_browsing"
    assert response.data["routing_guidance"]["route"] == "begin_new_order"


def test_check_active_orders_returns_one_placed_order_with_immutable_guidance():
    service, _repository = make_order_service()
    order_id = create_order(service)
    service.update_order_flow(order_id, "set_takeaway")
    service.update_order_flow(order_id, "confirm")

    response = service.check_active_orders("user")

    assert response.data["has_active_orders"] is True
    assert response.data["active_order_count"] == 1
    assert response.data["unfinished_orders"] == []
    assert response.data["placed_orders"] == [{
        "order_id": order_id,
        "status": "submitted_to_restaurant",
        "next_action": "await_restaurant_update",
        "required_input": None,
        "item_count": 1,
        "subtotal": 10,
        "total": 10,
        "currency": "CUR",
        "fulfillment_method": "takeaway",
        "updated_at": response.data["placed_orders"][0]["updated_at"],
    }]
    assert "You already have an active order" in response.data["routing_guidance"]["customer_message"]
    assert "Changes cannot be made once an order has been placed" in (
        response.data["routing_guidance"]["customer_message"]
    )
    assert "modify" in response.agent["instruction"]


def test_check_active_orders_uses_plural_guidance_for_multiple_placed_orders():
    service, _repository = make_order_service()
    for index in range(2):
        order_id = create_order(service, cart_id=f"cart-{index}")
        service.update_order_flow(order_id, "set_takeaway")
        service.update_order_flow(order_id, "confirm")

    response = service.check_active_orders("user")

    assert response.data["active_order_count"] == 2
    assert len(response.data["placed_orders"]) == 2
    assert response.data["unfinished_orders"] == []
    assert "active orders" in response.data["routing_guidance"]["customer_message"]
    assert "orders have been placed" in response.data["routing_guidance"]["customer_message"]
    assert "multiple active placed orders" in response.agent["instruction"]


def test_check_active_orders_returns_unfinished_orders_separately():
    service, _repository = make_order_service()
    order_id = create_order(service)

    response = service.check_active_orders("user")

    assert response.data["active_order_count"] == 1
    assert response.data["unfinished_orders"][0]["order_id"] == order_id
    assert response.data["unfinished_orders"][0]["status"] == "awaiting_fulfillment_method"
    assert response.data["placed_orders"] == []
    assert response.next_action == "ask_unfinished_order_choice"
    assert "continue it, cancel it, or start a separate order" in (
        response.data["routing_guidance"]["customer_message"]
    )
    assert "Do not silently resume" in response.agent["instruction"]


def test_check_active_orders_excludes_terminal_orders():
    service, _repository = make_order_service()
    order_id = create_order(service)
    service.update_order_flow(order_id, "cancel")

    response = service.check_active_orders("user")

    assert response.data["has_active_orders"] is False
    assert response.data["unfinished_orders"] == []
    assert response.data["placed_orders"] == []


def test_check_active_orders_isolates_by_user():
    service, _repository = make_order_service()
    other_order_id = create_order(service, user_id="other-user", cart_id="other-cart")
    service.update_order_flow(other_order_id, "set_takeaway")
    service.update_order_flow(other_order_id, "confirm")

    response = service.check_active_orders("user")

    assert response.data["has_active_orders"] is False
    assert response.data["active_order_count"] == 0


def test_saved_customer_address_can_be_reused_as_order_snapshot():
    customers = CustomerService(MemoryCustomerRepository())
    saved = customers.save_address(
        "cust-1",
        address_text="Original delivery address",
        label="Home",
    ).data["address"]
    menu = MemoryMenuRepository(
        [{"product_id": "item", "name": "Item", "available": True,
          "starting_price": 10, "customization_group_ids": []}], []
    )
    repository = MemoryOrderRepository()
    service = OrderService(repository, menu)
    cart = {"user_id": "cust-1", "agent_session_id": "session", "restaurant_id": "restaurant",
            "branch_id": "branch", "cart_id": "cart", "subtotal": 10, "currency": "CUR",
            "customer_id": "cust-1", "customer_name": "Ava", "customer_phone": "+923001234567",
            "items": [{"item_id": "item", "name": "Item", "quantity": 1,
                       "selected_options": {}, "current_price": 10}]}
    order_id = service.create_pending_from_cart(cart).data["order_id"]

    service.update_order_flow(order_id, "set_delivery")
    addressed = service.update_order_flow(order_id, "save_address", saved["address_text"])
    customers.save_address("cust-1", address_text="New default address", label="Office")

    assert addressed.data["delivery_address"] == "Original delivery address"
    assert repository.get_by_order_id(order_id)["delivery_address"] == "Original delivery address"


def test_admin_order_status_transitions_append_history():
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
    service.update_order_flow(order_id, "confirm")

    accepted = service.admin_update_status(order_id, "accept", "Kitchen accepted")
    preparing = service.admin_update_status(order_id, "start_preparing")
    ready = service.admin_update_status(order_id, "mark_ready")

    assert accepted["order"]["status"] == "accepted"
    assert preparing["order"]["status"] == "preparing"
    assert ready["order"]["status"] == "ready_for_pickup"
    assert ready["order"]["status_history"][-1]["action"] == "mark_ready"
    assert ready["order"]["status_history"][0]["reason"] == "Kitchen accepted"


def test_admin_invalid_delivery_specific_transition_is_rejected():
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
    service.update_order_flow(order_id, "confirm")
    service.admin_update_status(order_id, "accept")
    service.admin_update_status(order_id, "start_preparing")

    try:
        service.admin_update_status(order_id, "dispatch")
    except ValueError as exc:
        assert str(exc) == "INVALID_ORDER_STATE"
    else:
        raise AssertionError("dispatch should require a delivery order")
