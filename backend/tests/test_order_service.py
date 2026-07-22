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
    assert addressed.user_message == addressed.agent["confirmation_summary"]
    assert "Fulfilment: Delivery" in addressed.user_message
    assert "Delivery address: Configured address" in addressed.user_message
    assert "Should I confirm this order?" in addressed.user_message
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


def test_takeaway_confirmation_summary_uses_authoritative_prices():
    menu = MemoryMenuRepository(
        [
            {
                "product_id": "pizza",
                "name": "Pepperoni Pizza",
                "available": True,
                "starting_price": 1250,
                "customization_group_ids": [],
            },
            {
                "product_id": "wings",
                "name": "12 Pcs Chicken Wings",
                "available": True,
                "starting_price": 1300,
                "customization_group_ids": [],
            },
        ],
        [],
    )
    repository = MemoryOrderRepository()
    service = OrderService(repository, menu)

    pending = service.create_pending_from_cart(
        {
            "user_id": "user",
            "agent_session_id": "session",
            "restaurant_id": "restaurant",
            "branch_id": "branch",
            "cart_id": "cart",
            "subtotal": 1,
            "currency": "PKR",
            "items": [
                {
                    "item_id": "pizza",
                    "name": "Pepperoni Pizza",
                    "quantity": 2,
                    "selected_options": {},
                    "current_price": 1,
                },
                {
                    "item_id": "wings",
                    "name": "12 Pcs Chicken Wings",
                    "quantity": 1,
                    "selected_options": {},
                    "current_price": 1,
                },
            ],
        }
    )

    response = service.update_order_flow(
        pending.data["order_id"],
        "set_takeaway",
    )

    assert response.success
    assert response.data["status"] == "pending_confirmation"
    assert response.data["items"][0]["unit_price"] == 1250
    assert response.data["items"][0]["line_total"] == 2500
    assert response.data["items"][1]["unit_price"] == 1300
    assert response.data["items"][1]["line_total"] == 1300
    assert response.data["subtotal"] == 3800
    assert response.data["total"] == 3800
    assert response.user_message == response.agent["confirmation_summary"]
    assert "1) Pepperoni Pizza" in response.user_message
    assert "Quantity: 2" in response.user_message
    assert "Unit price: Rs 1,250.00" in response.user_message
    assert "Item total: Rs 2,500.00" in response.user_message
    assert "2) 12 Pcs Chicken Wings" in response.user_message
    assert "Subtotal: Rs 3,800.00" in response.user_message
    assert "Grand total: Rs 3,800.00" in response.user_message
    assert "Fulfilment: Takeaway" in response.user_message
    assert "Delivery address:" not in response.user_message
    assert response.user_message.endswith(
        "Should I confirm this order?"
    )


def test_price_change_requires_customer_reconfirmation_before_submission():
    menu = MemoryMenuRepository(
        [
            {
                "product_id": "item",
                "name": "Menu Item",
                "available": True,
                "starting_price": 1000,
                "customization_group_ids": [],
            }
        ],
        [],
    )
    repository = MemoryOrderRepository()
    service = OrderService(repository, menu)

    pending = service.create_pending_from_cart(
        {
            "user_id": "user",
            "agent_session_id": "session",
            "restaurant_id": "restaurant",
            "branch_id": "branch",
            "cart_id": "cart",
            "subtotal": 1000,
            "currency": "PKR",
            "items": [
                {
                    "item_id": "item",
                    "name": "Menu Item",
                    "quantity": 1,
                    "selected_options": {},
                    "current_price": 1000,
                }
            ],
        }
    )
    order_id = pending.data["order_id"]

    initial_summary = service.update_order_flow(
        order_id,
        "set_takeaway",
    )

    assert initial_summary.data["status"] == "pending_confirmation"
    assert initial_summary.data["total"] == 1000
    assert "Grand total: Rs 1,000.00" in initial_summary.user_message

    menu.items["item"]["starting_price"] = 1200

    changed_price = service.update_order_flow(
        order_id,
        "confirm",
        idempotency_key="confirm-key",
    )

    assert changed_price.success
    assert changed_price.data["status"] == "pending_confirmation"
    assert changed_price.data["total"] == 1200
    assert changed_price.next_action == "confirm_or_cancel"
    assert changed_price.agent["required_input"] == "confirm_or_cancel"
    assert changed_price.user_message == changed_price.agent["confirmation_summary"]
    assert "Grand total: Rs 1,200.00" in changed_price.user_message
    assert "Should I confirm this order?" in changed_price.user_message
    assert repository.get_by_order_id(order_id)["status"] == "pending_confirmation"
    assert repository.get_by_order_id(order_id)["idempotency_keys"] == []

    submitted = service.update_order_flow(
        order_id,
        "confirm",
        idempotency_key="confirm-key",
    )

    assert submitted.success
    assert submitted.data["status"] == "submitted_to_restaurant"
    assert submitted.data["total"] == 1200
    assert repository.get_by_order_id(order_id)["status"] == "submitted_to_restaurant"
def _build_customer_tracking_service():
    menu = MemoryMenuRepository(
        [
            {
                "product_id": "item",
                "name": "Item",
                "available": True,
                "starting_price": 1000,
                "customization_group_ids": [],
            }
        ],
        [],
    )
    repository = MemoryOrderRepository()
    service = OrderService(repository, menu)

    def create_order(
        user_id="user",
        session_id="session",
        cart_id="cart",
    ):
        return service.create_pending_from_cart(
            {
                "user_id": user_id,
                "agent_session_id": session_id,
                "restaurant_id": "restaurant",
                "branch_id": "branch",
                "cart_id": cart_id,
                "subtotal": 1000,
                "currency": "PKR",
                "items": [
                    {
                        "item_id": "item",
                        "name": "Item",
                        "quantity": 1,
                        "selected_options": {},
                        "current_price": 1000,
                    }
                ],
            }
        ).data["order_id"]

    return service, repository, create_order


def test_successful_confirmation_returns_customer_order_tracking_message():
    service, _, create_order = _build_customer_tracking_service()
    order_id = create_order()

    service.update_order_flow(order_id, "set_takeaway")
    response = service.update_order_flow(
        order_id,
        "confirm",
        idempotency_key="confirm-key",
    )

    expected = (
        "Your order has been confirmed and sent to the restaurant.\n"
        "\n"
        f"Order ID: {order_id}\n"
        "Status: Submitted to restaurant\n"
        "\n"
        "Please keep this Order ID for tracking."
    )

    assert response.success
    assert response.data["status"] == "submitted_to_restaurant"
    assert response.user_message == expected
    assert response.agent["submission_confirmation"] == expected
    assert response.agent["status_message"] == (
        f"Order ID: {order_id}\n"
        "Status: Submitted to restaurant"
    )


def test_duplicate_confirmation_repeats_customer_tracking_message():
    service, _, create_order = _build_customer_tracking_service()
    order_id = create_order()

    service.update_order_flow(order_id, "set_takeaway")

    submitted = service.update_order_flow(
        order_id,
        "confirm",
        idempotency_key="confirm-key",
    )
    duplicate = service.update_order_flow(
        order_id,
        "confirm",
        idempotency_key="confirm-key",
    )

    assert duplicate.success
    assert duplicate.user_message == submitted.user_message
    assert duplicate.agent["submission_confirmation"] == (
        submitted.agent["submission_confirmation"]
    )
    assert order_id in duplicate.user_message


def test_single_active_order_is_selected_without_requesting_order_id():
    service, _, create_order = _build_customer_tracking_service()
    order_id = create_order()

    response = service.get_order_status("user")

    assert response.success
    assert response.agent["tracking_state"] == "single_active_order"
    assert response.agent["selected_order_id"] == order_id
    assert response.agent["requires_order_id"] is False
    assert response.user_message == (
        f"Order ID: {order_id}\n"
        "Status: Awaiting fulfillment method"
    )
    assert response.agent["status_message"] == response.user_message


def test_multiple_active_orders_require_customer_to_choose_order_id():
    service, _, create_order = _build_customer_tracking_service()

    first_order_id = create_order(
        session_id="session-1",
        cart_id="cart-1",
    )
    second_order_id = create_order(
        session_id="session-2",
        cart_id="cart-2",
    )

    response = service.get_order_status("user")

    assert response.success
    assert response.agent["tracking_state"] == "multiple_active_orders"
    assert response.agent["requires_order_id"] is True
    assert response.agent["required_input"] == "order_id"
    assert first_order_id in response.user_message
    assert second_order_id in response.user_message
    assert "Please provide the Order ID" in response.user_message


def test_explicit_older_order_id_returns_status_for_owner():
    service, repository, create_order = _build_customer_tracking_service()
    order_id = create_order()

    repository.data[order_id]["status"] = "completed"

    response = service.get_order_status(
        "user",
        order_id,
    )

    assert response.success
    assert response.agent["tracking_state"] == "specific_order"
    assert response.agent["selected_order_id"] == order_id
    assert response.user_message == (
        f"Order ID: {order_id}\n"
        "Status: Completed"
    )
    assert response.agent["status_message"] == response.user_message


def test_explicit_order_id_does_not_reveal_another_customers_order():
    service, _, create_order = _build_customer_tracking_service()
    order_id = create_order(user_id="customer-a")

    response = service.get_order_status(
        "customer-b",
        order_id,
    )

    assert not response.success
    assert response.error_code == "ORDER_NOT_FOUND"
    assert response.user_message == "I couldn't find that order."
