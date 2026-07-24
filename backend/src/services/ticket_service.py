from __future__ import annotations

import hashlib
import secrets
from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable

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
    MAX_STATUS_HISTORY,
    NON_TERMINAL_TICKET_STATUSES,
    TICKET_PRIORITIES,
    TICKET_ITEM_TOO_LARGE,
    TICKET_STATUSES,
    TICKET_STATUS_LABELS,
    TICKET_TYPES,
    Ticket,
    TicketDomainValidationError,
    generate_ticket_id,
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


class TicketService:
    def __init__(
        self,
        repository,
        order_repository,
        *,
        support_phone_number: str,
        clock: Callable[[], datetime] | None = None,
        ticket_id_factory: Callable[[datetime], str] = generate_ticket_id,
    ):
        self.repository = repository
        self.orders = order_repository
        self.support_phone_number = support_phone_number.strip()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ticket_id_factory = ticket_id_factory

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
                data={"tickets": [self._public(ticket) for ticket in active]},
                user_message=(
                    "You have multiple active support tickets. "
                    "Please provide a Ticket ID."
                ),
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
            agent={
                "entity": "tickets",
                "tracking_state": "no_active_tickets",
                "requires_ticket_id": True,
                "required_input": "ticket_id",
            },
        )

    def get_ticket_for_admin(self, ticket_id: str) -> ToolResponse:
        ticket = self.repository.get(ticket_id)
        if not ticket:
            return self._ticket_not_found()
        return ToolResponse.ok(
            data={"ticket": self._public(ticket)},
            user_message="Ticket details retrieved.",
        )

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
                "tickets": [self._public(ticket) for ticket in result["items"]],
                "next_cursor": result["next_cursor"],
            },
            user_message="Tickets retrieved.",
        )

    def append_admin_note(
        self,
        ticket_id: str,
        text: str,
        *,
        actor: str | None = None,
    ) -> ToolResponse:
        normalized = self._optional_text(text)
        if normalized is None:
            return self._error(
                "ADMIN_NOTE_REQUIRED", "An admin note cannot be blank."
            )
        if len(normalized) > MAX_ADMIN_NOTE_LENGTH:
            return self._error(
                "ADMIN_NOTE_TOO_LONG",
                f"An admin note cannot exceed {MAX_ADMIN_NOTE_LENGTH} characters.",
            )
        actor_error = self._validate_actor(actor)
        if actor_error:
            return actor_error
        ticket = self.repository.get(ticket_id)
        if not ticket:
            return self._ticket_not_found()
        if len(ticket.get("admin_notes", [])) >= MAX_ADMIN_NOTES:
            return self._error(
                "ADMIN_NOTE_LIMIT_REACHED",
                "The ticket cannot accept more admin notes.",
            )
        timestamp = self._now().isoformat()
        note = {"text": normalized, "timestamp": timestamp}
        if self._non_blank(actor):
            note["actor"] = actor.strip()
        updated = deepcopy(ticket)
        updated.setdefault("admin_notes", []).append(note)
        updated["updated_at"] = timestamp
        self._refresh_status_index(updated)
        return self._save_admin_change(updated, ticket["version"])

    def update_status(
        self,
        ticket_id: str,
        status: str,
        *,
        actor: str | None = None,
    ) -> ToolResponse:
        invalid = self.validate_domain_value("status", status)
        if not invalid.success:
            return invalid
        ticket = self.repository.get(ticket_id)
        if not ticket:
            return self._ticket_not_found()
        if ticket.get("status") == status:
            return ToolResponse.ok(
                data={"ticket": self._public(ticket)},
                user_message="Ticket unchanged.",
            )
        actor_error = self._validate_actor(actor)
        if actor_error:
            return actor_error
        return self._apply_status(ticket, status, actor)

    def reopen_ticket(
        self,
        ticket_id: str,
        *,
        actor: str | None = None,
    ) -> ToolResponse:
        ticket = self.repository.get(ticket_id)
        if not ticket:
            return self._ticket_not_found()
        if ticket.get("status") not in {"resolved", "closed"}:
            return self._error(
                "INVALID_TICKET_STATE",
                "Only resolved or closed tickets can be reopened.",
            )
        actor_error = self._validate_actor(actor)
        if actor_error:
            return actor_error
        return self._apply_status(ticket, "open", actor)

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

    def _apply_status(
        self,
        ticket: dict,
        status: str,
        actor: str | None,
    ) -> ToolResponse:
        if len(ticket.get("status_history", [])) >= MAX_STATUS_HISTORY:
            return self._error(
                "STATUS_HISTORY_LIMIT_REACHED",
                "The ticket cannot accept more status history entries.",
            )
        timestamp = self._now().isoformat()
        history = {
            "previous_status": ticket["status"],
            "new_status": status,
            "timestamp": timestamp,
        }
        if self._non_blank(actor):
            history["actor"] = actor.strip()
        updated = deepcopy(ticket)
        updated["status"] = status
        updated["updated_at"] = timestamp
        updated.setdefault("status_history", []).append(history)
        self._refresh_status_index(updated)
        return self._save_admin_change(updated, ticket["version"])

    def _save_admin_change(
        self,
        ticket: dict,
        expected_version: int,
    ) -> ToolResponse:
        try:
            self.repository.save(ticket, expected_version)
        except TicketVersionConflictError:
            return self._error(
                "TICKET_VERSION_CONFLICT",
                "The ticket changed before this update could be saved.",
                retryable=True,
            )
        except TicketDomainValidationError as exc:
            return self._ticket_validation_error(exc)
        saved = deepcopy(ticket)
        saved["version"] = expected_version + 1
        return ToolResponse.ok(
            data={"ticket": self._public(saved)},
            user_message="Ticket updated.",
        )

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
        return ToolResponse.ok(
            data={"ticket": self._public(ticket)},
            user_message=message,
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
        return ToolResponse.ok(
            data={"ticket": self._public(ticket)},
            user_message=message,
            agent={
                "entity": "ticket",
                "tracking_state": tracking_state,
                "selected_ticket_id": ticket["ticket_id"],
                "requires_ticket_id": requires_ticket_id,
                "status_message": message,
            },
        )

    @staticmethod
    def _public(ticket: dict) -> dict:
        internal = {
            "PK",
            "SK",
            "GSI1PK",
            "GSI1SK",
            "GSI2PK",
            "GSI2SK",
            "expires_at",
        }
        return {
            key: deepcopy(value)
            for key, value in ticket.items()
            if key not in internal
        }

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
    def _validate_actor(cls, actor: str | None) -> ToolResponse | None:
        if actor is not None and len(actor.strip()) > MAX_ACTOR_LENGTH:
            return cls._error(
                "ACTOR_TOO_LONG",
                f"An actor cannot exceed {MAX_ACTOR_LENGTH} characters.",
            )
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
