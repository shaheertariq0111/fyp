from __future__ import annotations

import hashlib
import secrets
from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable

from pydantic import ValidationError

from src.models.ticket import (
    DEFAULT_TICKET_PRIORITY,
    DEFAULT_TICKET_STATUS,
    IDEMPOTENCY_TTL_SECONDS,
    MAX_ACTOR_LENGTH,
    MAX_ADMIN_NOTE_LENGTH,
    MAX_ADMIN_NOTES,
    MAX_CATEGORY_LENGTH,
    MAX_CUSTOMER_NAME_LENGTH,
    MAX_CUSTOMER_PHONE_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_SOURCE_LENGTH,
    MAX_PRIORITY_HISTORY,
    MAX_STATUS_HISTORY,
    NON_TERMINAL_TICKET_STATUSES,
    TICKET_PRIORITIES,
    TICKET_ITEM_TOO_LARGE,
    TICKET_STATUSES,
    TICKET_STATUS_LABELS,
    TICKET_TYPES,
    Ticket,
    TicketDomainValidationError,
    generate_note_id,
    generate_ticket_id,
    normalize_ticket_timestamp,
)
from src.models.tool_responses import ToolResponse
from src.repositories.ticket_repository import (
    HumanSessionGuardConflictError,
    IdempotencyConflictError,
    ReusableTicketChangedError,
    TicketIdCollisionError,
    TicketVersionConflictError,
    human_session_guard_key,
)


MAX_TICKET_ID_ATTEMPTS = 5
MAX_HUMAN_GUARD_ATTEMPTS = 5
MAX_NOTE_ID_ATTEMPTS = 8
ADMIN_STATUS_TRANSITIONS = {
    "open": {
        "in_review",
        "waiting_for_customer",
        "resolved",
        "closed",
    },
    "in_review": {"waiting_for_customer", "resolved", "closed"},
    "waiting_for_customer": {"in_review", "resolved", "closed"},
    "resolved": {"closed"},
    "closed": set(),
}
STATUSES_REQUIRING_REASON = {
    "waiting_for_customer",
    "resolved",
    "closed",
}
LINKED_ORDER_SUMMARY_KEYS = (
    "order_id",
    "status",
    "fulfillment_method",
    "total",
    "currency",
    "created_at",
    "updated_at",
)
ADMIN_TICKET_VIEW_KEYS = (
    "ticket_id",
    "user_id",
    "customer_id",
    "customer_name",
    "customer_phone",
    "ticket_type",
    "category",
    "description",
    "priority",
    "status",
    "order_id",
    "order_status_snapshot",
    "source",
    "created_at",
    "updated_at",
    "status_history",
    "admin_notes",
    "version",
)
CUSTOMER_NEXT_ACTIONS = {
    "open": "await_support_contact",
    "in_review": "await_support_contact",
    "waiting_for_customer": "respond_to_support",
    "resolved": "no_action_required",
    "closed": "no_action_required",
}


class AdminTicketError(Exception):
    def __init__(
        self,
        error_code: str,
        user_message: str,
        *,
        retryable: bool = False,
    ):
        super().__init__(user_message)
        self.error_code = error_code
        self.user_message = user_message
        self.retryable = retryable


def project_customer_ticket_view(ticket: object) -> dict:
    if isinstance(ticket, Ticket):
        candidate = ticket.model_dump(exclude_none=True)
    elif isinstance(ticket, dict):
        candidate = ticket
    else:
        return {}

    required_fields = (
        "ticket_id",
        "ticket_type",
        "status",
        "priority",
        "created_at",
        "updated_at",
    )
    if any(
        not isinstance(candidate.get(field), str)
        or not candidate[field].strip()
        for field in required_fields
    ):
        return {}

    ticket_id = candidate["ticket_id"]
    ticket_type = candidate["ticket_type"]
    status = candidate["status"]
    priority = candidate["priority"]
    if (
        ticket_type not in TICKET_TYPES
        or status not in TICKET_STATUSES
        or priority not in TICKET_PRIORITIES
    ):
        return {}

    try:
        Ticket.validate_ticket_id(ticket_id)
        created_at = normalize_ticket_timestamp(candidate["created_at"])
        updated_at = normalize_ticket_timestamp(candidate["updated_at"])
    except (TypeError, ValueError):
        return {}
    if datetime.fromisoformat(updated_at) < datetime.fromisoformat(created_at):
        return {}

    order_id = candidate.get("order_id")
    if ticket_type == "order_complaint":
        if not isinstance(order_id, str) or not order_id.strip():
            return {}
    elif "order_id" in candidate:
        return {}

    projected = {
        "ticket_id": ticket_id,
        "ticket_type": ticket_type,
        "status": status,
        "status_label": TICKET_STATUS_LABELS[status],
        "priority": priority,
        "created_at": created_at,
        "updated_at": updated_at,
        "next_action": CUSTOMER_NEXT_ACTIONS[status],
    }
    if ticket_type == "order_complaint":
        projected["order_id"] = order_id
    return projected


class TicketService:
    def __init__(
        self,
        repository,
        order_repository,
        *,
        support_phone_number: str,
        clock: Callable[[], datetime] | None = None,
        ticket_id_factory: Callable[[datetime], str] = generate_ticket_id,
        note_id_factory: Callable[[datetime], str] = generate_note_id,
    ):
        self.repository = repository
        self.orders = order_repository
        self.support_phone_number = support_phone_number.strip()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ticket_id_factory = ticket_id_factory
        self.note_id_factory = note_id_factory

    def create_human_assistance(
        self,
        *,
        user_id: str,
        session_id: str,
        description: str | None = None,
        customer_id: str | None = None,
        customer_name: str | None = None,
        customer_phone: str | None = None,
        category: str = "human_assistance",
        priority: str = DEFAULT_TICKET_PRIORITY,
        source: str = "web",
        idempotency_key: str | None = None,
    ) -> ToolResponse:
        if not self._non_blank(user_id):
            return self._error("USER_ID_REQUIRED", "A trusted user ID is required.")
        if not self._non_blank(session_id):
            return self._error(
                "SESSION_ID_REQUIRED", "A trusted session ID is required."
            )
        validation = self._validate_creation_values(
            ticket_type="human_assistance",
            priority=priority,
            description=description,
            category=category,
            customer_name=customer_name,
            customer_phone=customer_phone,
            source=source,
        )
        if validation:
            return validation

        now = self._now()
        operation = "human_assistance"
        idempotency_hash = self._idempotency_hash(
            user_id,
            operation,
            idempotency_key or secrets.token_urlsafe(24),
        )
        existing = self._resolve_idempotency(
            user_id, operation, idempotency_hash, now
        )
        if existing:
            return existing

        fields = {
            "user_id": user_id,
            "customer_id": self._optional_text(customer_id),
            "customer_name": self._optional_text(customer_name),
            "customer_phone": self._optional_text(customer_phone),
            "session_id": session_id,
            "ticket_type": operation,
            "category": self._non_empty_text(category) or operation,
            "description": self._optional_text(description),
            "priority": priority,
            "source": self._non_empty_text(source) or "web",
        }
        return self._create_or_reuse_human(
            fields, idempotency_hash, now
        )

    def create_order_complaint(
        self,
        *,
        user_id: str,
        order_id: str,
        description: str,
        session_id: str | None = None,
        customer_id: str | None = None,
        customer_name: str | None = None,
        customer_phone: str | None = None,
        category: str = "order_complaint",
        priority: str = DEFAULT_TICKET_PRIORITY,
        source: str = "web",
        idempotency_key: str | None = None,
    ) -> ToolResponse:
        if not self._non_blank(user_id):
            return self._error("USER_ID_REQUIRED", "A trusted user ID is required.")
        if not self._non_blank(order_id):
            return self._error("ORDER_ID_REQUIRED", "An Order ID is required.")
        if not description or not description.strip():
            return self._error(
                "DESCRIPTION_REQUIRED",
                "Please provide a description of the complaint.",
            )
        validation = self._validate_creation_values(
            ticket_type="order_complaint",
            priority=priority,
            description=description,
            category=category,
            customer_name=customer_name,
            customer_phone=customer_phone,
            source=source,
        )
        if validation:
            return validation

        order = self.orders.get_by_order_id(order_id)
        if not order or order.get("user_id") != user_id:
            return self._error(
                "ORDER_NOT_FOUND", "I couldn't find that order."
            )

        now = self._now()
        operation = "order_complaint"
        idempotency_hash = self._idempotency_hash(
            user_id,
            operation,
            idempotency_key or secrets.token_urlsafe(24),
        )
        existing = self._resolve_idempotency(
            user_id, operation, idempotency_hash, now
        )
        if existing:
            return existing

        fields = {
            "user_id": user_id,
            "customer_id": self._optional_text(customer_id),
            "customer_name": self._optional_text(customer_name),
            "customer_phone": self._optional_text(customer_phone),
            "session_id": self._optional_text(session_id),
            "ticket_type": operation,
            "category": self._non_empty_text(category) or operation,
            "description": description,
            "priority": priority,
            "order_id": order_id,
            "order_status_snapshot": order.get("status"),
            "source": self._non_empty_text(source) or "web",
        }
        return self._create_ticket(fields, idempotency_hash, now)

    def get_ticket_status(
        self,
        user_id: str,
        ticket_id: str | None = None,
    ) -> ToolResponse:
        if ticket_id:
            ticket = self.repository.get(ticket_id)
            if not ticket or ticket.get("user_id") != user_id:
                return self._ticket_not_found()
            return self._status_response(
                ticket,
                tracking_state="specific_ticket",
                requires_ticket_id=False,
            )

        active = [
            ticket
            for ticket in self.repository.list_for_customer(user_id)
            if ticket.get("user_id") == user_id
            and ticket.get("status") in NON_TERMINAL_TICKET_STATUSES
        ]
        active.sort(key=self._ticket_recency, reverse=True)
        if len(active) == 1:
            return self._status_response(
                active[0],
                tracking_state="single_active_ticket",
                requires_ticket_id=False,
            )
        if len(active) > 1:
            return ToolResponse.ok(
                data={
                    "tickets": [
                        self._customer_ticket_view(ticket)
                        for ticket in active
                    ]
                },
                user_message=(
                    "You have multiple active support tickets. "
                    "Please provide a Ticket ID."
                ),
                next_action="provide_ticket_id",
                agent={
                    "entity": "tickets",
                    "tracking_state": "multiple_active_tickets",
                    "requires_ticket_id": True,
                    "required_input": "ticket_id",
                },
            )
        return ToolResponse.ok(
            data={"tickets": []},
            user_message=(
                "You have no active support tickets. Please provide the "
                "Ticket ID for an older ticket."
            ),
            next_action="provide_ticket_id",
            agent={
                "entity": "tickets",
                "tracking_state": "no_active_tickets",
                "requires_ticket_id": True,
                "required_input": "ticket_id",
            },
        )

    def get_admin_ticket(self, ticket_id: str) -> dict:
        ticket = self._load_admin_ticket(ticket_id)
        linked_order = self._linked_order_summary(ticket)
        return {
            "ticket": self._admin_ticket_detail(ticket, linked_order)
        }

    def list_tickets_by_status(
        self,
        status: str,
        *,
        limit: int = 50,
        cursor: dict | None = None,
    ) -> ToolResponse:
        invalid = self.validate_domain_value("status", status)
        if not invalid.success:
            return invalid
        result = self.repository.list_by_status(
            status,
            limit=limit,
            exclusive_start_key=cursor,
        )
        return ToolResponse.ok(
            data={
                "tickets": [
                    self._admin_ticket_view(ticket)
                    for ticket in result["items"]
                ],
                "next_cursor": result["next_cursor"],
            },
            user_message="Tickets retrieved.",
        )

    def add_admin_note(
        self,
        ticket_id: str,
        text: str,
        *,
        expected_version: int,
        actor: str,
    ) -> dict:
        self._validate_expected_version(expected_version)
        self._require_admin_actor(actor)
        if not isinstance(text, str) or not text.strip():
            raise AdminTicketError(
                "NOTE_REQUIRED",
                "An administrator note is required.",
            )
        if len(text) > MAX_ADMIN_NOTE_LENGTH:
            raise AdminTicketError(
                "NOTE_TOO_LONG",
                f"A note cannot exceed {MAX_ADMIN_NOTE_LENGTH} characters.",
            )
        ticket = self._load_admin_ticket(ticket_id)
        self._require_admin_version(ticket, expected_version)
        if len(ticket.get("admin_notes", [])) >= MAX_ADMIN_NOTES:
            raise AdminTicketError(
                "ADMIN_NOTE_LIMIT_REACHED",
                "The ticket cannot accept more admin notes.",
            )
        now = self._now()
        timestamp = now.isoformat()
        existing_note_ids = {
            note["note_id"]
            for note in ticket.get("admin_notes", [])
            if note.get("note_id") is not None
        }
        note = {
            "note_id": self._generate_unique_note_id(
                now,
                existing_note_ids,
            ),
            "text": text,
            "timestamp": timestamp,
            "actor": actor,
        }
        updated = deepcopy(ticket)
        updated.setdefault("admin_notes", []).append(note)
        updated["updated_at"] = timestamp
        self._refresh_status_index(updated)
        linked_order = self._linked_order_summary(ticket)
        return self._save_admin_change(
            updated,
            expected_version,
            linked_order,
        )

    def update_admin_status(
        self,
        ticket_id: str,
        status: str,
        *,
        expected_version: int,
        actor: str,
        reason: str | None = None,
    ) -> dict:
        self._require_domain_value("status", status)
        self._validate_expected_version(expected_version)
        self._require_admin_actor(actor)
        ticket = self._load_admin_ticket(ticket_id)
        self._require_admin_version(ticket, expected_version)
        normalized_reason = self._optional_admin_reason(reason)
        if ticket.get("status") == status:
            linked_order = self._linked_order_summary(ticket)
            return {
                "ticket": self._admin_ticket_detail(ticket, linked_order)
            }
        if status not in ADMIN_STATUS_TRANSITIONS[ticket["status"]]:
            raise AdminTicketError(
                "INVALID_TICKET_TRANSITION",
                "The requested ticket status transition is not allowed.",
            )
        if (
            status in STATUSES_REQUIRING_REASON
            and normalized_reason is None
        ):
            raise AdminTicketError(
                "NOTE_REQUIRED",
                "A reason is required for this status change.",
            )
        linked_order = self._linked_order_summary(ticket)
        return self._apply_admin_status(
            ticket,
            status,
            expected_version=expected_version,
            actor=actor,
            reason=normalized_reason,
            linked_order=linked_order,
        )

    def update_admin_priority(
        self,
        ticket_id: str,
        priority: str,
        *,
        expected_version: int,
        actor: str,
        reason: str | None = None,
    ) -> dict:
        self._require_domain_value("priority", priority)
        self._validate_expected_version(expected_version)
        self._require_admin_actor(actor)
        ticket = self._load_admin_ticket(ticket_id)
        self._require_admin_version(ticket, expected_version)
        normalized_reason = self._optional_admin_reason(reason)
        if ticket.get("priority") == priority:
            linked_order = self._linked_order_summary(ticket)
            return {
                "ticket": self._admin_ticket_detail(ticket, linked_order)
            }
        if len(ticket.get("priority_history", [])) >= MAX_PRIORITY_HISTORY:
            raise AdminTicketError(
                "PRIORITY_HISTORY_LIMIT_REACHED",
                "The ticket cannot accept more priority history entries.",
            )
        timestamp = self._now().isoformat()
        history = {
            "previous_priority": ticket["priority"],
            "new_priority": priority,
            "timestamp": timestamp,
            "actor": actor,
        }
        if normalized_reason is not None:
            history["reason"] = normalized_reason
        updated = deepcopy(ticket)
        updated["priority"] = priority
        updated["updated_at"] = timestamp
        updated.setdefault("priority_history", []).append(history)
        self._refresh_status_index(updated)
        linked_order = self._linked_order_summary(ticket)
        return self._save_admin_change(
            updated,
            expected_version,
            linked_order,
        )

    def reopen_admin_ticket(
        self,
        ticket_id: str,
        *,
        target_status: str,
        reason: str,
        expected_version: int,
        actor: str,
    ) -> dict:
        if target_status not in {"open", "in_review"}:
            raise AdminTicketError(
                "INVALID_REOPEN_TARGET",
                "A reopened ticket must be open or in review.",
        )
        self._validate_expected_version(expected_version)
        self._require_admin_actor(actor)
        ticket = self._load_admin_ticket(ticket_id)
        self._require_admin_version(ticket, expected_version)
        normalized_reason = self._optional_admin_reason(reason)
        if normalized_reason is None:
            raise AdminTicketError(
                "REOPEN_REASON_REQUIRED",
                "A reason is required to reopen a ticket.",
            )
        if ticket.get("status") not in {"resolved", "closed"}:
            raise AdminTicketError(
                "INVALID_TICKET_TRANSITION",
                "Only resolved or closed tickets can be reopened.",
            )
        linked_order = self._linked_order_summary(ticket)
        return self._apply_admin_status(
            ticket,
            target_status,
            expected_version=expected_version,
            actor=actor,
            reason=normalized_reason,
            linked_order=linked_order,
        )

    def validate_domain_value(self, field: str, value: str) -> ToolResponse:
        definitions = {
            "ticket_type": (
                TICKET_TYPES,
                "INVALID_TICKET_TYPE",
                "The ticket type is invalid.",
            ),
            "priority": (
                TICKET_PRIORITIES,
                "INVALID_TICKET_PRIORITY",
                "The ticket priority is invalid.",
            ),
            "status": (
                TICKET_STATUSES,
                "INVALID_TICKET_STATUS",
                "The ticket status is invalid.",
            ),
        }
        if field not in definitions:
            return self._error(
                "INVALID_TICKET_FIELD", "The ticket field is invalid."
            )
        allowed, error_code, message = definitions[field]
        if value not in allowed:
            return self._error(error_code, message)
        return ToolResponse.ok(data={field: value}, user_message="Valid.")

    def _create_or_reuse_human(
        self,
        fields: dict,
        idempotency_hash: str,
        now: datetime,
    ) -> ToolResponse:
        excluded_ticket_ids: set[str] = set()
        for _attempt in range(MAX_HUMAN_GUARD_ATTEMPTS):
            guard = self.repository.get_human_session_guard(
                fields["user_id"], fields["session_id"]
            )
            guarded_ticket = None
            if guard:
                guarded_ticket = self.repository.get(
                    guard.get("active_ticket_id", "")
                )
                if self._is_reusable_human(guarded_ticket, fields):
                    try:
                        return self._bind_human_reuse(
                            guarded_ticket,
                            fields,
                            idempotency_hash,
                            now,
                            expected_guard=guard,
                        )
                    except (
                        HumanSessionGuardConflictError,
                        ReusableTicketChangedError,
                    ):
                        if guarded_ticket:
                            excluded_ticket_ids.add(guarded_ticket["ticket_id"])
                        continue

            reusable = self._find_reusable_human(
                fields,
                excluded_ticket_ids=excluded_ticket_ids,
            )
            if reusable:
                try:
                    return self._bind_human_reuse(
                        reusable,
                        fields,
                        idempotency_hash,
                        now,
                        expected_guard=guard,
                    )
                except (
                    HumanSessionGuardConflictError,
                    ReusableTicketChangedError,
                ):
                    excluded_ticket_ids.add(reusable["ticket_id"])
                    continue

            try:
                return self._create_ticket(
                    fields,
                    idempotency_hash,
                    now,
                    guard_identity={
                        "user_id": fields["user_id"],
                        "session_id": fields["session_id"],
                    },
                    expected_guard=guard,
                )
            except HumanSessionGuardConflictError:
                continue
        return self._error(
            "HUMAN_SESSION_CONFLICT",
            "The support request changed while it was being created.",
            retryable=True,
        )

    def _create_ticket(
        self,
        fields: dict,
        idempotency_hash: str,
        now: datetime,
        *,
        guard_identity: dict | None = None,
        expected_guard: dict | None = None,
    ) -> ToolResponse:
        now_epoch = int(now.timestamp())
        for _attempt in range(MAX_TICKET_ID_ATTEMPTS):
            ticket_id = self.ticket_id_factory(now)
            ticket = self._build_ticket(ticket_id, fields, now)
            marker = self._build_marker(
                fields["user_id"],
                fields["ticket_type"],
                ticket_id,
                idempotency_hash,
                now_epoch,
            )
            guard = None
            if guard_identity:
                guard = self._build_guard(
                    guard_identity["user_id"],
                    guard_identity["session_id"],
                    ticket_id,
                    now,
                    version=(expected_guard or {}).get("version", 0) + 1,
                )
            try:
                self.repository.create_with_idempotency(
                    ticket,
                    marker,
                    now_epoch,
                    guard=guard,
                    expected_guard=expected_guard,
                )
            except TicketIdCollisionError:
                continue
            except IdempotencyConflictError:
                existing = self._resolve_idempotency(
                    fields["user_id"],
                    fields["ticket_type"],
                    idempotency_hash,
                    now,
                )
                if existing:
                    return existing
                return self._idempotency_conflict()
            except TicketDomainValidationError as exc:
                return self._ticket_validation_error(exc)
            return self._creation_response(ticket)
        return self._error(
            "TICKET_ID_GENERATION_FAILED",
            "The support request could not be created.",
            retryable=True,
        )

    def _bind_human_reuse(
        self,
        ticket: dict,
        fields: dict,
        idempotency_hash: str,
        now: datetime,
        *,
        expected_guard: dict | None,
    ) -> ToolResponse:
        now_epoch = int(now.timestamp())
        marker = self._build_marker(
            fields["user_id"],
            "human_assistance",
            ticket["ticket_id"],
            idempotency_hash,
            now_epoch,
        )
        guard = self._build_guard(
            fields["user_id"],
            fields["session_id"],
            ticket["ticket_id"],
            now,
            version=(expected_guard or {}).get("version", 0) + 1,
        )
        try:
            self.repository.bind_human_reuse(
                ticket,
                marker,
                guard,
                now_epoch,
                expected_guard=expected_guard,
            )
        except IdempotencyConflictError:
            existing = self._resolve_idempotency(
                fields["user_id"],
                "human_assistance",
                idempotency_hash,
                now,
            )
            if existing:
                return existing
            return self._idempotency_conflict()
        return self._creation_response(ticket)

    def _build_ticket(
        self,
        ticket_id: str,
        fields: dict,
        now: datetime,
    ) -> dict:
        timestamp = now.isoformat()
        values = {
            **fields,
            "ticket_id": ticket_id,
            "status": DEFAULT_TICKET_STATUS,
            "created_at": timestamp,
            "updated_at": timestamp,
            "status_history": [],
            "admin_notes": [],
            "version": 1,
            "PK": f"TICKET#{ticket_id}",
            "SK": "METADATA",
            "GSI1PK": f"CUSTOMER#{fields['user_id']}",
            "GSI1SK": f"CREATED#{timestamp}#{ticket_id}",
            "GSI2PK": f"STATUS#{DEFAULT_TICKET_STATUS}",
            "GSI2SK": f"UPDATED#{timestamp}#{ticket_id}",
        }
        return Ticket.model_validate(values).model_dump(exclude_none=True)

    @staticmethod
    def _build_marker(
        user_id: str,
        operation: str,
        ticket_id: str,
        idempotency_hash: str,
        now_epoch: int,
    ) -> dict:
        return {
            "PK": f"IDEMPOTENCY#{idempotency_hash}",
            "SK": "METADATA",
            "ticket_id": ticket_id,
            "user_id": user_id,
            "operation": operation,
            "expires_at": now_epoch + IDEMPOTENCY_TTL_SECONDS,
        }

    @staticmethod
    def _build_guard(
        user_id: str,
        session_id: str,
        ticket_id: str,
        now: datetime,
        *,
        version: int,
    ) -> dict:
        return {
            "PK": human_session_guard_key(user_id, session_id),
            "SK": "METADATA",
            "user_id": user_id,
            "session_id": session_id,
            "operation": "human_assistance",
            "active_ticket_id": ticket_id,
            "updated_at": now.isoformat(),
            "version": version,
        }

    def _resolve_idempotency(
        self,
        user_id: str,
        operation: str,
        idempotency_hash: str,
        now: datetime,
    ) -> ToolResponse | None:
        marker = self.repository.get_idempotency_marker(idempotency_hash)
        if not marker or marker.get("expires_at", 0) <= int(now.timestamp()):
            return None
        if (
            marker.get("user_id") != user_id
            or marker.get("operation") != operation
        ):
            return self._idempotency_conflict()
        ticket = self.repository.get(marker.get("ticket_id", ""))
        if (
            not ticket
            or ticket.get("user_id") != user_id
            or ticket.get("ticket_type") != operation
        ):
            return self._idempotency_conflict()
        return self._creation_response(ticket)

    def _find_reusable_human(
        self,
        fields: dict,
        *,
        excluded_ticket_ids: set[str],
    ) -> dict | None:
        reusable = [
            ticket
            for ticket in self.repository.list_for_customer(fields["user_id"])
            if ticket.get("ticket_id") not in excluded_ticket_ids
            and self._is_reusable_human(ticket, fields)
        ]
        return max(reusable, key=self._ticket_recency) if reusable else None

    @staticmethod
    def _is_reusable_human(ticket: dict | None, fields: dict) -> bool:
        return bool(
            ticket
            and ticket.get("user_id") == fields["user_id"]
            and ticket.get("session_id") == fields["session_id"]
            and ticket.get("ticket_type") == "human_assistance"
            and ticket.get("status") in NON_TERMINAL_TICKET_STATUSES
        )

    @staticmethod
    def _ticket_recency(ticket: dict) -> tuple[str, str, str]:
        return (
            ticket.get("updated_at", ""),
            ticket.get("created_at", ""),
            ticket.get("ticket_id", ""),
        )

    def _apply_admin_status(
        self,
        ticket: dict,
        status: str,
        *,
        expected_version: int,
        actor: str,
        reason: str | None,
        linked_order: dict | None,
    ) -> dict:
        if len(ticket.get("status_history", [])) >= MAX_STATUS_HISTORY:
            raise AdminTicketError(
                "STATUS_HISTORY_LIMIT_REACHED",
                "The ticket cannot accept more status history entries.",
            )
        timestamp = self._now().isoformat()
        history = {
            "previous_status": ticket["status"],
            "new_status": status,
            "timestamp": timestamp,
            "actor": actor,
        }
        if reason is not None:
            history["reason"] = reason
        updated = deepcopy(ticket)
        updated["status"] = status
        updated["updated_at"] = timestamp
        updated.setdefault("status_history", []).append(history)
        self._refresh_status_index(updated)
        return self._save_admin_change(
            updated,
            expected_version,
            linked_order,
        )

    def _save_admin_change(
        self,
        ticket: dict,
        expected_version: int,
        linked_order: dict | None,
    ) -> dict:
        try:
            self.repository.save(ticket, expected_version)
        except TicketVersionConflictError:
            raise AdminTicketError(
                "TICKET_VERSION_CONFLICT",
                "The ticket changed before this update could be saved.",
                retryable=True,
            )
        except TicketDomainValidationError as exc:
            message = {
                TICKET_ITEM_TOO_LARGE: (
                    "The ticket has reached its maximum stored size."
                ),
                "INVALID_TICKET_ID": "The Ticket ID is invalid.",
                "INVALID_TIMESTAMP": "The ticket timestamp is invalid.",
            }.get(exc.code, "The ticket data is invalid.")
            raise AdminTicketError(exc.code, message) from exc
        saved = deepcopy(ticket)
        saved["version"] = expected_version + 1
        return {
            "ticket": self._admin_ticket_detail(saved, linked_order)
        }

    @staticmethod
    def _refresh_status_index(ticket: dict) -> None:
        ticket["GSI2PK"] = f"STATUS#{ticket['status']}"
        ticket["GSI2SK"] = (
            f"UPDATED#{ticket['updated_at']}#{ticket['ticket_id']}"
        )

    def _creation_response(self, ticket: dict) -> ToolResponse:
        if ticket["ticket_type"] == "order_complaint":
            message = (
                "Your complaint has been recorded.\n"
                "\n"
                f"Ticket ID: {ticket['ticket_id']}\n"
                f"Order ID: {ticket['order_id']}\n"
                f"Status: {TICKET_STATUS_LABELS[ticket['status']]}\n"
                "\n"
                "Our team will review your complaint and contact you using "
                "the contact details associated with your account."
            )
        else:
            message = (
                "Your support request has been created.\n"
                "\n"
                f"Ticket ID: {ticket['ticket_id']}\n"
                f"Status: {TICKET_STATUS_LABELS[ticket['status']]}\n"
                "\n"
                "Our team will review your request and contact you using the "
                "contact details associated with your account."
            )
        if self.support_phone_number:
            message += (
                f"\nCalls may come from {self.support_phone_number}."
            )
        next_action = self._customer_next_action(ticket["status"])
        return ToolResponse.ok(
            data={"ticket": self._customer_ticket_view(ticket)},
            user_message=message,
            next_action=next_action,
            agent={
                "entity": "ticket",
                "ticket_id": ticket["ticket_id"],
                "ticket_status": ticket["status"],
            },
        )

    def _status_response(
        self,
        ticket: dict,
        *,
        tracking_state: str,
        requires_ticket_id: bool,
    ) -> ToolResponse:
        message = (
            f"Ticket ID: {ticket['ticket_id']}\n"
            f"Status: {TICKET_STATUS_LABELS[ticket['status']]}"
        )
        next_action = self._customer_next_action(ticket["status"])
        return ToolResponse.ok(
            data={"ticket": self._customer_ticket_view(ticket)},
            user_message=message,
            next_action=next_action,
            agent={
                "entity": "ticket",
                "tracking_state": tracking_state,
                "selected_ticket_id": ticket["ticket_id"],
                "requires_ticket_id": requires_ticket_id,
                "status_message": message,
            },
        )

    @staticmethod
    def _customer_ticket_view(ticket: Ticket | dict) -> dict:
        projected = project_customer_ticket_view(ticket)
        if not projected:
            raise ValueError("invalid customer ticket data")
        return projected

    @staticmethod
    def _admin_ticket_view(ticket: dict) -> dict:
        projected = {
            key: deepcopy(ticket[key])
            for key in ADMIN_TICKET_VIEW_KEYS
            if key in ticket
        }
        projected["status_history"] = [
            {
                key: deepcopy(entry[key])
                for key in (
                    "previous_status",
                    "new_status",
                    "timestamp",
                    "actor",
                )
                if key in entry
            }
            for entry in ticket.get("status_history", [])
        ]
        projected["admin_notes"] = [
            {
                key: deepcopy(note[key])
                for key in ("text", "timestamp", "actor")
                if key in note
            }
            for note in ticket.get("admin_notes", [])
        ]
        return projected

    @staticmethod
    def _admin_ticket_detail(
        ticket: dict,
        linked_order: dict | None,
    ) -> dict:
        return {
            "ticket_id": ticket["ticket_id"],
            "user_id": ticket["user_id"],
            "customer_id": ticket.get("customer_id"),
            "customer_name": ticket.get("customer_name"),
            "customer_phone": ticket.get("customer_phone"),
            "ticket_type": ticket["ticket_type"],
            "category": ticket["category"],
            "description": ticket.get("description"),
            "priority": ticket["priority"],
            "status": ticket["status"],
            "order_id": ticket.get("order_id"),
            "order_status_snapshot": ticket.get(
                "order_status_snapshot"
            ),
            "source": ticket["source"],
            "created_at": ticket["created_at"],
            "updated_at": ticket["updated_at"],
            "status_history": [
                {
                    "previous_status": entry["previous_status"],
                    "new_status": entry["new_status"],
                    "timestamp": entry["timestamp"],
                    "actor": entry.get("actor"),
                    "reason": entry.get("reason"),
                }
                for entry in ticket.get("status_history", [])
            ],
            "priority_history": [
                {
                    "previous_priority": entry["previous_priority"],
                    "new_priority": entry["new_priority"],
                    "timestamp": entry["timestamp"],
                    "actor": entry["actor"],
                    "reason": entry.get("reason"),
                }
                for entry in ticket.get("priority_history", [])
            ],
            "admin_notes": [
                {
                    "note_id": note.get("note_id"),
                    "actor": note.get("actor"),
                    "timestamp": note["timestamp"],
                    "text": note["text"],
                }
                for note in ticket.get("admin_notes", [])
            ],
            "version": ticket["version"],
            "linked_order": deepcopy(linked_order),
        }

    def _linked_order_summary(self, ticket: dict) -> dict | None:
        order_id = ticket.get("order_id")
        if not order_id:
            return None
        order = self.orders.get(ticket["user_id"], order_id)
        if not order or order.get("user_id") != ticket["user_id"]:
            return None
        return {
            key: deepcopy(order.get(key))
            for key in LINKED_ORDER_SUMMARY_KEYS
        }

    def _load_admin_ticket(self, ticket_id: str) -> dict:
        try:
            Ticket.validate_ticket_id(ticket_id)
        except (TypeError, ValueError):
            raise AdminTicketError(
                "TICKET_NOT_FOUND",
                "The ticket could not be found.",
            ) from None
        ticket = self.repository.get(ticket_id)
        if not ticket:
            raise AdminTicketError(
                "TICKET_NOT_FOUND",
                "The ticket could not be found.",
            )
        try:
            validated = Ticket.model_validate(ticket)
        except ValidationError:
            raise AdminTicketError(
                "TICKET_DATA_INVALID",
                "The ticket record is invalid.",
            ) from None
        return validated.model_dump(exclude_none=True)

    def _generate_unique_note_id(
        self,
        now: datetime,
        existing_note_ids: set[str],
    ) -> str:
        for _attempt in range(MAX_NOTE_ID_ATTEMPTS):
            candidate = self.note_id_factory(now)
            if candidate not in existing_note_ids:
                return candidate
        raise AdminTicketError(
            "NOTE_ID_GENERATION_FAILED",
            "Unable to create a unique note ID. Please retry.",
            retryable=True,
        )

    @staticmethod
    def _validate_expected_version(expected_version: int) -> None:
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 1
        ):
            raise AdminTicketError(
                "INVALID_TICKET_VERSION",
                "The expected ticket version is invalid.",
            )

    @staticmethod
    def _require_admin_actor(actor: str) -> None:
        if (
            not isinstance(actor, str)
            or not actor.strip()
            or len(actor) > MAX_ACTOR_LENGTH
        ):
            raise AdminTicketError(
                "INVALID_ADMIN_ACTOR",
                "The administrator identity is invalid.",
            )

    @staticmethod
    def _require_admin_version(
        ticket: dict,
        expected_version: int,
    ) -> None:
        if ticket.get("version") != expected_version:
            raise AdminTicketError(
                "TICKET_VERSION_CONFLICT",
                "The ticket changed before this update could be applied.",
                retryable=True,
            )

    @staticmethod
    def _optional_admin_reason(reason: str | None) -> str | None:
        if reason is None:
            return None
        if not isinstance(reason, str):
            raise AdminTicketError(
                "NOTE_REQUIRED",
                "A valid reason is required.",
            )
        if len(reason) > MAX_ADMIN_NOTE_LENGTH:
            raise AdminTicketError(
                "NOTE_TOO_LONG",
                f"A reason cannot exceed {MAX_ADMIN_NOTE_LENGTH} characters.",
            )
        return reason if reason.strip() else None

    @staticmethod
    def _require_domain_value(field: str, value: str) -> None:
        definitions = {
            "priority": (
                TICKET_PRIORITIES,
                "INVALID_TICKET_PRIORITY",
                "The ticket priority is invalid.",
            ),
            "status": (
                TICKET_STATUSES,
                "INVALID_TICKET_STATUS",
                "The ticket status is invalid.",
            ),
        }
        allowed, error_code, message = definitions[field]
        if value not in allowed:
            raise AdminTicketError(error_code, message)

    @staticmethod
    def _customer_next_action(status: str) -> str:
        try:
            return CUSTOMER_NEXT_ACTIONS[status]
        except KeyError as exc:
            raise ValueError("invalid ticket status") from exc

    @staticmethod
    def _idempotency_hash(
        user_id: str,
        operation: str,
        raw_key: str,
    ) -> str:
        scoped = f"{user_id}\0{operation}\0{raw_key}".encode("utf-8")
        return hashlib.sha256(scoped).hexdigest()

    @classmethod
    def _validate_creation_values(
        cls,
        *,
        ticket_type: str,
        priority: str,
        description: str | None,
        category: str,
        customer_name: str | None,
        customer_phone: str | None,
        source: str,
    ) -> ToolResponse | None:
        if ticket_type not in TICKET_TYPES:
            return cls._error(
                "INVALID_TICKET_TYPE", "The ticket type is invalid."
            )
        if priority not in TICKET_PRIORITIES:
            return cls._error(
                "INVALID_TICKET_PRIORITY", "The ticket priority is invalid."
            )
        limits = (
            (
                description,
                MAX_DESCRIPTION_LENGTH,
                "DESCRIPTION_TOO_LONG",
                "The description is too long.",
            ),
            (
                category,
                MAX_CATEGORY_LENGTH,
                "CATEGORY_TOO_LONG",
                "The category is too long.",
            ),
            (
                customer_name,
                MAX_CUSTOMER_NAME_LENGTH,
                "CUSTOMER_NAME_TOO_LONG",
                "The customer name is too long.",
            ),
            (
                customer_phone,
                MAX_CUSTOMER_PHONE_LENGTH,
                "CUSTOMER_PHONE_TOO_LONG",
                "The customer phone is too long.",
            ),
            (
                source,
                MAX_SOURCE_LENGTH,
                "SOURCE_TOO_LONG",
                "The source is too long.",
            ),
        )
        for value, maximum, error_code, message in limits:
            if value is not None and len(value) > maximum:
                return cls._error(error_code, message)
        return None

    @classmethod
    def _ticket_not_found(cls) -> ToolResponse:
        return cls._error(
            "TICKET_NOT_FOUND", "I couldn't find that ticket."
        )

    @classmethod
    def _idempotency_conflict(cls) -> ToolResponse:
        return cls._error(
            "IDEMPOTENCY_CONFLICT",
            "The support request is already being processed.",
            retryable=True,
        )

    @classmethod
    def _ticket_validation_error(
        cls,
        exc: TicketDomainValidationError,
    ) -> ToolResponse:
        messages = {
            TICKET_ITEM_TOO_LARGE: (
                "The ticket has reached its maximum stored size."
            ),
            "INVALID_TICKET_ID": "The Ticket ID is invalid.",
            "INVALID_TIMESTAMP": "The ticket timestamp is invalid.",
        }
        return cls._error(
            exc.code,
            messages.get(exc.code, "The ticket data is invalid."),
        )

    @staticmethod
    def _error(
        error_code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> ToolResponse:
        return ToolResponse.error(
            error_code=error_code,
            user_message=message,
            retryable=retryable,
        )

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _non_blank(value: str | None) -> bool:
        return bool(value and value.strip())

    @staticmethod
    def _optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _non_empty_text(value: str | None) -> str:
        return TicketService._optional_text(value) or ""
