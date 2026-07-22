from collections.abc import Callable
import logging

from strands import tool

from src.agent.context import get_request_context
from src.agent.dependencies import get_services
from src.models.tool_responses import ToolResponse


logger = logging.getLogger(__name__)
MAX_AGENT_MENU_RESULTS = 5

WRITE_TOOLS = {
    "start_cart_item_customization",
    "set_customization_mode",
    "save_customization_choice",
    "handle_cart_upsell",
    "create_pending_order_from_cart",
    "update_order_flow",
    "update_customer_profile",
    "save_customer_address",
}


def _record_tool_call(tool_name: str, is_write: bool, result: dict) -> None:
    try:
        context = get_request_context()
    except RuntimeError:
        return
    logger.info(
        "Agent tool call completed",
        extra={
            "event": "agent_tool_completed",
            "tool_name": tool_name,
            "tool_success": bool(result.get("success", False)),
            "is_write": is_write,
            "actor_id": context.user_id,
            "agent_session_id": context.agent_session_id,
            "channel": context.channel,
            "error_code": result.get("error_code"),
        },
    )
    context.tool_calls.append({
        "tool_name": tool_name,
        "success": bool(result.get("success", False)),
        "is_write": is_write,
        "result": result,
        "error_code": result.get("error_code"),
    })


def _result(tool_name: str, call: Callable[[], ToolResponse], *, is_write: bool = False) -> dict:
    try:
        result = call().model_dump(exclude_none=True)
    except Exception as exc:
        try:
            context = get_request_context()
            extra = {
                "tool_name": tool_name,
                "actor_id": context.user_id,
                "agent_session_id": context.agent_session_id,
                "channel": context.channel,
                "error_code": "BACKEND_UNAVAILABLE",
                "exception_type": type(exc).__name__,
            }
        except RuntimeError:
            extra = {
                "tool_name": tool_name,
                "error_code": "BACKEND_UNAVAILABLE",
                "exception_type": type(exc).__name__,
            }
        logger.exception(
            "Strands tool execution failed",
            extra=extra,
        )
        try:
            services = get_services()
            context = get_request_context()
            services.audit.record(
                context.agent_session_id,
                context.user_id,
                "tool_error",
                {
                    "tool_name": tool_name,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "error_code": "BACKEND_UNAVAILABLE",
                },
            )
        except Exception:
            logger.exception("Failed to record tool error audit event", extra=extra)
        result = ToolResponse.error(
            error_code="BACKEND_UNAVAILABLE",
            user_message="I couldn't complete that request right now.",
            retryable=True,
        ).model_dump(exclude_none=True)
    _record_tool_call(tool_name, is_write, result)
    return result


@tool
def search_menu(query: str | None = None, category: str | None = None,
                tags: list[str] | None = None, max_price: int | None = None,
                available_only: bool = True, max_results: int = MAX_AGENT_MENU_RESULTS) -> dict:
    """Search current menu data for browsing and recommendations.

    Use query for descriptive user terms such as "pizza", "chicken", "spicy",
    "deal", or an item name. Use category only when you know the exact menu
    category id returned by menu data, such as "classic-flavors". Returns at
    most five items for chat readability.
    """
    limit = max(1, min(max_results, MAX_AGENT_MENU_RESULTS))
    return _result("search_menu", lambda: get_services().menu.search_menu(
        query=query, category=category, tags=tags, max_price=max_price,
        available_only=available_only, limit=limit,
    ))


@tool
def get_menu_item(item_id: str) -> dict:
    """Get current details and customization groups for one menu item."""
    return _result("get_menu_item", lambda: get_services().menu.get_menu_item(item_id))


@tool
def create_menu_session_link(item_id: str | None = None) -> dict:
    """Create a secure menu-site link tied to the current trusted agent session."""
    context = get_request_context()
    return _result("create_menu_session_link", lambda: get_services().menu_sessions.create_link(
        context.user_id, context.agent_session_id, item_id,
        customer_id=context.customer_id or context.user_id
    ))


@tool
def start_cart_item_customization(item_id: str, quantity: int = 1) -> dict:
    """Start chat customization; multiple customizable units require a mode choice."""
    context = get_request_context()
    return _result("start_cart_item_customization", lambda: get_services().carts.start_item_customization(
        context.user_id, context.agent_session_id, item_id, quantity,
        customer_id=context.customer_id,
        customer_name=context.customer_name,
        customer_phone=context.customer_phone,
    ), is_write=True)


@tool
def set_customization_mode(cart_id: str, mode: str) -> dict:
    """Set multiple units to same or separate customization, validated by the cart service."""
    return _result("set_customization_mode", lambda: get_services().carts.set_customization_mode(cart_id, mode),
                   is_write=True)


@tool
def save_customization_choice(cart_item_id: str, field_name: str,
                              selected_option_id: str) -> dict:
    """Save one customization choice and deterministically fetch final-step upsells."""

    def save_and_fetch_upsells() -> ToolResponse:
        services = get_services()
        response = services.carts.save_choice(
            cart_item_id,
            field_name,
            selected_option_id,
        )

        if not response.success or response.next_action != "offer_upsell":
            return response

        cart_id = (response.data or {}).get("cart_id")
        if not cart_id and response.agent:
            cart_id = response.agent.get("cart_id")

        if not cart_id:
            return response

        return services.carts.handle_upsell(
            cart_id,
            "get_options",
        )

    return _result(
        "save_customization_choice",
        save_and_fetch_upsells,
        is_write=True,
    )


@tool
def handle_cart_upsell(cart_id: str, action: str, item_id: str | None = None,
                       quantity: int = 1) -> dict:
    """Get, add, or skip data-driven cart upsells through the cart service."""
    return _result("handle_cart_upsell", lambda: get_services().carts.handle_upsell(
        cart_id, action, item_id, quantity
    ), is_write=True)


@tool
def create_pending_order_from_cart(cart_id: str) -> dict:
    """Validate and convert a ready chat cart into a pending-confirmation order."""
    return _result("create_pending_order_from_cart", lambda: get_services().carts.create_pending_order(cart_id),
                   is_write=True)


@tool
def update_order_flow(order_id: str, action: str, value: str | None = None,
                      idempotency_key: str | None = None) -> dict:
    """Apply a validated order action: confirm, cancel, fulfillment, or address."""
    return _result("update_order_flow", lambda: get_services().orders.update_order_flow(
        order_id, action, value, idempotency_key
    ), is_write=True)


@tool
def get_active_cart() -> dict:
    """Read the current active chat cart for the trusted user/session."""
    context = get_request_context()
    return _result("get_active_cart", lambda: get_services().carts.get_active_cart(
        context.user_id, context.agent_session_id
    ))


@tool
def get_order_status(order_id: str | None = None) -> dict:
    """Read one authorized order or the current user's active orders from DynamoDB."""
    context = get_request_context()
    return _result("get_order_status", lambda: get_services().orders.get_order_status(context.user_id, order_id))


@tool
def get_customer_profile() -> dict:
    """Read the trusted customer profile for this conversation."""
    context = get_request_context()
    return _result("get_customer_profile", lambda: get_services().customers.get_profile(
        context.customer_id or context.user_id
    ))


@tool
def update_customer_profile(display_name: str | None = None,
                            phone_number: str | None = None) -> dict:
    """Persist customer name and phone details collected in chat."""
    context = get_request_context()
    return _result("update_customer_profile", lambda: get_services().customers.update_profile(
        context.customer_id or context.user_id,
        display_name=display_name,
        phone_number=phone_number,
        channel=context.channel,
        phone_verified=context.channel == "whatsapp",
    ), is_write=True)


@tool
def save_customer_address(address_text: str, label: str | None = None,
                          make_default: bool = True) -> dict:
    """Persist a reusable customer delivery address collected in chat."""
    context = get_request_context()
    return _result("save_customer_address", lambda: get_services().customers.save_address(
        context.customer_id or context.user_id,
        address_text=address_text,
        label=label,
        make_default=make_default,
        channel=context.channel,
    ), is_write=True)


@tool
def retrieve_restaurant_knowledge(question: str, branch_id: str | None = None,
                                  language: str = "en") -> dict:
    """Retrieve approved restaurant policy and FAQ knowledge; never live menu/order data."""
    context = get_request_context()
    effective_branch = branch_id or context.branch_id
    return _result("retrieve_restaurant_knowledge", lambda: get_services().knowledge.retrieve(
        question, effective_branch, language
    ))


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
    get_active_cart,
    get_order_status,
    get_customer_profile,
    update_customer_profile,
    save_customer_address,
    retrieve_restaurant_knowledge,
]
