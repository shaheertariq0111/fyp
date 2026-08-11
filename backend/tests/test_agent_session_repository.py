from copy import deepcopy

import pytest
from botocore.exceptions import ClientError

from src.repositories.agent_session_repository import (
    AgentSessionRepository,
    SessionNotFoundError,
    SupportStateConflictError,
)


class FakeTable:
    def __init__(self):
        self.scan_responses = []
        self.get_response = {}
        self.get_error = None
        self.get_calls = []
        self.update_calls = []
        self.update_error = None
        self.on_update_error = None

    def scan(self, **kwargs):
        return self.scan_responses.pop(0)

    def get_item(self, **kwargs):
        self.get_calls.append(deepcopy(kwargs))
        if self.get_error:
            raise self.get_error
        return deepcopy(self.get_response)

    def update_item(self, **kwargs):
        self.update_calls.append(deepcopy(kwargs))
        if self.update_error:
            if self.on_update_error:
                self.on_update_error()
            raise self.update_error


class FakeDynamo:
    def __init__(self):
        self.table = FakeTable()

    def Table(self, table_name):
        assert table_name == "agent-sessions"
        return self.table


def session_item(**overrides):
    item = {
        "PK": "CUSTOMER#cust-1",
        "SK": "SESSION#session-1",
        "agent_session_id": "session-1",
        "customer_id": "cust-1",
        "status": "active",
    }
    item.update(overrides)
    return item


def conditional_error(code="ConditionalCheckFailedException"):
    return ClientError(
        {"Error": {"Code": code, "Message": "failed"}},
        "UpdateItem",
    )


def repository_with_session(**overrides):
    dynamo = FakeDynamo()
    dynamo.table.get_response = {"Item": session_item(**overrides)}
    return AgentSessionRepository(dynamo, "agent-sessions"), dynamo.table


def test_get_support_state_returns_only_pending_attributes():
    repository, _ = repository_with_session(
        pending_support_intent="order_complaint",
        pending_order_id="ORD-1",
        pending_support_updated_at="2026-07-24T10:00:00+00:00",
    )

    state = repository.get_support_state("cust-1", "session-1")

    assert state == {
        "pending_support_intent": "order_complaint",
        "pending_order_id": "ORD-1",
        "pending_support_updated_at": "2026-07-24T10:00:00+00:00",
    }


def test_get_support_state_is_empty_when_session_has_no_pending_state():
    repository, _ = repository_with_session()

    assert repository.get_support_state("cust-1", "session-1") == {}


def test_support_state_lookup_uses_owner_bound_primary_key():
    repository, table = repository_with_session()

    repository.get_support_state("cust-1", "session-1")

    assert table.get_calls == [
        {
            "Key": {
                "PK": "CUSTOMER#cust-1",
                "SK": "SESSION#session-1",
            },
            "ConsistentRead": True,
        }
    ]


def test_owned_session_lookup_uses_consistent_exact_key():
    repository, table = repository_with_session()

    session = repository.get_owned("cust-1", "session-1")

    assert session["customer_id"] == "cust-1"
    assert table.get_calls == [{
        "Key": {
            "PK": "CUSTOMER#cust-1",
            "SK": "SESSION#session-1",
        },
        "ConsistentRead": True,
    }]


def test_owned_session_lookup_does_not_cross_customer_ownership():
    repository, _ = repository_with_session(
        customer_id="cust-2", PK="CUSTOMER#cust-2"
    )

    assert repository.get_owned("cust-1", "session-1") is None


@pytest.mark.parametrize(
    "item",
    [
        None,
        session_item(customer_id="cust-2", PK="CUSTOMER#cust-2"),
        session_item(agent_session_id="session-2", SK="SESSION#session-2"),
        session_item(SK="SESSION#duplicate-session-record"),
    ],
)
def test_missing_mismatched_or_ambiguous_session_is_not_disclosed(item):
    dynamo = FakeDynamo()
    if item is not None:
        dynamo.table.get_response = {"Item": item}
    repository = AgentSessionRepository(dynamo, "agent-sessions")

    with pytest.raises(SessionNotFoundError) as exc_info:
        repository.get_support_state("cust-1", "session-1")

    assert exc_info.value.args == ()


def test_update_support_state_uses_narrow_conditional_update():
    repository, table = repository_with_session()

    repository.update_support_state(
        "cust-1",
        "session-1",
        expected_updated_at=None,
        intent="order_complaint",
        order_id=None,
        description="  delayed\nagain  ",
        updated_at="2026-07-24T10:00:00+00:00",
    )

    call = table.update_calls[0]
    assert call["Key"] == {
        "PK": "CUSTOMER#cust-1",
        "SK": "SESSION#session-1",
    }
    assert call["UpdateExpression"].startswith("SET ")
    assert "REMOVE" in call["UpdateExpression"]
    assert "attribute_not_exists(#updated_at)" in call["ConditionExpression"]
    assert "#customer_id = :customer_id" in call["ConditionExpression"]
    assert "#agent_session_id = :agent_session_id" in call["ConditionExpression"]
    assert call["ExpressionAttributeValues"][":description"] == "  delayed\nagain  "
    assert set(call["ExpressionAttributeNames"].values()) <= {
            "PK",
            "customer_id",
            "agent_session_id",
            "pending_support_intent",
        "pending_order_id",
        "pending_complaint_description",
        "pending_support_updated_at",
    }


def test_update_support_state_requires_exact_previous_timestamp():
    repository, table = repository_with_session(
        pending_support_updated_at="2026-07-24T10:00:00+00:00"
    )

    repository.update_support_state(
        "cust-1",
        "session-1",
        expected_updated_at="2026-07-24T10:00:00+00:00",
        intent="order_complaint",
        order_id="ORD-1",
        description=None,
        updated_at="2026-07-24T10:01:00+00:00",
    )

    call = table.update_calls[0]
    assert "#updated_at = :expected_updated_at" in call["ConditionExpression"]
    assert (
        call["ExpressionAttributeValues"][":expected_updated_at"]
        == "2026-07-24T10:00:00+00:00"
    )


def test_clear_support_state_removes_all_four_fields_without_replacing_item():
    repository, table = repository_with_session(
        pending_support_updated_at="2026-07-24T10:00:00+00:00"
    )

    repository.clear_support_state(
        "cust-1",
        "session-1",
        expected_updated_at="2026-07-24T10:00:00+00:00",
    )

    call = table.update_calls[0]
    assert call["UpdateExpression"].startswith("REMOVE ")
    assert set(call["ExpressionAttributeNames"].values()) >= {
        "pending_support_intent",
        "pending_order_id",
        "pending_complaint_description",
        "pending_support_updated_at",
    }


def test_stale_support_state_update_has_deterministic_conflict():
    repository, table = repository_with_session()
    table.update_error = conditional_error()

    with pytest.raises(SupportStateConflictError):
        repository.update_support_state(
            "cust-1",
            "session-1",
            expected_updated_at=None,
            intent="order_complaint",
            order_id=None,
            description=None,
            updated_at="2026-07-24T10:00:00+00:00",
        )


def test_unrelated_dynamodb_update_error_propagates():
    repository, table = repository_with_session()
    error = conditional_error("ProvisionedThroughputExceededException")
    table.update_error = error

    with pytest.raises(ClientError) as exc_info:
        repository.clear_support_state("cust-1", "session-1")

    assert exc_info.value is error


def test_unrelated_dynamodb_lookup_error_propagates():
    dynamo = FakeDynamo()
    error = conditional_error("ProvisionedThroughputExceededException")
    dynamo.table.get_error = error
    repository = AgentSessionRepository(dynamo, "agent-sessions")

    with pytest.raises(ClientError) as exc_info:
        repository.get_support_state("cust-1", "session-1")

    assert exc_info.value is error


@pytest.mark.parametrize("operation", ["read", "update", "clear"])
def test_wrong_owner_cannot_access_or_mutate_support_state(operation):
    dynamo = FakeDynamo()
    repository = AgentSessionRepository(dynamo, "agent-sessions")

    with pytest.raises(SessionNotFoundError):
        if operation == "read":
            repository.get_support_state("cust-2", "session-1")
        elif operation == "update":
            repository.update_support_state(
                "cust-2",
                "session-1",
                expected_updated_at=None,
                intent="order_complaint",
                order_id=None,
                description=None,
                updated_at="2026-07-24T10:00:00+00:00",
            )
        else:
            repository.clear_support_state("cust-2", "session-1")

    assert dynamo.table.update_calls == []


def test_correct_owner_with_stale_timestamp_still_gets_conflict():
    repository, table = repository_with_session()
    table.update_error = conditional_error()

    with pytest.raises(SupportStateConflictError):
        repository.update_support_state(
            "cust-1",
            "session-1",
            expected_updated_at="2026-07-24T09:59:00+00:00",
            intent="order_complaint",
            order_id="ORD-1",
            description="late",
            updated_at="2026-07-24T10:00:00+00:00",
        )


def test_session_removed_between_lookup_and_update_is_not_found():
    repository, table = repository_with_session()
    table.update_error = conditional_error()
    table.on_update_error = lambda: setattr(table, "get_response", {})

    with pytest.raises(SessionNotFoundError):
        repository.update_support_state(
            "cust-1",
            "session-1",
            expected_updated_at=None,
            intent="order_complaint",
            order_id=None,
            description=None,
            updated_at="2026-07-24T10:00:00+00:00",
        )


def test_owner_bound_update_changes_only_pending_support_fields():
    repository, table = repository_with_session(unrelated="preserved")

    repository.update_support_state(
        "cust-1",
        "session-1",
        expected_updated_at=None,
        intent="order_complaint",
        order_id="ORD-1",
        description="late",
        updated_at="2026-07-24T10:00:00+00:00",
    )

    call = table.update_calls[0]
    assert set(call["ExpressionAttributeNames"].values()) == {
        "PK",
        "customer_id",
        "agent_session_id",
        "pending_support_intent",
        "pending_order_id",
        "pending_complaint_description",
        "pending_support_updated_at",
    }


def test_verified_order_context_uses_separate_owner_bound_fields():
    repository, table = repository_with_session(
        verified_order_id="ORD-1",
        verified_order_status="delivered",
        verified_order_at="2026-07-24T10:00:00+00:00",
    )

    assert repository.get_verified_order_context("cust-1", "session-1") == {
        "verified_order_id": "ORD-1",
        "verified_order_status": "delivered",
        "verified_order_at": "2026-07-24T10:00:00+00:00",
    }

    repository.update_verified_order_context(
        "cust-1",
        "session-1",
        order_id="ORD-2",
        status="confirmed",
        verified_at="2026-07-24T10:05:00+00:00",
    )

    call = table.update_calls[0]
    assert set(call["ExpressionAttributeNames"].values()) == {
        "PK",
        "customer_id",
        "agent_session_id",
        "verified_order_id",
        "verified_order_status",
        "verified_order_at",
    }
    assert call["ExpressionAttributeValues"][":order_id"] == "ORD-2"
    assert call["ExpressionAttributeValues"][":status"] == "confirmed"
    assert call["ExpressionAttributeValues"][":verified_at"] == (
        "2026-07-24T10:05:00+00:00"
    )
    assert "#customer_id = :customer_id" in call["ConditionExpression"]
    assert "#agent_session_id = :agent_session_id" in call["ConditionExpression"]


def test_verified_order_cleanup_is_compare_and_set():
    repository, table = repository_with_session(
        verified_order_id="ORD-1",
        verified_order_status="delivered",
        verified_order_at="2026-07-24T10:00:00+00:00",
    )

    repository.clear_verified_order_context(
        "cust-1",
        "session-1",
        expected_verified_at="2026-07-24T10:00:00+00:00",
    )

    call = table.update_calls[0]
    assert call["UpdateExpression"] == (
        "REMOVE #order_id, #status, #verified_at"
    )
    assert "#verified_at = :expected_verified_at" in call["ConditionExpression"]
    assert call["ExpressionAttributeValues"][":expected_verified_at"] == (
        "2026-07-24T10:00:00+00:00"
    )
