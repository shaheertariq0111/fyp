from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from boto3.dynamodb.types import TypeDeserializer
from boto3.dynamodb.conditions import ConditionExpressionBuilder
from botocore.exceptions import ClientError

from src.repositories.ticket_repository import (
    HumanSessionGuardConflictError,
    IdempotencyConflictError,
    ReusableTicketChangedError,
    TicketIdCollisionError,
    TicketRepository,
    TicketPaginationStalledError,
    TicketVersionConflictError,
    human_session_guard_key,
)
from src.models.ticket import (
    INVALID_TICKET_ID,
    INVALID_TIMESTAMP,
    MAX_TICKET_ITEM_BYTES,
    TICKET_ITEM_TOO_LARGE,
    TicketDomainValidationError,
    ticket_item_size_bytes,
)
from fakes import MemoryTicketRepository


DESERIALIZER = TypeDeserializer()
HASH_VALUE = "a" * 64


def deserialize_item(item):
    return {key: DESERIALIZER.deserialize(value) for key, value in item.items()}


def expression_values(expression):
    built = ConditionExpressionBuilder().build_expression(expression)
    return set(built.attribute_value_placeholders.values())


class FakeClient:
    def __init__(self):
        self.transactions = []
        self.transaction_error = None

    def transact_write_items(self, **kwargs):
        self.transactions.append(deepcopy(kwargs))
        if self.transaction_error:
            raise self.transaction_error


class FakeTable:
    def __init__(self):
        self.get_responses = []
        self.query_responses = []
        self.put_calls = []

    def get_item(self, **kwargs):
        self.last_get = kwargs
        return self.get_responses.pop(0)

    def query(self, **kwargs):
        self.last_query = kwargs
        return self.query_responses.pop(0)

    def put_item(self, **kwargs):
        self.put_calls.append(deepcopy(kwargs))


class FakeDynamo:
    def __init__(self):
        self.table = FakeTable()
        self.meta = type("Meta", (), {"client": FakeClient()})()

    def Table(self, table_name):
        assert table_name == "tickets"
        return self.table


def sample_ticket(**overrides):
    ticket = {
        "ticket_id": "TKT-20260724-A1B2C3",
        "user_id": "user-1",
        "session_id": "session-1",
        "ticket_type": "human_assistance",
        "category": "human_assistance",
        "description": None,
        "priority": "normal",
        "status": "open",
        "source": "web",
        "created_at": "2026-07-24T10:00:00+00:00",
        "updated_at": "2026-07-24T10:00:00+00:00",
        "status_history": [],
        "admin_notes": [],
        "version": 1,
        "PK": "TICKET#TKT-20260724-A1B2C3",
        "SK": "METADATA",
        "GSI1PK": "CUSTOMER#user-1",
        "GSI1SK": "CREATED#2026-07-24T10:00:00+00:00#TKT-20260724-A1B2C3",
        "GSI2PK": "STATUS#open",
        "GSI2SK": "UPDATED#2026-07-24T10:00:00+00:00#TKT-20260724-A1B2C3",
    }
    ticket.update(overrides)
    if "ticket_id" in overrides:
        ticket_id = overrides["ticket_id"]
        if "PK" not in overrides:
            ticket["PK"] = f"TICKET#{ticket_id}"
        if "GSI1SK" not in overrides:
            ticket["GSI1SK"] = (
                f"CREATED#{ticket['created_at']}#{ticket_id}"
            )
        if "GSI2SK" not in overrides:
            ticket["GSI2SK"] = (
                f"UPDATED#{ticket['updated_at']}#{ticket_id}"
            )
    if "user_id" in overrides and "GSI1PK" not in overrides:
        ticket["GSI1PK"] = f"CUSTOMER#{overrides['user_id']}"
    if "status" in overrides and "GSI2PK" not in overrides:
        ticket["GSI2PK"] = f"STATUS#{overrides['status']}"
    if "created_at" in overrides and "GSI1SK" not in overrides:
        ticket["GSI1SK"] = (
            f"CREATED#{overrides['created_at']}#{ticket['ticket_id']}"
        )
    if "updated_at" in overrides and "GSI2SK" not in overrides:
        ticket["GSI2SK"] = (
            f"UPDATED#{overrides['updated_at']}#{ticket['ticket_id']}"
        )
    return ticket


def sample_marker():
    return {
        "PK": f"IDEMPOTENCY#{HASH_VALUE}",
        "SK": "METADATA",
        "ticket_id": "TKT-20260724-A1B2C3",
        "user_id": "user-1",
        "operation": "human_assistance",
        "expires_at": 1784973600,
    }


def sample_guard(**overrides):
    guard = {
        "PK": human_session_guard_key("user-1", "session-1"),
        "SK": "METADATA",
        "user_id": "user-1",
        "session_id": "session-1",
        "operation": "human_assistance",
        "active_ticket_id": "TKT-20260724-A1B2C3",
        "updated_at": "2026-07-24T10:00:00+00:00",
        "version": 1,
    }
    guard.update(overrides)
    return guard


def transaction_error(reasons):
    return ClientError(
        {
            "Error": {
                "Code": "TransactionCanceledException",
                "Message": "cancelled",
            },
            "CancellationReasons": reasons,
        },
        "TransactWriteItems",
    )


def test_create_ticket_and_marker_uses_atomic_conditional_transaction():
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")

    repository.create_with_idempotency(
        sample_ticket(),
        sample_marker(),
        now_epoch=1784887200,
    )

    transaction = dynamo.meta.client.transactions[0]
    assert len(transaction["TransactItems"]) == 2
    ticket_put = transaction["TransactItems"][0]["Put"]
    marker_put = transaction["TransactItems"][1]["Put"]
    assert ticket_put["TableName"] == "tickets"
    assert ticket_put["ConditionExpression"] == "attribute_not_exists(PK)"
    assert marker_put["ConditionExpression"] == (
        "attribute_not_exists(PK) OR expires_at <= :now"
    )
    expected_ticket = sample_ticket()
    expected_ticket.pop("description")
    assert deserialize_item(ticket_put["Item"]) == expected_ticket
    assert deserialize_item(marker_put["Item"]) == sample_marker()
    assert "ReturnConsumedCapacity" not in transaction


def test_ticket_collision_is_classified_for_safe_retry():
    dynamo = FakeDynamo()
    dynamo.meta.client.transaction_error = transaction_error(
        [{"Code": "ConditionalCheckFailed"}, {"Code": "None"}]
    )
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(TicketIdCollisionError):
        repository.create_with_idempotency(
            sample_ticket(), sample_marker(), now_epoch=1784887200
        )


def test_idempotency_collision_is_classified():
    dynamo = FakeDynamo()
    dynamo.meta.client.transaction_error = transaction_error(
        [{"Code": "None"}, {"Code": "ConditionalCheckFailed"}]
    )
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(IdempotencyConflictError):
        repository.create_with_idempotency(
            sample_ticket(), sample_marker(), now_epoch=1784887200
        )


def test_unrelated_transaction_cancellation_propagates():
    dynamo = FakeDynamo()
    error = transaction_error([{"Code": "TransactionConflict"}, {"Code": "None"}])
    dynamo.meta.client.transaction_error = error
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(ClientError) as exc_info:
        repository.create_with_idempotency(
            sample_ticket(), sample_marker(), now_epoch=1784887200
        )

    assert exc_info.value is error


def test_transaction_cancellation_without_reasons_propagates_without_guessing():
    dynamo = FakeDynamo()
    error = ClientError(
        {
            "Error": {
                "Code": "TransactionCanceledException",
                "Message": "cancelled",
            }
        },
        "TransactWriteItems",
    )
    dynamo.meta.client.transaction_error = error
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(ClientError) as exc_info:
        repository.create_with_idempotency(
            sample_ticket(), sample_marker(), now_epoch=1784887200
        )

    assert exc_info.value is error
    assert len(dynamo.meta.client.transactions) == 1


def test_human_creation_atomically_writes_ticket_marker_and_guard():
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")

    repository.create_with_idempotency(
        sample_ticket(),
        sample_marker(),
        now_epoch=1784887200,
        guard=sample_guard(),
    )

    items = dynamo.meta.client.transactions[0]["TransactItems"]
    assert len(items) == 3
    guard_put = items[2]["Put"]
    assert deserialize_item(guard_put["Item"]) == sample_guard()
    assert guard_put["ConditionExpression"] == "attribute_not_exists(PK)"


def test_stale_guard_replacement_has_atomic_version_and_ticket_conditions():
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")
    replacement = sample_guard(
        active_ticket_id="TKT-20260724-D4E5F6",
        version=2,
    )

    repository.create_with_idempotency(
        sample_ticket(ticket_id="TKT-20260724-D4E5F6"),
        {
            **sample_marker(),
            "ticket_id": "TKT-20260724-D4E5F6",
        },
        now_epoch=1784887200,
        guard=replacement,
        expected_guard={
            "version": 1,
            "active_ticket_id": "TKT-20260724-A1B2C3",
        },
    )

    guard_put = dynamo.meta.client.transactions[0]["TransactItems"][2]["Put"]
    assert "#version = :expected_version" in guard_put["ConditionExpression"]
    assert "active_ticket_id = :expected_ticket_id" in guard_put["ConditionExpression"]
    values = {
        key: DESERIALIZER.deserialize(value)
        for key, value in guard_put["ExpressionAttributeValues"].items()
    }
    assert values == {
        ":expected_version": 1,
        ":expected_ticket_id": "TKT-20260724-A1B2C3",
    }


def test_guard_conflict_is_not_classified_as_ticket_collision():
    dynamo = FakeDynamo()
    dynamo.meta.client.transaction_error = transaction_error(
        [{"Code": "None"}, {"Code": "None"}, {"Code": "ConditionalCheckFailed"}]
    )
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(HumanSessionGuardConflictError):
        repository.create_with_idempotency(
            sample_ticket(),
            sample_marker(),
            now_epoch=1784887200,
            guard=sample_guard(),
        )


def test_bind_reused_ticket_condition_checks_identity_and_active_status():
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")

    repository.bind_human_reuse(
        sample_ticket(),
        sample_marker(),
        sample_guard(),
        now_epoch=1784887200,
    )

    items = dynamo.meta.client.transactions[0]["TransactItems"]
    assert len(items) == 3
    check = items[0]["ConditionCheck"]
    assert check["Key"] == {
        "PK": {"S": "TICKET#TKT-20260724-A1B2C3"},
        "SK": {"S": "METADATA"},
    }
    assert "#status IN (:open, :in_review, :waiting)" in check["ConditionExpression"]


def test_terminal_change_while_binding_is_classified_separately():
    dynamo = FakeDynamo()
    dynamo.meta.client.transaction_error = transaction_error(
        [{"Code": "ConditionalCheckFailed"}, {"Code": "None"}, {"Code": "None"}]
    )
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(ReusableTicketChangedError):
        repository.bind_human_reuse(
            sample_ticket(),
            sample_marker(),
            sample_guard(),
            now_epoch=1784887200,
        )


def test_get_guard_uses_strongly_consistent_primary_key_read():
    dynamo = FakeDynamo()
    dynamo.table.get_responses = [{"Item": sample_guard()}]
    repository = TicketRepository(dynamo, "tickets")

    assert repository.get_human_session_guard("user-1", "session-1") == sample_guard()
    assert dynamo.table.last_get == {
        "Key": {
            "PK": human_session_guard_key("user-1", "session-1"),
            "SK": "METADATA",
        },
        "ConsistentRead": True,
    }


def test_get_ticket_uses_direct_consistent_lookup():
    dynamo = FakeDynamo()
    dynamo.table.get_responses = [{"Item": sample_ticket()}]
    repository = TicketRepository(dynamo, "tickets")

    assert repository.get("TKT-20260724-A1B2C3")["user_id"] == "user-1"
    assert dynamo.table.last_get == {
        "Key": {"PK": "TICKET#TKT-20260724-A1B2C3", "SK": "METADATA"},
        "ConsistentRead": True,
    }


def test_get_idempotency_marker_uses_direct_lookup():
    dynamo = FakeDynamo()
    dynamo.table.get_responses = [{"Item": sample_marker()}]
    repository = TicketRepository(dynamo, "tickets")

    assert repository.get_idempotency_marker(HASH_VALUE) == sample_marker()
    assert dynamo.table.last_get["Key"] == {
        "PK": f"IDEMPOTENCY#{HASH_VALUE}",
        "SK": "METADATA",
    }


def test_list_customer_tickets_uses_gsi1_without_scan():
    dynamo = FakeDynamo()
    dynamo.table.query_responses = [{"Items": [sample_ticket()]}]
    repository = TicketRepository(dynamo, "tickets")

    assert repository.list_for_customer("user-1") == [sample_ticket()]
    assert dynamo.table.last_query["IndexName"] == "GSI1"
    assert expression_values(
        dynamo.table.last_query["KeyConditionExpression"]
    ) == {"CUSTOMER#user-1"}


def test_list_customer_tickets_reads_every_gsi_page():
    dynamo = FakeDynamo()
    first = sample_ticket()
    second = sample_ticket(
        ticket_id="TKT-20260724-D4E5F6",
        PK="TICKET#TKT-20260724-D4E5F6",
        GSI1SK="CREATED#2026-07-24T11:00:00+00:00#TKT-20260724-D4E5F6",
        GSI2SK="UPDATED#2026-07-24T11:00:00+00:00#TKT-20260724-D4E5F6",
    )
    cursor = {"GSI1PK": "CUSTOMER#user-1", "GSI1SK": first["GSI1SK"]}
    dynamo.table.query_responses = [
        {"Items": [first], "LastEvaluatedKey": cursor},
        {"Items": [second]},
    ]
    repository = TicketRepository(dynamo, "tickets")

    assert repository.list_for_customer("user-1") == [first, second]
    assert dynamo.table.last_query["ExclusiveStartKey"] == cursor


@pytest.mark.parametrize(
    "responses",
    [
        [
            {
                "Items": [],
                "LastEvaluatedKey": {
                    "GSI1PK": "CUSTOMER#secret-cursor",
                    "position": Decimal("1"),
                    "blob": b"binary",
                },
            },
            {
                "Items": [],
                "LastEvaluatedKey": {
                    "GSI1PK": "CUSTOMER#secret-cursor",
                    "position": Decimal("1"),
                    "blob": b"binary",
                },
            },
        ],
        [
            {"Items": [], "LastEvaluatedKey": {"cursor": "A"}},
            {"Items": [], "LastEvaluatedKey": {"cursor": "B"}},
            {"Items": [], "LastEvaluatedKey": {"cursor": "A"}},
        ],
    ],
)
def test_customer_pagination_repeated_or_cyclic_cursor_fails_safely(responses):
    dynamo = FakeDynamo()
    dynamo.table.query_responses = responses
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(TicketPaginationStalledError) as exc_info:
        repository.list_for_customer("user-1")

    assert exc_info.value.code == "TICKET_PAGINATION_STALLED"
    assert "secret-cursor" not in str(exc_info.value)


def test_customer_pagination_excessive_page_count_fails_safely():
    class EndlessTable(FakeTable):
        def query(self, **kwargs):
            self.last_query = kwargs
            cursor = kwargs.get("ExclusiveStartKey", {"page": 0})
            return {
                "Items": [],
                "LastEvaluatedKey": {"page": cursor["page"] + 1},
            }

    dynamo = FakeDynamo()
    dynamo.table = EndlessTable()
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(TicketPaginationStalledError) as exc_info:
        repository.list_for_customer("user-1")

    assert exc_info.value.code == "TICKET_PAGINATION_STALLED"


def test_list_status_uses_gsi2_and_returns_pagination_cursor():
    dynamo = FakeDynamo()
    cursor = {"GSI2PK": "STATUS#open", "GSI2SK": "UPDATED#cursor"}
    dynamo.table.query_responses = [
        {"Items": [sample_ticket()], "LastEvaluatedKey": cursor}
    ]
    repository = TicketRepository(dynamo, "tickets")

    result = repository.list_by_status("open", limit=25)

    assert result == {"items": [sample_ticket()], "next_cursor": cursor}
    assert dynamo.table.last_query["IndexName"] == "GSI2"
    assert dynamo.table.last_query["Limit"] == 25
    assert expression_values(
        dynamo.table.last_query["KeyConditionExpression"]
    ) == {"STATUS#open"}


def test_save_uses_optimistic_lock_and_refreshes_status_index():
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")
    ticket = sample_ticket(
        status="resolved",
        updated_at="2026-07-24T11:00:00+00:00",
    )

    repository.save(ticket, expected_version=1)

    put = dynamo.table.put_calls[0]
    saved = put["Item"]
    assert saved["version"] == 2
    assert saved["GSI2PK"] == "STATUS#resolved"
    assert saved["GSI2SK"] == (
        "UPDATED#2026-07-24T11:00:00+00:00#TKT-20260724-A1B2C3"
    )
    assert put["ConditionExpression"] == "#version = :expected"
    assert put["ExpressionAttributeNames"] == {"#version": "version"}
    assert put["ExpressionAttributeValues"] == {":expected": 1}


def test_save_converts_conditional_failure_to_version_conflict():
    dynamo = FakeDynamo()

    def fail_put(**kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "conflict",
                }
            },
            "PutItem",
        )

    dynamo.table.put_item = fail_put
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(TicketVersionConflictError):
        repository.save(sample_ticket(), expected_version=1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"ticket_type": "refund_request"},
        {"status": "pending"},
        {"priority": "critical"},
        {"ticket_id": "bad-id"},
        {"user_id": "   "},
        {"version": 0},
        {"PK": "TICKET#wrong"},
        {"SK": "WRONG"},
        {"GSI1PK": "CUSTOMER#wrong"},
        {"GSI1SK": "CREATED#wrong"},
        {"GSI2PK": "STATUS#closed"},
        {"GSI2SK": "UPDATED#wrong"},
    ],
)
def test_create_rejects_invalid_ticket_before_boto_call(overrides):
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(ValueError):
        repository.create_with_idempotency(
            sample_ticket(**overrides),
            sample_marker(),
            now_epoch=1784887200,
        )

    assert dynamo.meta.client.transactions == []


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "ticket_type": "order_complaint",
            "order_id": "",
            "description": "Complaint",
            "order_status_snapshot": "accepted",
        },
        {
            "ticket_type": "order_complaint",
            "order_id": "ORD-1",
            "description": "   ",
            "order_status_snapshot": "accepted",
        },
        {
            "ticket_type": "order_complaint",
            "order_id": "ORD-1",
            "description": "Complaint",
            "order_status_snapshot": "",
        },
    ],
)
def test_create_rejects_malformed_complaint_before_boto_call(overrides):
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(ValueError):
        repository.create_with_idempotency(
            sample_ticket(**overrides),
            {**sample_marker(), "operation": "order_complaint"},
            now_epoch=1784887200,
        )

    assert dynamo.meta.client.transactions == []


def test_save_rejects_invalid_record_before_boto_call():
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(ValueError):
        repository.save(sample_ticket(status="pending"), expected_version=1)

    assert dynamo.table.put_calls == []


@pytest.mark.parametrize(
    ("ticket_id", "valid"),
    [
        ("TKT-20260724-ABC123", True),
        ("TKT-20240229-ABC123", True),
        ("TKT-20261301-ABC123", False),
        ("TKT-20260431-ABC123", False),
        ("TKT-20230229-ABC123", False),
        ("TKT-20260724-abc123", False),
        ("TKT-２０２６０７２４-ABC123", False),
        ("BAD-20260724-ABC123", False),
        ("TKT-20260724-ABC12", False),
    ],
)
def test_repository_enforces_strict_calendar_ticket_ids(ticket_id, valid):
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")
    ticket = sample_ticket(ticket_id=ticket_id)
    marker = {**sample_marker(), "ticket_id": ticket_id}

    if valid:
        repository.create_with_idempotency(ticket, marker, now_epoch=1784887200)
        assert len(dynamo.meta.client.transactions) == 1
    else:
        with pytest.raises(TicketDomainValidationError) as exc_info:
            repository.create_with_idempotency(
                ticket, marker, now_epoch=1784887200
            )
        assert exc_info.value.code == INVALID_TICKET_ID
        assert dynamo.meta.client.transactions == []


@pytest.mark.parametrize(
    "ticket_id",
    [
        "TKT-20261301-ABC123",
        "TKT-20260431-ABC123",
        "TKT-20230229-ABC123",
        "TKT-20260724-abc123",
    ],
)
def test_save_enforces_strict_calendar_ticket_ids_before_boto_call(ticket_id):
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(TicketDomainValidationError) as exc_info:
        repository.save(sample_ticket(ticket_id=ticket_id), expected_version=1)

    assert exc_info.value.code == INVALID_TICKET_ID
    assert dynamo.table.put_calls == []


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-07-24T10:00:00",
        "not-a-timestamp",
        "2026-02-30T10:00:00+00:00",
    ],
)
def test_repository_rejects_invalid_or_naive_timestamps(timestamp):
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")
    ticket = sample_ticket(created_at=timestamp, updated_at=timestamp)

    with pytest.raises(TicketDomainValidationError) as exc_info:
        repository.create_with_idempotency(
            ticket, sample_marker(), now_epoch=1784887200
        )

    assert exc_info.value.code == INVALID_TIMESTAMP
    assert dynamo.meta.client.transactions == []


def test_save_rejects_invalid_timestamp_before_boto_call():
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(TicketDomainValidationError) as exc_info:
        repository.save(
            sample_ticket(
                created_at="2026-07-24T10:00:00",
                updated_at="2026-07-24T10:00:00",
            ),
            expected_version=1,
        )

    assert exc_info.value.code == INVALID_TIMESTAMP
    assert dynamo.table.put_calls == []


def test_repository_normalizes_z_and_offset_timestamps_to_canonical_utc():
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")
    ticket = sample_ticket(
        created_at="2026-07-24T10:00:00Z",
        updated_at="2026-07-24T15:30:00+05:30",
        GSI1SK="CREATED#2026-07-24T10:00:00+00:00#TKT-20260724-A1B2C3",
        GSI2SK="UPDATED#2026-07-24T10:00:00+00:00#TKT-20260724-A1B2C3",
    )

    repository.create_with_idempotency(
        ticket, sample_marker(), now_epoch=1784887200
    )

    stored = deserialize_item(
        dynamo.meta.client.transactions[0]["TransactItems"][0]["Put"]["Item"]
    )
    assert stored["created_at"] == "2026-07-24T10:00:00+00:00"
    assert stored["updated_at"] == "2026-07-24T10:00:00+00:00"
    assert stored["GSI1SK"].startswith("CREATED#2026-07-24T10:00:00+00:00#")
    assert stored["GSI2SK"].startswith("UPDATED#2026-07-24T10:00:00+00:00#")


def test_repository_rejects_created_at_after_updated_at():
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")
    ticket = sample_ticket(
        created_at="2026-07-24T11:00:00+00:00",
        updated_at="2026-07-24T10:00:00+00:00",
    )

    with pytest.raises(TicketDomainValidationError) as exc_info:
        repository.create_with_idempotency(
            ticket, sample_marker(), now_epoch=1784887200
        )

    assert exc_info.value.code == INVALID_TIMESTAMP


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "admin_notes": [
                {"text": "note", "timestamp": "bad-timestamp"}
            ]
        },
        {
            "status_history": [
                {
                    "previous_status": "open",
                    "new_status": "in_review",
                    "timestamp": "bad-timestamp",
                }
            ]
        },
    ],
)
def test_repository_rejects_malformed_nested_timestamps(overrides):
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(TicketDomainValidationError) as exc_info:
        repository.create_with_idempotency(
            sample_ticket(**overrides),
            sample_marker(),
            now_epoch=1784887200,
        )

    assert exc_info.value.code == INVALID_TIMESTAMP


def test_repository_rejects_malformed_guard_timestamp():
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(TicketDomainValidationError) as exc_info:
        repository.create_with_idempotency(
            sample_ticket(),
            sample_marker(),
            now_epoch=1784887200,
            guard=sample_guard(updated_at="bad-timestamp"),
        )

    assert exc_info.value.code == INVALID_TIMESTAMP
    assert dynamo.meta.client.transactions == []


def oversized_ticket():
    ticket = sample_ticket()
    ticket["admin_notes"] = [
        {
            "text": "😀" * 2_000,
            "timestamp": "2026-07-24T10:00:00+00:00",
            "actor": "😀" * 200,
        }
        for _index in range(45)
    ]
    return ticket


def test_serialized_ticket_size_counts_multibyte_content():
    ascii_ticket = sample_ticket(description="a" * 4_000)
    unicode_ticket = sample_ticket(description="😀" * 4_000)

    assert ticket_item_size_bytes(unicode_ticket) > ticket_item_size_bytes(
        ascii_ticket
    )
    assert ticket_item_size_bytes(sample_ticket()) < MAX_TICKET_ITEM_BYTES


def test_oversized_creation_is_rejected_before_boto_call():
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(TicketDomainValidationError) as exc_info:
        repository.create_with_idempotency(
            oversized_ticket(), sample_marker(), now_epoch=1784887200
        )

    assert exc_info.value.code == TICKET_ITEM_TOO_LARGE
    assert dynamo.meta.client.transactions == []


def test_oversized_save_is_rejected_before_boto_call():
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(TicketDomainValidationError) as exc_info:
        repository.save(oversized_ticket(), expected_version=1)

    assert exc_info.value.code == TICKET_ITEM_TOO_LARGE
    assert dynamo.table.put_calls == []


def test_aggregate_history_can_push_otherwise_valid_item_over_limit():
    ticket = sample_ticket()
    ticket["admin_notes"] = [
        {
            "text": "😀" * 2_000,
            "timestamp": "2026-07-24T10:00:00+00:00",
        }
        for _index in range(36)
    ]
    notes_only_size = ticket_item_size_bytes(ticket)
    ticket["status_history"] = [
        {
            "previous_status": "open",
            "new_status": "in_review",
            "timestamp": "2026-07-24T10:00:00+00:00",
            "actor": "😀" * 200,
        }
        for _index in range(100)
    ]

    assert notes_only_size < MAX_TICKET_ITEM_BYTES
    assert ticket_item_size_bytes(ticket) > MAX_TICKET_ITEM_BYTES

    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")
    with pytest.raises(TicketDomainValidationError) as exc_info:
        repository.save(ticket, expected_version=1)

    assert exc_info.value.code == TICKET_ITEM_TOO_LARGE
    assert dynamo.table.put_calls == []


@pytest.mark.parametrize(
    ("marker_overrides", "guard_overrides"),
    [
        ({"user_id": "other-user"}, None),
        ({"operation": "order_complaint"}, None),
        ({"ticket_id": "TKT-20260724-D4E5F6"}, None),
        (None, {"user_id": "other-user"}),
        (None, {"session_id": "other-session"}),
        (None, {"active_ticket_id": "TKT-20260724-D4E5F6"}),
        (None, {"PK": "HUMAN_SESSION#wrong"}),
    ],
)
def test_fake_and_production_reject_same_cross_record_mismatches(
    marker_overrides,
    guard_overrides,
):
    ticket = sample_ticket()
    marker = {**sample_marker(), **(marker_overrides or {})}
    guard = sample_guard(**(guard_overrides or {})) if guard_overrides else None
    production_dynamo = FakeDynamo()
    production = TicketRepository(production_dynamo, "tickets")
    memory = MemoryTicketRepository()

    for repository in (production, memory):
        with pytest.raises(ValueError):
            repository.create_with_idempotency(
                ticket,
                marker,
                now_epoch=1784887200,
                guard=guard,
            )

    assert production_dynamo.meta.client.transactions == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"description": "x" * 4001},
        {"category": "x" * 101},
        {"customer_name": "x" * 201},
        {"customer_phone": "x" * 65},
        {"source": "x" * 65},
        {
            "admin_notes": [
                {"text": "note", "timestamp": "2026-07-24T10:00:00+00:00"}
                for _index in range(101)
            ]
        },
        {
            "status_history": [
                {
                    "previous_status": "open",
                    "new_status": "in_review",
                    "timestamp": "2026-07-24T10:00:00+00:00",
                }
                for _index in range(101)
            ]
        },
        {
            "admin_notes": [
                {
                    "text": "x" * 2001,
                    "timestamp": "2026-07-24T10:00:00+00:00",
                }
            ]
        },
        {
            "status_history": [
                {
                    "previous_status": "open",
                    "new_status": "in_review",
                    "timestamp": "2026-07-24T10:00:00+00:00",
                    "actor": "x" * 201,
                }
            ]
        },
    ],
)
def test_repository_enforces_item_size_limits_before_boto_call(overrides):
    dynamo = FakeDynamo()
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(ValueError):
        repository.create_with_idempotency(
            sample_ticket(**overrides),
            sample_marker(),
            now_epoch=1784887200,
        )

    assert dynamo.meta.client.transactions == []


@pytest.mark.parametrize(
    "error_code",
    [
        "ProvisionedThroughputExceededException",
        "AccessDeniedException",
        "ValidationException",
        "ResourceNotFoundException",
    ],
)
def test_non_conditional_create_errors_are_not_mapped_to_collisions(error_code):
    dynamo = FakeDynamo()
    error = ClientError(
        {"Error": {"Code": error_code, "Message": "failure"}},
        "TransactWriteItems",
    )
    dynamo.meta.client.transaction_error = error
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(ClientError) as exc_info:
        repository.create_with_idempotency(
            sample_ticket(), sample_marker(), now_epoch=1784887200
        )

    assert exc_info.value is error


@pytest.mark.parametrize(
    "error_code",
    [
        "ProvisionedThroughputExceededException",
        "AccessDeniedException",
        "ValidationException",
    ],
)
def test_non_conditional_save_errors_are_not_version_conflicts(error_code):
    dynamo = FakeDynamo()
    error = ClientError(
        {"Error": {"Code": error_code, "Message": "failure"}},
        "PutItem",
    )

    def fail_put(**kwargs):
        raise error

    dynamo.table.put_item = fail_put
    repository = TicketRepository(dynamo, "tickets")

    with pytest.raises(ClientError) as exc_info:
        repository.save(sample_ticket(), expected_version=1)

    assert exc_info.value is error
