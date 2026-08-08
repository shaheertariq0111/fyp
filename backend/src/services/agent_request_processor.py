from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from src.agent_client.schemas import AgentInvocationRequest
from src.agent.context import AgentRequestContext
from src.agent.response_grounding import AssistantClaimAssessment, ground_agent_response
from src.api.schemas import ChatResponse, ToolCallResult
from src.services.customer_service import CustomerService


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
        if record.get("status") == "completed":
            return AgentProcessingResult(record, context, identity_state, "completed")
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
            grounding_state = self._whatsapp_grounding_state(context)
            invocation = self.agent_client_provider().invoke(AgentInvocationRequest(
                message=payload.message, user_id=context.user_id,
                agent_session_id=context.agent_session_id, request_id=record["request_id"],
                branch_id=payload.branch_id, customer_id=context.customer_id,
                customer_name=context.customer_name, customer_phone=context.customer_phone,
                channel=context.channel,
                expected_write_tool=grounding_state.get("expected_write_tool"),
                available_options=grounding_state.get("available_options"),
            ))
            self.logger.info("Agent runtime invocation completed", extra={
                "event": "agentcore_invocation_completed", "http_request_id": http_request_id,
                "actor_id": context.user_id, "channel": context.channel,
                "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
            })
            response = self.response_builder(context, identity_state, invocation).model_dump(exclude_none=True)
            self._persist_whatsapp_grounding_state(
                context,
                invocation,
                prior_expected_write_tool=grounding_state.get("expected_write_tool"),
            )
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
        except Exception:
            self.logger.exception("Agent request failed", extra={
                "event": "agentcore_invocation_failed", "http_request_id": http_request_id,
                "request_id": record["request_id"], "channel": context.channel,
                "error_code": "AGENT_INVOCATION_FAILED",
            })
            if ambiguous_on_invocation_failure:
                requests.mark_invocation_ambiguous(record["request_id"])
                return AgentProcessingResult(requests.get(record["request_id"]) or record, context, identity_state, "ambiguous")
            record = requests.fail(record["request_id"], error_code="AGENT_INVOCATION_FAILED", message="The request could not be completed.")
            return AgentProcessingResult(record, context, identity_state, "failed")

    def _whatsapp_grounding_state(self, context) -> dict[str, Any]:
        if context.channel != "whatsapp":
            return {}
        state = self.services_provider().agent_sessions.get_whatsapp_order_state(
            context.customer_id or context.user_id,
            context.agent_session_id,
        )
        items = state.get("offered_menu_items") if isinstance(state, dict) else None
        if not isinstance(items, list) or not items:
            return {}
        options = [
            {"id": str(item["product_id"]), "label": str(item.get("name") or item["product_id"])}
            for item in items
            if isinstance(item, dict) and item.get("product_id")
        ]
        return {
            "expected_write_tool": "start_cart_item_customization",
            "available_options": options,
        } if options else {}

    def _persist_whatsapp_grounding_state(
        self,
        context,
        invocation,
        *,
        prior_expected_write_tool: str | None = None,
    ) -> None:
        if context.channel != "whatsapp":
            return
        raw = invocation.raw_result
        calls = raw.get("tool_calls", []) if isinstance(raw, dict) else getattr(raw, "tool_calls", [])
        expected_write_tool = (
            raw.get("expected_write_tool")
            if isinstance(raw, dict)
            else getattr(raw, "expected_write_tool", None)
        )
        sessions = self.services_provider().agent_sessions
        for call in reversed(list(calls or [])):
            tool_name = call.get("tool_name") if isinstance(call, dict) else getattr(call, "tool_name", None)
            success = call.get("success") if isinstance(call, dict) else getattr(call, "success", False)
            result = call.get("result") if isinstance(call, dict) else getattr(call, "result", None)
            if not success or not isinstance(result, dict) or result.get("success") is not True:
                continue
            if tool_name == "start_cart_item_customization":
                sessions.clear_whatsapp_order_state(
                    context.customer_id or context.user_id,
                    context.agent_session_id,
                )
                return
            if tool_name == "search_menu":
                data = result.get("data")
                items = data.get("items") if isinstance(data, dict) else None
                offered = [
                    item for item in items or []
                    if isinstance(item, dict) and item.get("product_id")
                ]
                if (
                    offered
                    and prior_expected_write_tool is None
                    and expected_write_tool == "start_cart_item_customization"
                ):
                    sessions.save_whatsapp_order_state(
                        context.customer_id or context.user_id,
                        context.agent_session_id,
                        offered_menu_items=offered,
                        shown_menu_item_ids=[str(item["product_id"]) for item in offered],
                        menu_has_more=bool(data.get("has_more")),
                    )
                return


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


def build_response_builder(services_provider: Callable[[], Any]):
    def response(context, identity_state, invocation):
        raw = invocation.raw_result if isinstance(invocation.raw_result, dict) else {}
        calls = [call if isinstance(call, ToolCallResult) else ToolCallResult(**call) for call in (raw.get("tool_calls") or [])]
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
            if call.success and call.tool_name == "search_menu" and isinstance(data.get("items"), list):
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
            if call.success and call.tool_name == "get_menu_item" and isinstance(data.get("item"), dict):
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
            assessment_payload = raw.get("claim_assessment")
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
                tool_calls=calls,
                claim_assessment=assessment,
                no_write_authorized=bool(raw.get("no_write_authorized", False)),
                informational_turn=bool(raw.get("informational_turn", False)),
                expected_write_tool=raw.get("expected_write_tool"),
            ).text
        return ChatResponse(
            text=response_text, session_id=context.agent_session_id,
            user_id=context.user_id, customer_id=context.customer_id,
            customer=identity_state["customer"], data=state, tool_calls=calls,
            write_succeeded=write_succeeded, state=state, buttons=buttons,
        )
    return response
