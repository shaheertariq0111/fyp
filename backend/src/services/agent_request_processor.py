from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from src.agent_client.schemas import AgentInvocationRequest
from src.agent.context import AgentRequestContext
from src.agent.response_grounding import (
    ground_agent_response,
    grounding_decision_log_fields,
)
from src.api.schemas import ChatResponse, ToolCallResult
from src.services.customer_service import CustomerService


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentProcessingResult:
    record: dict[str, Any]
    context: Any
    identity_state: dict[str, Any]
    outcome: str


@dataclass(frozen=True, slots=True)
class PreparedAgentRequest:
    payload: Any
    record: dict[str, Any]
    context: Any
    identity_state: dict[str, Any]


class AgentRequestProcessor:
    """Transport-neutral AgentRequest orchestration shared by HTTP and workers."""

    def __init__(self, *, services_provider: Callable[[], Any], agent_client_provider: Callable[[], Any], identity_resolver: Callable[..., tuple[Any, dict[str, Any]]], response_builder: Callable[[Any, dict[str, Any], Any], Any], logger: logging.Logger | None = None) -> None:
        self.services_provider = services_provider
        self.agent_client_provider = agent_client_provider
        self.identity_resolver = identity_resolver
        self.response_builder = response_builder
        self.logger = logger or logging.getLogger(__name__)

    def process(self, payload, http_request_id: str | None = None, *, allow_requested_session_creation: bool = False, deterministic_request_id: str | None = None, ambiguous_on_invocation_failure: bool = False) -> AgentProcessingResult:
        prepared = self.prepare(
            payload,
            allow_requested_session_creation=allow_requested_session_creation,
            deterministic_request_id=deterministic_request_id,
        )
        return self.invoke_prepared(
            prepared,
            http_request_id=http_request_id,
            ambiguous_on_invocation_failure=ambiguous_on_invocation_failure,
        )

    def prepare(self, payload, *, allow_requested_session_creation: bool = False, deterministic_request_id: str | None = None) -> PreparedAgentRequest:
        context, identity_state = self.identity_resolver(
            payload, allow_requested_session_creation=allow_requested_session_creation
        )
        context.current_message = payload.message
        requests = self.services_provider().agent_requests
        start_args = dict(
            actor_id=context.user_id, session_id=context.agent_session_id,
            message=payload.message, channel=context.channel,
            request_payload=payload.model_dump(),
        )
        if hasattr(requests, "start_or_resume_processing"):
            record, _created = requests.start_or_resume_processing(
                **start_args, request_id=deterministic_request_id
            )
        else:
            record, _created = requests.start_processing(**start_args), True
        context.request_id = record["request_id"]
        return PreparedAgentRequest(payload, record, context, identity_state)

    def invoke_prepared(self, prepared: PreparedAgentRequest, *, http_request_id: str | None = None, ambiguous_on_invocation_failure: bool = False) -> AgentProcessingResult:
        payload, record = prepared.payload, prepared.record
        context, identity_state = prepared.context, prepared.identity_state
        requests = self.services_provider().agent_requests
        if record.get("status") in {"completed", "failed"}:
            return AgentProcessingResult(
                record, context, identity_state, record["status"]
            )
        if hasattr(requests, "claim_invocation") and not requests.claim_invocation(record["request_id"]):
            latest = requests.get(record["request_id"]) or record
            state = latest.get("invocation_state")
            outcome = "ambiguous" if state in {"invoking", "ambiguous"} else "in_progress"
            return AgentProcessingResult(latest, context, identity_state, outcome)
        self.logger.info("Agent request processing started", extra={
            "event": "agent_request_started", "http_request_id": http_request_id,
            "actor_id": context.user_id, "channel": context.channel,
        })
        try:
            started = time.perf_counter()
            invocation = self.agent_client_provider().invoke(AgentInvocationRequest(
                message=payload.message, user_id=context.user_id,
                agent_session_id=context.agent_session_id, request_id=record["request_id"],
                branch_id=payload.branch_id, customer_id=context.customer_id,
                customer_name=context.customer_name, customer_phone=context.customer_phone,
                channel=context.channel,
            ))
            self.logger.info("Agent runtime invocation completed", extra={
                "event": "agentcore_invocation_completed", "http_request_id": http_request_id,
                "actor_id": context.user_id, "channel": context.channel,
                "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
            })
            response = self.response_builder(context, identity_state, invocation).model_dump(exclude_none=True)
            record = requests.complete(record["request_id"], response)
            self.logger.info(
                "Agent request processing completed",
                extra={
                    "event": "agent_request_completed",
                    "http_request_id": http_request_id,
                    "actor_id": context.user_id,
                    "channel": context.channel,
                    "agent_request_status": record["status"],
                },
            )
            return AgentProcessingResult(record, context, identity_state, "completed")
        except Exception as exc:
            self.logger.error("Agent request failed", extra={
                "event": "agentcore_invocation_failed", "http_request_id": http_request_id,
                "request_id": record["request_id"], "channel": context.channel,
                "error_code": "AGENT_INVOCATION_FAILED",
                "exception_type": type(exc).__name__,
            })
            if ambiguous_on_invocation_failure:
                requests.mark_invocation_ambiguous(record["request_id"])
                return AgentProcessingResult(requests.get(record["request_id"]) or record, context, identity_state, "ambiguous")
            record = requests.fail(record["request_id"], error_code="AGENT_INVOCATION_FAILED", message="The request could not be completed.")
            return AgentProcessingResult(record, context, identity_state, "failed")


def build_identity_resolver(services_provider: Callable[[], Any]):
    def resolve(payload, *, allow_requested_session_creation: bool = False):
        services = services_provider()
        requested_customer_id = payload.customer_id or (
            payload.user_id if payload.user_id != "anonymous" else None
        )
        resolved = services.agent_sessions.resolve(
            requested_session_id=payload.session_id,
            customer_id=requested_customer_id,
            channel=payload.channel or "web",
            preserve_expired=allow_requested_session_creation,
            force_new=payload.force_new_session,
            allow_requested_session_creation=allow_requested_session_creation,
        )
        session, customer = resolved["session"], resolved["customer"]
        public = {
            "customer_id": customer.get("customer_id"),
            "display_name": customer.get("display_name"),
            "phone_e164": customer.get("phone_e164"),
            "phone_verified": customer.get("phone_verified", False),
        }
        context = AgentRequestContext(
            user_id=customer["customer_id"], agent_session_id=session["agent_session_id"],
            branch_id=payload.branch_id, customer_id=customer["customer_id"],
            customer_name=CustomerService.confirmed_name(customer),
            customer_phone=customer.get("phone_e164"), channel=session.get("channel", "web"),
        )
        return context, {"session": {
            "session_id": session["agent_session_id"], "expires_at": session.get("expires_at"),
            "channel": session.get("channel"), "rotated": resolved.get("rotated", False),
        }, "customer": public}
    return resolve


def _tool_calls_from_result(result: Any) -> list[ToolCallResult]:
    raw_calls = (
        result.get("tool_calls")
        if isinstance(result, dict)
        else getattr(result, "tool_calls", None)
    )
    if not isinstance(raw_calls, list):
        return []
    return [
        ToolCallResult.model_validate(call, from_attributes=True)
        for call in raw_calls
    ]


def build_response_builder(services_provider: Callable[[], Any]):
    def response(context, identity_state, invocation):
        calls = _tool_calls_from_result(invocation.raw_result)
        state: dict[str, Any] = dict(identity_state)
        buttons: list[dict[str, Any]] = []
        grounded: str | None = None
        write_succeeded = False
        for call in calls:
            write_succeeded = write_succeeded or (call.is_write and call.success)
            result = call.result or {}
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            for key in ("cart", "order", "orders"):
                if key in data:
                    state[key] = data[key]
            if result.get("buttons"):
                buttons = result["buttons"]
            if (
                context.channel != "whatsapp"
                and call.success
                and call.tool_name == "search_menu"
                and isinstance(data.get("items"), list)
            ):
                items = data["items"]
                if not items:
                    grounded = result.get("user_message") or "I couldn't find a matching available menu item."
                else:
                    lines = []
                    for item in items:
                        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                            continue
                        price = item.get("price", item.get("starting_price"))
                        label = f"{item.get('currency', '')} {price}".strip() if price is not None else "price shown on menu"
                        lines.append(f"{len(lines) + 1}. {item['name']} - {label}")
                    grounded = "Here are the current menu options I found:\n" + "\n".join(lines) + "\nWhich item would you like?"
            if (
                context.channel != "whatsapp"
                and call.success
                and call.tool_name == "get_menu_item"
                and isinstance(data.get("item"), dict)
            ):
                item = data["item"]
                if item.get("name"):
                    grounded = str(item["name"])
                    if item.get("description"):
                        grounded += "\n" + str(item["description"])
        if write_succeeded:
            services = services_provider()
            try:
                state["cart"] = services.carts.get_active_cart(context.user_id, context.agent_session_id).model_dump(exclude_none=True).get("data", {}).get("cart")
            except Exception:
                pass
            try:
                state["orders"] = services.orders.get_order_status(context.user_id).model_dump(exclude_none=True).get("data", {}).get("orders", [])
            except Exception:
                pass
        response_text = grounded or invocation.text
        if context.channel == "whatsapp":
            backend_grounded = ground_agent_response(
                text=response_text,
                tool_calls=calls,
            )
            response_text = backend_grounded.text
            logger.info(
                "Backend WhatsApp response selected",
                extra={
                    "event": "backend_response_selected",
                    **grounding_decision_log_fields(backend_grounded),
                },
            )
        return ChatResponse(
            text=response_text, session_id=context.agent_session_id,
            user_id=context.user_id, customer_id=context.customer_id,
            customer=identity_state["customer"], data=state, tool_calls=calls,
            write_succeeded=write_succeeded, state=state, buttons=buttons,
        )
    return response
