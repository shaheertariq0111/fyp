from __future__ import annotations

import logging
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from src.api.schemas import ChatRequest
from src.api.whatsapp import WhatsAppInboundMessage
from src.services.customer_service import CustomerService


@dataclass(frozen=True, slots=True)
class WhatsAppConversationReply:
    reply: str
    request_id: str
    session_id: str
    customer_id: str
    resumed: bool = False


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
        reply = response.get("text") if isinstance(response, dict) else None
        if result.record.get("status") != "completed" or not isinstance(reply, str) or not reply.strip():
            return None
        return WhatsAppConversationReply(reply, result.record["request_id"], result.context.agent_session_id, result.context.customer_id, resumed=result.outcome == "completed")

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
        services_provider().customers.update_profile(
            customer_id, whatsapp_profile_name=inbound.customer_name,
            phone_number=normalized_phone, channel="whatsapp", phone_verified=normalized_phone is not None,
            name_source="whatsapp_profile",
        )
    return customer_id, session_id
