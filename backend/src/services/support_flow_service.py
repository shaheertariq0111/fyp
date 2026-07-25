from __future__ import annotations

from src.models.ticket import MAX_DESCRIPTION_LENGTH
from src.models.tool_responses import ToolResponse
from src.repositories.agent_session_repository import (
    SessionNotFoundError,
    SupportStateConflictError,
)


class SupportFlowService:
    def __init__(self, agent_sessions, ticket_service, order_repository):
        self.agent_sessions = agent_sessions
        self.tickets = ticket_service
        self.orders = order_repository

    def handle_order_complaint(
        self,
        *,
        user_id: str,
        agent_session_id: str,
        request_id: str,
        order_id: str | None = None,
        description: str | None = None,
        action: str = "continue",
        customer_id: str | None = None,
        customer_name: str | None = None,
        customer_phone: str | None = None,
        source: str | None = None,
    ) -> ToolResponse:
        required_error = self._trusted_field_error(
            user_id=user_id,
            agent_session_id=agent_session_id,
            request_id=request_id,
        )
        if required_error:
            return required_error
        if action not in {"continue", "cancel"}:
            return self._error(
                "INVALID_SUPPORT_ACTION",
                "The support action is invalid.",
            )
        if action == "cancel":
            return self._cancel(user_id, agent_session_id)

        try:
            state = self.agent_sessions.get_active_support_state(
                user_id,
                agent_session_id,
            )
        except SessionNotFoundError:
            return self._session_not_found()

        supplied_order_id = None
        if order_id is not None:
            order = self.orders.get_by_order_id(order_id)
            if not order or order.get("user_id") != user_id:
                return self._error(
                    "ORDER_NOT_FOUND",
                    "I couldn't find that order.",
                )
            supplied_order_id = order_id

        supplied_description = None
        if description is not None and description.strip():
            if len(description) > MAX_DESCRIPTION_LENGTH:
                return self._error(
                    "DESCRIPTION_TOO_LONG",
                    "The description is too long.",
                )
            supplied_description = description

        for attempt in range(2):
            if attempt:
                try:
                    state = self.agent_sessions.get_active_support_state(
                        user_id,
                        agent_session_id,
                    )
                except SessionNotFoundError:
                    return self._session_not_found()
            merged_order_id = (
                supplied_order_id
                if supplied_order_id is not None
                else state.get("pending_order_id")
            )
            merged_description = (
                supplied_description
                if supplied_description is not None
                else state.get("pending_complaint_description")
            )
            try:
                saved = self.agent_sessions.save_support_state(
                    user_id,
                    agent_session_id,
                    expected_updated_at=state.get(
                        "pending_support_updated_at"
                    ),
                    intent="order_complaint",
                    order_id=merged_order_id,
                    description=merged_description,
                )
            except SupportStateConflictError:
                if attempt == 0:
                    continue
                return self._conflict()
            except SessionNotFoundError:
                return self._session_not_found()

            if merged_order_id is None:
                return ToolResponse.ok(
                    user_message=(
                        "Please provide the Order ID for the order you are "
                        "complaining about."
                    ),
                    next_action="request_order_id",
                    agent={
                        "entity": "pending_support",
                        "pending_support_intent": "order_complaint",
                        "required_input": "order_id",
                    },
                )
            if merged_description is None:
                return ToolResponse.ok(
                    user_message="Please describe what went wrong with your order.",
                    next_action="request_complaint_description",
                    agent={
                        "entity": "pending_support",
                        "pending_support_intent": "order_complaint",
                        "order_id": merged_order_id,
                        "required_input": "complaint_description",
                    },
                )

            response = self.tickets.create_order_complaint(
                **self._ticket_arguments(
                    user_id=user_id,
                    agent_session_id=agent_session_id,
                    request_id=request_id,
                    order_id=merged_order_id,
                    description=merged_description,
                    customer_id=customer_id,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                    source=source,
                )
            )
            if response.success:
                self._clear_submitted_state(
                    user_id=user_id,
                    agent_session_id=agent_session_id,
                    saved_updated_at=saved["pending_support_updated_at"],
                    order_id=merged_order_id,
                    description=merged_description,
                )
            return response
        return self._conflict()

    def _cancel(
        self,
        customer_id: str,
        agent_session_id: str,
    ) -> ToolResponse:
        try:
            state = self.agent_sessions.get_active_support_state(
                customer_id,
                agent_session_id,
            )
            self.agent_sessions.clear_support_state(
                customer_id,
                agent_session_id,
                expected_updated_at=state.get("pending_support_updated_at"),
            )
        except SessionNotFoundError:
            return self._session_not_found()
        except SupportStateConflictError:
            return self._conflict()
        return ToolResponse.ok(
            user_message="Your complaint request has been cancelled.",
            next_action="support_cancelled",
            agent={
                "entity": "pending_support",
                "pending_support_intent": None,
            },
        )

    def _clear_submitted_state(
        self,
        *,
        user_id: str,
        agent_session_id: str,
        saved_updated_at: str,
        order_id: str,
        description: str,
    ) -> None:
        try:
            self.agent_sessions.clear_support_state(
                user_id,
                agent_session_id,
                expected_updated_at=saved_updated_at,
            )
            return
        except SessionNotFoundError:
            return
        except SupportStateConflictError:
            pass

        try:
            latest = self.agent_sessions.get_active_support_state(
                user_id,
                agent_session_id,
            )
        except (SessionNotFoundError, SupportStateConflictError):
            return
        if (
            latest.get("pending_order_id") != order_id
            or latest.get("pending_complaint_description") != description
        ):
            return
        try:
            self.agent_sessions.clear_support_state(
                user_id,
                agent_session_id,
                expected_updated_at=latest.get(
                    "pending_support_updated_at"
                ),
            )
        except (SessionNotFoundError, SupportStateConflictError):
            pass

    @staticmethod
    def _ticket_arguments(
        *,
        user_id: str,
        agent_session_id: str,
        request_id: str,
        order_id: str,
        description: str,
        customer_id: str | None,
        customer_name: str | None,
        customer_phone: str | None,
        source: str | None,
    ) -> dict:
        values = {
            "user_id": user_id,
            "order_id": order_id,
            "description": description,
            "session_id": agent_session_id,
            "idempotency_key": request_id,
        }
        optional = {
            "customer_id": customer_id,
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            "source": source,
        }
        values.update(
            {key: value for key, value in optional.items() if value is not None}
        )
        return values

    @classmethod
    def _trusted_field_error(
        cls,
        *,
        user_id: str,
        agent_session_id: str,
        request_id: str,
    ) -> ToolResponse | None:
        if not user_id or not user_id.strip():
            return cls._error(
                "USER_ID_REQUIRED",
                "A trusted user ID is required.",
            )
        if not agent_session_id or not agent_session_id.strip():
            return cls._error(
                "SESSION_ID_REQUIRED",
                "A trusted session ID is required.",
            )
        if not request_id or not request_id.strip():
            return cls._error(
                "REQUEST_ID_REQUIRED",
                "A trusted request ID is required.",
            )
        return None

    @staticmethod
    def _conflict() -> ToolResponse:
        return ToolResponse.error(
            error_code="SUPPORT_STATE_CONFLICT",
            user_message=(
                "The complaint details changed while they were being saved."
            ),
            retryable=True,
        )

    @staticmethod
    def _session_not_found() -> ToolResponse:
        return ToolResponse.error(
            error_code="SESSION_NOT_FOUND",
            user_message="The session could not be found.",
        )

    @staticmethod
    def _error(error_code: str, user_message: str) -> ToolResponse:
        return ToolResponse.error(
            error_code=error_code,
            user_message=user_message,
        )
