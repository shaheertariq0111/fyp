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
from urllib.parse import urlparse

from fastapi import (
    Body,
    Cookie,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from src.agent import tools
from src.agent.context import AgentRequestContext, request_context
from src.agent.dependencies import get_services
from src.agent.response_grounding import (
    AssistantClaimAssessment,
    ground_agent_response,
    ground_classifier_unavailable_response,
)
from src.agent_client import get_agent_runtime_client
from src.api.schemas import (
    ActionRequest,
    AdminAvailabilityRequest,
    AdminCategoryRequest,
    AdminLoginRequest,
    AdminMenuItemRequest,
    AdminOptionGroupRequest,
    AdminStatusUpdateRequest,
    AdminTicketDetailResponse,
    AdminTicketListResponse,
    AdminTicketNoteCreateRequest,
    AdminTicketPriorityUpdateRequest,
    AdminTicketReopenRequest,
    AdminTicketStatusUpdateRequest,
    AdminUpsellGroupRequest,
    ChatRequest,
    ChatRequestStatusResponse,
    ChatResponse,
    ChatSubmitResponse,
    MenuOrderRequest,
    ToolCallResult,
)
from src.api.whatsapp import WhatsAppInboundMessage, extract_whatsapp_message
from src.api.whatsapp import extract_whatsapp_audio_message
from src.infrastructure.config import get_settings, parse_frontend_cors_origins
from src.infrastructure.config import CORS_ALLOW_HEADERS, CORS_ALLOW_METHODS, CORS_EXPOSE_HEADERS
from src.infrastructure.logging import configure_logging
from src.infrastructure.dynamodb import get_dynamodb_resource
from src.infrastructure.sqs import create_sqs_client
from src.models.ticket import MAX_ACTOR_LENGTH
from src.services.agentflo_gateway_service import AgentfloGatewayService
from src.services.agent_request_processor import AgentRequestProcessor
from src.repositories.whatsapp_voice_job_repository import WhatsAppVoiceJobRepository
from src.services.voice_queue_service import VoiceQueueService
from src.services.whatsapp_voice_job_service import WhatsAppVoiceJobService
from src.services.whatsapp_conversation_service import (
    WhatsAppConversationService,
    WhatsAppDeliveryOutcome,
    build_whatsapp_identity,
)
from src.services.customer_service import CustomerService
from src.services.ticket_service import (
    AdminTicketError,
    project_customer_ticket_view,
)


configure_logging(os.getenv("LOG_LEVEL", "INFO"))
app = FastAPI(title="Pizza Restaurant Ordering Agent API")
logger = logging.getLogger(__name__)
ADMIN_COOKIE_NAME = "pizza_admin_session"
ADMIN_TICKET_CURSOR_KIND = "admin_ticket_http_cursor"
ADMIN_TICKET_CURSOR_DOMAIN = b"admin-ticket-http-cursor-v1."
ADMIN_TICKET_CURSOR_TTL_SECONDS = 3600
ADMIN_TICKET_CURSOR_FUTURE_SKEW_SECONDS = 60
MAX_ADMIN_TICKET_CURSOR_LENGTH = 16 * 1024
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
    "begin_checkout": tools.begin_checkout,
    "choose_delivery": tools.choose_delivery,
    "choose_takeaway": tools.choose_takeaway,
    "save_order_address": tools.save_order_address,
    "confirm_order": tools.confirm_order,
    "cancel_order": tools.cancel_order,
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


def _admin_ticket_cursor_now() -> int:
    return int(time.time())


def _base64url_without_padding(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _strict_base64url_decode(value: str) -> bytes:
    allowed = (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789-_"
    )
    if (
        not value
        or "=" in value
        or any(character not in allowed for character in value)
    ):
        raise ValueError("invalid base64url")
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(
        f"{value}{padding}",
        altchars=b"-_",
        validate=True,
    )


def _json_object_without_duplicate_keys(pairs) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _invalid_admin_ticket_cursor() -> AdminTicketError:
    return AdminTicketError(
        "INVALID_CURSOR",
        "The ticket cursor is invalid.",
    )


def _encode_admin_ticket_cursor(state: dict[str, Any]) -> str:
    if not isinstance(state, dict):
        raise ValueError("ticket cursor state must be a dictionary")
    issued_at = _admin_ticket_cursor_now()
    payload = {
        "v": 1,
        "kind": ADMIN_TICKET_CURSOR_KIND,
        "iat": issued_at,
        "exp": issued_at + ADMIN_TICKET_CURSOR_TTL_SECONDS,
        "state": state,
    }
    encoded_payload = _base64url_without_padding(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = hmac.new(
        _admin_secret().encode("utf-8"),
        ADMIN_TICKET_CURSOR_DOMAIN + encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    cursor = (
        f"{encoded_payload}.{_base64url_without_padding(signature)}"
    )
    if len(cursor) > MAX_ADMIN_TICKET_CURSOR_LENGTH:
        raise ValueError("ticket cursor is too large")
    return cursor


def _decode_admin_ticket_cursor(cursor: str) -> dict[str, Any]:
    try:
        if (
            not isinstance(cursor, str)
            or not cursor
            or len(cursor) > MAX_ADMIN_TICKET_CURSOR_LENGTH
        ):
            raise ValueError
        parts = cursor.split(".")
        if len(parts) != 2:
            raise ValueError
        encoded_payload, encoded_signature = parts
        signature = _strict_base64url_decode(encoded_signature)
        if len(signature) != hashlib.sha256().digest_size:
            raise ValueError
        expected = hmac.new(
            _admin_secret().encode("utf-8"),
            ADMIN_TICKET_CURSOR_DOMAIN + encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload_bytes = _strict_base64url_decode(encoded_payload)
        payload = json.loads(
            payload_bytes.decode("utf-8"),
            object_pairs_hook=_json_object_without_duplicate_keys,
        )
        if (
            not isinstance(payload, dict)
            or set(payload) != {"v", "kind", "iat", "exp", "state"}
            or isinstance(payload["v"], bool)
            or not isinstance(payload["v"], int)
            or payload["v"] != 1
            or payload["kind"] != ADMIN_TICKET_CURSOR_KIND
            or isinstance(payload["iat"], bool)
            or not isinstance(payload["iat"], int)
            or isinstance(payload["exp"], bool)
            or not isinstance(payload["exp"], int)
            or not isinstance(payload["state"], dict)
        ):
            raise ValueError
        issued_at = payload["iat"]
        expires_at = payload["exp"]
        now = _admin_ticket_cursor_now()
        if (
            expires_at <= issued_at
            or expires_at - issued_at > ADMIN_TICKET_CURSOR_TTL_SECONDS
            or expires_at <= now
            or issued_at > now + ADMIN_TICKET_CURSOR_FUTURE_SKEW_SECONDS
        ):
            raise ValueError
        return payload["state"]
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise _invalid_admin_ticket_cursor() from None


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


def _admin_ticket_http_error(exc: AdminTicketError) -> HTTPException:
    status_by_code = {
        "INVALID_TICKET_STATUS": 400,
        "INVALID_TICKET_TYPE": 400,
        "INVALID_TICKET_PRIORITY": 400,
        "INVALID_TICKET_TRANSITION": 400,
        "INVALID_REOPEN_TARGET": 400,
        "INVALID_TICKET_VERSION": 400,
        "NOTE_REQUIRED": 400,
        "NOTE_TOO_LONG": 400,
        "REOPEN_REASON_REQUIRED": 400,
        "INVALID_TICKET_LIMIT": 400,
        "INVALID_CURSOR": 400,
        "TICKET_NOT_FOUND": 404,
        "TICKET_PAGINATION_STALLED": 409,
        "TICKET_VERSION_CONFLICT": 409,
        "TICKET_ITEM_TOO_LARGE": 409,
        "TICKET_DATA_INVALID": 409,
        "ADMIN_NOTE_LIMIT_REACHED": 409,
        "STATUS_HISTORY_LIMIT_REACHED": 409,
        "PRIORITY_HISTORY_LIMIT_REACHED": 409,
        "NOTE_ID_GENERATION_FAILED": 503,
    }
    status = status_by_code.get(exc.error_code)
    if status is None:
        return _admin_ticket_internal_error()
    user_message = (
        "The ticket cursor is invalid."
        if exc.error_code == "INVALID_CURSOR"
        else exc.user_message
    )
    return HTTPException(
        status_code=status,
        detail={
            "error_code": exc.error_code,
            "user_message": user_message,
        },
    )


def _admin_ticket_backend_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error_code": "TICKET_BACKEND_UNAVAILABLE",
            "user_message": "Ticket service is temporarily unavailable.",
        },
    )


def _admin_ticket_internal_error() -> HTTPException:
    return HTTPException(
        status_code=500,
        detail={
            "error_code": "TICKET_INTERNAL_ERROR",
            "user_message": "Ticket service returned an invalid response.",
        },
    )


def _authenticated_admin_actor(admin: dict[str, Any]) -> str:
    actor = admin.get("sub")
    if (
        not isinstance(actor, str)
        or not actor.strip()
        or len(actor) > MAX_ACTOR_LENGTH
    ):
        raise HTTPException(
            status_code=401,
            detail="Admin login required",
        )
    return actor


def _admin_ticket_mutation_response(
    operation: Callable[[], dict[str, Any]],
) -> AdminTicketDetailResponse:
    try:
        result = operation()
    except HTTPException:
        raise
    except AdminTicketError as exc:
        raise _admin_ticket_http_error(exc) from exc
    except Exception as exc:
        raise _admin_ticket_backend_error() from exc
    try:
        if not isinstance(result, dict) or set(result) != {"ticket"}:
            raise ValueError
        return AdminTicketDetailResponse.model_validate(result)
    except (TypeError, ValueError, ValidationError):
        raise _admin_ticket_internal_error() from None


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
        result = call.result if isinstance(call.result, dict) else {}
        raw_data = result.get("data")
        data = raw_data if isinstance(raw_data, dict) else {}
        raw_agent = result.get("agent")
        agent = raw_agent if isinstance(raw_agent, dict) else {}
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
        entity = agent.get("entity")
        if entity in {"ticket", "support_ticket"}:
            ticket = data.get("ticket")
            if isinstance(ticket, dict):
                projected = project_customer_ticket_view(ticket)
                if projected:
                    state["support_ticket"] = projected
        elif entity in {"tickets", "support_tickets"}:
            tickets = data.get("tickets")
            if isinstance(tickets, list):
                projected_tickets = [
                    projected
                    for ticket in tickets
                    if (projected := project_customer_ticket_view(ticket))
                ]
                if projected_tickets or not tickets:
                    state["support_tickets"] = projected_tickets
        tracking_state = agent.get("tracking_state")
        if entity in {
            "ticket",
            "tickets",
            "support_ticket",
            "support_tickets",
        } and isinstance(tracking_state, str):
            tracking = {"tracking_state": tracking_state}
            required_input = agent.get("required_input")
            if isinstance(required_input, str):
                tracking["required_input"] = required_input
            state["support_tracking"] = tracking
        if entity == "pending_support":
            pending: dict[str, Any] = {}
            intent = agent.get("pending_support_intent")
            if intent is None or isinstance(intent, str):
                pending["pending_support_intent"] = intent
            order_id = agent.get("order_id")
            if isinstance(order_id, str) and order_id.strip():
                pending["order_id"] = order_id
            required_input = agent.get("required_input")
            if isinstance(required_input, str):
                pending["required_input"] = required_input
            next_action = result.get("next_action")
            if isinstance(next_action, str):
                pending["next_action"] = next_action
            if pending:
                state["pending_support"] = pending
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


def _menu_price_label(item: dict[str, Any]) -> str:
    currency = str(item.get("currency") or "").strip()
    if item.get("price") is not None:
        return f"{currency} {item['price']}".strip()
    if item.get("starting_price") is not None:
        return f"from {currency} {item['starting_price']}".strip()
    base_prices = item.get("base_prices")
    if isinstance(base_prices, dict):
        sizes = [
            size
            for size in ("small", "medium", "large")
            if base_prices.get(size) is not None
        ]
        sizes.extend(
            size
            for size, price in base_prices.items()
            if size not in sizes and price is not None
        )
        if sizes:
            return ", ".join(
                f"{str(size).replace('_', ' ')} {currency} {base_prices[size]}".strip()
                for size in sizes
            )
    return "price shown on menu"


def _menu_item_name(item: dict[str, Any]) -> str | None:
    name = item.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _search_menu_guard_response(call: ToolCallResult) -> str | None:
    result = call.result if isinstance(call.result, dict) else {}
    data = result.get("data")
    if not isinstance(data, dict) or "items" not in data:
        return None
    items = data.get("items")
    if not isinstance(items, list):
        return None
    if not items:
        user_message = result.get("user_message")
        if isinstance(user_message, str) and user_message.strip():
            return user_message.strip()
        return "I couldn't find a matching available menu item."

    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _menu_item_name(item)
        if name is None:
            continue
        lines.append(f"{len(lines) + 1}. {name} - {_menu_price_label(item)}")
    if not lines:
        user_message = result.get("user_message")
        return user_message.strip() if isinstance(user_message, str) and user_message.strip() else None

    response = "Here are the current menu options I found:\n" + "\n".join(lines)
    if data.get("has_more"):
        response += "\nThere are more matching items too."
    return response + "\nWhich item would you like?"


def _get_menu_item_guard_response(call: ToolCallResult) -> str | None:
    result = call.result if isinstance(call.result, dict) else {}
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    item = data.get("item")
    if not isinstance(item, dict):
        return None
    name = _menu_item_name(item)
    if name is None:
        return None

    lines = [f"{name} - {_menu_price_label(item)}"]
    description = item.get("description")
    if isinstance(description, str) and description.strip():
        lines.append(description.strip())

    groups = item.get("customization_groups")
    option_lines = []
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_name = group.get("name")
            options = group.get("options")
            if not isinstance(group_name, str) or not isinstance(options, list):
                continue
            option_names = [
                option.get("name").strip()
                for option in options
                if isinstance(option, dict)
                and isinstance(option.get("name"), str)
                and option.get("name").strip()
            ]
            if option_names:
                option_lines.append(f"{group_name.strip()}: {', '.join(option_names)}")
    if option_lines:
        lines.append("Options:")
        lines.extend(option_lines)

    return "\n".join(lines)


def _menu_grounded_response_from_tool_calls(tool_calls: list[ToolCallResult]) -> str | None:
    for call in reversed(tool_calls):
        if not call.success:
            continue
        if call.tool_name == "get_menu_item":
            response = _get_menu_item_guard_response(call)
        elif call.tool_name == "search_menu":
            response = _search_menu_guard_response(call)
        else:
            response = None
        if response:
            return response
    return None


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
    if context.channel == "whatsapp":
        def result_value(key, default=None):
            return (
                result.get(key, default)
                if isinstance(result, dict)
                else getattr(result, key, default)
            )
        expected_write_tool = result_value("expected_write_tool")
        required_effect = result_value("required_effect")
        available_options = result_value("available_options")
        assessment_payload = result_value("claim_assessment")
        if result_value("semantic_classifier_available") is False:
            response_text = ground_classifier_unavailable_response(
                tool_calls=tool_calls,
                expected_write_tool=expected_write_tool,
                required_effect=required_effect,
                available_options=available_options,
            ).text
        else:
            assessment = (
                AssistantClaimAssessment.model_validate(assessment_payload)
                if assessment_payload is not None
                else AssistantClaimAssessment(
                    claims_transactional_progression=True,
                    claimed_actions=["other_transactional_progression"],
                )
            )
            response_text = ground_agent_response(
                text=invocation.text,
                tool_calls=tool_calls,
                claim_assessment=assessment,
                no_write_authorized=bool(result_value("no_write_authorized", False)),
                informational_turn=bool(result_value("informational_turn", False)),
                expected_write_tool=expected_write_tool,
                required_effect=required_effect,
                available_options=available_options,
            ).text
    else:
        response_text = _menu_grounded_response_from_tool_calls(tool_calls) or invocation.text
    return ChatResponse(
        text=response_text,
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


def _resolve_identity(
    request,
    *,
    allow_requested_session_creation: bool = False,
) -> tuple[AgentRequestContext, dict[str, Any]]:
    services = get_services()
    requested_customer_id = _customer_identity(request)
    preserve_expired = (
        allow_requested_session_creation
        or _has_active_state(
            services,
            requested_customer_id or getattr(request, "user_id", "anonymous"),
            getattr(request, "session_id", None),
        )
    )
    resolved = services.agent_sessions.resolve(
        requested_session_id=getattr(request, "session_id", None),
        customer_id=requested_customer_id,
        channel=getattr(request, "channel", "web") or "web",
        preserve_expired=preserve_expired,
        force_new=getattr(request, "force_new_session", False),
        allow_requested_session_creation=allow_requested_session_creation,
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
        customer_name=CustomerService.confirmed_name(customer),
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


@app.get(
    "/api/admin/tickets",
    response_model=AdminTicketListResponse,
)
def admin_tickets(
    status: str | None = None,
    ticket_type: str | None = None,
    priority: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
    _admin: dict[str, Any] = Depends(require_admin),
) -> AdminTicketListResponse:
    try:
        cursor_state = (
            _decode_admin_ticket_cursor(cursor)
            if cursor is not None
            else None
        )
    except AdminTicketError as exc:
        raise _admin_ticket_http_error(exc) from None
    try:
        ticket_service = get_services().tickets
        result = ticket_service.list_admin_tickets(
            status=status,
            ticket_type=ticket_type,
            priority=priority,
            limit=limit,
            cursor_state=cursor_state,
        )
    except AdminTicketError as exc:
        raise _admin_ticket_http_error(exc) from exc
    except Exception as exc:
        raise _admin_ticket_backend_error() from exc
    try:
        if (
            not isinstance(result, dict)
            or set(result) != {"tickets", "next_cursor_state"}
        ):
            raise ValueError
        validated = AdminTicketListResponse(
            tickets=result["tickets"],
            next_cursor=None,
        )
        next_state = result["next_cursor_state"]
        if next_state is not None and not isinstance(next_state, dict):
            raise ValueError
        if next_state is None:
            next_cursor = None
        else:
            validated_next_state = (
                ticket_service.validate_admin_cursor_state(
                    next_state,
                    status=status,
                    ticket_type=ticket_type,
                    priority=priority,
                )
            )
            next_cursor = _encode_admin_ticket_cursor(
                validated_next_state
            )
        return AdminTicketListResponse(
            tickets=validated.tickets,
            next_cursor=next_cursor,
        )
    except (
        AdminTicketError,
        KeyError,
        TypeError,
        ValueError,
        ValidationError,
    ):
        raise _admin_ticket_internal_error() from None


@app.get(
    "/api/admin/tickets/{ticket_id}",
    response_model=AdminTicketDetailResponse,
)
def admin_ticket(
    ticket_id: str,
    _admin: dict[str, Any] = Depends(require_admin),
) -> AdminTicketDetailResponse:
    try:
        result = get_services().tickets.get_admin_ticket(ticket_id)
    except AdminTicketError as exc:
        raise _admin_ticket_http_error(exc) from exc
    except Exception as exc:
        raise _admin_ticket_backend_error() from exc
    try:
        if not isinstance(result, dict) or set(result) != {"ticket"}:
            raise ValueError
        return AdminTicketDetailResponse.model_validate(result)
    except (TypeError, ValueError, ValidationError):
        raise _admin_ticket_internal_error() from None


@app.patch(
    "/api/admin/tickets/{ticket_id}/status",
    response_model=AdminTicketDetailResponse,
)
def admin_ticket_status(
    ticket_id: str,
    request: AdminTicketStatusUpdateRequest,
    admin: dict[str, Any] = Depends(require_admin),
) -> AdminTicketDetailResponse:
    actor = _authenticated_admin_actor(admin)
    return _admin_ticket_mutation_response(
        lambda: get_services().tickets.update_admin_status(
            ticket_id,
            request.status,
            expected_version=request.expected_version,
            actor=actor,
            reason=request.reason,
        )
    )


@app.patch(
    "/api/admin/tickets/{ticket_id}/priority",
    response_model=AdminTicketDetailResponse,
)
def admin_ticket_priority(
    ticket_id: str,
    request: AdminTicketPriorityUpdateRequest,
    admin: dict[str, Any] = Depends(require_admin),
) -> AdminTicketDetailResponse:
    actor = _authenticated_admin_actor(admin)
    return _admin_ticket_mutation_response(
        lambda: get_services().tickets.update_admin_priority(
            ticket_id,
            request.priority,
            expected_version=request.expected_version,
            actor=actor,
            reason=request.reason,
        )
    )


@app.post(
    "/api/admin/tickets/{ticket_id}/notes",
    response_model=AdminTicketDetailResponse,
)
def admin_ticket_note(
    ticket_id: str,
    request: AdminTicketNoteCreateRequest,
    admin: dict[str, Any] = Depends(require_admin),
) -> AdminTicketDetailResponse:
    actor = _authenticated_admin_actor(admin)
    return _admin_ticket_mutation_response(
        lambda: get_services().tickets.add_admin_note(
            ticket_id,
            request.text,
            expected_version=request.expected_version,
            actor=actor,
        )
    )


@app.post(
    "/api/admin/tickets/{ticket_id}/reopen",
    response_model=AdminTicketDetailResponse,
)
def admin_ticket_reopen(
    ticket_id: str,
    request: AdminTicketReopenRequest,
    admin: dict[str, Any] = Depends(require_admin),
) -> AdminTicketDetailResponse:
    actor = _authenticated_admin_actor(admin)
    return _admin_ticket_mutation_response(
        lambda: get_services().tickets.reopen_admin_ticket(
            ticket_id,
            target_status=request.target_status,
            reason=request.reason,
            expected_version=request.expected_version,
            actor=actor,
        )
    )


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


@app.get("/api/admin/conversations")
def admin_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_services().conversation_history.admin_list_conversations(
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "CONVERSATION_HISTORY_UNAVAILABLE",
                "user_message": "Conversation history is temporarily unavailable.",
            },
        ) from exc


@app.get("/api/admin/conversations/{conversation_id}/messages")
def admin_conversation_messages(
    conversation_id: str,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_services().conversation_history.admin_list_messages(
            conversation_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "CONVERSATION_HISTORY_UNAVAILABLE",
                "user_message": "Conversation history is temporarily unavailable.",
            },
        ) from exc


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


def _process_chat_request(
    payload: ChatRequest,
    http_request_id: str | None,
    *,
    allow_requested_session_creation: bool = False,
) -> tuple[dict[str, Any], AgentRequestContext, dict[str, Any]]:
    result = AgentRequestProcessor(
        services_provider=lambda: get_services(),
        agent_client_provider=lambda: get_agent_runtime_client(),
        identity_resolver=_resolve_identity,
        response_builder=_chat_response_from_invocation,
        logger=logger,
    ).process(
        payload,
        http_request_id,
        allow_requested_session_creation=allow_requested_session_creation,
    )
    return result.record, result.context, result.identity_state


def _whatsapp_identity(
    inbound: WhatsAppInboundMessage,
) -> tuple[str, str]:
    return build_whatsapp_identity(inbound, get_services)


def _agentflo_failure_response() -> dict[str, Any]:
    return {
        "success": False,
        "error_code": "AGENT_INVOCATION_FAILED",
        "reply": "I couldn't complete that request right now.",
    }


def _store_whatsapp_inbound_history(
    *,
    inbound: WhatsAppInboundMessage,
    customer_id: str,
    session_id: str,
    http_request_id: str | None,
) -> None:
    try:
        get_services().conversation_history.store_inbound_whatsapp_message(
            conversation_id=session_id,
            customer_id=customer_id,
            message_text=inbound.text,
            inbound_message_id=inbound.message_id,
            customer_number=inbound.customer_number,
        )
    except Exception as exc:
        logger.warning(
            "WhatsApp conversation history inbound write failed",
            extra={
                "event": "conversation_history_write_failed",
                "http_request_id": http_request_id,
                "actor_id": customer_id,
                "agent_session_id": session_id,
                "channel": "whatsapp",
                "direction": "inbound",
                "exception_type": type(exc).__name__,
                "error_code": "CONVERSATION_HISTORY_WRITE_FAILED",
            },
        )


def _store_whatsapp_outbound_history(
    *,
    inbound: WhatsAppInboundMessage,
    customer_id: str,
    session_id: str,
    request_id: str,
    reply: str,
    outbound: dict[str, Any],
    http_request_id: str | None,
) -> None:
    try:
        get_services().conversation_history.store_outbound_whatsapp_message(
            conversation_id=session_id,
            customer_id=customer_id,
            message_text=reply,
            request_id=request_id,
            inbound_message_id=inbound.message_id,
            outbound=outbound,
        )
    except Exception as exc:
        logger.warning(
            "WhatsApp conversation history outbound write failed",
            extra={
                "event": "conversation_history_write_failed",
                "http_request_id": http_request_id,
                "request_id": request_id,
                "actor_id": customer_id,
                "agent_session_id": session_id,
                "channel": "whatsapp",
                "direction": "outbound",
                "exception_type": type(exc).__name__,
                "error_code": "CONVERSATION_HISTORY_WRITE_FAILED",
            },
        )


def _agentflo_gateway_service() -> AgentfloGatewayService:
    settings = get_settings()
    return AgentfloGatewayService(
        base_url=settings.agentflo_gateway_base_url,
        api_key=settings.agentflo_gateway_api_key,
        tenant_id=settings.agentflo_gateway_tenant_id,
        agent_id=settings.agentflo_gateway_agent_id,
        actor_id=settings.agentflo_gateway_actor_id,
    )


def _whatsapp_conversation_service() -> WhatsAppConversationService:
    processor = AgentRequestProcessor(
        services_provider=lambda: get_services(),
        agent_client_provider=lambda: get_agent_runtime_client(),
        identity_resolver=_resolve_identity,
        response_builder=_chat_response_from_invocation,
        logger=logger,
    )
    return WhatsAppConversationService(
        services_provider=lambda: get_services(),
        processor=processor,
        identity_builder=_whatsapp_identity,
        gateway_provider=_agentflo_gateway_service,
        logger=logger,
    )


def _agentflo_outbound_response(
    *,
    reply: str,
    request_id: str,
    session_id: str,
    outbound: dict[str, Any],
) -> dict[str, Any]:
    response = {
        "success": bool(outbound.get("sent") or outbound.get("skipped")),
        "reply": reply,
        "text": reply,
        "request_id": request_id,
        "session_id": session_id,
        "outbound": outbound,
    }
    if not response["success"]:
        response["error_code"] = "AGENTFLO_OUTBOUND_FAILED"
    return response


def _agentflo_duplicate_response() -> dict[str, Any]:
    return {
        "success": True,
        "ignored": True,
        "duplicate": True,
        "reason": "duplicate_message",
    }


def _safe_agentflo_type_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 64:
        return None
    if not all(character.isascii() and (character.isalnum() or character in "._-") for character in candidate):
        return None
    return candidate


def _agentflo_messages_array(payload: dict[str, Any]) -> list[Any] | None:
    pending: list[tuple[Any, int]] = [(payload, 0)]
    visited = 0
    while pending and visited < 200:
        value, depth = pending.pop(0)
        visited += 1
        if isinstance(value, dict):
            messages = value.get("messages")
            if isinstance(messages, list):
                return messages
            if depth < 6:
                pending.extend((nested, depth + 1) for nested in value.values())
        elif isinstance(value, list) and depth < 6:
            pending.extend((nested, depth + 1) for nested in value)
    return None


def _agentflo_ignored_payload_shape(payload: dict[str, Any]) -> dict[str, Any]:
    messages = _agentflo_messages_array(payload)
    first_message = (
        messages[0]
        if messages and isinstance(messages[0], dict)
        else None
    )
    first_message_type = _safe_agentflo_type_name(
        first_message.get("type") if first_message is not None else None
    )
    detected_message_type = first_message_type or _safe_agentflo_type_name(
        payload.get("type")
    )
    audio = first_message.get("audio") if first_message is not None else None
    media = first_message.get("media") if first_message is not None else None
    return {
        "top_level_payload_keys": sorted(payload),
        "detected_message_type": detected_message_type,
        "messages_array_exists": messages is not None,
        "message_count": len(messages) if messages is not None else 0,
        "first_message_keys": sorted(first_message) if first_message is not None else [],
        "first_message_type": first_message_type,
        "first_message_has_audio": first_message is not None and "audio" in first_message,
        "first_message_has_voice": first_message is not None and "voice" in first_message,
        "first_message_has_media": first_message is not None and "media" in first_message,
        "first_message_has_document": first_message is not None and "document" in first_message,
        "first_message_has_image": first_message is not None and "image" in first_message,
        "first_message_has_video": first_message is not None and "video" in first_message,
        "first_message_has_sticker": first_message is not None and "sticker" in first_message,
        "first_message_audio_has_id": isinstance(audio, dict) and "id" in audio,
        "first_message_audio_has_url_or_link": isinstance(audio, dict)
        and ("url" in audio or "link" in audio),
        "first_message_media_has_id": isinstance(media, dict) and "id" in media,
        "first_message_media_has_url_or_link": isinstance(media, dict)
        and ("url" in media or "link" in media),
    }


def _log_agentflo_delivery_transition(
    *,
    previous_state: str,
    next_state: str,
    applied: bool,
    http_request_id: str | None,
    request_id: str | None = None,
) -> None:
    logger.info(
        "Agentflo WhatsApp delivery state transition",
        extra={
            "event": "agentflo_whatsapp_delivery_transition",
            "http_request_id": http_request_id,
            "request_id": request_id,
            "channel": "whatsapp",
            "previous_delivery_state": previous_state,
            "delivery_state": next_state,
            "transition_applied": applied,
        },
    )


def _claim_agentflo_outbound_send(
    message_id: str,
    *,
    http_request_id: str | None,
    request_id: str | None = None,
) -> bool:
    try:
        claimed = get_services().agent_requests.claim_agentflo_whatsapp_outbound(
            message_id
        )
    except Exception as exc:
        logger.error(
            "Agentflo WhatsApp outbound claim failed",
            extra={
                "event": "agentflo_whatsapp_delivery_transition_failed",
                "http_request_id": http_request_id,
                "request_id": request_id,
                "channel": "whatsapp",
                "previous_delivery_state": "response_ready",
                "delivery_state": "outbound_sending",
                "exception_type": type(exc).__name__,
                "error_code": "AGENTFLO_OUTBOUND_FAILED",
            },
        )
        return False
    _log_agentflo_delivery_transition(
        previous_state="response_ready",
        next_state="outbound_sending",
        applied=claimed,
        http_request_id=http_request_id,
        request_id=request_id,
    )
    return claimed


def _finalize_agentflo_outbound_send(
    message_id: str,
    *,
    success: bool,
    http_request_id: str | None,
    request_id: str | None = None,
) -> None:
    next_state = "completed" if success else "response_ready"
    transition = (
        get_services().agent_requests.complete_agentflo_whatsapp_message
        if success
        else get_services().agent_requests.retry_agentflo_whatsapp_outbound
    )
    try:
        applied = transition(message_id)
    except Exception as exc:
        logger.error(
            "Agentflo WhatsApp delivery finalization failed",
            extra={
                "event": "agentflo_whatsapp_delivery_transition_failed",
                "http_request_id": http_request_id,
                "request_id": request_id,
                "channel": "whatsapp",
                "previous_delivery_state": "outbound_sending",
                "delivery_state": next_state,
                "exception_type": type(exc).__name__,
                "error_code": "AGENTFLO_OUTBOUND_FAILED",
            },
        )
        return
    _log_agentflo_delivery_transition(
        previous_state="outbound_sending",
        next_state=next_state,
        applied=applied,
        http_request_id=http_request_id,
        request_id=request_id,
    )


def _agentflo_text_definitely_sent(delivery: WhatsAppDeliveryOutcome) -> bool:
    return (
        delivery.status == "sent"
        and isinstance(delivery.outbound, dict)
        and delivery.outbound.get("sent") is True
    )


def _activate_pending_agentflo_receipt(
    inbound: WhatsAppInboundMessage,
    marker: dict[str, Any] | None,
    *,
    recovery: bool,
) -> None:
    if (
        not isinstance(marker, dict)
        or marker.get("delivery_state") != "completed"
        or marker.get("receipt_activation_state") != "pending"
        or not isinstance(inbound.message_id, str)
        or not inbound.message_id
        or not isinstance(inbound.customer_number, str)
        or not inbound.customer_number
        or not isinstance(inbound.sender_id, str)
        or not inbound.sender_id
    ):
        return
    activation = get_services().whatsapp_receipt_activation
    if activation is None:
        raise RuntimeError("RECEIPT_ACTIVATION_DEPENDENCY_UNAVAILABLE")
    result = activation.activate_pending(
        message_id=inbound.message_id,
        marker=marker,
        customer_number=inbound.customer_number,
        sender_id=inbound.sender_id,
    )
    logger.info(
        "Agentflo WhatsApp receipt activation handled",
        extra={
            "event": "agentflo_whatsapp_receipt_activation",
            "receipt_activation_status": result.status,
            "retryable": result.retryable,
            "recovery": recovery,
        },
    )


def _finalize_agentflo_text_send(
    inbound: WhatsAppInboundMessage,
    *,
    success: bool,
    definitely_sent: bool,
    submitted_order_id: str | None,
    http_request_id: str | None,
    request_id: str | None = None,
) -> None:
    message_id = inbound.message_id
    if message_id is None:
        return
    settings = get_settings()
    receipt_handoff = (
        settings.receipt_activation_enabled
        and submitted_order_id is not None
        and definitely_sent
    )
    if not receipt_handoff:
        _finalize_agentflo_outbound_send(
            message_id,
            success=success,
            http_request_id=http_request_id,
            request_id=request_id,
        )
        return

    try:
        applied = (
            get_services().agent_requests
            .complete_agentflo_whatsapp_with_receipt_pending(message_id)
        )
    except Exception as exc:
        logger.error(
            "Agentflo WhatsApp receipt handoff failed",
            extra={
                "event": "agentflo_whatsapp_receipt_handoff_failed",
                "channel": "whatsapp",
                "previous_delivery_state": "outbound_sending",
                "delivery_state": "completed",
                "exception_type": type(exc).__name__,
                "error_code": "RECEIPT_ACTIVATION_HANDOFF_FAILED",
            },
        )
        return
    logger.info(
        "Agentflo WhatsApp receipt handoff checkpointed",
        extra={
            "event": "agentflo_whatsapp_receipt_handoff",
            "previous_delivery_state": "outbound_sending",
            "delivery_state": "completed",
            "transition_applied": applied,
        },
    )
    marker = get_services().agent_requests.get_agentflo_whatsapp_message(
        message_id
    )
    _activate_pending_agentflo_receipt(
        inbound,
        marker,
        recovery=not applied,
    )


def _retry_cached_agentflo_outbound(
    inbound: WhatsAppInboundMessage,
    marker: dict[str, Any],
    http_request_id: str | None,
) -> dict[str, Any]:
    reply = marker.get("reply")
    request_id = marker.get("request_id")
    session_id = marker.get("session_id")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (reply, request_id, session_id)
    ):
        return _agentflo_failure_response()
    gateway = _agentflo_gateway_service()
    if not gateway.configured:
        outbound = {
            "sent": False,
            "skipped": True,
            "reason": "gateway_not_configured",
        }
    elif inbound.sender_id is None or inbound.customer_number is None:
        outbound = {
            "sent": False,
            "error_code": "AGENTFLO_OUTBOUND_FAILED",
        }
    else:
        try:
            outbound = gateway.send_text(
                customer_number=inbound.customer_number,
                conversation_id=session_id,
                sender_id=inbound.sender_id,
                text=reply,
                request_id=request_id,
            )
        except Exception:
            outbound = {
                "sent": False,
                "error_code": "AGENTFLO_OUTBOUND_FAILED",
            }
    response = _agentflo_outbound_response(
        reply=reply,
        request_id=request_id,
        session_id=session_id,
        outbound=outbound,
    )
    if inbound.message_id is not None:
        _finalize_agentflo_text_send(
            inbound,
            success=response["success"],
            definitely_sent=outbound.get("sent") is True,
            submitted_order_id=marker.get("submitted_order_id"),
            http_request_id=http_request_id,
            request_id=request_id,
        )
    return response


def _require_agentflo_webhook_secret(
    request: Request,
    http_request_id: str | None,
) -> None:
    configured_secret = get_settings().agentflo_whatsapp_webhook_secret
    if not configured_secret:
        logger.warning(
            "Agentflo WhatsApp webhook authentication is not configured",
            extra={
                "event": "agentflo_whatsapp_unauthenticated",
                "http_request_id": http_request_id,
                "channel": "whatsapp",
            },
        )
        return
    provided_secret = (
        request.path_params.get("webhook_secret")
        or request.headers.get("X-Agentflo-Webhook-Secret")
    )
    if (
        not isinstance(provided_secret, str)
        or not hmac.compare_digest(provided_secret, configured_secret)
    ):
        logger.warning(
            "Agentflo WhatsApp webhook authentication failed",
            extra={
                "event": "agentflo_whatsapp_authentication_failed",
                "http_request_id": http_request_id,
                "channel": "whatsapp",
                "error_code": "AGENTFLO_WEBHOOK_UNAUTHORIZED",
            },
        )
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "AGENTFLO_WEBHOOK_UNAUTHORIZED",
                "user_message": "Webhook authentication failed.",
            },
        )


@app.post("/api/chat", response_model=ChatSubmitResponse)
def chat(payload: ChatRequest, http_request: Request, response: Response) -> ChatSubmitResponse:
    http_request_id = getattr(http_request.state, "http_request_id", None)
    record, context, identity_state = _process_chat_request(
        payload,
        http_request_id,
    )
    response.headers["X-Agent-Request-ID"] = record["request_id"]
    return ChatSubmitResponse(
        request_id=record["request_id"],
        status=record["status"],
        session_id=context.agent_session_id,
        user_id=context.user_id,
        customer_id=context.customer_id,
        customer=identity_state["customer"],
    )


@app.post("/api/channels/agentflo/whatsapp")
@app.post("/api/channels/agentflo/whatsapp/{webhook_secret}")
def agentflo_whatsapp(
    http_request: Request,
    response: Response,
    payload: Any = Body(...),
) -> dict[str, Any]:
    http_request_id = getattr(http_request.state, "http_request_id", None)
    _require_agentflo_webhook_secret(http_request, http_request_id)
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "INVALID_WEBHOOK_PAYLOAD",
                "user_message": "The webhook payload is invalid.",
            },
        )
    inbound = extract_whatsapp_message(payload)
    if inbound is None:
        audio = extract_whatsapp_audio_message(payload)

        if audio is not None and audio.media_url:
            try:
                audio_media_hostname = (
                    urlparse(audio.media_url).hostname or ""
                ).lower()
            except ValueError:
                audio_media_hostname = ""

            if audio_media_hostname:
                logger.info(
                    "Agentflo audio media hostname observed",
                    extra={
                        "event": "agentflo_whatsapp_audio_media_hostname",
                        "http_request_id": http_request_id,
                        "channel": "whatsapp",
                        "audio_media_hostname": audio_media_hostname,
                    },
                )

        settings = get_settings()
        if audio is not None and settings.whatsapp_voice_enabled:
            if not all((audio.message_id, audio.audio_id, audio.customer_number, audio.sender_id)):
                logger.warning(
                    "Agentflo audio missing required metadata",
                    extra={"event": "voice_permanent_failure", "http_request_id": http_request_id, "channel": "whatsapp", "failure_stage": "webhook_validation"},
                )
                return {"success": True, "ignored": True, "reason": "incomplete_audio_message"}
            queue = VoiceQueueService(
                create_sqs_client(region_name=settings.aws_region),
                settings.voice_job_queue_url,
                wait_time_seconds=settings.voice_sqs_wait_time_seconds,
                visibility_timeout_seconds=settings.voice_sqs_visibility_timeout_seconds,
            )
            jobs = WhatsAppVoiceJobService(
                WhatsAppVoiceJobRepository(
                    get_dynamodb_resource(settings),
                    settings.whatsapp_voice_jobs_table_name,
                ),
                queue,
                settings,
                logger=logger,
            )
            submission = jobs.submit_audio(audio)
            if submission.duplicate:
                logger.info("Duplicate audio webhook", extra={"event": "agentflo_whatsapp_audio_duplicate", "http_request_id": http_request_id, "channel": "whatsapp", "voice_job_id": submission.job_id, "duplicate": True})
            return {
                "success": True,
                "accepted": True,
                "queued": submission.queued,
                "message_type": "audio",
            }
        logger.info(
            "Agentflo WhatsApp event ignored",
            extra={
                "event": "agentflo_whatsapp_ignored_payload_shape",
                "http_request_id": http_request_id,
                "channel": "whatsapp",
                "reason": "no_text_message",
                "payload_shape": _agentflo_ignored_payload_shape(payload),
            },
        )
        return {
            "success": True,
            "ignored": True,
            "reason": "no_text_message",
        }

    if inbound.message_id is None:
        logger.warning(
            "Agentflo WhatsApp idempotency skipped",
            extra={
                "event": "agentflo_whatsapp_idempotency_skipped",
                "http_request_id": http_request_id,
                "channel": "whatsapp",
                "reason": "missing_message_id",
                "idempotency_status": "skipped",
            },
        )
    else:
        try:
            claimed = get_services().agent_requests.claim_agentflo_whatsapp_message(
                inbound.message_id
            )
        except Exception as exc:
            logger.error(
                "Agentflo WhatsApp idempotency claim failed",
                extra={
                    "event": "agentflo_whatsapp_idempotency_failed",
                    "http_request_id": http_request_id,
                    "channel": "whatsapp",
                    "exception_type": type(exc).__name__,
                    "idempotency_status": "failed",
                    "error_code": "AGENT_INVOCATION_FAILED",
                },
            )
            return _agentflo_failure_response()
        if not claimed:
            marker = get_services().agent_requests.get_agentflo_whatsapp_message(
                inbound.message_id
            )
            delivery_state = marker.get("delivery_state") if marker else None
            request_id = marker.get("request_id") if marker else None
            if get_settings().receipt_activation_enabled:
                _activate_pending_agentflo_receipt(
                    inbound,
                    marker,
                    recovery=True,
                )
            if (
                delivery_state == "response_ready"
                and _claim_agentflo_outbound_send(
                    inbound.message_id,
                    http_request_id=http_request_id,
                    request_id=request_id,
                )
            ):
                return _retry_cached_agentflo_outbound(
                    inbound,
                    marker,
                    http_request_id,
                )
            logger.info(
                "Agentflo WhatsApp duplicate ignored",
                extra={
                    "event": "agentflo_whatsapp_duplicate",
                    "http_request_id": http_request_id,
                    "channel": "whatsapp",
                    "reason": "duplicate_message",
                    "idempotency_status": "duplicate",
                    "delivery_state": delivery_state,
                },
            )
            return _agentflo_duplicate_response()

    try:
        conversation_reply = _whatsapp_conversation_service().process_text(
            inbound,
            http_request_id=http_request_id,
        )
    except Exception as exc:
        if inbound.message_id is not None:
            get_services().agent_requests.release_agentflo_whatsapp_message(
                inbound.message_id
            )
        logger.error(
            "Agentflo WhatsApp processing failed",
            extra={
                "event": "agentflo_whatsapp_failed",
                "http_request_id": http_request_id,
                "channel": "whatsapp",
                "exception_type": type(exc).__name__,
                "error_code": "AGENT_INVOCATION_FAILED",
            },
        )
        return _agentflo_failure_response()

    if conversation_reply is None:
        if inbound.message_id is not None:
            get_services().agent_requests.release_agentflo_whatsapp_message(
                inbound.message_id
            )
        return _agentflo_failure_response()
    request_id = conversation_reply.request_id
    session_id = conversation_reply.session_id
    customer_id = conversation_reply.customer_id
    reply_text = conversation_reply.reply
    response.headers["X-Agent-Request-ID"] = request_id
    if inbound.message_id is not None:
        cache_kwargs = {
            "request_id": request_id,
            "session_id": session_id,
            "customer_id": customer_id,
            "reply": reply_text,
        }
        if get_settings().receipt_activation_enabled:
            cache_kwargs["submitted_order_id"] = (
                conversation_reply.submitted_order_id
            )
        get_services().agent_requests.cache_agentflo_whatsapp_response(
            inbound.message_id,
            **cache_kwargs,
        )
        _log_agentflo_delivery_transition(
            previous_state="processing",
            next_state="response_ready",
            applied=True,
            http_request_id=http_request_id,
            request_id=request_id,
        )
        if not _claim_agentflo_outbound_send(
            inbound.message_id,
            http_request_id=http_request_id,
            request_id=request_id,
        ):
            return _agentflo_duplicate_response()
    delivery = _whatsapp_conversation_service().deliver(inbound, conversation_reply)
    outbound = delivery.outbound
    logger.info(
        "Agentflo WhatsApp message completed",
        extra={
            "event": "agentflo_whatsapp_completed",
            "http_request_id": http_request_id,
            "request_id": request_id,
            "actor_id": customer_id,
            "agent_session_id": session_id,
            "channel": "whatsapp",
        },
    )
    outbound_response = _agentflo_outbound_response(
        reply=reply_text,
        request_id=request_id,
        session_id=session_id,
        outbound=outbound,
    )
    if inbound.message_id is not None:
        _finalize_agentflo_text_send(
            inbound,
            success=outbound_response["success"],
            definitely_sent=_agentflo_text_definitely_sent(delivery),
            submitted_order_id=conversation_reply.submitted_order_id,
            http_request_id=http_request_id,
            request_id=request_id,
        )
    return outbound_response


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
        channel=context.channel,
    )
    return _raise_if_error(result.model_dump(exclude_none=True))
