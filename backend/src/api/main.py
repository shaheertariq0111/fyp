from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from src.agent import tools
from src.agent.context import AgentRequestContext, request_context
from src.agent.dependencies import get_services
from src.agent.restaurant_agent import agent_result_text, invoke_restaurant_agent
from src.api.schemas import ActionRequest, ChatRequest, ChatResponse, MenuOrderRequest, ToolCallResult


app = FastAPI(title="Pizza Restaurant Ordering Agent API")
logger = logging.getLogger(__name__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


ACTION_HANDLERS: dict[str, Callable[..., dict]] = {
    "create_menu_session_link": tools.create_menu_session_link,
    "start_cart_item_customization": tools.start_cart_item_customization,
    "set_customization_mode": tools.set_customization_mode,
    "save_customization_choice": tools.save_customization_choice,
    "handle_cart_upsell": tools.handle_cart_upsell,
    "create_pending_order_from_cart": tools.create_pending_order_from_cart,
    "update_order_flow": tools.update_order_flow,
    "get_active_cart": tools.get_active_cart,
    "get_order_status": tools.get_order_status,
}

def _raise_if_error(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("success", True):
        return result
    status_code = 404 if result.get("error_code", "").endswith("_NOT_FOUND") else 400
    raise HTTPException(status_code=status_code, detail=result)


def _tool_calls_from_result(result: Any) -> list[ToolCallResult]:
    raw_calls = getattr(result, "tool_calls", []) or []
    calls: list[ToolCallResult] = []
    for call in raw_calls:
        if isinstance(call, ToolCallResult):
            calls.append(call)
        elif isinstance(call, dict):
            calls.append(ToolCallResult(**call))
    return calls


def _state_from_tool_calls(tool_calls: list[ToolCallResult]) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for call in tool_calls:
        result = call.result or {}
        data = result.get("data") or {}
        agent = result.get("agent") or {}
        if "cart" in data:
            state["cart"] = data["cart"]
        elif agent.get("entity") == "cart":
            state["cart"] = agent.get("cart_summary") or data
            if agent.get("cart_id"):
                state.setdefault("cart", {})["cart_id"] = agent["cart_id"]
                state["cart"]["status"] = agent.get("cart_status")
        if "order" in data:
            state["order"] = data["order"]
        if "orders" in data:
            state["orders"] = data["orders"]
        elif agent.get("entity") == "order":
            state["order"] = data
        elif agent.get("entity") == "orders":
            state["orders"] = data.get("orders", [])
    return state


def _refresh_authoritative_state(user_id: str, session_id: str, state: dict[str, Any]) -> dict[str, Any]:
    refreshed = dict(state)
    services = get_services()
    try:
        cart_result = services.carts.get_active_cart(user_id, session_id).model_dump(exclude_none=True)
        refreshed["cart"] = cart_result.get("data", {}).get("cart")
    except Exception:
        logger.exception("Failed to refresh active cart after chat write", extra={
            "user_id": user_id,
            "agent_session_id": session_id,
        })
    try:
        order_result = services.orders.get_order_status(user_id).model_dump(exclude_none=True)
        refreshed["orders"] = order_result.get("data", {}).get("orders", [])
    except Exception:
        logger.exception("Failed to refresh active orders after chat write", extra={
            "user_id": user_id,
            "agent_session_id": session_id,
        })
    return refreshed


def _buttons_from_tool_calls(tool_calls: list[ToolCallResult]) -> list[dict[str, Any]]:
    for call in reversed(tool_calls):
        result = call.result or {}
        buttons = result.get("buttons") or []
        if buttons:
            return buttons
    return []


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    result = invoke_restaurant_agent(
        request.message,
        user_id=request.user_id,
        agent_session_id=request.session_id,
        branch_id=request.branch_id,
    )
    tool_calls = _tool_calls_from_result(result)
    write_succeeded = any(call.is_write and call.success for call in tool_calls)
    text = agent_result_text(result)
    state = _state_from_tool_calls(tool_calls)
    if write_succeeded:
        state = _refresh_authoritative_state(request.user_id, request.session_id, state)
    buttons = _buttons_from_tool_calls(tool_calls)
    return ChatResponse(
        text=text,
        session_id=request.session_id,
        user_id=request.user_id,
        data=state,
        tool_calls=tool_calls,
        write_succeeded=write_succeeded,
        state=state,
        buttons=buttons,
    )


@app.post("/api/actions")
def actions(request: ActionRequest) -> dict[str, Any]:
    handler = ACTION_HANDLERS.get(request.action)
    if not handler:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error_code": "UNKNOWN_ACTION",
                "user_message": "That action isn't supported.",
            },
        )
    context = AgentRequestContext(request.user_id, request.session_id, request.branch_id)
    with request_context(context):
        return handler(**request.metadata)


@app.get("/api/menu")
def menu(
    query: str | None = None,
    category: str | None = None,
    tags: list[str] | None = Query(default=None),
    max_price: int | None = None,
    available_only: bool = True,
    limit: int | None = Query(default=None, ge=1, le=100),
) -> dict[str, Any]:
    return get_services().menu.search_menu(
        query=query,
        category=category,
        tags=tags,
        max_price=max_price,
        available_only=available_only,
        limit=limit,
    ).model_dump(exclude_none=True)


@app.get("/api/menu/items/{item_id}")
def menu_item(item_id: str) -> dict[str, Any]:
    return _raise_if_error(
        get_services().menu.get_menu_item(item_id).model_dump(exclude_none=True)
    )


@app.get("/api/menu-session")
def menu_session(session_token: str) -> dict[str, Any]:
    return _raise_if_error(
        get_services().menu_sessions.resolve_token(session_token).model_dump(exclude_none=True)
    )


@app.post("/api/menu-orders")
def menu_orders(request: MenuOrderRequest) -> dict[str, Any]:
    user_id = request.user_id
    session_id = request.session_id
    if request.session_token:
        session_result = _raise_if_error(
            get_services().menu_sessions.resolve_token(request.session_token).model_dump(
                exclude_none=True
            )
        )
        data = session_result.get("data", {})
        user_id = user_id or data.get("user_id")
        session_id = session_id or data.get("agent_session_id")
    if not user_id or not session_id:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error_code": "SESSION_REQUIRED",
                "user_message": "A user and session are required to create an order.",
            },
        )
    result = get_services().carts.create_pending_from_menu_order(
        user_id=user_id,
        session_id=session_id,
        items=[item.model_dump() for item in request.items],
    )
    return _raise_if_error(result.model_dump(exclude_none=True))
