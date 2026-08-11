from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Callable

from src.agent_client.schemas import AgentInvocationRequest
from src.agent.context import AgentRequestContext
from src.agent.response_grounding import (
    AssistantClaimAssessment,
    ground_agent_response,
    ground_authoritative_tool_response,
    grounding_comparison_log_fields,
    grounding_decision_log_fields,
    safe_assessment_origin_log_value,
    safe_semantic_classifier_status_log_value,
    safe_transactional_effect_log_value,
)
from src.api.schemas import ChatResponse, ToolCallResult
from src.models.tool_responses import (
    apply_legacy_item_selection_compatibility,
)
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
            grounding_state = self._whatsapp_grounding_state(context)
        except Exception as exc:
            self.logger.error("Agent pre-invocation state load failed", extra={
                "event": "agent_pre_invocation_state_failed",
                "http_request_id": http_request_id,
                "request_id": record["request_id"],
                "channel": context.channel,
                "error_code": "AGENT_SESSION_STATE_LOAD_FAILED",
                "exception_type": type(exc).__name__,
            })
            record = requests.fail_before_invocation(
                record["request_id"],
                error_code="AGENT_SESSION_STATE_LOAD_FAILED",
                message="The request could not be completed.",
            )
            return AgentProcessingResult(
                record, context, identity_state, "failed"
            )
        try:
            started = time.perf_counter()
            invocation = self.agent_client_provider().invoke(AgentInvocationRequest(
                message=payload.message, user_id=context.user_id,
                agent_session_id=context.agent_session_id, request_id=record["request_id"],
                branch_id=payload.branch_id, customer_id=context.customer_id,
                customer_name=context.customer_name, customer_phone=context.customer_phone,
                channel=context.channel,
                expected_write_tool=grounding_state.get("expected_write_tool"),
                required_effect=grounding_state.get("required_effect"),
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
                prior_required_effect=grounding_state.get("required_effect"),
                prior_available_option_count=len(
                    grounding_state.get("available_options") or []
                ),
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

    def _whatsapp_grounding_state(self, context) -> dict[str, Any]:
        if context.channel != "whatsapp":
            return {}
        state = self.services_provider().agent_sessions.get_whatsapp_order_state(
            context.customer_id or context.user_id,
            context.agent_session_id,
        )
        items = state.get("offered_menu_items") if isinstance(state, dict) else None
        state_age_ms = None
        updated_at = (
            state.get("whatsapp_order_state_updated_at")
            if isinstance(state, dict)
            else None
        )
        if isinstance(updated_at, str):
            try:
                timestamp = datetime.fromisoformat(updated_at)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                state_age_ms = max(
                    0,
                    round(
                        (
                            datetime.now(timezone.utc)
                            - timestamp.astimezone(timezone.utc)
                        ).total_seconds()
                        * 1000,
                        2,
                    ),
                )
            except ValueError:
                state_age_ms = None
        self.logger.info(
            "WhatsApp grounding state loaded",
            extra={
                "event": "whatsapp_grounding_state_loaded",
                "contract_present": bool(isinstance(items, list) and items),
                "required_effect_present": bool(
                    safe_transactional_effect_log_value(
                        state.get("whatsapp_required_effect")
                        if isinstance(state, dict)
                        else None
                    )
                ),
                "required_effect": safe_transactional_effect_log_value(
                    state.get("whatsapp_required_effect")
                    if isinstance(state, dict)
                    else None
                ),
                "available_option_count": (
                    len(items) if isinstance(items, list) else 0
                ),
                "state_age_ms": state_age_ms,
            },
        )
        if not isinstance(items, list) or not items:
            return {}
        options = [
            {"id": str(item["product_id"]), "label": str(item.get("name") or item["product_id"])}
            for item in items
            if isinstance(item, dict) and item.get("product_id")
        ]
        required_effect, expected_write_tool = (
            apply_legacy_item_selection_compatibility(
                state.get("whatsapp_required_effect"),
                has_legacy_offered_options=bool(options),
            )
        )
        return {
            "expected_write_tool": expected_write_tool,
            "required_effect": required_effect,
            "available_options": options,
        } if options else {}

    def _persist_whatsapp_grounding_state(
        self,
        context,
        invocation,
        *,
        prior_expected_write_tool: str | None = None,
        prior_required_effect: str | None = None,
        prior_available_option_count: int = 0,
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
        raw_required_effect = (
            raw.get("required_effect")
            if isinstance(raw, dict)
            else getattr(raw, "required_effect", None)
        )
        required_effect, _legacy_expected_write_tool = (
            apply_legacy_item_selection_compatibility(
                raw_required_effect,
                expected_write_tool=expected_write_tool,
            )
        )
        sessions = self.services_provider().agent_sessions
        successful_effects: set[str] = set()
        declared_requirement: str | None = None
        offered_options: list[dict[str, str]] = []
        menu_has_more = False
        for call in reversed(list(calls or [])):
            success = call.get("success") if isinstance(call, dict) else getattr(call, "success", False)
            result = call.get("result") if isinstance(call, dict) else getattr(call, "result", None)
            if not success or not isinstance(result, dict) or result.get("success") is not True:
                continue
            evidence = result.get("grounding")
            if not isinstance(evidence, dict):
                continue
            successful_effects.update(
                str(effect)
                for effect in evidence.get("transactional_effects", [])
            )
            if declared_requirement is None and evidence.get("required_next_effect"):
                declared_requirement = str(evidence["required_next_effect"])
                offered_options = [
                    option
                    for option in evidence.get("offered_options", [])
                    if isinstance(option, dict) and option.get("id") and option.get("label")
                ]
                data = result.get("data")
                menu_has_more = bool(
                    isinstance(data, dict) and data.get("has_more")
                )
        if prior_required_effect and prior_required_effect in successful_effects:
            sessions.clear_whatsapp_order_state(
                context.customer_id or context.user_id,
                context.agent_session_id,
            )
            self._log_whatsapp_grounding_state_transition(
                state_action="contract_cleared_required_effect_satisfied",
                prior_required_effect=prior_required_effect,
                declared_requirement=declared_requirement,
                prior_available_option_count=prior_available_option_count,
                produced_option_count=len(offered_options),
                prior_required_effect_satisfied=True,
                state_cleared=True,
                state_clear_reason="required_effect_satisfied",
            )
            return
        if (
            offered_options
            and prior_required_effect is None
            and required_effect == declared_requirement
        ):
            offered = [
                {"product_id": option["id"], "name": option["label"]}
                for option in offered_options
            ]
            sessions.save_whatsapp_order_state(
                context.customer_id or context.user_id,
                context.agent_session_id,
                offered_menu_items=offered,
                shown_menu_item_ids=[option["id"] for option in offered_options],
                menu_has_more=menu_has_more,
                required_effect=declared_requirement,
            )
            self._log_whatsapp_grounding_state_transition(
                state_action="contract_created",
                prior_required_effect=prior_required_effect,
                declared_requirement=declared_requirement,
                prior_available_option_count=prior_available_option_count,
                produced_option_count=len(offered_options),
                prior_required_effect_satisfied=False,
                state_cleared=False,
            )
            return
        if offered_options and prior_required_effect is not None:
            state_action = "contract_retained_existing_requirement"
        elif offered_options and required_effect != declared_requirement:
            state_action = "contract_not_persisted_requirement_mismatch"
        else:
            state_action = "no_contract_change"
        self._log_whatsapp_grounding_state_transition(
            state_action=state_action,
            prior_required_effect=prior_required_effect,
            declared_requirement=declared_requirement,
            prior_available_option_count=prior_available_option_count,
            produced_option_count=len(offered_options),
            prior_required_effect_satisfied=False,
            state_cleared=False,
        )

    def _log_whatsapp_grounding_state_transition(
        self,
        *,
        state_action: str,
        prior_required_effect: str | None,
        declared_requirement: str | None,
        prior_available_option_count: int,
        produced_option_count: int,
        prior_required_effect_satisfied: bool,
        state_cleared: bool,
        state_clear_reason: str | None = None,
    ) -> None:
        self.logger.info(
            "WhatsApp grounding state transition observed",
            extra={
                "event": "whatsapp_grounding_state_transition",
                "state_action": state_action,
                "existing_contract_present": prior_required_effect is not None,
                "new_contract_produced": declared_requirement is not None,
                "existing_option_count": prior_available_option_count,
                "produced_option_count": produced_option_count,
                "prior_required_effect_satisfied": prior_required_effect_satisfied,
                "contract_created": state_action == "contract_created",
                "contract_retained": state_action
                == "contract_retained_existing_requirement",
                "contract_replaced": False,
                "state_cleared": state_cleared,
                "state_clear_reason": state_clear_reason,
                "required_effect_present": bool(
                    safe_transactional_effect_log_value(declared_requirement)
                ),
                "required_effect": safe_transactional_effect_log_value(
                    declared_requirement
                ),
            },
        )


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
            authoritative = ground_authoritative_tool_response(
                tool_calls=calls,
                expected_write_tool=raw.get("expected_write_tool"),
                required_effect=raw.get("required_effect"),
            )
            if authoritative is not None:
                response_text = authoritative.text
                backend_grounded = authoritative
                assessment_transport_status = "not_applicable_fast_path"
            else:
                assessment_payload = raw.get("claim_assessment")
                if assessment_payload is None:
                    assessment_transport_status = "missing"
                    logger.info(
                        "Runtime grounding metadata was missing",
                        extra={
                            "event": "grounding_boundary_metadata_issue",
                            "boundary_metadata_issue": "claim_assessment_missing",
                            "assessment_origin": "boundary_missing_synthetic",
                        },
                    )
                    assessment = AssistantClaimAssessment(
                        claims_transactional_progression=True,
                        claimed_actions=["other_transactional_progression"],
                    )
                else:
                    try:
                        assessment = AssistantClaimAssessment.model_validate(
                            assessment_payload
                        )
                    except Exception as exc:
                        logger.warning(
                            "Runtime grounding metadata was invalid",
                            extra={
                                "event": "grounding_boundary_metadata_issue",
                                "boundary_metadata_issue": "claim_assessment_invalid",
                                "exception_type": type(exc).__name__,
                            },
                        )
                        raise
                    assessment_transport_status = "present_valid"
                backend_grounded = ground_agent_response(
                    text=invocation.text,
                    tool_calls=calls,
                    claim_assessment=assessment,
                    no_write_authorized=bool(raw.get("no_write_authorized", False)),
                    informational_turn=bool(raw.get("informational_turn", False)),
                    expected_write_tool=raw.get("expected_write_tool"),
                    required_effect=raw.get("required_effect"),
                )
                response_text = backend_grounded.text
            runtime_source = raw.get("grounding_source")
            runtime_reason = raw.get("grounding_rejection_reason")
            runtime_metadata_present = bool(
                raw.get("semantic_classifier_status")
                or raw.get("assessment_origin")
                or "grounding_rejection_reason" in raw
            )
            if runtime_source is None:
                logger.info(
                    "Runtime grounding source was missing",
                    extra={
                        "event": "grounding_boundary_metadata_issue",
                        "boundary_metadata_issue": "grounding_source_missing",
                    },
                )
            if (
                runtime_source == "ungrounded_transaction_fallback"
                and "grounding_rejection_reason" not in raw
            ):
                logger.info(
                    "Runtime grounding rejection reason was missing",
                    extra={
                        "event": "grounding_boundary_metadata_issue",
                        "boundary_metadata_issue": "grounding_rejection_reason_missing",
                    },
                )
            comparison = grounding_comparison_log_fields(
                runtime_grounding_source=runtime_source,
                runtime_grounding_rejection_reason=runtime_reason,
                runtime_text=invocation.text,
                backend_response=backend_grounded,
                runtime_claim_assessment_present=raw.get("claim_assessment") is not None,
                runtime_grounding_metadata_present=runtime_metadata_present,
                assessment_transport_status=assessment_transport_status,
            )
            logger.info(
                "Backend WhatsApp response grounded",
                extra={
                    "event": "backend_grounding_completed",
                    "assessment_origin": (
                        safe_assessment_origin_log_value(
                            raw.get("assessment_origin")
                        )
                        if assessment_transport_status != "missing"
                        else "boundary_missing_synthetic"
                    ),
                    "semantic_classifier_status": (
                        safe_semantic_classifier_status_log_value(
                            raw.get("semantic_classifier_status")
                        )
                    ),
                    **grounding_decision_log_fields(backend_grounded),
                    **comparison,
                },
            )
            if not comparison["runtime_backend_grounding_agree"]:
                logger.warning(
                    "Runtime and backend grounding decisions differed",
                    extra={
                        "event": "grounding_decision_mismatch",
                        **comparison,
                    },
                )
        return ChatResponse(
            text=response_text, session_id=context.agent_session_id,
            user_id=context.user_id, customer_id=context.customer_id,
            customer=identity_state["customer"], data=state, tool_calls=calls,
            write_succeeded=write_succeeded, state=state, buttons=buttons,
        )
    return response
