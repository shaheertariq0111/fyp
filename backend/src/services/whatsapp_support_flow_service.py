from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from src.models.tool_responses import ToolResponse


COMPLAINT_INTENT_PATTERN = re.compile(
    r"\b(?:complain|complaint|refund|replacement)\b|"
    r"\b(?:order|pizza|food|item|delivery)\b.*"
    r"\b(?:cold|late|missing|wrong|damaged|burnt|bad|problem|issue)\b|"
    r"\b(?:cold|late|missing|wrong|damaged|burnt|bad)\b.*"
    r"\b(?:order|pizza|food|item|delivery)\b",
    re.IGNORECASE,
)
COMPLAINT_DETAIL_PATTERN = re.compile(
    r"\b(?:cold|late|missing|wrong|damaged|burnt|bad|stale|raw|"
    r"undercooked|overcooked|soggy|because)\b",
    re.IGNORECASE,
)
HUMAN_SUPPORT_PATTERN = re.compile(
    r"\b(?:talk|speak|connect|contact|chat)\s+(?:to|with)\s+"
    r"(?:a\s+)?(?:human|person|agent|staff|manager)\b|"
    r"\b(?:human|live)\s+(?:support|agent)\b|"
    r"\b(?:need|want)\s+(?:a\s+)?(?:human|manager)\b|"
    r"\b(?:need|want)\s+(?:support|assistance)\b",
    re.IGNORECASE,
)
TICKET_STATUS_PATTERN = re.compile(
    r"\b(?:ticket|complaint|support\s+request)\b.*"
    r"\b(?:status|track|tracking|update)\b|"
    r"\b(?:status|track|tracking|update)\b.*"
    r"\b(?:ticket|complaint|support\s+request)\b",
    re.IGNORECASE,
)
DELAY_COMPLAINT_PATTERN = re.compile(
    r"\b(?:tired\s+of\s+waiting|waiting\s+(?:too\s+)?long|"
    r"taking\s+(?:too\s+)?long|order\s+(?:is\s+)?delayed)\b",
    re.IGNORECASE,
)
DELAY_TICKET_CONFIRMATION_PATTERN = re.compile(
    r"\b(?:create|open|raise)\b.*\b(?:delay|waiting)\b.*"
    r"\b(?:ticket|complaint|support)\b",
    re.IGNORECASE,
)
COMPLAINT_CANCEL_PATTERN = re.compile(
    r"\b(?:cancel|stop|forget|never\s*mind)\b.*\bcomplaint\b|"
    r"\bcomplaint\b.*\b(?:cancel|stop)\b",
    re.IGNORECASE,
)
UNRELATED_PENDING_SUPPORT_PATTERN = re.compile(
    r"\b(?:menu|recommend|suggest|order\s+status|track\s+(?:my\s+)?order|"
    r"where\s+is\s+(?:my\s+)?order|checkout|delivery|takeaway)\b",
    re.IGNORECASE,
)
ORDER_ID_PATTERN = re.compile(r"\bORD-[A-Z0-9][A-Z0-9-]*\b", re.IGNORECASE)
TICKET_ID_PATTERN = re.compile(r"\bTKT-[A-Z0-9][A-Z0-9-]*\b", re.IGNORECASE)
SUPPORT_BACKEND_FAILURE_MESSAGE = (
    "I couldn't log that support request right now. Please try again or contact staff."
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WhatsAppSupportFlowResult:
    text: str
    tool_calls: list[dict[str, Any]]


class WhatsAppSupportFlowService:
    """Route WhatsApp support turns through authoritative backend services."""

    def __init__(self, support_flow, tickets, agent_sessions, orders):
        self.support_flow = support_flow
        self.tickets = tickets
        self.agent_sessions = agent_sessions
        self.orders = orders

    def handle(
        self,
        *,
        user_id: str,
        session_id: str,
        message: str,
        request_id: str,
        customer_id: str | None = None,
        customer_name: str | None = None,
        customer_phone: str | None = None,
    ) -> WhatsAppSupportFlowResult | None:
        normalized = self._normalize(message)
        if not normalized:
            return None

        if TICKET_STATUS_PATTERN.search(message):
            ticket_id = self._match_id(TICKET_ID_PATTERN, message)
            return self._call(
                "get_support_ticket_status",
                False,
                lambda: self.tickets.get_ticket_status(user_id, ticket_id),
            )

        if DELAY_TICKET_CONFIRMATION_PATTERN.search(message):
            order = self.orders.get_active_order_for_session(user_id, session_id)
            if order is None:
                return self._missing_delay_order()
            return self._call(
                "handle_order_complaint",
                True,
                lambda: self.support_flow.handle_order_complaint(
                    user_id=user_id,
                    agent_session_id=session_id,
                    request_id=request_id,
                    order_id=order["order_id"],
                    description="The customer reports that the order is taking too long.",
                    customer_id=customer_id,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                    source="whatsapp",
                ),
            )

        if DELAY_COMPLAINT_PATTERN.search(message):
            order = self.orders.get_active_order_for_session(user_id, session_id)
            if order is None:
                return self._missing_delay_order()
            response = self.orders.get_order_status(user_id, order["order_id"])
            return self._result(
                "get_order_status",
                response,
                is_write=False,
                text=(
                    "I'm sorry this is taking longer than expected.\n"
                    f"{response.user_message}\n\n"
                    "Would you like me to create a support ticket for this delay? "
                    "Reply 'create a delay support ticket' to confirm."
                ),
            )

        complaint_intent = bool(COMPLAINT_INTENT_PATTERN.search(message))
        if complaint_intent:
            return self._complaint(
                user_id=user_id,
                session_id=session_id,
                message=message,
                request_id=request_id,
                customer_id=customer_id,
                customer_name=customer_name,
                customer_phone=customer_phone,
            )

        if HUMAN_SUPPORT_PATTERN.search(message):
            return self._call(
                "create_human_assistance_ticket",
                True,
                lambda: self.tickets.create_human_assistance(
                    user_id=user_id,
                    session_id=session_id,
                    idempotency_key=request_id,
                    description=message,
                    customer_id=customer_id,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                    source="whatsapp",
                ),
            )

        try:
            pending = self.agent_sessions.get_active_support_state(
                user_id,
                session_id,
            )
        except Exception as exc:
            return self._failure("support_state_lookup_failed", exc)
        if pending.get("pending_support_intent") != "order_complaint":
            return None
        if COMPLAINT_CANCEL_PATTERN.search(message):
            return self._call(
                "handle_order_complaint",
                True,
                lambda: self.support_flow.handle_order_complaint(
                    user_id=user_id,
                    agent_session_id=session_id,
                    request_id=request_id,
                    action="cancel",
                ),
            )
        if UNRELATED_PENDING_SUPPORT_PATTERN.search(message):
            return None
        if len(normalized.split()) < 2 and not ORDER_ID_PATTERN.search(message):
            return None
        return self._complaint(
            user_id=user_id,
            session_id=session_id,
            message=message,
            request_id=request_id,
            customer_id=customer_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            continuation=True,
        )

    def _complaint(
        self,
        *,
        user_id: str,
        session_id: str,
        message: str,
        request_id: str,
        customer_id: str | None,
        customer_name: str | None,
        customer_phone: str | None,
        continuation: bool = False,
    ) -> WhatsAppSupportFlowResult:
        order_id = self._match_id(ORDER_ID_PATTERN, message)
        description = None
        if COMPLAINT_DETAIL_PATTERN.search(message) or (
            continuation and order_id is None
        ):
            description = message
        return self._call(
            "handle_order_complaint",
            True,
            lambda: self.support_flow.handle_order_complaint(
                user_id=user_id,
                agent_session_id=session_id,
                request_id=request_id,
                order_id=order_id,
                description=description,
                customer_id=customer_id,
                customer_name=customer_name,
                customer_phone=customer_phone,
                source="whatsapp",
            ),
        )

    def _call(self, tool_name: str, is_write: bool, operation) -> WhatsAppSupportFlowResult:
        try:
            response = operation()
        except Exception as exc:
            return self._failure(
                f"{tool_name}_failed",
                exc,
                tool_name=tool_name,
                is_write=is_write,
            )
        return self._result(tool_name, response, is_write=is_write)

    @staticmethod
    def _result(
        tool_name: str,
        response: ToolResponse,
        *,
        is_write: bool,
        text: str | None = None,
    ) -> WhatsAppSupportFlowResult:
        dumped = response.model_dump(exclude_none=True)
        return WhatsAppSupportFlowResult(
            text=text or response.user_message,
            tool_calls=[{
                "tool_name": tool_name,
                "success": response.success,
                "is_write": is_write,
                "result": dumped,
                "error_code": response.error_code,
            }],
        )

    @classmethod
    def _failure(
        cls,
        event: str,
        exc: Exception,
        *,
        tool_name: str = "handle_order_complaint",
        is_write: bool = True,
    ) -> WhatsAppSupportFlowResult:
        logger.warning(
            "Authoritative WhatsApp support operation failed",
            extra={
                "event": event,
                "channel": "whatsapp",
                "error_type": type(exc).__name__,
            },
        )
        response = ToolResponse.error(
            error_code="SUPPORT_BACKEND_UNAVAILABLE",
            user_message=SUPPORT_BACKEND_FAILURE_MESSAGE,
            retryable=True,
        )
        return cls._result(tool_name, response, is_write=is_write)

    @classmethod
    def _missing_delay_order(cls) -> WhatsAppSupportFlowResult:
        response = ToolResponse.ok(
            user_message=(
                "I couldn't find an active order for this conversation. Please "
                "provide the Order ID, or tell me if you want staff support."
            ),
            next_action="request_order_id_or_support",
        )
        return cls._result("get_order_status", response, is_write=False)

    @staticmethod
    def _match_id(pattern: re.Pattern[str], message: str) -> str | None:
        match = pattern.search(message)
        return match.group(0).upper() if match else None

    @staticmethod
    def _normalize(message: str) -> str:
        return re.sub(r"\s+", " ", message.casefold()).strip()
