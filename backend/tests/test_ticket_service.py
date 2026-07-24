import re
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from botocore.exceptions import ClientError

from fakes import MemoryOrderRepository, MemoryTicketRepository
from src.models.ticket import (
    MAX_ADMIN_NOTES,
    MAX_STATUS_HISTORY,
    MAX_TICKET_ITEM_BYTES,
    NON_TERMINAL_TICKET_STATUSES,
    Ticket,
    generate_ticket_id,
    ticket_item_size_bytes,
)
from src.repositories.ticket_repository import TicketVersionConflictError
from src.services.ticket_service import TicketService, project_customer_ticket_view


NOW = datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)
SUPPORT_PHONE = "+92 21 111 222 333"
CUSTOMER_TICKET_KEYS = {
    "ticket_id",
    "ticket_type",
    "status",
    "status_label",
    "priority",
    "created_at",
    "updated_at",
    "next_action",
}
COMPLAINT_CUSTOMER_TICKET_KEYS = CUSTOMER_TICKET_KEYS | {"order_id"}
STATUS_CUSTOMER_FIELDS = {
    "open": ("Open", "await_support_contact"),
    "in_review": ("In review", "await_support_contact"),
    "waiting_for_customer": ("Waiting for customer", "respond_to_support"),
    "resolved": ("Resolved", "no_action_required"),
    "closed": ("Closed", "no_action_required"),
}


class TicketIdFactory:
    def __init__(self, *ids):
        self.ids = iter(ids or ["TKT-20260724-A1B2C3"])

    def __call__(self, _now):
        return next(self.ids)


def build_service(ticket_ids=(), support_phone=SUPPORT_PHONE):
    tickets = MemoryTicketRepository()
    orders = MemoryOrderRepository()
    service = TicketService(
        tickets,
        orders,
        support_phone_number=support_phone,
        clock=lambda: NOW,
        ticket_id_factory=TicketIdFactory(*ticket_ids),
    )
    return service, tickets, orders


def human_kwargs(**overrides):
    values = {
        "user_id": "user-1",
        "session_id": "session-1",
        "customer_name": "Ava",
        "customer_phone": "+923001234567",
        "description": "Please ask a person to contact me.",
        "source": "web",
        "idempotency_key": "request-1",
    }
    values.update(overrides)
    return values


def complaint_kwargs(**overrides):
    values = {
        "user_id": "user-1",
        "session_id": "session-1",
        "order_id": "ORD-1",
        "customer_name": "Ava",
        "customer_phone": "+923001234567",
        "description": "The order arrived incomplete.",
        "source": "web",
        "idempotency_key": "complaint-1",
    }
    values.update(overrides)
    return values


def customer_ticket_input(**overrides):
    ticket = {
        "ticket_id": "TKT-20260724-A1B2C3",
        "ticket_type": "human_assistance",
        "status": "open",
        "status_label": "forged",
        "priority": "normal",
        "created_at": "2026-07-24T15:00:00+05:00",
        "updated_at": "2026-07-24T15:30:00+05:00",
        "next_action": "forged",
        "future_customer_field": "must not survive",
    }
    ticket.update(overrides)
    return ticket


def create_order(repository, order_id="ORD-1", user_id="user-1", status="accepted"):
    repository.create(
        {
            "order_id": order_id,
            "user_id": user_id,
            "status": status,
            "version": 1,
        }
    )


def test_domain_constants_are_locked():
    assert NON_TERMINAL_TICKET_STATUSES == {
        "open",
        "in_review",
        "waiting_for_customer",
    }


def test_ticket_id_uses_locked_date_and_random_suffix_format():
    ticket_id = generate_ticket_id(NOW)
    assert re.fullmatch(r"TKT-20260724-[0-9A-F]{6}", ticket_id)


def test_create_human_assistance_and_deterministic_confirmation():
    service, tickets, _ = build_service()

    response = service.create_human_assistance(**human_kwargs())

    assert response.success
    assert response.data["ticket"]["ticket_type"] == "human_assistance"
    assert response.data["ticket"]["status"] == "open"
    assert response.data["ticket"]["priority"] == "normal"
    assert response.user_message == (
        "Your support request has been created.\n"
        "\n"
        "Ticket ID: TKT-20260724-A1B2C3\n"
        "Status: Open\n"
        "\n"
        "Our team will review your request and contact you using the contact "
        "details associated with your account.\n"
        f"Calls may come from {SUPPORT_PHONE}."
    )
    stored = tickets.get("TKT-20260724-A1B2C3")
    assert stored["PK"] == "TICKET#TKT-20260724-A1B2C3"
    assert stored["GSI1PK"] == "CUSTOMER#user-1"
    assert stored["GSI2PK"] == "STATUS#open"
    assert "expires_at" not in stored


def assert_customer_ticket_view(ticket, *, complaint=False):
    expected = (
        COMPLAINT_CUSTOMER_TICKET_KEYS
        if complaint
        else CUSTOMER_TICKET_KEYS
    )
    assert set(ticket) == expected


def test_customer_ticket_projector_valid_dictionary_has_exact_safe_keys():
    projected = project_customer_ticket_view(customer_ticket_input())

    assert projected == {
        "ticket_id": "TKT-20260724-A1B2C3",
        "ticket_type": "human_assistance",
        "status": "open",
        "status_label": "Open",
        "priority": "normal",
        "created_at": "2026-07-24T10:00:00+00:00",
        "updated_at": "2026-07-24T10:30:00+00:00",
        "next_action": "await_support_contact",
    }


def test_customer_ticket_projector_accepts_valid_ticket_model():
    service, tickets, _ = build_service()
    created = service.create_human_assistance(**human_kwargs())
    model = Ticket.model_validate(
        tickets.get(created.data["ticket"]["ticket_id"])
    )

    projected = project_customer_ticket_view(model)

    assert_customer_ticket_view(projected)
    assert projected == created.data["ticket"]


@pytest.mark.parametrize(
    ("status", "status_label", "next_action"),
    [
        (status, expected[0], expected[1])
        for status, expected in STATUS_CUSTOMER_FIELDS.items()
    ],
)
def test_customer_ticket_projector_derives_every_status_field(
    status,
    status_label,
    next_action,
):
    projected = project_customer_ticket_view(
        customer_ticket_input(
            status=status,
            status_label="forged label",
            next_action="forged_action",
        )
    )

    assert projected["status_label"] == status_label
    assert projected["next_action"] == next_action


@pytest.mark.parametrize(
    "overrides",
    [
        {"ticket_type": "internal_escalation"},
        {"status": "pending_manager"},
        {"priority": "internal_only"},
        {"ticket_id": "   "},
        {"ticket_id": "TKT-20260230-A1B2C3"},
        {"created_at": "not-a-timestamp"},
        {"updated_at": "not-a-timestamp"},
        {"created_at": "2026-07-24T10:00:00"},
        {"updated_at": "2026-07-24T10:00:00"},
        {
            "created_at": "2026-07-24T10:30:00+00:00",
            "updated_at": "2026-07-24T10:00:00+00:00",
        },
        {"ticket_type": "order_complaint", "order_id": "   "},
        {"order_id": "ORD-1"},
    ],
)
def test_customer_ticket_projector_rejects_malformed_data(overrides):
    assert project_customer_ticket_view(
        customer_ticket_input(**overrides)
    ) == {}


def test_customer_ticket_projector_retains_valid_complaint_order_id():
    projected = project_customer_ticket_view(
        customer_ticket_input(
            ticket_type="order_complaint",
            order_id="ORD-1",
        )
    )

    assert_customer_ticket_view(projected, complaint=True)
    assert projected["order_id"] == "ORD-1"


@pytest.mark.parametrize(
    ("status", "next_action"),
    [
        (status, expected[1])
        for status, expected in STATUS_CUSTOMER_FIELDS.items()
    ],
)
def test_service_customer_serializer_maps_every_valid_status(
    status,
    next_action,
):
    projected = TicketService._customer_ticket_view(
        customer_ticket_input(status=status)
    )

    assert_customer_ticket_view(projected)
    assert projected["next_action"] == next_action


def test_service_next_action_rejects_unknown_status_without_fallback():
    with pytest.raises(ValueError, match="invalid ticket status"):
        TicketService._customer_next_action("pending_manager")


def test_service_customer_serializer_rejects_malformed_internal_ticket():
    with pytest.raises(ValueError, match="invalid customer ticket data"):
        TicketService._customer_ticket_view(
            customer_ticket_input(status="pending_manager")
        )


def test_customer_ticket_view_uses_strict_allowlist_for_all_internal_fields():
    service, tickets, _ = build_service()
    created = service.create_human_assistance(**human_kwargs())
    stored = tickets.data[created.data["ticket"]["ticket_id"]]
    stored["admin_notes"] = [{
        "text": "Internal note",
        "timestamp": NOW.isoformat(),
        "actor": "admin-1",
    }]
    stored["status_history"] = [{
        "previous_status": "open",
        "new_status": "in_review",
        "timestamp": NOW.isoformat(),
        "actor": "admin-1",
    }]
    stored["request_id"] = "request-private"
    stored["idempotency_key"] = "idempotency-private"
    stored["idempotency_hash"] = "hash-private"
    stored["raw_customer_metadata"] = {"private": True}
    stored["expires_at"] = 123

    response = service.get_ticket_status("user-1", stored["ticket_id"])

    assert_customer_ticket_view(response.data["ticket"])
    assert response.data["ticket"] == {
        "ticket_id": "TKT-20260724-A1B2C3",
        "ticket_type": "human_assistance",
        "status": "open",
        "status_label": "Open",
        "priority": "normal",
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "next_action": "await_support_contact",
    }


def test_human_creation_reuse_and_idempotent_resolution_are_customer_safe():
    service, _, _ = build_service()

    created = service.create_human_assistance(**human_kwargs())
    reused = service.create_human_assistance(
        **human_kwargs(idempotency_key="request-2")
    )
    idempotent = service.create_human_assistance(**human_kwargs())

    for response in (created, reused, idempotent):
        assert_customer_ticket_view(response.data["ticket"])
        assert response.next_action == "await_support_contact"


def test_complaint_creation_is_customer_safe_and_preserves_message():
    service, _, orders = build_service()
    create_order(orders, status="preparing")

    response = service.create_order_complaint(**complaint_kwargs())

    assert_customer_ticket_view(response.data["ticket"], complaint=True)
    assert response.data["ticket"]["order_id"] == "ORD-1"
    assert "description" not in response.data["ticket"]
    assert "order_status_snapshot" not in response.data["ticket"]
    assert response.next_action == "await_support_contact"
    assert response.user_message == (
        "Your complaint has been recorded.\n"
        "\n"
        "Ticket ID: TKT-20260724-A1B2C3\n"
        "Order ID: ORD-1\n"
        "Status: Open\n"
        "\n"
        "Our team will review your complaint and contact you using the contact "
        "details associated with your account.\n"
        f"Calls may come from {SUPPORT_PHONE}."
    )


@pytest.mark.parametrize("support_phone", ["", "   "])
def test_human_creation_without_support_phone_omits_call_sentence(support_phone):
    service, tickets, _ = build_service(support_phone=support_phone)

    response = service.create_human_assistance(**human_kwargs())

    assert response.success
    assert response.user_message == (
        "Your support request has been created.\n"
        "\n"
        "Ticket ID: TKT-20260724-A1B2C3\n"
        "Status: Open\n"
        "\n"
        "Our team will review your request and contact you using the contact "
        "details associated with your account."
    )
    assert "Calls may come from" not in response.user_message
    assert tickets.get("TKT-20260724-A1B2C3") is not None


def test_missing_customer_phone_and_blank_description_are_allowed():
    service, tickets, _ = build_service()

    response = service.create_human_assistance(
        **human_kwargs(customer_phone=None, description="   ")
    )

    assert response.success
    stored = tickets.get(response.data["ticket"]["ticket_id"])
    assert "customer_phone" not in stored
    assert stored.get("description") is None


def test_same_user_session_non_terminal_human_ticket_is_reused():
    service, tickets, _ = build_service()
    first = service.create_human_assistance(**human_kwargs())

    second = service.create_human_assistance(
        **human_kwargs(idempotency_key="request-2", description="Another request")
    )

    assert second.data["ticket"]["ticket_id"] == first.data["ticket"]["ticket_id"]
    assert len(tickets.data) == 1
    assert len(tickets.markers) == 2


def test_reused_ticket_idempotency_survives_later_terminal_status():
    service, tickets, _ = build_service()
    first = service.create_human_assistance(**human_kwargs())
    reused = service.create_human_assistance(
        **human_kwargs(idempotency_key="request-2")
    )
    ticket_id = first.data["ticket"]["ticket_id"]
    tickets.data[ticket_id]["status"] = "closed"

    retry = service.create_human_assistance(
        **human_kwargs(idempotency_key="request-2")
    )

    assert reused.data["ticket"]["ticket_id"] == ticket_id
    assert retry.data["ticket"]["ticket_id"] == ticket_id
    assert len(tickets.data) == 1


def test_terminal_change_during_reuse_binding_prevents_invalid_binding():
    service, tickets, _ = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    first = service.create_human_assistance(**human_kwargs())
    tickets.reusable_change_count = 1

    response = service.create_human_assistance(
        **human_kwargs(idempotency_key="request-2")
    )

    assert response.data["ticket"]["ticket_id"] == "TKT-20260724-D4E5F6"
    assert tickets.get(first.data["ticket"]["ticket_id"])["status"] == "closed"


def test_unrelated_reuse_binding_failure_propagates():
    service, tickets, _ = build_service()
    service.create_human_assistance(**human_kwargs())
    error = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "TransactWriteItems",
    )
    tickets.bind_error = error

    with pytest.raises(ClientError) as exc_info:
        service.create_human_assistance(
            **human_kwargs(idempotency_key="request-2")
        )

    assert exc_info.value is error


@pytest.mark.parametrize(
    "change",
    [
        {"session_id": "session-2"},
        {"user_id": "user-2"},
    ],
)
def test_different_session_or_customer_does_not_reuse(change):
    service, tickets, _ = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    service.create_human_assistance(**human_kwargs())

    second = service.create_human_assistance(
        **human_kwargs(**change, idempotency_key="request-2")
    )

    assert second.data["ticket"]["ticket_id"] == "TKT-20260724-D4E5F6"
    assert len(tickets.data) == 2


@pytest.mark.parametrize("status", ["resolved", "closed"])
def test_terminal_human_ticket_is_not_reused(status):
    service, tickets, _ = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    first = service.create_human_assistance(**human_kwargs())
    tickets.data[first.data["ticket"]["ticket_id"]]["status"] = status

    second = service.create_human_assistance(
        **human_kwargs(idempotency_key="request-2")
    )

    assert second.data["ticket"]["ticket_id"] == "TKT-20260724-D4E5F6"


def test_complaint_is_not_reused_as_human_assistance():
    service, tickets, orders = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    create_order(orders)
    service.create_order_complaint(**complaint_kwargs())

    human = service.create_human_assistance(
        **human_kwargs(idempotency_key="request-2")
    )

    assert human.data["ticket"]["ticket_type"] == "human_assistance"
    assert len(tickets.data) == 2


def test_same_raw_key_has_independent_human_and_complaint_identities():
    service, tickets, orders = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    create_order(orders)

    human = service.create_human_assistance(
        **human_kwargs(idempotency_key="shared-key")
    )
    complaint = service.create_order_complaint(
        **complaint_kwargs(idempotency_key="shared-key")
    )

    assert human.data["ticket"]["ticket_type"] == "human_assistance"
    assert complaint.data["ticket"]["ticket_type"] == "order_complaint"
    assert human.data["ticket"]["ticket_id"] != complaint.data["ticket"]["ticket_id"]
    assert {marker["operation"] for marker in tickets.markers.values()} == {
        "human_assistance",
        "order_complaint",
    }


def test_human_operation_cannot_resolve_complaint_with_same_raw_key():
    service, _, orders = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    create_order(orders)
    complaint = service.create_order_complaint(
        **complaint_kwargs(idempotency_key="shared-key")
    )

    human = service.create_human_assistance(
        **human_kwargs(idempotency_key="shared-key")
    )

    assert complaint.data["ticket"]["ticket_type"] == "order_complaint"
    assert human.data["ticket"]["ticket_type"] == "human_assistance"
    assert complaint.data["ticket"]["ticket_id"] != human.data["ticket"]["ticket_id"]


def test_marker_operation_or_ticket_type_mismatch_is_rejected_safely():
    service, tickets, orders = build_service()
    create_order(orders)
    marker_hash = service._idempotency_hash(
        "user-1", "order_complaint", "complaint-1"
    )
    tickets.markers[marker_hash] = {
        "PK": f"IDEMPOTENCY#{marker_hash}",
        "SK": "METADATA",
        "ticket_id": "TKT-20260724-A1B2C3",
        "user_id": "user-1",
        "operation": "human_assistance",
        "expires_at": int(NOW.timestamp()) + 86400,
    }
    tickets.data["TKT-20260724-A1B2C3"] = {
        "ticket_id": "TKT-20260724-A1B2C3",
        "user_id": "user-1",
        "ticket_type": "human_assistance",
    }

    response = service.create_order_complaint(**complaint_kwargs())

    assert not response.success
    assert response.error_code == "IDEMPOTENCY_CONFLICT"


def test_marker_does_not_store_raw_key_hash_input_or_redundant_hash():
    service, tickets, _ = build_service()
    service.create_human_assistance(
        **human_kwargs(idempotency_key="private-request-value")
    )

    marker = next(iter(tickets.markers.values()))
    persisted = str(marker)
    assert "private-request-value" not in persisted
    assert "user-1\x00human_assistance\x00private-request-value" not in persisted
    assert "idempotency_hash" not in marker


def test_most_recent_matching_human_ticket_is_reused_deterministically():
    service, tickets, _ = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    first = service.create_human_assistance(**human_kwargs())
    tickets.data[first.data["ticket"]["ticket_id"]]["session_id"] = "other"
    second = service.create_human_assistance(
        **human_kwargs(idempotency_key="request-2")
    )
    tickets.data[first.data["ticket"]["ticket_id"]]["session_id"] = "session-1"
    tickets.data[first.data["ticket"]["ticket_id"]]["updated_at"] = (
        "2026-07-24T09:00:00+00:00"
    )
    tickets.data[first.data["ticket"]["ticket_id"]]["GSI2SK"] = (
        f"UPDATED#2026-07-24T09:00:00+00:00#"
        f"{first.data['ticket']['ticket_id']}"
    )
    tickets.data[second.data["ticket"]["ticket_id"]]["updated_at"] = (
        "2026-07-24T11:00:00+00:00"
    )
    tickets.data[second.data["ticket"]["ticket_id"]]["GSI2SK"] = (
        f"UPDATED#2026-07-24T11:00:00+00:00#"
        f"{second.data['ticket']['ticket_id']}"
    )

    reused = service.create_human_assistance(
        **human_kwargs(idempotency_key="request-3")
    )

    assert reused.data["ticket"]["ticket_id"] == second.data["ticket"]["ticket_id"]


def test_valid_owned_order_complaint_captures_status_and_message():
    service, tickets, orders = build_service()
    create_order(orders, status="preparing")

    response = service.create_order_complaint(**complaint_kwargs())

    assert response.success
    assert "order_status_snapshot" not in response.data["ticket"]
    assert response.user_message == (
        "Your complaint has been recorded.\n"
        "\n"
        "Ticket ID: TKT-20260724-A1B2C3\n"
        "Order ID: ORD-1\n"
        "Status: Open\n"
        "\n"
        "Our team will review your complaint and contact you using the contact "
        "details associated with your account.\n"
        f"Calls may come from {SUPPORT_PHONE}."
    )
    assert tickets.get("TKT-20260724-A1B2C3")["order_id"] == "ORD-1"
    assert (
        tickets.get("TKT-20260724-A1B2C3")["order_status_snapshot"]
        == "preparing"
    )


@pytest.mark.parametrize("support_phone", ["", "   "])
def test_complaint_without_support_phone_succeeds_without_call_sentence(
    support_phone,
):
    service, tickets, orders = build_service(support_phone=support_phone)
    create_order(orders)

    response = service.create_order_complaint(**complaint_kwargs())

    assert response.success
    assert response.user_message.endswith(
        "contact details associated with your account."
    )
    assert "Calls may come from" not in response.user_message
    assert tickets.get("TKT-20260724-A1B2C3") is not None


@pytest.mark.parametrize("status", ["awaiting_fulfillment_method", "cancelled"])
def test_complaints_are_allowed_for_any_owned_order_status(status):
    service, _, orders = build_service()
    create_order(orders, status=status)
    assert service.create_order_complaint(**complaint_kwargs()).success


def test_missing_and_unauthorized_orders_return_identical_not_found():
    missing_service, _, _ = build_service()
    unauthorized_service, _, orders = build_service()
    create_order(orders, user_id="another-user")

    missing = missing_service.create_order_complaint(**complaint_kwargs())
    unauthorized = unauthorized_service.create_order_complaint(**complaint_kwargs())

    assert missing.model_dump() == unauthorized.model_dump()
    assert missing.error_code == "ORDER_NOT_FOUND"


@pytest.mark.parametrize("description", ["", "   \n\t"])
def test_blank_complaint_is_rejected(description):
    service, _, orders = build_service()
    create_order(orders)

    response = service.create_order_complaint(
        **complaint_kwargs(description=description)
    )

    assert not response.success
    assert response.error_code == "DESCRIPTION_REQUIRED"


def test_complaint_description_preserves_original_evidence():
    service, tickets, orders = build_service()
    create_order(orders)
    description = "  first line\nsecond line  "

    response = service.create_order_complaint(
        **complaint_kwargs(description=description)
    )

    assert response.success
    stored = tickets.get(response.data["ticket"]["ticket_id"])
    assert stored["description"] == description


def test_complaint_size_limit_applies_to_unmodified_original():
    service, _, orders = build_service()
    create_order(orders)

    response = service.create_order_complaint(
        **complaint_kwargs(description=(" " + ("x" * 4_000)))
    )

    assert response.error_code == "DESCRIPTION_TOO_LONG"


def test_two_complaints_for_same_order_with_different_keys_are_distinct():
    service, tickets, orders = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    create_order(orders)

    first = service.create_order_complaint(**complaint_kwargs())
    second = service.create_order_complaint(
        **complaint_kwargs(idempotency_key="complaint-2")
    )

    assert first.data["ticket"]["ticket_id"] != second.data["ticket"]["ticket_id"]
    assert len(tickets.data) == 2


def test_exact_idempotent_retry_returns_same_ticket():
    service, tickets, orders = build_service()
    create_order(orders)

    first = service.create_order_complaint(**complaint_kwargs())
    retry = service.create_order_complaint(**complaint_kwargs())

    assert retry.data["ticket"]["ticket_id"] == first.data["ticket"]["ticket_id"]
    assert_customer_ticket_view(first.data["ticket"], complaint=True)
    assert_customer_ticket_view(retry.data["ticket"], complaint=True)
    assert len(tickets.data) == 1


def test_idempotency_hash_is_customer_scoped_and_raw_key_is_not_stored():
    service, tickets, orders = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    create_order(orders, "ORD-1", "user-1")
    create_order(orders, "ORD-2", "user-2")

    service.create_order_complaint(**complaint_kwargs())
    service.create_order_complaint(
        **complaint_kwargs(user_id="user-2", order_id="ORD-2")
    )

    assert len(tickets.markers) == 2
    assert all("complaint-1" not in str(marker) for marker in tickets.markers.values())


def test_idempotency_marker_expires_after_exactly_24_hours():
    service, tickets, _ = build_service()
    service.create_human_assistance(**human_kwargs())

    marker = next(iter(tickets.markers.values()))
    assert marker["expires_at"] == int(NOW.timestamp()) + 24 * 60 * 60


def test_expired_marker_does_not_block_new_ticket():
    service, tickets, orders = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    create_order(orders)
    first = service.create_order_complaint(**complaint_kwargs())
    marker = next(iter(tickets.markers.values()))
    marker["expires_at"] = int(NOW.timestamp()) - 1

    second = service.create_order_complaint(**complaint_kwargs())

    assert second.data["ticket"]["ticket_id"] != first.data["ticket"]["ticket_id"]


def test_ticket_id_collision_retries_with_new_server_generated_id():
    service, tickets, _ = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    tickets.ticket_collision_count = 1

    response = service.create_human_assistance(**human_kwargs())

    assert response.data["ticket"]["ticket_id"] == "TKT-20260724-D4E5F6"


def test_ticket_id_collision_retry_exhaustion_is_deterministic():
    ids = tuple(f"TKT-20260724-AAAAA{index}" for index in range(5))
    service, tickets, _ = build_service(ids)
    tickets.ticket_collision_count = 5

    response = service.create_human_assistance(**human_kwargs())

    assert not response.success
    assert response.error_code == "TICKET_ID_GENERATION_FAILED"
    assert len(tickets.data) == 0


def test_different_keys_for_same_session_use_one_guarded_human_ticket():
    service, tickets, _ = build_service()

    first = service.create_human_assistance(**human_kwargs())
    second = service.create_human_assistance(
        **human_kwargs(idempotency_key="request-2")
    )

    assert first.data["ticket"]["ticket_id"] == second.data["ticket"]["ticket_id"]
    assert len(tickets.data) == 1
    assert len(tickets.guards) == 1
    assert len(tickets.markers) == 2


@pytest.mark.parametrize("guarded_state", ["closed", "missing"])
def test_terminal_or_missing_guarded_ticket_allows_atomic_replacement(guarded_state):
    service, tickets, _ = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    first = service.create_human_assistance(**human_kwargs())
    first_id = first.data["ticket"]["ticket_id"]
    if guarded_state == "missing":
        del tickets.data[first_id]
    else:
        tickets.data[first_id]["status"] = guarded_state

    replacement = service.create_human_assistance(
        **human_kwargs(idempotency_key="request-2")
    )

    assert replacement.data["ticket"]["ticket_id"] == "TKT-20260724-D4E5F6"
    guard = next(iter(tickets.guards.values()))
    assert guard["active_ticket_id"] == "TKT-20260724-D4E5F6"
    assert guard["version"] == 2


def test_explicit_ticket_lookup_enforces_ownership_with_identical_error():
    service, _, _ = build_service()
    created = service.create_human_assistance(**human_kwargs())
    ticket_id = created.data["ticket"]["ticket_id"]

    owned = service.get_ticket_status("user-1", ticket_id)
    unauthorized = service.get_ticket_status("user-2", ticket_id)
    missing = service.get_ticket_status("user-1", "TKT-20260724-FFFFFF")

    assert owned.success
    assert owned.user_message == f"Ticket ID: {ticket_id}\nStatus: Open"
    assert unauthorized.model_dump() == missing.model_dump()
    assert unauthorized.error_code == "TICKET_NOT_FOUND"


def test_explicit_ticket_lookup_excludes_notes_history_session_and_version():
    service, tickets, _ = build_service()
    created = service.create_human_assistance(**human_kwargs())
    stored = tickets.data[created.data["ticket"]["ticket_id"]]
    stored["admin_notes"] = [{
        "text": "Do not disclose",
        "timestamp": NOW.isoformat(),
        "actor": "admin-1",
    }]
    stored["status_history"] = [{
        "previous_status": "open",
        "new_status": "in_review",
        "timestamp": NOW.isoformat(),
        "actor": "admin-1",
    }]

    response = service.get_ticket_status("user-1", stored["ticket_id"])

    assert_customer_ticket_view(response.data["ticket"])
    assert not {
        "admin_notes",
        "status_history",
        "session_id",
        "version",
    } & response.data["ticket"].keys()


def test_single_non_terminal_ticket_is_selected_automatically():
    service, _, _ = build_service()
    created = service.create_human_assistance(**human_kwargs())

    response = service.get_ticket_status("user-1")

    assert response.agent["tracking_state"] == "single_active_ticket"
    assert response.agent["selected_ticket_id"] == created.data["ticket"]["ticket_id"]
    assert response.agent["requires_ticket_id"] is False
    assert_customer_ticket_view(response.data["ticket"])


def test_multiple_non_terminal_tickets_require_ticket_id():
    service, tickets, _ = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    service.create_human_assistance(**human_kwargs())
    tickets.data["TKT-20260724-A1B2C3"]["session_id"] = "session-1"
    service.create_human_assistance(
        **human_kwargs(session_id="session-2", idempotency_key="request-2")
    )

    response = service.get_ticket_status("user-1")

    assert response.agent["tracking_state"] == "multiple_active_tickets"
    assert response.agent["requires_ticket_id"] is True
    assert response.agent["required_input"] == "ticket_id"
    assert all(
        set(ticket) == CUSTOMER_TICKET_KEYS
        for ticket in response.data["tickets"]
    )


@pytest.mark.parametrize("status", ["resolved", "closed"])
def test_terminal_ticket_remains_available_by_explicit_id(status):
    service, tickets, _ = build_service()
    created = service.create_human_assistance(**human_kwargs())
    ticket_id = created.data["ticket"]["ticket_id"]
    tickets.data[ticket_id]["status"] = status

    response = service.get_ticket_status("user-1", ticket_id)

    assert response.success
    assert response.data["ticket"]["status"] == status
    assert_customer_ticket_view(response.data["ticket"])


def test_no_non_terminal_ticket_requests_older_ticket_id():
    service, tickets, _ = build_service()
    created = service.create_human_assistance(**human_kwargs())
    tickets.data[created.data["ticket"]["ticket_id"]]["status"] = "closed"

    response = service.get_ticket_status("user-1")

    assert response.agent["tracking_state"] == "no_active_tickets"
    assert response.agent["requires_ticket_id"] is True
    assert "older ticket" in response.user_message


def test_customer_tracking_considers_tickets_across_conceptual_pages():
    service, tickets, _ = build_service(
        (
            "TKT-20260724-A1B2C3",
            "TKT-20260724-D4E5F6",
            "TKT-20260724-112233",
        )
    )
    first = service.create_human_assistance(**human_kwargs())
    tickets.data[first.data["ticket"]["ticket_id"]]["status"] = "closed"
    service.create_human_assistance(
        **human_kwargs(session_id="session-2", idempotency_key="request-2")
    )
    service.create_human_assistance(
        **human_kwargs(session_id="session-3", idempotency_key="request-3")
    )
    response = service.get_ticket_status("user-1")

    assert response.agent["tracking_state"] == "multiple_active_tickets"


def test_reusable_human_ticket_on_later_page_is_bound_to_guard():
    service, tickets, _ = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    older = service.create_human_assistance(**human_kwargs())
    newer = service.create_human_assistance(
        **human_kwargs(session_id="session-2", idempotency_key="request-2")
    )
    older_ticket = tickets.data[older.data["ticket"]["ticket_id"]]
    newer_ticket = tickets.data[newer.data["ticket"]["ticket_id"]]
    older_ticket["status"] = "closed"
    newer_ticket["session_id"] = "session-1"
    tickets.guards.clear()
    tickets.customer_pages = [[older_ticket], [newer_ticket]]

    response = service.create_human_assistance(
        **human_kwargs(idempotency_key="request-3")
    )

    assert response.data["ticket"]["ticket_id"] == newer_ticket["ticket_id"]
    assert next(iter(tickets.guards.values()))["active_ticket_id"] == newer_ticket[
        "ticket_id"
    ]


def test_one_non_terminal_ticket_on_later_page_is_selected():
    service, tickets, _ = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    closed = service.create_human_assistance(**human_kwargs())
    active = service.create_human_assistance(
        **human_kwargs(session_id="session-2", idempotency_key="request-2")
    )
    tickets.data[closed.data["ticket"]["ticket_id"]]["status"] = "closed"
    tickets.customer_pages = [
        [tickets.data[closed.data["ticket"]["ticket_id"]]],
        [tickets.data[active.data["ticket"]["ticket_id"]]],
    ]

    response = service.get_ticket_status("user-1")

    assert response.agent["tracking_state"] == "single_active_ticket"
    assert response.agent["selected_ticket_id"] == active.data["ticket"]["ticket_id"]


def test_multiple_non_terminal_tickets_split_across_pages_require_id():
    service, tickets, _ = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    first = service.create_human_assistance(**human_kwargs())
    second = service.create_human_assistance(
        **human_kwargs(session_id="session-2", idempotency_key="request-2")
    )
    tickets.customer_pages = [
        [tickets.data[first.data["ticket"]["ticket_id"]]],
        [tickets.data[second.data["ticket"]["ticket_id"]]],
    ]

    response = service.get_ticket_status("user-1")

    assert response.agent["tracking_state"] == "multiple_active_tickets"


def test_no_non_terminal_tickets_across_pages_requests_older_id():
    service, tickets, _ = build_service(
        ("TKT-20260724-A1B2C3", "TKT-20260724-D4E5F6")
    )
    first = service.create_human_assistance(**human_kwargs())
    second = service.create_human_assistance(
        **human_kwargs(session_id="session-2", idempotency_key="request-2")
    )
    tickets.data[first.data["ticket"]["ticket_id"]]["status"] = "resolved"
    tickets.data[second.data["ticket"]["ticket_id"]]["status"] = "closed"
    tickets.customer_pages = [
        [tickets.data[first.data["ticket"]["ticket_id"]]],
        [tickets.data[second.data["ticket"]["ticket_id"]]],
    ]

    response = service.get_ticket_status("user-1")

    assert response.agent["tracking_state"] == "no_active_tickets"


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("ticket_type", "refund_request", "INVALID_TICKET_TYPE"),
        ("priority", "critical", "INVALID_TICKET_PRIORITY"),
        ("status", "pending", "INVALID_TICKET_STATUS"),
    ],
)
def test_invalid_domain_values_are_rejected(field, value, error_code):
    service, _, _ = build_service()

    response = service.validate_domain_value(field, value)

    assert not response.success
    assert response.error_code == error_code


def test_status_update_appends_history_and_refreshes_gsi2():
    service, tickets, _ = build_service()
    created = service.create_human_assistance(**human_kwargs())
    ticket_id = created.data["ticket"]["ticket_id"]

    response = service.update_status(ticket_id, "in_review", actor="admin-1")

    assert response.success
    stored = tickets.get(ticket_id)
    assert stored["status_history"] == [
        {
            "previous_status": "open",
            "new_status": "in_review",
            "timestamp": NOW.isoformat(),
            "actor": "admin-1",
        }
    ]
    assert stored["GSI2PK"] == "STATUS#in_review"
    assert stored["version"] == 2


def test_unchanged_status_is_a_complete_no_op():
    service, tickets, _ = build_service()
    ticket_id = service.create_human_assistance(
        **human_kwargs()
    ).data["ticket"]["ticket_id"]
    before = tickets.get(ticket_id)

    response = service.update_status(ticket_id, "open", actor="admin-1")

    assert response.success
    assert tickets.get(ticket_id) == before
    assert response.data["ticket"]["version"] == before["version"]
    assert tickets.save_calls == []


def test_admin_note_append_preserves_prior_notes_and_rejects_blank():
    service, tickets, _ = build_service()
    ticket_id = service.create_human_assistance(
        **human_kwargs()
    ).data["ticket"]["ticket_id"]

    service.append_admin_note(ticket_id, "First note", actor="admin-1")
    service.append_admin_note(ticket_id, "Second note")
    blank = service.append_admin_note(ticket_id, "   ")

    stored = tickets.get(ticket_id)
    assert [note["text"] for note in stored["admin_notes"]] == [
        "First note",
        "Second note",
    ]
    assert stored["admin_notes"][0]["actor"] == "admin-1"
    assert "actor" not in stored["admin_notes"][1]
    assert blank.error_code == "ADMIN_NOTE_REQUIRED"


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("description", "x" * 4001, "DESCRIPTION_TOO_LONG"),
        ("category", "x" * 101, "CATEGORY_TOO_LONG"),
        ("customer_name", "x" * 201, "CUSTOMER_NAME_TOO_LONG"),
        ("customer_phone", "x" * 65, "CUSTOMER_PHONE_TOO_LONG"),
        ("source", "x" * 65, "SOURCE_TOO_LONG"),
    ],
)
def test_human_creation_rejects_oversized_fields(field, value, error_code):
    service, _, _ = build_service()

    response = service.create_human_assistance(
        **human_kwargs(**{field: value})
    )

    assert not response.success
    assert response.error_code == error_code


def test_complaint_rejects_oversized_description():
    service, _, orders = build_service()
    create_order(orders)

    response = service.create_order_complaint(
        **complaint_kwargs(description="x" * 4001)
    )

    assert response.error_code == "DESCRIPTION_TOO_LONG"


def test_admin_note_and_actor_limits_are_deterministic():
    service, _, _ = build_service()
    ticket_id = service.create_human_assistance(
        **human_kwargs()
    ).data["ticket"]["ticket_id"]

    note = service.append_admin_note(ticket_id, "x" * 2001)
    actor = service.update_status(ticket_id, "in_review", actor="x" * 201)

    assert note.error_code == "ADMIN_NOTE_TOO_LONG"
    assert actor.error_code == "ACTOR_TOO_LONG"


def test_note_and_history_collection_limits_do_not_discard_existing_entries():
    service, tickets, _ = build_service()
    ticket_id = service.create_human_assistance(
        **human_kwargs()
    ).data["ticket"]["ticket_id"]
    tickets.data[ticket_id]["admin_notes"] = [
        {"text": f"note-{index}", "timestamp": NOW.isoformat()}
        for index in range(MAX_ADMIN_NOTES)
    ]
    tickets.data[ticket_id]["status_history"] = [
        {
            "previous_status": "open",
            "new_status": "in_review",
            "timestamp": NOW.isoformat(),
        }
        for _index in range(MAX_STATUS_HISTORY)
    ]

    note = service.append_admin_note(ticket_id, "one more")
    status = service.update_status(ticket_id, "in_review")

    assert note.error_code == "ADMIN_NOTE_LIMIT_REACHED"
    assert status.error_code == "STATUS_HISTORY_LIMIT_REACHED"
    assert len(tickets.get(ticket_id)["admin_notes"]) == MAX_ADMIN_NOTES
    assert len(tickets.get(ticket_id)["status_history"]) == MAX_STATUS_HISTORY


def test_oversized_note_update_returns_deterministic_error_without_mutation():
    service, tickets, _ = build_service()
    ticket_id = service.create_human_assistance(
        **human_kwargs()
    ).data["ticket"]["ticket_id"]
    ticket = tickets.data[ticket_id]
    note = {
        "text": "😀" * 2_000,
        "timestamp": NOW.isoformat(),
        "actor": "😀" * 200,
    }
    ticket["admin_notes"] = []
    while len(ticket["admin_notes"]) < 99:
        candidate = deepcopy(ticket)
        candidate["admin_notes"].append(note)
        if ticket_item_size_bytes(candidate) >= MAX_TICKET_ITEM_BYTES:
            break
        ticket["admin_notes"].append(deepcopy(note))
    assert ticket_item_size_bytes(ticket) < MAX_TICKET_ITEM_BYTES
    before = tickets.get(ticket_id)

    response = service.append_admin_note(
        ticket_id,
        "😀" * 2_000,
        actor="😀" * 200,
    )

    assert not response.success
    assert response.error_code == "TICKET_ITEM_TOO_LARGE"
    assert tickets.get(ticket_id) == before


def test_optimistic_lock_conflict_is_deterministic():
    service, tickets, _ = build_service()
    ticket_id = service.create_human_assistance(
        **human_kwargs()
    ).data["ticket"]["ticket_id"]
    tickets.version_conflict_count = 1

    response = service.update_status(ticket_id, "in_review")

    assert not response.success
    assert response.error_code == "TICKET_VERSION_CONFLICT"
    assert response.retryable is True


@pytest.mark.parametrize("first_change", ["note", "status"])
def test_stale_note_or_status_update_cannot_overwrite_concurrent_change(first_change):
    service, tickets, _ = build_service()
    ticket_id = service.create_human_assistance(
        **human_kwargs()
    ).data["ticket"]["ticket_id"]
    first = tickets.get(ticket_id)
    stale = tickets.get(ticket_id)
    if first_change == "note":
        first["admin_notes"].append(
            {"text": "first", "timestamp": NOW.isoformat()}
        )
    else:
        first["status"] = "in_review"
        first["GSI2PK"] = "STATUS#in_review"
    tickets.save(first, expected_version=1)

    with pytest.raises(TicketVersionConflictError):
        tickets.save(stale, expected_version=1)


@pytest.mark.parametrize("status", ["resolved", "closed"])
def test_reopen_from_terminal_status(status):
    service, tickets, _ = build_service()
    ticket_id = service.create_human_assistance(
        **human_kwargs()
    ).data["ticket"]["ticket_id"]
    tickets.data[ticket_id]["status"] = status

    response = service.reopen_ticket(ticket_id, actor="admin-1")

    assert response.success
    assert tickets.get(ticket_id)["status"] == "open"
    assert tickets.get(ticket_id)["status_history"][-1]["previous_status"] == status


def test_reopen_from_non_terminal_is_rejected():
    service, _, _ = build_service()
    ticket_id = service.create_human_assistance(
        **human_kwargs()
    ).data["ticket"]["ticket_id"]

    response = service.reopen_ticket(ticket_id)

    assert not response.success
    assert response.error_code == "INVALID_TICKET_STATE"


def test_status_change_never_updates_linked_order():
    service, _, orders = build_service()
    create_order(orders, status="accepted")
    ticket_id = service.create_order_complaint(
        **complaint_kwargs()
    ).data["ticket"]["ticket_id"]

    service.update_status(ticket_id, "resolved")

    assert orders.get_by_order_id("ORD-1")["status"] == "accepted"


def test_admin_get_and_paginated_status_list():
    service, _, _ = build_service()
    ticket_id = service.create_human_assistance(
        **human_kwargs()
    ).data["ticket"]["ticket_id"]

    detail = service.get_ticket_for_admin(ticket_id)
    listing = service.list_tickets_by_status("open", limit=25)

    assert detail.data["ticket"]["ticket_id"] == ticket_id
    assert listing.data["tickets"][0]["ticket_id"] == ticket_id
    assert listing.data["next_cursor"] is None


def test_admin_ticket_view_keeps_details_without_repository_metadata():
    service, tickets, _ = build_service()
    ticket_id = service.create_human_assistance(
        **human_kwargs()
    ).data["ticket"]["ticket_id"]
    tickets.data[ticket_id]["admin_notes"] = [{
        "text": "Internal note",
        "timestamp": NOW.isoformat(),
        "actor": "admin-1",
    }]
    tickets.data[ticket_id]["status_history"] = [{
        "previous_status": "open",
        "new_status": "in_review",
        "timestamp": NOW.isoformat(),
        "actor": "admin-1",
    }]

    ticket = service.get_ticket_for_admin(ticket_id).data["ticket"]

    assert ticket["description"] == human_kwargs()["description"]
    assert ticket["customer_phone"] == human_kwargs()["customer_phone"]
    assert ticket["admin_notes"][0]["actor"] == "admin-1"
    assert ticket["status_history"][0]["actor"] == "admin-1"
    assert ticket["version"] == 1
    assert not {
        "PK",
        "SK",
        "GSI1PK",
        "GSI1SK",
        "GSI2PK",
        "GSI2SK",
        "session_id",
        "expires_at",
        "idempotency_key",
        "idempotency_hash",
    } & ticket.keys()
