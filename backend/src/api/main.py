from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from typing import Any, Callable

from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from src.agent import tools
from src.agent.context import AgentRequestContext, request_context
from src.agent.dependencies import get_services
from src.agent_client import AgentInvocationRequest, get_agent_runtime_client
from src.api.schemas import (
    ActionRequest,
    AdminAvailabilityRequest,
    AdminCategoryRequest,
    AdminLoginRequest,
    AdminMenuItemRequest,
    AdminOptionGroupRequest,
    AdminStatusUpdateRequest,
    AdminUpsellGroupRequest,
    ChatRequest,
    ChatRequestStatusResponse,
    ChatResponse,
    ChatSubmitResponse,
    MenuOrderRequest,
    ToolCallResult,
)
from src.infrastructure.config import get_settings, parse_frontend_cors_origins
from src.infrastructure.config import CORS_ALLOW_HEADERS, CORS_ALLOW_METHODS, CORS_EXPOSE_HEADERS
from src.infrastructure.logging import configure_logging


configure_logging(os.getenv("LOG_LEVEL", "INFO"))
app = FastAPI(title="Pizza Restaurant Ordering Agent API")
logger = logging.getLogger(__name__)
ADMIN_COOKIE_NAME = "pizza_admin_session"
app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_frontend_cors_origins(
        os.getenv("FRONTEND_CORS_ORIGINS"),
        os.getenv("ENVIRONMENT", "local"),
    ),
    allow_credentials=True,
    allow_methods=CORS_ALLOW_METHODS,
    allow_headers=CORS_ALLOW_HEADERS,
    expose_headers=CORS_EXPOSE_HEADERS,
)


@app.middleware("http")
async def log_request(request: Request, call_next):
    started = time.perf_counter()
    status_code = 500
    http_request_id = _http_request_id(request)
    request.state.http_request_id = http_request_id
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = http_request_id
        return response
    finally:
        route = getattr(request.scope.get("route"), "path", None) or "unmatched"
        logger.info(
            "HTTP request completed",
            extra={
                "event": "http_request_completed",
                "http_request_id": http_request_id,
                "route": route,
                "method": request.method,
                "status_code": status_code,
                "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
            },
)


def _http_request_id(request: Request) -> str:
    header_value = (request.headers.get("x-request-id") or "").strip()
    if header_value and len(header_value) <= 128 and all(char.isprintable() for char in header_value):
        return header_value
    return f"http-{uuid.uuid4()}"


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
    "get_customer_profile": tools.get_customer_profile,
    "update_customer_profile": tools.update_customer_profile,
    "save_customer_address": tools.save_customer_address,
}


def _admin_secret() -> str:
    settings = get_settings()
    return settings.admin_session_secret or settings.session_token_secret


def _sign_admin_payload(payload: dict[str, Any]) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    signature = hmac.new(_admin_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{signature}"


def _verify_admin_token(token: str | None) -> dict[str, Any]:
    if not token or "." not in token:
        raise HTTPException(status_code=401, detail="Admin login required")
    raw, signature = token.rsplit(".", 1)
    expected = hmac.new(_admin_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Admin login required")
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Admin login required") from exc
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=401, detail="Admin login required")
    return payload


def require_admin(pizza_admin_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    return _verify_admin_token(pizza_admin_session)


def _admin_cookie_options(settings) -> dict[str, Any]:
    cross_site = settings.cross_site_admin_cookie()
    return {
        "httponly": True,
        "secure": cross_site,
        "samesite": "none" if cross_site else "lax",
    }


def _admin_http_error(exc: ValueError) -> HTTPException:
    code = str(exc)
    status = 404 if code.endswith("_NOT_FOUND") or code == "MENU_ENTITY_NOT_FOUND" else 400
    return HTTPException(status_code=status, detail={"error_code": code, "user_message": code.replace("_", " ").title()})

def _raise_if_error(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("success", True):
        return result
    status_code = 404 if result.get("error_code", "").endswith("_NOT_FOUND") else 400
    raise HTTPException(status_code=status_code, detail=result)


def _tool_calls_from_result(result: Any) -> list[ToolCallResult]:
    if isinstance(result, dict):
        raw_calls = result.get("tool_calls", []) or []
    else:
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
            "actor_id": user_id,
            "agent_session_id": session_id,
            "dynamodb_operation": "get_active_cart",
        })
    try:
        order_result = services.orders.get_order_status(user_id).model_dump(exclude_none=True)
        refreshed["orders"] = order_result.get("data", {}).get("orders", [])
    except Exception:
        logger.exception("Failed to refresh active orders after chat write", extra={
            "actor_id": user_id,
            "agent_session_id": session_id,
            "dynamodb_operation": "get_order_status",
        })
    return refreshed


def _buttons_from_tool_calls(tool_calls: list[ToolCallResult]) -> list[dict[str, Any]]:
    for call in reversed(tool_calls):
        result = call.result or {}
        buttons = result.get("buttons") or []
        if buttons:
            return buttons
    return []


def _chat_response_from_invocation(
    context: AgentRequestContext,
    identity_state: dict[str, Any],
    invocation,
) -> ChatResponse:
    result = invocation.raw_result
    tool_calls = _tool_calls_from_result(result)
    write_succeeded = any(call.is_write and call.success for call in tool_calls)
    state = _state_from_tool_calls(tool_calls)
    state.update(identity_state)
    if write_succeeded:
        state = _refresh_authoritative_state(context.user_id, context.agent_session_id, state)
    buttons = _buttons_from_tool_calls(tool_calls)
    return ChatResponse(
        text=invocation.text,
        session_id=context.agent_session_id,
        user_id=context.user_id,
        customer_id=context.customer_id,
        customer=identity_state["customer"],
        data=state,
        tool_calls=tool_calls,
        write_succeeded=write_succeeded,
        state=state,
        buttons=buttons,
    )


def _status_response_from_record(record: dict[str, Any]) -> ChatRequestStatusResponse:
    status = record.get("status", "processing")
    response_payload = record.get("response") or {}
    if status == "completed" and isinstance(response_payload, dict):
        text = response_payload.get("text")
        return ChatRequestStatusResponse(
            request_id=record["request_id"],
            status=status,
            session_id=response_payload.get("session_id"),
            user_id=response_payload.get("user_id"),
            customer_id=response_payload.get("customer_id"),
            customer=response_payload.get("customer"),
            response=text,
            text=text,
            data=response_payload.get("data") or {},
            tool_calls=response_payload.get("tool_calls") or [],
            write_succeeded=bool(response_payload.get("write_succeeded", False)),
            state=response_payload.get("state") or {},
            buttons=response_payload.get("buttons") or [],
        )
    if status == "failed":
        return ChatRequestStatusResponse(
            request_id=record["request_id"],
            status=status,
            session_id=record.get("session_id"),
            user_id=record.get("actor_id"),
            error_code=record.get("error_code") or "AGENT_INVOCATION_FAILED",
            message=record.get("failure_message") or "The request could not be completed.",
        )
    return ChatRequestStatusResponse(
        request_id=record["request_id"],
        status=status,
        session_id=record.get("session_id"),
        user_id=record.get("actor_id"),
    )


def _customer_identity(request) -> str | None:
    if getattr(request, "customer_id", None):
        return request.customer_id
    user_id = getattr(request, "user_id", None)
    if user_id and user_id != "anonymous":
        return user_id
    return None


def _has_active_state(services, user_id: str, session_id: str | None) -> bool:
    if not session_id:
        return False
    try:
        cart = services.carts.get_active_cart(user_id, session_id).data.get("cart")
        if cart:
            return True
    except Exception:
        logger.exception("Failed to inspect active cart during session resolution", extra={
            "actor_id": user_id,
            "agent_session_id": session_id,
            "dynamodb_operation": "get_active_cart",
        })
    try:
        orders = services.orders.get_order_status(user_id).data.get("orders", [])
        return bool(orders)
    except Exception:
        logger.exception("Failed to inspect active orders during session resolution", extra={
            "actor_id": user_id,
            "agent_session_id": session_id,
            "dynamodb_operation": "get_order_status",
        })
    return False


def _resolve_identity(request) -> tuple[AgentRequestContext, dict[str, Any]]:
    services = get_services()
    requested_customer_id = _customer_identity(request)
    preserve_expired = _has_active_state(
        services,
        requested_customer_id or getattr(request, "user_id", "anonymous"),
        getattr(request, "session_id", None),
    )
    resolved = services.agent_sessions.resolve(
        requested_session_id=getattr(request, "session_id", None),
        customer_id=requested_customer_id,
        channel=getattr(request, "channel", "web") or "web",
        preserve_expired=preserve_expired,
        force_new=getattr(request, "force_new_session", False),
    )
    session = resolved["session"]
    customer = resolved["customer"]
    customer_public = {
        "customer_id": customer.get("customer_id"),
        "display_name": customer.get("display_name"),
        "phone_e164": customer.get("phone_e164"),
        "phone_verified": customer.get("phone_verified", False),
    }
    context = AgentRequestContext(
        user_id=customer["customer_id"],
        agent_session_id=session["agent_session_id"],
        branch_id=getattr(request, "branch_id", None),
        customer_id=customer["customer_id"],
        customer_name=customer.get("display_name"),
        customer_phone=customer.get("phone_e164"),
        channel=session.get("channel", "web"),
    )
    state = {
        "session": {
            "session_id": session["agent_session_id"],
            "expires_at": session.get("expires_at"),
            "channel": session.get("channel"),
            "rotated": resolved.get("rotated", False),
        },
        "customer": customer_public,
    }
    return context, state


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/admin/login")
def admin_login(request: AdminLoginRequest, response: Response) -> dict[str, Any]:
    settings = get_settings()
    if not settings.admin_username or not settings.admin_password:
        raise HTTPException(status_code=503, detail="Admin login is not configured")
    if not (
        hmac.compare_digest(request.username, settings.admin_username)
        and hmac.compare_digest(request.password, settings.admin_password)
    ):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    expires_at = int(time.time()) + settings.admin_session_ttl_hours * 3600
    token = _sign_admin_payload({"sub": request.username, "exp": expires_at})
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        token,
        max_age=settings.admin_session_ttl_hours * 3600,
        **_admin_cookie_options(settings),
    )
    return {"admin": {"username": request.username}, "expires_at": expires_at}


@app.post("/api/admin/logout")
def admin_logout(response: Response) -> dict[str, bool]:
    settings = get_settings()
    response.delete_cookie(ADMIN_COOKIE_NAME, **_admin_cookie_options(settings))
    return {"success": True}


@app.get("/api/admin/me")
def admin_me(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return {"admin": {"username": admin.get("sub")}, "expires_at": admin.get("exp")}


@app.get("/api/admin/analytics")
def admin_analytics(_admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return get_services().orders.admin_analytics()


@app.get("/api/admin/orders")
def admin_orders(
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    return get_services().orders.admin_list_orders(status=status, limit=limit)


@app.get("/api/admin/orders/{order_id}")
def admin_order(order_id: str, _admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        return get_services().orders.admin_get_order(order_id)
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.patch("/api/admin/orders/{order_id}/status")
def admin_order_status(
    order_id: str,
    request: AdminStatusUpdateRequest,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_services().orders.admin_update_status(order_id, request.action, request.reason)
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.get("/api/admin/menu/entities")
def admin_menu_entities(
    type: str = Query(default="menu_item"),
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_services().menu.admin_list_entities(type)
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.get("/api/admin/menu/entities/{entity_type}/{entity_id}")
def admin_menu_entity(
    entity_type: str,
    entity_id: str,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_services().menu.admin_get_entity(entity_type, entity_id)
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.post("/api/admin/menu/items")
def admin_create_menu_item(
    request: AdminMenuItemRequest,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_services().menu.admin_save_menu_item(request.model_dump())
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.put("/api/admin/menu/items/{item_id}")
def admin_update_menu_item(
    item_id: str,
    request: AdminMenuItemRequest,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_services().menu.admin_save_menu_item(request.model_dump(), existing_id=item_id)
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.patch("/api/admin/menu/items/{item_id}/availability")
def admin_item_availability(
    item_id: str,
    request: AdminAvailabilityRequest,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_services().menu.admin_set_item_availability(item_id, request.available)
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.patch("/api/admin/menu/items/{item_id}/archive")
def admin_archive_item(item_id: str, _admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        return get_services().menu.admin_archive_item(item_id)
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.post("/api/admin/menu/categories")
def admin_create_category(
    request: AdminCategoryRequest,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_services().menu.admin_save_category(request.model_dump())
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.put("/api/admin/menu/categories/{category_id}")
def admin_update_category(
    category_id: str,
    request: AdminCategoryRequest,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_services().menu.admin_save_category(request.model_dump(), existing_id=category_id)
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.post("/api/admin/menu/option-groups")
def admin_create_option_group(
    request: AdminOptionGroupRequest,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_services().menu.admin_save_option_group(request.model_dump())
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.put("/api/admin/menu/option-groups/{group_id}")
def admin_update_option_group(
    group_id: str,
    request: AdminOptionGroupRequest,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_services().menu.admin_save_option_group(request.model_dump(), existing_id=group_id)
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.post("/api/admin/menu/upsell-groups")
def admin_create_upsell_group(
    request: AdminUpsellGroupRequest,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_services().menu.admin_save_upsell_group(request.model_dump())
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.put("/api/admin/menu/upsell-groups/{group_id}")
def admin_update_upsell_group(
    group_id: str,
    request: AdminUpsellGroupRequest,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_services().menu.admin_save_upsell_group(request.model_dump(), existing_id=group_id)
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.get("/api/admin/customers")
def admin_customers(
    query: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    return get_services().customers.admin_search(query, limit)


@app.get("/api/admin/customers/{customer_id}")
def admin_customer(customer_id: str, _admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        services = get_services()
        return services.customers.admin_get(customer_id, services.orders)
    except ValueError as exc:
        raise _admin_http_error(exc) from exc


@app.get("/api/admin/monitoring/errors")
def admin_monitoring_errors(
    limit: int = Query(default=50, ge=1, le=200),
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    return get_services().audit.admin_list_errors(limit)


@app.get("/api/admin/monitoring/failed-orders")
def admin_monitoring_failed_orders(
    limit: int = Query(default=50, ge=1, le=200),
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    return get_services().orders.admin_failed_orders(limit)


@app.post("/api/chat", response_model=ChatSubmitResponse)
def chat(payload: ChatRequest, http_request: Request, response: Response) -> ChatSubmitResponse:
    http_request_id = getattr(http_request.state, "http_request_id", None)
    context, identity_state = _resolve_identity(payload)
    agent_requests = get_services().agent_requests
    record = agent_requests.start_processing(
        actor_id=context.user_id,
        session_id=context.agent_session_id,
        message=payload.message,
        channel=context.channel,
        request_payload=payload.model_dump(),
    )
    response.headers["X-Agent-Request-ID"] = record["request_id"]
    logger.info(
        "Agent request processing started",
        extra={
            "event": "agent_request_started",
            "http_request_id": http_request_id,
            "request_id": record["request_id"],
            "actor_id": context.user_id,
            "agent_session_id": context.agent_session_id,
            "channel": context.channel,
            "agent_request_status": record["status"],
        },
    )
    try:
        invoke_started = time.perf_counter()
        invocation = get_agent_runtime_client().invoke(
            AgentInvocationRequest(
                message=payload.message,
                user_id=context.user_id,
                agent_session_id=context.agent_session_id,
                branch_id=payload.branch_id,
                customer_id=context.customer_id,
                customer_name=context.customer_name,
                customer_phone=context.customer_phone,
                channel=context.channel,
            )
        )
        logger.info(
            "Agent runtime invocation completed",
            extra={
                "event": "agentcore_invocation_completed",
                "http_request_id": http_request_id,
                "request_id": record["request_id"],
                "actor_id": context.user_id,
                "agent_session_id": context.agent_session_id,
                "channel": context.channel,
                "agentcore_invocation_status": "completed",
                "response_time_ms": round((time.perf_counter() - invoke_started) * 1000, 2),
            },
        )
        response_payload = _chat_response_from_invocation(
            context, identity_state, invocation
        ).model_dump(exclude_none=True)
        record = agent_requests.complete(record["request_id"], response_payload)
        logger.info(
            "Agent request processing completed",
            extra={
                "event": "agent_request_completed",
                "http_request_id": http_request_id,
                "request_id": record["request_id"],
                "actor_id": context.user_id,
                "agent_session_id": context.agent_session_id,
                "channel": context.channel,
                "agent_request_status": record["status"],
            },
        )
    except Exception:
        logger.exception(
            "Agent request failed",
            extra={
                "event": "agentcore_invocation_failed",
                "http_request_id": http_request_id,
                "request_id": record["request_id"],
                "actor_id": context.user_id,
                "agent_session_id": context.agent_session_id,
                "channel": context.channel,
                "agentcore_invocation_status": "failed",
                "error_code": "AGENT_INVOCATION_FAILED",
            },
        )
        record = agent_requests.fail(
            record["request_id"],
            error_code="AGENT_INVOCATION_FAILED",
            message="The request could not be completed.",
        )
        logger.info(
            "Agent request status updated",
            extra={
                "event": "agent_request_failed",
                "http_request_id": http_request_id,
                "request_id": record["request_id"],
                "actor_id": context.user_id,
                "agent_session_id": context.agent_session_id,
                "channel": context.channel,
                "agent_request_status": record["status"],
                "error_code": record.get("error_code"),
            },
        )
    return ChatSubmitResponse(
        request_id=record["request_id"],
        status=record["status"],
        session_id=context.agent_session_id,
        user_id=context.user_id,
        customer_id=context.customer_id,
        customer=identity_state["customer"],
    )


@app.get("/api/chat/{request_id}", response_model=ChatRequestStatusResponse)
def chat_status(request_id: str, http_request: Request, response: Response) -> ChatRequestStatusResponse:
    http_request_id = getattr(http_request.state, "http_request_id", None)
    response.headers["X-Agent-Request-ID"] = request_id
    record = get_services().agent_requests.get(request_id)
    if not record:
        logger.info(
            "Agent request status not found",
            extra={
                "event": "agent_request_not_found",
                "http_request_id": http_request_id,
                "request_id": request_id,
                "agent_request_status": "not_found",
                "error_code": "AGENT_REQUEST_NOT_FOUND",
            },
        )
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "AGENT_REQUEST_NOT_FOUND",
                "user_message": "The chat request was not found.",
            },
        )
    logger.info(
        "Agent request status read",
        extra={
            "event": "agent_request_status_read",
            "http_request_id": http_request_id,
            "request_id": request_id,
            "actor_id": record.get("actor_id"),
            "agent_session_id": record.get("session_id"),
            "agent_request_status": record.get("status"),
            "error_code": record.get("error_code"),
        },
    )
    return _status_response_from_record(record)


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
    context, identity_state = _resolve_identity(request)
    with request_context(context):
        result = handler(**request.metadata)
    result.setdefault("data", {})
    if isinstance(result["data"], dict):
        result["data"].setdefault("session", identity_state["session"])
        result["data"].setdefault("customer", identity_state["customer"])
    return result


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
    customer_id = request.customer_id
    if request.session_token:
        session_result = _raise_if_error(
            get_services().menu_sessions.resolve_token(request.session_token).model_dump(
                exclude_none=True
            )
        )
        data = session_result.get("data", {})
        user_id = user_id or data.get("user_id")
        session_id = session_id or data.get("agent_session_id")
        customer_id = customer_id or data.get("customer_id")
    identity_request = type("IdentityRequest", (), {
        "user_id": user_id or "anonymous",
        "customer_id": customer_id,
        "session_id": session_id,
        "channel": request.channel,
        "branch_id": None,
        "force_new_session": False,
    })()
    context, _identity_state = _resolve_identity(identity_request)
    if not context.user_id or not context.agent_session_id:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error_code": "SESSION_REQUIRED",
                "user_message": "A user and session are required to create an order.",
            },
        )
    result = get_services().carts.create_pending_from_menu_order(
        user_id=context.user_id,
        session_id=context.agent_session_id,
        items=[item.model_dump() for item in request.items],
        customer_id=context.customer_id,
        customer_name=context.customer_name,
        customer_phone=context.customer_phone,
    )
    return _raise_if_error(result.model_dump(exclude_none=True))
