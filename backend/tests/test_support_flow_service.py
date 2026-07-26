from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from fakes import MemoryAgentSessionRepository, MemoryOrderRepository
from src.models.tool_responses import ToolResponse
from src.repositories.agent_session_repository import SupportStateConflictError
from src.services.agent_session_service import AgentSessionService
from src.services.support_flow_service import SupportFlowService


NOW = datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)


class StubCustomers:
    pass


class StubSettings:
    agent_session_ttl_hours = 24


class StubTicketService:
    def __init__(self, response=None, error=None):
        self.response = response or ToolResponse.ok(
            data={"ticket": {"ticket_id": "TKT-20260724-A1B2C3"}},
            user_message="Your complaint has been recorded.",
        )
        self.error = error
        self.calls = []

    def create_order_complaint(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        if self.error:
            raise self.error
        return self.response


def make_services(*, now=NOW, ticket_response=None, ticket_error=None):
    sessions_repo = MemoryAgentSessionRepository()
    sessions_repo.data["session-1"] = {
        "PK": "CUSTOMER#user-1",
        "SK": "SESSION#session-1",
        "agent_session_id": "session-1",
        "customer_id": "user-1",
        "status": "active",
        "unrelated": "preserved",
    }
    sessions = AgentSessionService(
        sessions_repo,
        StubCustomers(),
        StubSettings(),
        clock=lambda: now,
    )
    orders = MemoryOrderRepository()
    tickets = StubTicketService(ticket_response, ticket_error)
    flow = SupportFlowService(sessions, tickets, orders)
    return flow, sessions, sessions_repo, orders, tickets


def put_order(orders, order_id="ORD-1", user_id="user-1"):
    orders.data[order_id] = {
        "order_id": order_id,
        "user_id": user_id,
        "status": "confirmed",
    }


def state_at(sessions_repo, *, age, order_id="ORD-1", description="late"):
    sessions_repo.data["session-1"].update(
        {
            "pending_support_intent": "order_complaint",
            "pending_order_id": order_id,
            "pending_complaint_description": description,
            "pending_support_updated_at": (NOW - age).isoformat(),
        }
    )


def verified_order_at(
    sessions_repo,
    *,
    age,
    order_id="ORD-1",
    status="delivered",
):
    sessions_repo.data["session-1"].update(
        {
            "verified_order_id": order_id,
            "verified_order_status": status,
            "verified_order_at": (NOW - age).isoformat(),
        }
    )


@pytest.mark.parametrize(
    ("kwargs", "error_code"),
    [
        ({"user_id": "", "agent_session_id": "session-1", "request_id": "req-1"}, "USER_ID_REQUIRED"),
        ({"user_id": "user-1", "agent_session_id": "", "request_id": "req-1"}, "SESSION_ID_REQUIRED"),
        ({"user_id": "user-1", "agent_session_id": "session-1", "request_id": ""}, "REQUEST_ID_REQUIRED"),
        (
            {
                "user_id": "user-1",
                "agent_session_id": "session-1",
                "request_id": "req-1",
                "action": "pause",
            },
            "INVALID_SUPPORT_ACTION",
        ),
    ],
)
def test_trusted_fields_and_action_are_required(kwargs, error_code):
    flow, *_ = make_services()

    response = flow.handle_order_complaint(**kwargs)

    assert response.success is False
    assert response.error_code == error_code


def test_initial_complaint_asks_for_order_id_and_persists_intent():
    flow, _, repo, _, _ = make_services()

    response = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
    )

    assert response.success is True
    assert response.next_action == "request_order_id"
    assert response.agent["required_input"] == "order_id"
    assert response.user_message == (
        "Please provide the Order ID for the order you are complaining about."
    )
    assert repo.data["session-1"]["pending_support_intent"] == "order_complaint"
    assert (
        repo.data["session-1"]["pending_support_updated_at"]
        == "2026-07-24T10:00:00+00:00"
    )
    assert repo.data["session-1"]["unrelated"] == "preserved"


def test_description_only_preserves_original_text_and_asks_for_order_id():
    flow, _, repo, _, _ = make_services()
    description = "  Food was cold.\nPlease investigate.  "

    response = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
        description=description,
    )

    assert response.next_action == "request_order_id"
    assert repo.data["session-1"]["pending_complaint_description"] == description


def test_valid_order_only_asks_for_description():
    flow, _, repo, orders, _ = make_services()
    put_order(orders)

    response = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
        order_id="ORD-1",
    )

    assert response.next_action == "request_complaint_description"
    assert response.agent["required_input"] == "complaint_description"
    assert response.agent["order_id"] == "ORD-1"
    assert response.user_message == "Please describe what went wrong with your order."
    assert repo.data["session-1"]["pending_order_id"] == "ORD-1"


def test_recent_verified_order_is_revalidated_and_used_for_complaint():
    flow, _, repo, orders, tickets = make_services()
    put_order(orders)
    orders.data["ORD-1"]["status"] = "delivered"
    verified_order_at(repo, age=timedelta(minutes=1))

    response = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-verified",
        description="The order was missing sauce.",
    )

    assert response.success
    assert tickets.calls[0]["order_id"] == "ORD-1"
    assert tickets.calls[0]["description"] == "The order was missing sauce."


def test_expired_verified_order_is_not_used_and_description_is_preserved():
    flow, _, repo, orders, tickets = make_services()
    put_order(orders)
    verified_order_at(repo, age=timedelta(minutes=30))

    response = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-expired",
        description="  Missing dip.\nPlease investigate.  ",
    )

    assert response.next_action == "request_order_id"
    assert tickets.calls == []
    assert repo.data["session-1"]["pending_complaint_description"] == (
        "  Missing dip.\nPlease investigate.  "
    )


def test_verified_order_ownership_mismatch_clears_exact_context(
    monkeypatch,
    caplog,
):
    flow, sessions, repo, orders, tickets = make_services()
    put_order(orders, user_id="user-2")
    verified_order_at(repo, age=timedelta(minutes=1))
    expected_verified_at = repo.data["session-1"]["verified_order_at"]
    cleanup_calls = []

    def clear_verified_context(
        customer_id,
        agent_session_id,
        *,
        expected_verified_at,
    ):
        cleanup_calls.append(
            (customer_id, agent_session_id, expected_verified_at)
        )
        repo.clear_verified_order_context(
            customer_id,
            agent_session_id,
            expected_verified_at=expected_verified_at,
        )

    monkeypatch.setattr(
        sessions,
        "clear_verified_order_context",
        clear_verified_context,
        raising=False,
    )

    with caplog.at_level("INFO"):
        response = flow.handle_order_complaint(
            user_id="user-1",
            agent_session_id="session-1",
            request_id="req-mismatch",
            description="Wrong item received.",
        )

    assert response.next_action == "request_order_id"
    assert response.agent["required_input"] == "order_id"
    assert tickets.calls == []
    assert cleanup_calls == [
        ("user-1", "session-1", expected_verified_at)
    ]
    assert "verified_order_id" not in repo.data["session-1"]
    assert repo.data["session-1"]["pending_complaint_description"] == (
        "Wrong item received."
    )
    rejection = next(
        record
        for record in caplog.records
        if getattr(record, "event", None)
        == "complaint_order_context_rejected"
    )
    assert rejection.order_context_source == "verified_order_context"
    assert rejection.order_context_rejection_reason == "ownership_mismatch"
    assert rejection.verified_context_cleanup_outcome == "succeeded"
    assert "Wrong item received." not in caplog.text


def test_verified_order_cleanup_conflict_preserves_concurrent_replacement(
    monkeypatch,
    caplog,
):
    flow, sessions, repo, orders, tickets = make_services()
    put_order(orders, user_id="user-2")
    verified_order_at(repo, age=timedelta(minutes=1))
    expected_verified_at = repo.data["session-1"]["verified_order_at"]
    newer_verified_at = (NOW + timedelta(seconds=1)).isoformat()
    cleanup_calls = []

    def conflict_on_cleanup(
        customer_id,
        agent_session_id,
        *,
        expected_verified_at,
    ):
        cleanup_calls.append(
            (customer_id, agent_session_id, expected_verified_at)
        )
        repo.data["session-1"].update(
            {
                "verified_order_id": "ORD-NEW",
                "verified_order_status": "preparing",
                "verified_order_at": newer_verified_at,
            }
        )
        raise SupportStateConflictError

    monkeypatch.setattr(
        sessions,
        "clear_verified_order_context",
        conflict_on_cleanup,
        raising=False,
    )

    with caplog.at_level("INFO"):
        response = flow.handle_order_complaint(
            user_id="user-1",
            agent_session_id="session-1",
            request_id="req-conflict",
            description="The order was missing sauce.",
        )

    assert response.next_action == "request_order_id"
    assert tickets.calls == []
    assert cleanup_calls == [
        ("user-1", "session-1", expected_verified_at)
    ]
    assert {
        key: repo.data["session-1"][key]
        for key in repo.VERIFIED_ORDER_FIELDS
    } == {
        "verified_order_id": "ORD-NEW",
        "verified_order_status": "preparing",
        "verified_order_at": newer_verified_at,
    }
    assert repo.data["session-1"]["pending_complaint_description"] == (
        "The order was missing sauce."
    )
    rejection = next(
        record
        for record in caplog.records
        if getattr(record, "event", None)
        == "complaint_order_context_rejected"
    )
    assert rejection.verified_context_cleanup_outcome == (
        "compare_and_set_conflict"
    )
    assert "The order was missing sauce." not in caplog.text


def test_invalid_pending_order_does_not_clear_verified_context(
    monkeypatch,
    caplog,
):
    flow, sessions, repo, orders, tickets = make_services()
    state_at(
        repo,
        age=timedelta(minutes=1),
        order_id="ORD-PENDING",
        description="Missing sauce.",
    )
    verified_order_at(
        repo,
        age=timedelta(minutes=1),
        order_id="ORD-VERIFIED",
    )
    verified_before = {
        key: repo.data["session-1"][key]
        for key in repo.VERIFIED_ORDER_FIELDS
    }
    cleanup_calls = []
    monkeypatch.setattr(
        sessions,
        "clear_verified_order_context",
        lambda *args, **kwargs: cleanup_calls.append((args, kwargs)),
        raising=False,
    )

    with caplog.at_level("INFO"):
        response = flow.handle_order_complaint(
            user_id="user-1",
            agent_session_id="session-1",
            request_id="req-pending",
        )

    assert response.next_action == "request_order_id"
    assert tickets.calls == []
    assert cleanup_calls == []
    assert {
        key: repo.data["session-1"][key]
        for key in repo.VERIFIED_ORDER_FIELDS
    } == verified_before
    assert repo.data["session-1"]["pending_complaint_description"] == (
        "Missing sauce."
    )
    rejection = next(
        record
        for record in caplog.records
        if getattr(record, "event", None)
        == "complaint_order_context_rejected"
    )
    assert rejection.order_context_source == "pending_support"
    assert rejection.verified_context_cleanup_outcome == "not_attempted"


@pytest.mark.parametrize(
    ("age", "active"),
    [
        (timedelta(minutes=29, seconds=59, milliseconds=999), True),
        (timedelta(minutes=30), False),
    ],
)
def test_verified_order_expiry_boundary(age, active):
    _, sessions, repo, _, _ = make_services()
    verified_order_at(repo, age=age)

    context = sessions.get_active_verified_order_context(
        "user-1",
        "session-1",
    )

    assert bool(context) is active
    assert ("verified_order_id" in repo.data["session-1"]) is active


@pytest.mark.parametrize(
    ("offset", "active"),
    [
        (timedelta(seconds=5), True),
        (timedelta(seconds=5, microseconds=1), False),
    ],
)
def test_verified_order_future_clock_skew_boundary(offset, active):
    _, sessions, repo, _, _ = make_services()
    verified_order_at(repo, age=-offset)

    context = sessions.get_active_verified_order_context(
        "user-1",
        "session-1",
    )

    assert bool(context) is active
    assert ("verified_order_id" in repo.data["session-1"]) is active


def test_malformed_verified_order_context_is_cleared():
    _, sessions, repo, _, _ = make_services()
    verified_order_at(repo, age=timedelta(minutes=1))
    repo.data["session-1"]["verified_order_at"] = "not-a-timestamp"

    assert sessions.get_active_verified_order_context(
        "user-1",
        "session-1",
    ) == {}
    assert "verified_order_id" not in repo.data["session-1"]


@pytest.mark.parametrize("owner", [None, "user-2"])
def test_missing_and_unauthorized_orders_are_identical_and_not_persisted(owner):
    flow, _, repo, orders, _ = make_services()
    if owner:
        put_order(orders, user_id=owner)

    response = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
        order_id="ORD-1",
    )

    assert response.success is False
    assert response.error_code == "ORDER_NOT_FOUND"
    assert response.user_message == "I couldn't find that order."
    assert "pending_order_id" not in repo.data["session-1"]


def test_validation_failure_does_not_destroy_previously_valid_state():
    flow, _, repo, orders, _ = make_services()
    put_order(orders)
    state_at(repo, age=timedelta(minutes=1))

    response = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
        order_id="ORD-missing",
    )

    assert response.error_code == "ORDER_NOT_FOUND"
    assert repo.data["session-1"]["pending_order_id"] == "ORD-1"
    assert repo.data["session-1"]["pending_complaint_description"] == "late"


@pytest.mark.parametrize(
    ("age", "active"),
    [
        (timedelta(minutes=29, seconds=59, milliseconds=999), True),
        (timedelta(minutes=30), False),
        (timedelta(minutes=30, microseconds=1), False),
    ],
)
def test_pending_state_expiry_boundary(age, active):
    _, sessions, repo, _, _ = make_services()
    state_at(repo, age=age)

    state = sessions.get_active_support_state("user-1", "session-1")

    assert bool(state) is active
    assert ("pending_support_intent" in repo.data["session-1"]) is active


def test_malformed_pending_state_is_cleared():
    _, sessions, repo, _, _ = make_services()
    repo.data["session-1"].update(
        {
            "pending_support_intent": "other",
            "pending_order_id": "ORD-1",
            "pending_support_updated_at": "not-a-time",
        }
    )

    assert sessions.get_active_support_state("user-1", "session-1") == {}
    assert "pending_support_intent" not in repo.data["session-1"]
    assert repo.data["session-1"]["unrelated"] == "preserved"


def test_fresh_input_after_expiry_does_not_merge_stale_values():
    flow, _, repo, _, _ = make_services()
    state_at(repo, age=timedelta(minutes=30), order_id="ORD-old", description="old")

    response = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
        description="new",
    )

    assert response.next_action == "request_order_id"
    assert "pending_order_id" not in repo.data["session-1"]
    assert repo.data["session-1"]["pending_complaint_description"] == "new"


@pytest.mark.parametrize("with_state", [False, True])
def test_cancel_succeeds_with_or_without_state_and_never_creates_ticket(with_state):
    flow, _, repo, _, tickets = make_services()
    if with_state:
        state_at(repo, age=timedelta(minutes=1))

    response = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
        action="cancel",
    )

    assert response.success is True
    assert response.next_action == "support_cancelled"
    assert response.agent == {
        "entity": "pending_support",
        "pending_support_intent": None,
    }
    assert response.user_message == "Your complaint request has been cancelled."
    assert tickets.calls == []
    assert "pending_support_intent" not in repo.data["session-1"]


def test_two_turn_creation_passes_trusted_values_and_clears_state():
    flow, _, repo, orders, tickets = make_services()
    put_order(orders)
    first_description = "  Missing item\nand cold fries.  "

    flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
        description=first_description,
    )
    response = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-2",
        order_id="ORD-1",
        customer_id="cust-1",
        customer_name="Ava",
        customer_phone="+1000000",
        source="web",
    )

    assert response.user_message == "Your complaint has been recorded."
    assert tickets.calls[0] == {
        "user_id": "user-1",
        "order_id": "ORD-1",
        "description": first_description,
        "session_id": "session-1",
        "customer_id": "cust-1",
        "customer_name": "Ava",
        "customer_phone": "+1000000",
        "source": "web",
        "idempotency_key": "req-2",
    }
    assert not any(key.startswith("pending_") for key in repo.data["session-1"])


def test_three_turn_and_single_turn_creation():
    flow, _, _, orders, tickets = make_services()
    put_order(orders)

    flow.handle_order_complaint(
        user_id="user-1", agent_session_id="session-1", request_id="req-1"
    )
    flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-2",
        order_id="ORD-1",
    )
    result = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-3",
        description="late",
    )
    assert result.success
    assert len(tickets.calls) == 1

    flow2, _, _, orders2, tickets2 = make_services()
    put_order(orders2)
    result2 = flow2.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-single",
        order_id="ORD-1",
        description="late",
    )
    assert result2.success
    assert len(tickets2.calls) == 1


def test_later_valid_values_replace_prior_values_and_blank_does_not_erase():
    flow, _, repo, orders, tickets = make_services()
    put_order(orders, "ORD-1")
    put_order(orders, "ORD-2")
    state_at(repo, age=timedelta(minutes=1), order_id="ORD-1", description="old")

    flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
        order_id="ORD-2",
        description="new",
    )

    assert tickets.calls[0]["order_id"] == "ORD-2"
    assert tickets.calls[0]["description"] == "new"
    assert repo.data["session-1"].get("pending_order_id") is None
    assert repo.data["session-1"].get("pending_complaint_description") is None

    error_response = ToolResponse.error(
        error_code="DESCRIPTION_TOO_LONG",
        user_message="The description is too long.",
    )
    flow2, _, repo2, orders2, _ = make_services(ticket_response=error_response)
    put_order(orders2, "ORD-1")
    state_at(repo2, age=timedelta(minutes=1), description="old")
    flow2.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-2",
        description="   ",
    )
    assert repo2.data["session-1"]["pending_complaint_description"] == "old"


@pytest.mark.parametrize("idempotent", [False, True])
def test_successful_or_idempotent_creation_clears_state(idempotent):
    response = ToolResponse.ok(
        data={"ticket": {"ticket_id": "TKT-20260724-A1B2C3"}},
        user_message="Existing complaint found." if idempotent else "Complaint created.",
    )
    flow, _, repo, orders, _ = make_services(ticket_response=response)
    put_order(orders)

    result = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
        order_id="ORD-1",
        description="late",
    )

    assert result.user_message == response.user_message
    assert not any(key.startswith("pending_") for key in repo.data["session-1"])


def test_ticket_validation_error_and_infrastructure_failure_preserve_state():
    validation = ToolResponse.error(
        error_code="DESCRIPTION_TOO_LONG",
        user_message="The description is too long.",
    )
    flow, _, repo, orders, _ = make_services(ticket_response=validation)
    put_order(orders)

    response = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
        order_id="ORD-1",
        description="late",
    )

    assert response.error_code == "DESCRIPTION_TOO_LONG"
    assert repo.data["session-1"]["pending_order_id"] == "ORD-1"

    failure = RuntimeError("dynamodb unavailable")
    flow2, _, repo2, orders2, _ = make_services(ticket_error=failure)
    put_order(orders2)
    with pytest.raises(RuntimeError, match="dynamodb unavailable"):
        flow2.handle_order_complaint(
            user_id="user-1",
            agent_session_id="session-1",
            request_id="req-1",
            order_id="ORD-1",
            description="late",
        )
    assert repo2.data["session-1"]["pending_order_id"] == "ORD-1"


def test_compare_and_set_conflict_retries_once_without_discarding_newer_state():
    flow, _, repo, orders, tickets = make_services()
    put_order(orders)
    repo.conflicts_remaining = 1
    repo.on_conflict = lambda: repo.data["session-1"].update(
        {
            "pending_support_intent": "order_complaint",
            "pending_order_id": "ORD-1",
            "pending_support_updated_at": (
                NOW + timedelta(seconds=1)
            ).isoformat(),
        }
    )

    response = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
        description="new description",
    )

    assert response.success
    assert tickets.calls[0]["order_id"] == "ORD-1"


def test_second_stale_write_returns_support_state_conflict():
    flow, _, repo, _, _ = make_services()
    repo.conflicts_remaining = 2

    response = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
        description="new description",
    )

    assert response.success is False
    assert response.error_code == "SUPPORT_STATE_CONFLICT"


def test_concurrent_clear_does_not_remove_newer_state():
    _, sessions, repo, _, _ = make_services()
    state_at(repo, age=timedelta(minutes=1))
    old_timestamp = repo.data["session-1"]["pending_support_updated_at"]
    repo.data["session-1"]["pending_support_updated_at"] = (
        NOW + timedelta(seconds=1)
    ).isoformat()

    with pytest.raises(SupportStateConflictError):
        sessions.clear_support_state(
            "user-1",
            "session-1",
            expected_updated_at=old_timestamp,
        )

    assert repo.data["session-1"]["pending_order_id"] == "ORD-1"


@pytest.mark.parametrize(
    ("offset", "active"),
    [
        (timedelta(0), True),
        (timedelta(seconds=5), True),
        (timedelta(seconds=5, microseconds=1), False),
    ],
)
def test_future_support_timestamp_skew_boundary(offset, active):
    _, sessions, repo, _, _ = make_services()
    state_at(repo, age=-offset)

    state = sessions.get_active_support_state("user-1", "session-1")

    assert bool(state) is active
    assert ("pending_support_intent" in repo.data["session-1"]) is active


def test_future_state_cleanup_cannot_clear_newer_state():
    _, sessions, repo, _, _ = make_services()
    state_at(repo, age=-timedelta(seconds=6))
    repo.conflicts_remaining = 1
    repo.on_conflict = lambda: repo.data["session-1"].update(
        {
            "pending_order_id": "ORD-2",
            "pending_complaint_description": "new",
            "pending_support_updated_at": NOW.isoformat(),
        }
    )

    state = sessions.get_active_support_state("user-1", "session-1")

    assert state["pending_order_id"] == "ORD-2"
    assert repo.data["session-1"]["pending_complaint_description"] == "new"


@pytest.mark.parametrize("owner", ["missing-user", "user-2"])
def test_missing_and_wrong_owner_sessions_return_identical_error(owner):
    flow, _, repo, _, _ = make_services()
    before = deepcopy(repo.data["session-1"])

    response = flow.handle_order_complaint(
        user_id=owner,
        agent_session_id="session-1",
        request_id="req-owner",
    )

    assert response.success is False
    assert response.error_code == "SESSION_NOT_FOUND"
    assert response.user_message == "The session could not be found."
    assert response.data == {}
    assert repo.data["session-1"] == before


def test_wrong_owner_never_reaches_compare_and_set_mutation():
    flow, _, repo, _, _ = make_services()
    repo.conflicts_remaining = 1

    response = flow.handle_order_complaint(
        user_id="user-2",
        agent_session_id="session-1",
        request_id="req-owner",
        description="must not persist",
    )

    assert response.error_code == "SESSION_NOT_FOUND"
    assert repo.conflicts_remaining == 1
    assert "pending_support_intent" not in repo.data["session-1"]


def test_wrong_owner_with_unknown_order_still_returns_session_not_found():
    flow, _, repo, _, _ = make_services()

    response = flow.handle_order_complaint(
        user_id="user-2",
        agent_session_id="session-1",
        request_id="req-owner",
        order_id="ORD-missing",
    )

    assert response.error_code == "SESSION_NOT_FOUND"
    assert "pending_support_intent" not in repo.data["session-1"]


def test_success_clear_conflict_preserves_newer_different_complaint():
    response = ToolResponse.ok(
        data={"ticket": {"ticket_id": "TKT-20260724-A1B2C3"}},
        user_message="Authoritative ticket response.",
    )
    flow, _, repo, orders, tickets = make_services(ticket_response=response)
    put_order(orders, "ORD-1")
    put_order(orders, "ORD-2")
    repo.clear_conflicts_remaining = 1
    repo.on_clear_conflict = lambda: repo.data["session-1"].update(
        {
            "pending_order_id": "ORD-2",
            "pending_complaint_description": "new complaint",
            "pending_support_updated_at": (
                NOW + timedelta(seconds=1)
            ).isoformat(),
        }
    )

    result = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-clear",
        order_id="ORD-1",
        description="submitted complaint",
    )

    assert result is response
    assert result.user_message == "Authoritative ticket response."
    assert len(tickets.calls) == 1
    assert repo.data["session-1"]["pending_order_id"] == "ORD-2"
    assert repo.data["session-1"]["pending_complaint_description"] == "new complaint"


def test_success_clear_conflict_retries_once_for_equivalent_newer_state():
    flow, _, repo, orders, tickets = make_services()
    put_order(orders)
    repo.clear_conflicts_remaining = 1
    repo.on_clear_conflict = lambda: repo.data["session-1"].update(
        {
            "pending_order_id": "ORD-1",
            "pending_complaint_description": "late",
            "pending_support_updated_at": (
                NOW + timedelta(seconds=1)
            ).isoformat(),
        }
    )

    result = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-clear",
        order_id="ORD-1",
        description="late",
    )

    assert result.success
    assert len(tickets.calls) == 1
    assert repo.clear_calls == 2
    assert not any(key.startswith("pending_") for key in repo.data["session-1"])


def test_second_clear_conflict_after_success_is_swallowed_and_retry_is_idempotent():
    authoritative = ToolResponse.ok(
        data={"ticket": {"ticket_id": "TKT-20260724-A1B2C3"}},
        user_message="Existing complaint found.",
    )
    flow, _, repo, orders, tickets = make_services(ticket_response=authoritative)
    put_order(orders)
    repo.clear_conflicts_remaining = 2
    repo.on_clear_conflict = lambda: repo.data["session-1"].update(
        {
            "pending_order_id": "ORD-1",
            "pending_complaint_description": "late",
            "pending_support_updated_at": (
                NOW + timedelta(seconds=1)
            ).isoformat(),
        }
    )

    first = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-idempotent",
        order_id="ORD-1",
        description="late",
    )

    assert first is authoritative
    assert first.user_message == "Existing complaint found."
    assert len(tickets.calls) == 1
    assert repo.data["session-1"]["pending_order_id"] == "ORD-1"

    second = flow.handle_order_complaint(
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-idempotent",
        order_id="ORD-1",
        description="late",
    )

    assert second is authoritative
    assert [call["idempotency_key"] for call in tickets.calls] == [
        "req-idempotent",
        "req-idempotent",
    ]
