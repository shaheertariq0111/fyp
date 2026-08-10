from __future__ import annotations

import logging
import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from src.api.schemas import ChatRequest
from src.api.whatsapp import WhatsAppInboundMessage
from src.services.customer_service import CustomerService


UNGROUNDED_ORDER_SUBMISSION_FALLBACK = (
    "I couldn't verify that this order was submitted, so I won't confirm it as "
    "placed. Please ask me to check your order status before trying again."
)
UNGROUNDED_ORDER_SUBMISSION_ERROR_CODE = (
    "UNGROUNDED_ORDER_SUBMISSION_CLAIM"
)
AUTHORITATIVE_SUBMISSION_TOOLS = {"confirm_order", "update_order_flow"}
WHATSAPP_MENU_LINK_FORBIDDEN_ERROR_CODE = "WHATSAPP_MENU_LINK_FORBIDDEN"
WHATSAPP_MENU_LINK_FORBIDDEN_FALLBACK = (
    "I can help you browse the menu and complete your order here in WhatsApp."
)
_ORDER_SUBMISSION_CLAIM_PATTERNS = (
    re.compile(
        r"\b(?:your|the|this)\s+order\s+"
        r"(?:has\s+been|was|is)\s+(?:now\s+)?(?:successfully\s+)?"
        r"(?:submitted|placed|confirmed)(?:\s+successfully)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:your|the|this)\s+order\s+\S+\s+"
        r"(?:has\s+been|was|is)\s+(?:now\s+)?(?:successfully\s+)?"
        r"(?:submitted|placed|confirmed)(?:\s+successfully)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\border\s+(?:now\s+)?successfully\s+"
        r"(?:submitted|placed|confirmed)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:i|we)(?:'ve|\s+have)?\s+(?:successfully\s+)?"
        r"(?:submitted|placed|confirmed)\s+(?:your|the)\s+order\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\border\s+id\s*:\s*\S+.{0,500}"
        r"\bstatus\s*:\s*submitted\s+to\s+(?:the\s+)?restaurant\b",
        re.IGNORECASE | re.DOTALL,
    ),
)


@dataclass(frozen=True, slots=True)
class AuthoritativeOrderSubmission:
    order_id: str
    confirmation_text: str


@dataclass(frozen=True, slots=True)
class WhatsAppConversationReply:
    reply: str
    request_id: str
    session_id: str
    customer_id: str
    resumed: bool = False
    submitted_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class WhatsAppDeliveryOutcome:
    status: str
    outbound: dict[str, Any]

    @property
    def successful(self) -> bool:
        return self.status in {"sent", "skipped"}


@dataclass(frozen=True, slots=True)
class PreparedWhatsAppConversation:
    inbound: WhatsAppInboundMessage
    prepared_agent_request: Any


class WhatsAppConversationService:
    """Shared WhatsApp history, AgentRequest, and outbound delivery behavior."""

    def __init__(self, *, services_provider: Callable[[], Any], processor, identity_builder: Callable[[WhatsAppInboundMessage], tuple[str, str]], gateway_provider: Callable[[], Any], logger: logging.Logger | None = None) -> None:
        self.services_provider = services_provider
        self.processor = processor
        self.identity_builder = identity_builder
        self.gateway_provider = gateway_provider
        self.logger = logger or logging.getLogger(__name__)

    def process_text(self, inbound: WhatsAppInboundMessage, *, http_request_id: str | None = None, deterministic_request_id: str | None = None, history_identifier: str | None = None, ambiguous_on_failure: bool = False) -> WhatsAppConversationReply | None:
        prepared = self.prepare_text(
            inbound, deterministic_request_id=deterministic_request_id,
            history_identifier=history_identifier, http_request_id=http_request_id,
        )
        return self.invoke_prepared(
            prepared, http_request_id=http_request_id,
            ambiguous_on_failure=ambiguous_on_failure,
        )

    def prepare_text(self, inbound: WhatsAppInboundMessage, *, deterministic_request_id: str | None = None, history_identifier: str | None = None, http_request_id: str | None = None) -> PreparedWhatsAppConversation:
        customer_id, session_id = self.identity_builder(inbound)
        history = self.services_provider().conversation_history
        try:
            history.store_inbound_whatsapp_message(
                conversation_id=session_id, customer_id=customer_id,
                message_text=inbound.text, inbound_message_id=inbound.message_id,
                customer_number=inbound.customer_number,
                idempotency_identifier=history_identifier,
            )
        except TypeError:
            # Compatibility for existing test doubles and pre-voice callers.
            history.store_inbound_whatsapp_message(
                conversation_id=session_id, customer_id=customer_id,
                message_text=inbound.text, inbound_message_id=inbound.message_id,
                customer_number=inbound.customer_number,
            )
        except Exception:
            self.logger.warning("WhatsApp inbound history unavailable", extra={
                "event": "conversation_history_write_failed",
                "error_code": "CONVERSATION_HISTORY_WRITE_FAILED",
                "http_request_id": http_request_id,
                "channel": "whatsapp",
                "direction": "inbound",
            })
        prepared = self.processor.prepare(
            ChatRequest(message=inbound.text, session_id=session_id, user_id=customer_id, customer_id=customer_id, channel="whatsapp"),
            allow_requested_session_creation=True,
            deterministic_request_id=deterministic_request_id,
        )
        return PreparedWhatsAppConversation(inbound, prepared)

    def invoke_prepared(self, prepared: PreparedWhatsAppConversation, *, http_request_id: str | None = None, ambiguous_on_failure: bool = False) -> WhatsAppConversationReply | None:
        result = self.processor.invoke_prepared(
            prepared.prepared_agent_request, http_request_id=http_request_id,
            ambiguous_on_invocation_failure=ambiguous_on_failure,
        )
        if result.outcome == "ambiguous":
            return None
        response = result.record.get("response") if isinstance(result.record, dict) else None
        if result.record.get("status") != "completed":
            return None
        return whatsapp_reply_from_response(
            response,
            request_id=result.record["request_id"],
            session_id=result.context.agent_session_id,
            customer_id=result.context.customer_id,
            resumed=result.outcome == "completed",
            logger=self.logger,
        )

    def deliver(self, inbound: WhatsAppInboundMessage, reply: WhatsAppConversationReply, *, history_identifier: str | None = None, ambiguous_on_exception: bool = False) -> WhatsAppDeliveryOutcome:
        gateway = self.gateway_provider()
        if not gateway.configured:
            outbound = {"sent": False, "skipped": True, "reason": "gateway_not_configured"}
            status = "skipped"
        elif inbound.sender_id is None or inbound.customer_number is None:
            outbound = {"sent": False, "error_code": "AGENTFLO_OUTBOUND_FAILED"}
            status = "permanent_failure"
        else:
            try:
                outbound = gateway.send_text(
                    customer_number=inbound.customer_number, conversation_id=reply.session_id,
                    sender_id=inbound.sender_id, text=reply.reply, request_id=reply.request_id,
                )
                status = "sent" if outbound.get("sent") else "retryable_failure"
            except Exception:
                # The gateway operation may have been accepted. Callers must not
                # automatically replay this ambiguous outcome.
                outbound = {"sent": False, "error_code": (
                    "AGENTFLO_OUTBOUND_AMBIGUOUS" if ambiguous_on_exception
                    else "AGENTFLO_OUTBOUND_FAILED"
                )}
                status = "ambiguous" if ambiguous_on_exception else "retryable_failure"
        try:
            self.services_provider().conversation_history.store_outbound_whatsapp_message(
                conversation_id=reply.session_id, customer_id=reply.customer_id,
                message_text=reply.reply, request_id=reply.request_id,
                inbound_message_id=inbound.message_id, outbound=outbound,
                idempotency_identifier=history_identifier,
            )
        except TypeError:
            self.services_provider().conversation_history.store_outbound_whatsapp_message(
                conversation_id=reply.session_id, customer_id=reply.customer_id,
                message_text=reply.reply, request_id=reply.request_id,
                inbound_message_id=inbound.message_id, outbound=outbound,
            )
        except Exception:
            self.logger.warning("WhatsApp outbound history unavailable", extra={
                "event": "conversation_history_write_failed",
                "error_code": "CONVERSATION_HISTORY_WRITE_FAILED",
                "channel": "whatsapp",
                "direction": "outbound",
            })
        return WhatsAppDeliveryOutcome(status, outbound)


def build_whatsapp_identity(inbound: WhatsAppInboundMessage, services_provider: Callable[[], Any]) -> tuple[str, str]:
    normalized_phone = CustomerService.normalize_phone(inbound.customer_number) if inbound.customer_number else None
    identity_parts = [normalized_phone] if normalized_phone else [part for part in (inbound.sender_id, inbound.message_id) if part]
    identity_hash = hashlib.sha256(("|".join(identity_parts) or str(uuid.uuid4())).encode()).hexdigest()[:32]
    customer_id = session_id = f"whatsapp-{identity_hash}"
    if normalized_phone is not None or inbound.customer_name is not None:
        profile_result = services_provider().customers.update_profile(
            customer_id, whatsapp_profile_name=inbound.customer_name,
            phone_number=normalized_phone, channel="whatsapp", phone_verified=normalized_phone is not None,
            name_source="whatsapp_profile",
        )
        if profile_result.success:
            profile = profile_result.data.get("customer") or {}
            actual_customer_id = profile.get("customer_id")
            if isinstance(actual_customer_id, str) and actual_customer_id:
                customer_id = actual_customer_id
    return customer_id, session_id


def submitted_order_id_from_response(response: Any) -> str | None:
    submission = authoritative_order_submission_from_response(response)
    return submission.order_id if submission is not None else None


def authoritative_order_submission_from_response(
    response: Any,
) -> AuthoritativeOrderSubmission | None:
    if not isinstance(response, dict):
        return None
    tool_calls = response.get("tool_calls")
    if not isinstance(tool_calls, list):
        return None

    proofs: set[tuple[str, str]] = set()
    for call in tool_calls:
        if (
            not isinstance(call, dict)
            or call.get("success") is not True
            or call.get("tool_name") not in AUTHORITATIVE_SUBMISSION_TOOLS
        ):
            continue
        result = call.get("result")
        if not isinstance(result, dict) or result.get("success") is not True:
            continue
        data = result.get("data")
        if not isinstance(data, dict):
            continue
        if data.get("status") != "submitted_to_restaurant":
            continue
        order_id = data.get("order_id")
        if not _is_canonical_string(order_id):
            continue
        agent = result.get("agent")
        if not isinstance(agent, dict):
            continue
        submitted_order_id = agent.get("submitted_order_id")
        confirmation_text = agent.get("submission_confirmation")
        if (
            not _is_canonical_string(submitted_order_id)
            or submitted_order_id != order_id
            or not _is_canonical_string(confirmation_text)
        ):
            continue
        proofs.add((order_id, confirmation_text))

    if len(proofs) != 1:
        return None
    order_id, confirmation_text = next(iter(proofs))
    return AuthoritativeOrderSubmission(order_id, confirmation_text)


def whatsapp_reply_from_response(
    response: Any,
    *,
    request_id: str,
    session_id: str,
    customer_id: str,
    resumed: bool = False,
    logger: logging.Logger | None = None,
    menu_site_base_url: str | None = None,
) -> WhatsAppConversationReply | None:
    reply = response.get("text") if isinstance(response, dict) else None
    if not isinstance(reply, str) or not reply.strip():
        return None

    if menu_site_base_url is None:
        try:
            from src.infrastructure.config import get_settings
            menu_site_base_url = str(get_settings().menu_site_base_url)
        except Exception:
            menu_site_base_url = None
    if _contains_forbidden_menu_session_evidence(response) or (
        menu_site_base_url and _contains_configured_menu_site_url(reply, menu_site_base_url)
    ):
        (logger or logging.getLogger(__name__)).warning(
            "WhatsApp menu-session artifact blocked",
            extra={
                "event": "whatsapp_menu_link_forbidden",
                "error_code": WHATSAPP_MENU_LINK_FORBIDDEN_ERROR_CODE,
                "channel": "whatsapp",
                "request_id": request_id,
                "agent_session_id": session_id,
            },
        )
        return WhatsAppConversationReply(
            WHATSAPP_MENU_LINK_FORBIDDEN_FALLBACK,
            request_id,
            session_id,
            customer_id,
            resumed=resumed,
        )

    submission = authoritative_order_submission_from_response(response)
    submitted_order_id = None
    if submission is not None:
        reply = submission.confirmation_text
        submitted_order_id = submission.order_id
    elif (
        _claims_successful_order_submission(reply)
        and not _is_authoritative_existing_order_status(reply, response)
    ):
        (logger or logging.getLogger(__name__)).warning(
            "Ungrounded order submission claim blocked",
            extra={
                "event": "ungrounded_order_submission_claim_blocked",
                "error_code": UNGROUNDED_ORDER_SUBMISSION_ERROR_CODE,
                "channel": "whatsapp",
                "request_id": request_id,
                "agent_session_id": session_id,
            },
        )
        reply = UNGROUNDED_ORDER_SUBMISSION_FALLBACK

    return WhatsAppConversationReply(
        reply,
        request_id,
        session_id,
        customer_id,
        resumed=resumed,
        submitted_order_id=submitted_order_id,
    )


def _claims_successful_order_submission(text: str) -> bool:
    return any(pattern.search(text) for pattern in _ORDER_SUBMISSION_CLAIM_PATTERNS)


def _is_authoritative_existing_order_status(text: str, response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    tool_calls = response.get("tool_calls")
    if not isinstance(tool_calls, list):
        return False

    trusted_messages: set[str] = set()
    for call in tool_calls:
        if (
            not isinstance(call, dict)
            or call.get("tool_name") != "get_order_status"
            or call.get("success") is not True
        ):
            continue
        result = call.get("result")
        if not isinstance(result, dict) or result.get("success") is not True:
            continue
        data = result.get("data")
        agent = result.get("agent")
        if not isinstance(data, dict) or not isinstance(agent, dict):
            continue
        status_message = agent.get("status_message")
        selected_order_id = agent.get("selected_order_id")
        if (
            not _is_canonical_string(status_message)
            or result.get("user_message") != status_message
            or not _is_canonical_string(selected_order_id)
        ):
            continue
        orders = []
        if isinstance(data.get("order"), dict):
            orders.append(data["order"])
        if isinstance(data.get("orders"), list):
            orders.extend(
                order for order in data["orders"] if isinstance(order, dict)
            )
        if any(
            order.get("order_id") == selected_order_id
            and order.get("status") == "submitted_to_restaurant"
            for order in orders
        ):
            trusted_messages.add(status_message)
    return text in trusted_messages


def _is_canonical_string(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
    )


def _contains_forbidden_menu_session_evidence(response: Any) -> bool:
    if not isinstance(response, dict) or not isinstance(response.get("tool_calls"), list):
        return False
    for call in response["tool_calls"]:
        if not isinstance(call, dict) or call.get("success") is not True:
            continue
        result = call.get("result")
        if not isinstance(result, dict) or result.get("success") is not True:
            continue
        grounding = result.get("grounding")
        if not isinstance(grounding, dict):
            continue
        if "menu_session_created" in (grounding.get("transactional_effects") or []):
            return True
        for artifact in grounding.get("artifacts") or []:
            if isinstance(artifact, dict) and artifact.get("kind") in {
                "menu_session", "menu_site"
            }:
                return True
    return False


def _contains_configured_menu_site_url(text: str, base_url: str) -> bool:
    base = urlparse(base_url)
    base_path = base.path.rstrip("/")
    for candidate in re.findall(r"https?://[^\s<>\]\[()]+", text):
        parsed = urlparse(candidate.rstrip(".,!?;:'\""))
        candidate_path = parsed.path.rstrip("/")
        if (
            parsed.scheme.casefold() == base.scheme.casefold()
            and parsed.netloc.casefold() == base.netloc.casefold()
            and (
                candidate_path == base_path
                or candidate_path.startswith(f"{base_path}/")
            )
        ):
            return True
    return False
