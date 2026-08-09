from collections.abc import Callable
import logging
from typing import Literal

from strands import tool

from src.agent.context import get_request_context
from src.agent.dependencies import get_services
from src.models.tool_responses import ToolResponse


logger = logging.getLogger(__name__)

WRITE_TOOLS = {
    "start_cart_item_customization",
    "set_customization_mode",
    "save_customization_choice",
    "handle_cart_upsell",
    "create_pending_order_from_cart",
    "update_order_flow",
    "begin_checkout",
    "choose_delivery",
    "choose_takeaway",
    "save_order_address",
    "confirm_order",
    "cancel_order",
    "update_customer_profile",
    "save_customer_address",
    "create_human_assistance_ticket",
    "handle_order_complaint",
    "request_human_support",
    "create_order_complaint",
    "cancel_support_request",
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
                available_only: bool = True, max_results: int | None = None) -> dict:
    """Search current menu data for browsing and recommendations.

    Use query for descriptive user terms such as "pizza", "chicken", "spicy",
    "deal", or an item name. Use category only when you know the exact menu
    category id returned by menu data, such as "classic-flavors".
    """
    menu = get_services().menu
    configured_limit = menu.customer_result_limit
    limit = min(max(1, max_results or configured_limit), configured_limit)
    return _result("search_menu", lambda: menu.search_menu(
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
        channel=context.channel,
    ), is_write=True)


@tool
def set_customization_mode(cart_id: str, mode: str) -> dict:
    """Set multiple units to same or separate customization, validated by the cart service."""
    context = get_request_context()
    return _result("set_customization_mode", lambda: get_services().carts.set_customization_mode(context.user_id, cart_id, mode),
                   is_write=True)


@tool
def save_customization_choice(
    cart_item_id: str,
    field_name: str,
    selected_option_id: str | list[str],
) -> dict:
    """Save authoritative single- or multi-select customization choices."""

    def save_and_fetch_upsells() -> ToolResponse:
        services = get_services()
        context = get_request_context()
        response = services.carts.save_choice(
            context.user_id,
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
            context.user_id,
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
    context = get_request_context()
    return _result("handle_cart_upsell", lambda: get_services().carts.handle_upsell(
        context.user_id, cart_id, action, item_id, quantity
    ), is_write=True)


@tool
def create_pending_order_from_cart(cart_id: str) -> dict:
    """Validate and convert a ready chat cart into a pending-confirmation order."""
    context = get_request_context()
    return _result("create_pending_order_from_cart", lambda: get_services().carts.create_pending_order(context.user_id, cart_id),
                   is_write=True)


@tool
def update_order_flow(order_id: str, action: str, value: str | None = None,
                      idempotency_key: str | None = None) -> dict:
    """Apply a validated order action: confirm, cancel, fulfillment, or address."""
    context = get_request_context()
    return _result("update_order_flow", lambda: get_services().orders.update_order_flow(
        context.user_id, order_id, action, value, idempotency_key
    ), is_write=True)


@tool
def begin_checkout(cart_id: str) -> dict:
    """Begin checkout by converting a ready cart into a backend pending order."""
    context = get_request_context()
    return _result(
        "begin_checkout",
        lambda: get_services().carts.create_pending_order(context.user_id, cart_id),
        is_write=True,
    )


@tool
def choose_delivery(order_id: str) -> dict:
    """Choose delivery for an order that is awaiting fulfillment method."""
    context = get_request_context()
    return _result(
        "choose_delivery",
        lambda: get_services().orders.update_order_flow(
            context.user_id,
            order_id,
            "set_delivery",
        ),
        is_write=True,
    )


@tool
def choose_takeaway(order_id: str) -> dict:
    """Choose takeaway or pickup for an order that is awaiting fulfillment method."""
    context = get_request_context()
    return _result(
        "choose_takeaway",
        lambda: get_services().orders.update_order_flow(
            context.user_id,
            order_id,
            "set_takeaway",
        ),
        is_write=True,
    )


@tool
def save_order_address(
    order_id: str,
    address_text: str,
    label: str | None = None,
    make_default: bool = True,
) -> dict:
    """Save a delivery address to the customer profile, then apply it to the order."""

    def save_and_apply_address() -> ToolResponse:
        context = get_request_context()
        services = get_services()
        saved = services.customers.save_address(
            context.customer_id or context.user_id,
            address_text=address_text,
            label=label,
            make_default=make_default,
            channel=context.channel,
        )
        if not saved.success:
            return saved
        return services.orders.update_order_flow(
            context.user_id,
            order_id,
            "save_address",
            address_text,
        )

    return _result("save_order_address", save_and_apply_address, is_write=True)


@tool
def confirm_order(order_id: str) -> dict:
    """Submit a pending-confirmation order after the customer explicitly confirms."""
    context = get_request_context()
    return _result(
        "confirm_order",
        lambda: get_services().orders.update_order_flow(
            context.user_id,
            order_id,
            "confirm",
            idempotency_key=context.request_id,
        ),
        is_write=True,
    )


@tool
def cancel_order(order_id: str) -> dict:
    """Cancel a backend order only when its current state allows cancellation."""
    context = get_request_context()
    return _result(
        "cancel_order",
        lambda: get_services().orders.update_order_flow(
            context.user_id,
            order_id,
            "cancel",
        ),
        is_write=True,
    )


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

    def get_and_remember_order() -> ToolResponse:
        services = get_services()
        response = services.orders.get_order_status(context.user_id, order_id)
        selected = _selected_verified_order(response)
        if selected is not None:
            try:
                services.agent_sessions.save_verified_order_context(
                    context.user_id,
                    context.agent_session_id,
                    order_id=selected["order_id"],
                    status=selected["status"],
                )
            except Exception as exc:
                logger.error(
                    "Verified order context could not be persisted",
                    extra={
                        "event": "verified_order_context_persistence_failed",
                        "tool_name": "get_order_status",
                        "actor_id": context.user_id,
                        "agent_session_id": context.agent_session_id,
                        "order_id": selected["order_id"],
                        "exception_type": type(exc).__name__,
                    },
                )
        return response

    return _result("get_order_status", get_and_remember_order)


def _selected_verified_order(response: ToolResponse) -> dict | None:
    if not response.success or not isinstance(response.agent, dict):
        return None
    selected_order_id = response.agent.get("selected_order_id")
    if not isinstance(selected_order_id, str) or not selected_order_id.strip():
        return None
    data = response.data if isinstance(response.data, dict) else {}
    candidates = []
    order = data.get("order")
    if isinstance(order, dict):
        candidates.append(order)
    orders = data.get("orders")
    if isinstance(orders, list):
        candidates.extend(item for item in orders if isinstance(item, dict))
    matches = [
        item
        for item in candidates
        if item.get("order_id") == selected_order_id
        and isinstance(item.get("status"), str)
        and item["status"].strip()
    ]
    if len(matches) != 1:
        return None
    return {
        "order_id": selected_order_id,
        "status": matches[0]["status"],
    }


def _human_assistance_context_error() -> ToolResponse | None:
    context = get_request_context()
    if not context.user_id or not context.user_id.strip():
        return ToolResponse.error(
            error_code="USER_ID_REQUIRED",
            user_message="A trusted user ID is required.",
        )
    if (
        not context.agent_session_id
        or not context.agent_session_id.strip()
    ):
        return ToolResponse.error(
            error_code="SESSION_ID_REQUIRED",
            user_message="A trusted session ID is required.",
        )
    if not context.request_id or not context.request_id.strip():
        return ToolResponse.error(
            error_code="REQUEST_ID_REQUIRED",
            user_message="A trusted request ID is required.",
        )
    return None


@tool
def create_human_assistance_ticket(
    description: str | None = None,
) -> dict:
    """Create generic human help for the trusted session.

    Use for requests to speak to a person or obtain general assistance. This is
    not for complaints about an order; use handle_order_complaint for those.
    """
    context = get_request_context()

    def create_ticket() -> ToolResponse:
        error = _human_assistance_context_error()
        if error:
            return error
        return get_services().tickets.create_human_assistance(
            user_id=context.user_id,
            session_id=context.agent_session_id,
            description=description,
            customer_id=context.customer_id,
            customer_name=context.customer_name,
            customer_phone=context.customer_phone,
            source=context.channel,
            idempotency_key=context.request_id,
        )

    return _result(
        "create_human_assistance_ticket",
        create_ticket,
        is_write=True,
    )


@tool
def request_human_support(description: str | None = None) -> dict:
    """Create a generic human-support ticket for non-order-problem assistance."""
    context = get_request_context()

    def create_ticket() -> ToolResponse:
        error = _human_assistance_context_error()
        if error:
            return error
        return get_services().tickets.create_human_assistance(
            user_id=context.user_id,
            session_id=context.agent_session_id,
            description=description,
            customer_id=context.customer_id,
            customer_name=context.customer_name,
            customer_phone=context.customer_phone,
            source=context.channel,
            idempotency_key=context.request_id,
        )

    return _result("request_human_support", create_ticket, is_write=True)


@tool
def handle_order_complaint(
    order_id: str | None = None,
    description: str | None = None,
    action: Literal["continue", "cancel"] = "continue",
) -> dict:
    """Create, continue, or cancel a trusted order complaint.

    Use for an order-related missing item, wrong item, damaged food, cold food,
    late delivery, quality complaint, or refund or replacement request. Reuse
    the one selected order ID from a recent get_order_status result when
    available, and pass complaint details from the same customer message.
    Ownership is validated by the backend. If the order or description is
    missing, this persists the complaint flow and safely requests only the
    missing input.
    """
    context = get_request_context()
    return _result(
        "handle_order_complaint",
        lambda: get_services().support_flow.handle_order_complaint(
            user_id=context.user_id,
            agent_session_id=context.agent_session_id,
            request_id=context.request_id,
            order_id=order_id,
            description=description,
            action=action,
            customer_id=context.customer_id,
            customer_name=context.customer_name,
            customer_phone=context.customer_phone,
            source=context.channel,
        ),
        is_write=True,
    )


@tool
def create_order_complaint(
    order_id: str | None = None,
    description: str | None = None,
) -> dict:
    """Create or continue an order complaint; backend validates ownership and missing inputs."""
    context = get_request_context()
    return _result(
        "create_order_complaint",
        lambda: get_services().support_flow.handle_order_complaint(
            user_id=context.user_id,
            agent_session_id=context.agent_session_id,
            request_id=context.request_id,
            order_id=order_id,
            description=description,
            action="continue",
            customer_id=context.customer_id,
            customer_name=context.customer_name,
            customer_phone=context.customer_phone,
            source=context.channel,
        ),
        is_write=True,
    )


@tool
def cancel_support_request() -> dict:
    """Cancel the current pending order-complaint support flow, if one exists."""
    context = get_request_context()
    return _result(
        "cancel_support_request",
        lambda: get_services().support_flow.handle_order_complaint(
            user_id=context.user_id,
            agent_session_id=context.agent_session_id,
            request_id=context.request_id,
            action="cancel",
            customer_id=context.customer_id,
            customer_name=context.customer_name,
            customer_phone=context.customer_phone,
            source=context.channel,
        ),
        is_write=True,
    )


@tool
def get_support_ticket_status(
    ticket_id: str | None = None,
) -> dict:
    """Read support-ticket status for the trusted customer."""
    context = get_request_context()
    return _result(
        "get_support_ticket_status",
        lambda: get_services().tickets.get_ticket_status(
            context.user_id,
            ticket_id,
        ),
    )


@tool
def get_support_ticket(ticket_id: str | None = None) -> dict:
    """Read support-ticket status for the trusted customer."""
    context = get_request_context()
    return _result(
        "get_support_ticket",
        lambda: get_services().tickets.get_ticket_status(
            context.user_id,
            ticket_id,
        ),
    )


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
    begin_checkout,
    choose_delivery,
    choose_takeaway,
    save_order_address,
    confirm_order,
    cancel_order,
    get_active_cart,
    get_order_status,
    create_human_assistance_ticket,
    handle_order_complaint,
    get_support_ticket_status,
    request_human_support,
    create_order_complaint,
    cancel_support_request,
    get_support_ticket,
    get_customer_profile,
    update_customer_profile,
    save_customer_address,
    retrieve_restaurant_knowledge,
]
