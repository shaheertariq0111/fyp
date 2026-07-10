from collections.abc import Callable

from strands import tool

from src.agent.context import get_request_context
from src.agent.dependencies import get_services
from src.models.tool_responses import ToolResponse


def _result(call: Callable[[], ToolResponse]) -> dict:
    try:
        return call().model_dump(exclude_none=True)
    except Exception:
        return ToolResponse.error(
            error_code="BACKEND_UNAVAILABLE",
            user_message="I couldn't complete that request right now.",
            retryable=True,
        ).model_dump(exclude_none=True)


@tool
def search_menu(query: str | None = None, category: str | None = None,
                tags: list[str] | None = None, max_price: int | None = None,
                available_only: bool = True) -> dict:
    """Search current menu data for browsing and recommendations.

    Use query for descriptive user terms such as "pizza", "chicken", "spicy",
    "deal", or an item name. Use category only when you know the exact menu
    category id returned by menu data, such as "classic-flavors".
    """
    return _result(lambda: get_services().menu.search_menu(
        query=query, category=category, tags=tags, max_price=max_price,
        available_only=available_only,
    ))


@tool
def get_menu_item(item_id: str) -> dict:
    """Get current details and customization groups for one menu item."""
    return _result(lambda: get_services().menu.get_menu_item(item_id))


@tool
def create_menu_session_link(item_id: str | None = None) -> dict:
    """Create a secure menu-site link tied to the current trusted agent session."""
    context = get_request_context()
    return _result(lambda: get_services().menu_sessions.create_link(
        context.user_id, context.agent_session_id, item_id
    ))


@tool
def start_cart_item_customization(item_id: str, quantity: int = 1) -> dict:
    """Start chat customization; multiple customizable units require a mode choice."""
    context = get_request_context()
    return _result(lambda: get_services().carts.start_item_customization(
        context.user_id, context.agent_session_id, item_id, quantity
    ))


@tool
def set_customization_mode(cart_id: str, mode: str) -> dict:
    """Set multiple units to same or separate customization, validated by the cart service."""
    return _result(lambda: get_services().carts.set_customization_mode(cart_id, mode))


@tool
def save_customization_choice(cart_item_id: str, field_name: str,
                              selected_option_id: str) -> dict:
    """Save one backend-returned customization option and receive the next cart step."""
    return _result(lambda: get_services().carts.save_choice(
        cart_item_id, field_name, selected_option_id
    ))


@tool
def handle_cart_upsell(cart_id: str, action: str, item_id: str | None = None,
                       quantity: int = 1) -> dict:
    """Get, add, or skip data-driven cart upsells through the cart service."""
    return _result(lambda: get_services().carts.handle_upsell(
        cart_id, action, item_id, quantity
    ))


@tool
def create_pending_order_from_cart(cart_id: str) -> dict:
    """Validate and convert a ready chat cart into a pending-confirmation order."""
    return _result(lambda: get_services().carts.create_pending_order(cart_id))


@tool
def update_order_flow(order_id: str, action: str, value: str | None = None,
                      idempotency_key: str | None = None) -> dict:
    """Apply a validated order action: confirm, cancel, fulfillment, address, or submit."""
    return _result(lambda: get_services().orders.update_order_flow(
        order_id, action, value, idempotency_key
    ))


@tool
def get_order_status(order_id: str | None = None) -> dict:
    """Read one authorized order or the current user's active orders from DynamoDB."""
    context = get_request_context()
    return _result(lambda: get_services().orders.get_order_status(context.user_id, order_id))


@tool
def retrieve_restaurant_knowledge(question: str, branch_id: str | None = None,
                                  language: str = "en") -> dict:
    """Retrieve approved restaurant policy and FAQ knowledge; never live menu/order data."""
    context = get_request_context()
    effective_branch = branch_id or context.branch_id
    return _result(lambda: get_services().knowledge.retrieve(question, effective_branch, language))


MVP_TOOLS = [
    search_menu,
    get_menu_item,
    create_menu_session_link,
    start_cart_item_customization,
    set_customization_mode,
    save_customization_choice,
    handle_cart_upsell,
    create_pending_order_from_cart,
    update_order_flow,
    get_order_status,
    retrieve_restaurant_knowledge,
]
