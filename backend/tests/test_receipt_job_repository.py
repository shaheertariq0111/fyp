from copy import deepcopy

import pytest
from botocore.exceptions import ClientError

from src.models.receipt_job import ReceiptJobState, receipt_job_id
from src.repositories.base import from_dynamodb, to_dynamodb
from src.repositories.receipt_job_repository import (
    ReceiptJobConditionFailed,
    ReceiptJobRepository,
)
from test_receipt_job import receipt_record


def client_error(code="ConditionalCheckFailedException"):
    return ClientError(
        {"Error": {"Code": code, "Message": "private backend body"}},
        "ReceiptJobOperation",
    )


class ReceiptJobTable:
    def __init__(self):
        self.calls = []
        self.items = {}
        self.fail_code = None

    def _maybe_fail(self):
        if self.fail_code:
            raise client_error(self.fail_code)

    def put_item(self, **kwargs):
        self.calls.append(("put", kwargs))
        self._maybe_fail()
        item = from_dynamodb(kwargs["Item"])
        key = item["PK"]
        if key in self.items:
            raise client_error()
        self.items[key] = deepcopy(item)

    def get_item(self, **kwargs):
        self.calls.append(("get", kwargs))
        self._maybe_fail()
        item = self.items.get(kwargs["Key"]["PK"])
        return {"Item": to_dynamodb(deepcopy(item))} if item else {}

    def update_item(self, **kwargs):
        self.calls.append(("update", kwargs))
        self._maybe_fail()
        key = kwargs["Key"]["PK"]
        current = self.items.get(key)
        values = from_dynamodb(kwargs.get("ExpressionAttributeValues", {}))
        update = kwargs["UpdateExpression"]

        if current is None:
            raise client_error()
        if ":next" in values:
            expected_states = {
                value for token, value in values.items() if token.startswith(":state")
            }
            if (
                current["version"] != values[":version"]
                or current["state"] not in expected_states
                or (
                    ":lease_owner" in values
                    and current.get("lease_owner") != values[":lease_owner"]
                )
            ):
                raise client_error()
            updated = deepcopy(current)
            updated["state"] = values[":next"]
            updated["version"] += 1
            updated["updated_at"] = values[":updated"]
            names = kwargs["ExpressionAttributeNames"]
            for token, field in names.items():
                if token.startswith("#field"):
                    index = token.removeprefix("#field")
                    updated[field] = values[f":value{index}"]
                elif token.startswith("#remove"):
                    updated.pop(field, None)
            self.items[key] = updated
            return {"Attributes": to_dynamodb(deepcopy(updated))}

        owner = values[":owner"]
        if "if_not_exists(attempt_count" in update:
            held = (
                current.get("lease_owner")
                and current.get("lease_expires_at", 0) > values[":now"]
                and current.get("lease_owner") != owner
            )
            if held:
                raise client_error()
            current["lease_owner"] = owner
            current["lease_expires_at"] = values[":expires"]
            current["updated_at"] = values[":updated"]
            current["attempt_count"] = current.get("attempt_count", 0) + 1
            return {"Attributes": to_dynamodb(deepcopy(current))}

        if current.get("lease_owner") != owner:
            raise client_error()
        current["updated_at"] = values[":updated"]
        if "REMOVE lease_owner" in update:
            current.pop("lease_owner", None)
            current.pop("lease_expires_at", None)
        else:
            current["lease_expires_at"] = values[":expires"]
        return {}

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        self._maybe_fail()
        due = [
            to_dynamodb(deepcopy(record))
            for record in self.items.values()
            if record.get("GSI1PK") == "RECEIPT_OUTBOX"
        ]
        return {"Items": due[:kwargs["Limit"]]}


class Dynamo:
    def __init__(self, table):
        self.table = table

    def Table(self, _name):
        return self.table


def repository_with_record():
    table = ReceiptJobTable()
    repository = ReceiptJobRepository(Dynamo(table), "receipt-jobs")
    record = receipt_record()
    assert repository.create_if_absent(record)
    return repository, table, record


def test_create_if_absent_is_conditional_and_duplicate_returns_false():
    table = ReceiptJobTable()
    repository = ReceiptJobRepository(Dynamo(table), "receipt-jobs")
    record = receipt_record()

    assert repository.create_if_absent(record) is True
    assert table.calls[0][1]["ConditionExpression"] == "attribute_not_exists(PK)"
    assert repository.create_if_absent(record) is False


def test_create_if_absent_propagates_nonconditional_aws_errors():
    table = ReceiptJobTable()
    table.fail_code = "AccessDeniedException"
    repository = ReceiptJobRepository(Dynamo(table), "receipt-jobs")

    with pytest.raises(ClientError) as error:
        repository.create_if_absent(receipt_record())

    assert error.value.response["Error"]["Code"] == "AccessDeniedException"


def test_get_uses_consistent_read_and_validates_record():
    repository, table, record = repository_with_record()

    restored = repository.get(record["job_id"])

    assert restored == record
    get_call = table.calls[-1][1]
    assert get_call["ConsistentRead"] is True
    assert get_call["Key"] == {
        "PK": f"JOB#{record['job_id']}",
        "SK": "METADATA",
    }


def test_transition_requires_state_and_version_and_updates_atomically():
    repository, table, record = repository_with_record()

    updated = repository.transition(
        record["job_id"],
        expected_states={"pending_enqueue"},
        expected_version=1,
        next_state="queued",
        updated_at="2026-08-07T00:01:00+00:00",
        values={
            "enqueue_attempt_count": 1,
            "provider_message_id": "provider-safe",
        },
        remove=("GSI1PK", "GSI1SK", "generic_failure_code"),
    )

    call = table.calls[-1][1]
    assert "#version = :version" in call["ConditionExpression"]
    assert "#state IN" in call["ConditionExpression"]
    assert call["ExpressionAttributeValues"][":version"] == 1
    assert "#version = #version + :one" in call["UpdateExpression"]
    assert " REMOVE " in call["UpdateExpression"]
    assert updated["state"] == "queued"
    assert updated["version"] == 2
    assert updated["enqueue_attempt_count"] == 1
    assert updated["provider_message_id"] == "provider-safe"
    assert "GSI1PK" not in updated
    assert "GSI1SK" not in updated


def test_transition_can_condition_on_current_lease_owner():
    repository, table, record = repository_with_record()
    leased = repository.acquire_lease(
        record["job_id"],
        owner="worker-a",
        now_epoch=100,
        lease_expires_at=200,
        updated_at="lease-time",
    )

    updated = repository.transition(
        record["job_id"],
        expected_states={leased["state"]},
        expected_version=leased["version"],
        expected_lease_owner="worker-a",
        next_state="queued",
        updated_at="transition-time",
        remove=("GSI1PK", "GSI1SK"),
    )

    call = table.calls[-1][1]
    assert "#lease_owner = :lease_owner" in call["ConditionExpression"]
    assert call["ExpressionAttributeNames"]["#lease_owner"] == "lease_owner"
    assert call["ExpressionAttributeValues"][":lease_owner"] == "worker-a"
    assert updated["state"] == "queued"


def test_transition_rejects_stale_lease_owner():
    repository, _, record = repository_with_record()
    leased = repository.acquire_lease(
        record["job_id"],
        owner="worker-current",
        now_epoch=100,
        lease_expires_at=200,
        updated_at="lease-time",
    )

    with pytest.raises(ReceiptJobConditionFailed):
        repository.transition(
            record["job_id"],
            expected_states={leased["state"]},
            expected_version=leased["version"],
            expected_lease_owner="worker-stale",
            next_state="queued",
            updated_at="transition-time",
            remove=("GSI1PK", "GSI1SK"),
        )


@pytest.mark.parametrize(
    ("expected_states", "expected_version"),
    [({"queued"}, 1), ({"pending_enqueue"}, 2)],
)
def test_transition_condition_failure_is_not_hidden(expected_states, expected_version):
    repository, _, record = repository_with_record()

    with pytest.raises(
        ReceiptJobConditionFailed,
        match="RECEIPT_JOB_CONDITION_FAILED",
    ):
        repository.transition(
            record["job_id"],
            expected_states=expected_states,
            expected_version=expected_version,
            next_state="queued",
            updated_at="2026-08-07T00:01:00+00:00",
        )


@pytest.mark.parametrize(
    ("values", "remove"),
    [
        ({"GSI1PK": "RECEIPT_OUTBOX"}, ()),
        ({"GSI1PK": "WRONG", "GSI1SK": 100}, ()),
        ({"GSI1PK": "RECEIPT_OUTBOX", "GSI1SK": -1}, ()),
        ({"GSI1PK": "RECEIPT_OUTBOX", "GSI1SK": True}, ()),
        ({"enqueue_attempt_count": True}, ()),
        ({"lease_owner": "worker"}, ()),
        ({"customer_name": "Private Customer"}, ()),
        ({}, ("GSI1PK",)),
        ({}, ("enqueue_attempt_count",)),
    ],
)
def test_transition_rejects_unsafe_checkpoint_mutations_before_write(values, remove):
    repository, table, record = repository_with_record()
    calls_before = len(table.calls)

    with pytest.raises(ValueError):
        repository.transition(
            record["job_id"],
            expected_states={"pending_enqueue"},
            expected_version=1,
            next_state="queued",
            updated_at="2026-08-07T00:01:00+00:00",
            values=values,
            remove=remove,
        )

    assert len(table.calls) == calls_before


def test_transition_accepts_known_optional_checkpoint_values():
    repository, _, record = repository_with_record()

    updated = repository.transition(
        record["job_id"],
        expected_states={"pending_enqueue"},
        expected_version=1,
        next_state="retryable_failure",
        updated_at="2026-08-07T00:01:00+00:00",
        values={
            "enqueue_attempt_count": 1,
            "GSI1PK": "RECEIPT_OUTBOX",
            "GSI1SK": 1786060100,
            "s3_key": "receipts/opaque.pdf",
            "provider_message_id": "provider-safe",
            "generic_failure_code": "RECEIPT_OPERATION_FAILED",
            "next_retry_at": 1786060100,
        },
    )

    assert updated["enqueue_attempt_count"] == 1
    assert updated["GSI1PK"] == "RECEIPT_OUTBOX"
    assert updated["GSI1SK"] == 1786060100
    assert updated["s3_key"] == "receipts/opaque.pdf"
    assert updated["provider_message_id"] == "provider-safe"
    assert updated["generic_failure_code"] == "RECEIPT_OPERATION_FAILED"
    assert updated["next_retry_at"] == 1786060100


def test_transition_can_remove_both_outbox_fields_together():
    repository, _, record = repository_with_record()

    updated = repository.transition(
        record["job_id"],
        expected_states={"pending_enqueue"},
        expected_version=1,
        next_state="queued",
        updated_at="2026-08-07T00:01:00+00:00",
        remove=("GSI1PK", "GSI1SK"),
    )

    assert "GSI1PK" not in updated
    assert "GSI1SK" not in updated


def test_receipt_specific_terminal_helper_uses_optimistic_transition():
    repository, _, record = repository_with_record()

    sent = repository.mark_sent(
        record["job_id"],
        expected_states={"pending_enqueue"},
        expected_version=1,
        updated_at="2026-08-07T00:01:00+00:00",
    )

    assert sent["state"] == ReceiptJobState.SENT.value
    assert sent["version"] == 2


def test_lease_acquisition_allows_same_owner_or_expiry_and_counts_attempts():
    repository, _, record = repository_with_record()

    first = repository.acquire_lease(
        record["job_id"],
        owner="worker-a",
        now_epoch=100,
        lease_expires_at=200,
        updated_at="timestamp-1",
    )
    same_owner = repository.acquire_lease(
        record["job_id"],
        owner="worker-a",
        now_epoch=150,
        lease_expires_at=250,
        updated_at="timestamp-2",
    )
    expired = repository.acquire_lease(
        record["job_id"],
        owner="worker-b",
        now_epoch=250,
        lease_expires_at=350,
        updated_at="timestamp-3",
    )

    assert first["attempt_count"] == 1
    assert same_owner["attempt_count"] == 2
    assert expired["attempt_count"] == 3
    assert expired["lease_owner"] == "worker-b"


def test_live_lease_held_by_other_owner_raises_condition_failure():
    repository, _, record = repository_with_record()
    repository.acquire_lease(
        record["job_id"],
        owner="worker-a",
        now_epoch=100,
        lease_expires_at=200,
        updated_at="timestamp-1",
    )

    with pytest.raises(ReceiptJobConditionFailed, match="RECEIPT_JOB_LEASE_HELD"):
        repository.acquire_lease(
            record["job_id"],
            owner="worker-b",
            now_epoch=150,
            lease_expires_at=250,
            updated_at="timestamp-2",
        )


def test_extend_and_release_lease_require_owner():
    repository, table, record = repository_with_record()
    repository.acquire_lease(
        record["job_id"],
        owner="worker-a",
        now_epoch=100,
        lease_expires_at=200,
        updated_at="timestamp-1",
    )

    assert repository.extend_lease(
        record["job_id"],
        owner="worker-a",
        lease_expires_at=300,
        updated_at="timestamp-2",
    ) is True
    assert table.items[record["PK"]]["lease_expires_at"] == 300
    assert repository.extend_lease(
        record["job_id"],
        owner="worker-b",
        lease_expires_at=400,
        updated_at="timestamp-3",
    ) is False
    assert repository.release_lease(
        record["job_id"],
        owner="worker-a",
        updated_at="timestamp-4",
    ) is True
    assert "lease_owner" not in table.items[record["PK"]]
    assert repository.release_lease(
        record["job_id"],
        owner="worker-a",
        updated_at="timestamp-5",
    ) is False


def test_query_due_uses_receipt_outbox_partition_and_due_time_condition():
    repository, table, record = repository_with_record()

    records = repository.query_due(now_epoch=1786060800, limit=10)

    assert records == [record]
    call = table.calls[-1][1]
    assert call["IndexName"] == "DueJobsIndex"
    assert call["Limit"] == 10
    root = call["KeyConditionExpression"].get_expression()
    left, right = root["values"]
    left_values = left.get_expression()["values"]
    right_values = right.get_expression()["values"]
    assert left_values[0].name == "GSI1PK"
    assert left_values[1] == "RECEIPT_OUTBOX"
    assert right_values[0].name == "GSI1SK"
    assert right_values[1] == 1786060800


def test_queue_identity_and_dynamodb_key_exclude_order_and_routing_pii():
    order_id = "private-order-id"
    job_id = receipt_job_id(order_id, 1)
    record = receipt_record(
        order_id=order_id,
        job_id=job_id,
        PK=f"JOB#{job_id}",
        customer_number="+923001234567",
        sender_id="sender-private",
        conversation_id="conversation-private",
    )
    key_text = f"{record['PK']} {record['SK']}"

    for private_value in (
        order_id,
        record["customer_number"],
        record["sender_id"],
        record["conversation_id"],
    ):
        assert private_value not in key_text
