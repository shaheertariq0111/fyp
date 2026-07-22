from types import SimpleNamespace

from src.agent import tools
from src.agent.context import AgentRequestContext, request_context
from src.models.tool_responses import ToolResponse


class MenuStub:
    def search_menu(self, **kwargs):
        return ToolResponse.ok(data=kwargs, user_message="ok")

    def get_menu_item(self, item_id):
        return ToolResponse.ok(data={"item_id": item_id}, user_message="ok")


class SessionStub:
    def create_link(self, user_id, session_id, item_id, customer_id=None):
        return ToolResponse.ok(data={"user_id": user_id, "session_id": session_id,
                                     "item_id": item_id, "customer_id": customer_id}, user_message="ok")


class CartStub:
    def get_active_cart(self, user_id, session_id):
        return ToolResponse.ok(
            data={"cart": {"user_id": user_id, "session_id": session_id}},
            user_message="cart",
        )


class CustomerStub:
    def get_profile(self, customer_id):
        return ToolResponse.ok(data={"customer": {"customer_id": customer_id}}, user_message="ok")

    def update_profile(self, customer_id, **kwargs):
        return ToolResponse.ok(data={"customer": {"customer_id": customer_id, **kwargs}},
                               user_message="ok")

    def save_address(self, customer_id, **kwargs):
        return ToolResponse.ok(data={"customer": {"customer_id": customer_id},
                                     "address": kwargs},
                               user_message="ok")


def test_mvp_tools_include_active_cart_lookup():
    assert len(tools.MVP_TOOLS) == 15
    assert tools.get_active_cart in tools.MVP_TOOLS
    assert tools.get_customer_profile in tools.MVP_TOOLS
    assert tools.update_customer_profile in tools.MVP_TOOLS
    assert tools.save_customer_address in tools.MVP_TOOLS


def test_menu_link_injects_trusted_context(monkeypatch):
    container = SimpleNamespace(menu=MenuStub(), menu_sessions=SessionStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)
    with request_context(AgentRequestContext("trusted-user", "trusted-session")):
        result = tools.create_menu_session_link(item_id="dynamic-item")
    assert result["data"] == {
        "user_id": "trusted-user", "session_id": "trusted-session",
        "item_id": "dynamic-item", "customer_id": "trusted-user"
    }


def test_search_menu_tool_caps_chat_results_to_five(monkeypatch):
    container = SimpleNamespace(menu=MenuStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)

    result = tools.search_menu(query="recommend", max_results=12)

    assert result["data"]["limit"] == 5


def test_get_active_cart_uses_trusted_user_and_session(monkeypatch):
    container = SimpleNamespace(carts=CartStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)
    with request_context(AgentRequestContext("trusted-user", "trusted-session")):
        result = tools.get_active_cart()
    assert result["data"]["cart"] == {
        "user_id": "trusted-user",
        "session_id": "trusted-session",
    }


def test_customer_tools_use_trusted_customer_context(monkeypatch):
    container = SimpleNamespace(customers=CustomerStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)
    with request_context(AgentRequestContext(
        "trusted-user", "trusted-session", customer_id="customer-1", channel="web"
    )):
        profile = tools.get_customer_profile()
        updated = tools.update_customer_profile(display_name="Ava", phone_number="+923001234567")
        address = tools.save_customer_address(
            address_text="House 1, Street 2", label="Home", make_default=True
        )
    assert profile["data"]["customer"]["customer_id"] == "customer-1"
    assert updated["data"]["customer"]["display_name"] == "Ava"
    assert address["data"]["address"]["address_text"] == "House 1, Street 2"
    assert address["data"]["address"]["channel"] == "web"


def test_tool_converts_service_exception_to_safe_error_and_logs(monkeypatch, caplog):
    class BrokenMenu:
        def get_menu_item(self, _item_id):
            raise RuntimeError("internal table details")

    monkeypatch.setattr(tools, "get_services",
                        lambda: SimpleNamespace(menu=BrokenMenu()))
    with caplog.at_level("ERROR", logger="src.agent.tools"):
        with request_context(AgentRequestContext("trusted-user", "trusted-session")):
            result = tools.get_menu_item(item_id="item")
    assert result["error_code"] == "BACKEND_UNAVAILABLE"
    assert "table" not in result["user_message"]
    assert "get_menu_item" in caplog.text
    assert "RuntimeError" in caplog.text

def test_save_customization_choice_fetches_upsells_after_final_required_choice(
    monkeypatch,
):
    calls = []
    upsell_prompt = (
        "Would you like to add anything?\n"
        "\n"
        "1. Ranch Dip - PKR 100\n"
        "2. Lava Cake - 1 Pc - PKR 450\n"
        "\n"
        "You can choose one add-on or proceed to checkout."
    )
    upsell_items = [
        {
            "product_id": "ranch-dip",
            "name": "Ranch Dip",
            "display_label": "Ranch Dip - PKR 100",
        },
        {
            "product_id": "lava-cake",
            "name": "Lava Cake - 1 Pc",
            "display_label": "Lava Cake - 1 Pc - PKR 450",
        },
    ]

    class CartStub:
        def save_choice(
            self,
            cart_item_id,
            field_name,
            selected_option_id,
        ):
            calls.append(
                (
                    "save_choice",
                    cart_item_id,
                    field_name,
                    selected_option_id,
                )
            )
            return ToolResponse.ok(
                data={
                    "cart_id": "CART-1",
                    "status": "item_ready",
                },
                user_message=(
                    "All required item choices are complete."
                ),
                next_action="offer_upsell",
                agent={
                    "entity": "cart",
                    "cart_id": "CART-1",
                    "cart_status": "item_ready",
                    "next_action": "offer_upsell",
                },
            )

        def handle_upsell(
            self,
            cart_id,
            action,
            item_id=None,
            quantity=1,
        ):
            calls.append(
                (
                    "handle_upsell",
                    cart_id,
                    action,
                    item_id,
                    quantity,
                )
            )
            return ToolResponse.ok(
                data={
                    "cart_id": cart_id,
                    "status": "awaiting_upsell_decision",
                    "upsell_items": upsell_items,
                    "upsell_prompt": upsell_prompt,
                },
                user_message=upsell_prompt,
                next_action="choose_upsell",
                agent={
                    "entity": "cart",
                    "cart_id": cart_id,
                    "cart_status": "awaiting_upsell_decision",
                    "next_action": "choose_upsell",
                    "upsell_items": upsell_items,
                    "upsell_prompt": upsell_prompt,
                },
            )

    container = SimpleNamespace(carts=CartStub())
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: container,
    )

    with request_context(
        AgentRequestContext(
            "trusted-user",
            "trusted-session",
        )
    ):
        result = tools.save_customization_choice(
            cart_item_id="CARTITEM-1",
            field_name="pizza-crust",
            selected_option_id="regular",
        )

    assert calls == [
        (
            "save_choice",
            "CARTITEM-1",
            "pizza-crust",
            "regular",
        ),
        (
            "handle_upsell",
            "CART-1",
            "get_options",
            None,
            1,
        ),
    ]
    assert result["success"] is True
    assert result["next_action"] == "choose_upsell"
    assert result["user_message"] == upsell_prompt
    assert result["data"]["upsell_prompt"] == upsell_prompt
    assert result["agent"]["upsell_prompt"] == upsell_prompt
    assert result["agent"]["upsell_items"] == upsell_items


def test_save_customization_choice_preserves_non_upsell_response(
    monkeypatch,
):
    class CartStub:
        def save_choice(
            self,
            cart_item_id,
            field_name,
            selected_option_id,
        ):
            return ToolResponse.ok(
                data={
                    "cart_id": "CART-1",
                    "cart_item_id": cart_item_id,
                    "field_name": "pizza-crust",
                },
                user_message="Choose a crust.",
                next_action="ask_customization_choice",
                agent={
                    "entity": "cart",
                    "cart_id": "CART-1",
                    "next_action": "ask_customization_choice",
                    "active_choice": {
                        "field_name": "pizza-crust",
                    },
                },
            )

        def handle_upsell(self, *args, **kwargs):
            raise AssertionError(
                "Upsells must not be fetched before "
                "required choices are complete."
            )

    container = SimpleNamespace(carts=CartStub())
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: container,
    )

    with request_context(
        AgentRequestContext(
            "trusted-user",
            "trusted-session",
        )
    ):
        result = tools.save_customization_choice(
            cart_item_id="CARTITEM-1",
            field_name="pizza-size",
            selected_option_id="medium",
        )

    assert result["success"] is True
    assert result["next_action"] == "ask_customization_choice"
    assert result["user_message"] == "Choose a crust."
    assert (
        result["agent"]["active_choice"]["field_name"]
        == "pizza-crust"
    )
