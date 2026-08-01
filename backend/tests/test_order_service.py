import pytest

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
    delivery = service.update_order_flow("user", order_id, "set_delivery")
    assert delivery.agent["required_input"] == "delivery_address"
    addressed = service.update_order_flow("user", order_id, "save_address", "Configured address")
    assert addressed.data["status"] == "pending_confirmation"
    assert addressed.data["delivery_address"] == "Configured address"
    assert addressed.agent["order_summary"]["delivery_address"] == "Configured address"
    assert addressed.agent["required_input"] == "confirm_or_cancel"
    assert addressed.user_message == addressed.agent["confirmation_summary"]
    assert "Fulfilment: Delivery" in addressed.user_message
    assert "Delivery address: Configured address" in addressed.user_message
    assert "Should I confirm this order?" in addressed.user_message
    submitted = service.update_order_flow("user", order_id, "confirm", idempotency_key="key")
    duplicate = service.update_order_flow("user", order_id, "confirm", idempotency_key="key")
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
    response = service.update_order_flow("user", order_id, "set_takeaway")
    assert response.data["status"] == "pending_confirmation"
    assert response.agent["required_input"] == "confirm_or_cancel"


def _whatsapp_order(*, confirmed_name=None, profile_name=None):
    customers = CustomerService(MemoryCustomerRepository())
    if confirmed_name:
        customers.confirm_customer_name("cust-1", confirmed_name)
    if profile_name:
        customers.update_profile(
            "cust-1",
            whatsapp_profile_name=profile_name,
            channel="whatsapp",
        )
    menu = MemoryMenuRepository(
        [{"product_id": "item", "name": "Item", "available": True,
          "starting_price": 10, "customization_group_ids": []}], []
    )
    repository = MemoryOrderRepository()
    service = OrderService(
        repository,
        menu,
        customer_service=customers,
    )
    order_id = service.create_pending_from_cart({
        "user_id": "cust-1",
        "customer_id": "cust-1",
        "customer_name": profile_name,
        "agent_session_id": "whatsapp-session",
        "restaurant_id": "restaurant",
        "branch_id": "branch",
        "cart_id": "cart-whatsapp",
        "subtotal": 10,
        "currency": "PKR",
        "channel": "whatsapp",
        "items": [{
            "item_id": "item", "name": "Item", "quantity": 1,
            "selected_options": {}, "current_price": 10,
        }],
    }).data["order_id"]
    return service, repository, customers, order_id


def test_whatsapp_takeaway_requires_customer_name_before_confirmation():
    service, repository, _, order_id = _whatsapp_order()

    response = service.update_order_flow("cust-1", order_id, "set_takeaway")

    assert response.data["status"] == "awaiting_customer_name"
    assert response.user_message == "Can I have your name for the order?"
    assert repository.data[order_id]["customer_name"] is None


def test_confirmed_profile_name_is_reused_for_whatsapp_order_snapshot():
    service, repository, _, order_id = _whatsapp_order(confirmed_name="Ava Khan")

    response = service.update_order_flow("cust-1", order_id, "set_takeaway")

    assert response.data["status"] == "pending_confirmation"
    assert response.data["customer_name"] == "Ava Khan"
    assert repository.data[order_id]["customer_name"] == "Ava Khan"
    assert "Name: Ava Khan" in response.user_message


def test_whatsapp_profile_name_is_suggested_but_not_snapshotted():
    service, repository, _, order_id = _whatsapp_order(profile_name="Profile Alias")

    response = service.update_order_flow("cust-1", order_id, "set_takeaway")

    assert response.data["status"] == "awaiting_customer_name"
    assert response.data["customer_name"] is None
    assert response.user_message == (
        "Should I put this order under the name Profile Alias?"
    )
    assert repository.data[order_id]["customer_name_confirmed"] is False


def test_legacy_whatsapp_display_name_is_suggested_during_checkout():
    service, _, customers, order_id = _whatsapp_order()
    customers.repository.data["cust-1"] = {
        "customer_id": "cust-1",
        "display_name": "Legacy WhatsApp Alias",
        "channel_profiles": {"whatsapp": {"created_at": "legacy"}},
        "addresses": [],
    }

    response = service.update_order_flow("cust-1", order_id, "set_takeaway")
    confirmed = service.update_order_flow("cust-1", order_id, "confirm_customer_name")
    profile = customers.get_profile("cust-1").data["customer"]

    assert response.data["status"] == "awaiting_customer_name"
    assert response.data["customer_name"] is None
    assert response.user_message == (
        "Should I put this order under the name Legacy WhatsApp Alias?"
    )
    assert confirmed.data["status"] == "pending_confirmation"
    assert confirmed.data["customer_name"] == "Legacy WhatsApp Alias"
    assert profile["name_confirmed"] is True
    assert profile["name_source"] == "whatsapp_profile"
    assert profile["name_confirmed_at"]


def test_confirming_whatsapp_profile_name_updates_profile_and_order():
    service, repository, customers, order_id = _whatsapp_order(
        profile_name="Profile Alias"
    )
    service.update_order_flow("cust-1", order_id, "set_takeaway")

    response = service.update_order_flow("cust-1", order_id, "confirm_customer_name")

    profile = customers.get_profile("cust-1").data["customer"]
    assert response.data["status"] == "pending_confirmation"
    assert response.data["customer_name"] == "Profile Alias"
    assert profile["display_name"] == "Profile Alias"
    assert profile["name_confirmed"] is True
    assert profile["name_source"] == "whatsapp_profile"
    assert repository.data[order_id]["customer_name_confirmed"] is True


def test_rejected_whatsapp_profile_name_cannot_be_confirmed_later():
    service, repository, _, order_id = _whatsapp_order(
        profile_name="Profile Alias"
    )
    service.update_order_flow("cust-1", order_id, "set_takeaway")

    rejected = service.update_order_flow("cust-1", order_id, "reject_customer_name")
    confirmed = service.update_order_flow("cust-1", order_id, "confirm_customer_name")

    assert rejected.user_message == "Can I have your name for the order?"
    assert rejected.data["suggested_customer_name"] is None
    assert confirmed.success is False
    assert confirmed.error_code == "CUSTOMER_NAME_REQUIRED"
    assert repository.data[order_id]["status"] == "awaiting_customer_name"


def test_customer_provided_whatsapp_name_updates_profile_and_order():
    service, _, customers, order_id = _whatsapp_order()
    service.update_order_flow("cust-1", order_id, "set_takeaway")

    response = service.update_order_flow(
        "cust-1",
        order_id,
        "save_customer_name",
        "Ava Khan",
    )

    profile = customers.get_profile("cust-1").data["customer"]
    assert response.data["status"] == "pending_confirmation"
    assert response.data["customer_name"] == "Ava Khan"
    assert profile["display_name"] == "Ava Khan"
    assert profile["name_source"] == "customer_provided"


def test_whatsapp_order_cannot_submit_without_confirmed_name():
    service, repository, _, order_id = _whatsapp_order()
    repository.data[order_id].update({
        "status": "pending_confirmation",
        "fulfillment_method": "takeaway",
        "customer_name": None,
        "customer_name_confirmed": False,
    })

    response = service.update_order_flow("cust-1", order_id, "confirm")

    assert response.success is False
    assert response.error_code == "CUSTOMER_NAME_REQUIRED"
    assert repository.data[order_id]["status"] == "pending_confirmation"


def test_pending_whatsapp_name_correction_regenerates_summary_before_submit():
    service, repository, customers, order_id = _whatsapp_order(
        confirmed_name="shaheer"
    )
    service.update_order_flow("cust-1", order_id, "set_takeaway")

    corrected = service.update_order_flow(
        "cust-1",
        order_id,
        "save_customer_name",
        "Shaheer Tariq",
    )

    assert corrected.data["status"] == "pending_confirmation"
    assert corrected.data["customer_name"] == "Shaheer Tariq"
    assert "Name: Shaheer Tariq" in corrected.user_message
    assert repository.data[order_id]["status"] == "pending_confirmation"
    assert customers.get_profile("cust-1").data["customer"]["display_name"] == (
        "Shaheer Tariq"
    )

    submitted = service.update_order_flow("cust-1", order_id, "confirm")

    assert submitted.data["status"] == "submitted_to_restaurant"


def _delivery_order():
    menu = MemoryMenuRepository(
        [{"product_id": "item", "name": "Item", "available": True,
          "starting_price": 10, "customization_group_ids": []}], []
    )
    repository = MemoryOrderRepository()
    service = OrderService(repository, menu)
    order_id = service.create_pending_from_cart(
        {"user_id": "user", "agent_session_id": "session",
         "restaurant_id": "restaurant", "branch_id": "branch",
         "cart_id": "cart-address", "subtotal": 10, "currency": "CUR",
         "items": [{"item_id": "item", "name": "Item", "quantity": 1,
                    "selected_options": {}, "current_price": 10}]}
    ).data["order_id"]
    service.update_order_flow("user", order_id, "set_delivery")
    return service, repository, order_id


@pytest.mark.parametrize(
    "address",
    ["No", "nah", "none", "n/a", "skip", "later", "Ok"],
)
def test_delivery_address_rejects_refusals_and_short_placeholders(address):
    service, repository, order_id = _delivery_order()

    response = service.update_order_flow("user", order_id, "save_address", address)

    assert not response.success
    assert response.error_code == "INVALID_DELIVERY_ADDRESS"
    assert response.user_message == (
        "I still need a valid delivery address for delivery. Please send your "
        "full address, or reply takeaway to switch to pickup."
    )
    saved = repository.get_by_order_id(order_id)
    assert saved["status"] == "awaiting_delivery_address"
    assert saved["delivery_address"] is None


@pytest.mark.parametrize(
    "address",
    [
        "D-07-07, Flexis, One South",
        "A-12-03, PV21 Setapak",
        "Block B, APU residence",
        "123 Jalan Ampang",
    ],
)
def test_delivery_address_accepts_realistic_addresses(address):
    service, repository, order_id = _delivery_order()

    response = service.update_order_flow("user", order_id, "save_address", address)

    assert response.success
    assert response.data["status"] == "pending_confirmation"
    assert response.data["delivery_address"] == address
    assert repository.get_by_order_id(order_id)["delivery_address"] == address


def test_delivery_order_with_legacy_invalid_address_cannot_be_confirmed():
    service, repository, order_id = _delivery_order()
    repository.data[order_id]["status"] = "pending_confirmation"
    repository.data[order_id]["delivery_address"] = "No"

    response = service.update_order_flow("user", order_id, "confirm")

    assert not response.success
    assert response.error_code == "INVALID_DELIVERY_ADDRESS"
    assert repository.get_by_order_id(order_id)["status"] == "pending_confirmation"


def test_delivery_address_step_can_switch_to_takeaway():
    service, repository, order_id = _delivery_order()

    response = service.update_order_flow("user", order_id, "set_takeaway")

    assert response.success
    assert response.data["status"] == "pending_confirmation"
    assert response.data["fulfillment_method"] == "takeaway"
    assert response.data["delivery_address"] is None
    assert repository.get_by_order_id(order_id)["fulfillment_method"] == "takeaway"


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
    service.update_order_flow("user", order_id, "set_takeaway")

    response = service.update_order_flow("user", order_id, "submit")

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

    service.update_order_flow("cust-1", order_id, "set_delivery")
    addressed = service.update_order_flow(
        "cust-1", order_id, "save_address", saved["address_text"]
    )
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
    service.update_order_flow("user", order_id, "set_takeaway")
    service.update_order_flow("user", order_id, "confirm")

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
    service.update_order_flow("user", order_id, "set_takeaway")
    service.update_order_flow("user", order_id, "confirm")
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
        "user",
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
        "user",
        order_id,
        "set_takeaway",
    )

    assert initial_summary.data["status"] == "pending_confirmation"
    assert initial_summary.data["total"] == 1000
    assert "Grand total: Rs 1,000.00" in initial_summary.user_message

    menu.items["item"]["starting_price"] = 1200

    changed_price = service.update_order_flow(
        "user",
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
        "user",
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

    service.update_order_flow("user", order_id, "set_takeaway")
    response = service.update_order_flow(
        "user",
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

    service.update_order_flow("user", order_id, "set_takeaway")

    submitted = service.update_order_flow(
        "user",
        order_id,
        "confirm",
        idempotency_key="confirm-key",
    )
    duplicate = service.update_order_flow(
        "user",
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


def test_order_mutation_does_not_accept_another_customers_order_id():
    service, repository, create_order = _build_customer_tracking_service()
    order_id = create_order(user_id="customer-a")
    original = repository.get("customer-a", order_id)

    response = service.update_order_flow(
        "customer-b", order_id, "set_takeaway"
    )

    assert response.error_code == "ORDER_NOT_FOUND"
    assert repository.get("customer-a", order_id) == original


def test_invalid_delivery_acknowledgement_does_not_change_order():
    service, repository, create_order = _build_customer_tracking_service()
    order_id = create_order(user_id="user")
    service.update_order_flow("user", order_id, "set_delivery")
    before = repository.get("user", order_id)

    response = service.update_order_flow(
        "user", order_id, "save_address", "no thanks"
    )

    assert response.error_code == "INVALID_DELIVERY_ADDRESS"
    assert repository.get("user", order_id) == before
