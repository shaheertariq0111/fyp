from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from src.models.receipt_job import (
    ReceiptJobState,
    receipt_job_id,
    validate_receipt_job_record,
)
from src.repositories.receipt_job_repository import ReceiptJobConditionFailed
from src.services.receipt_job_service import (
    ReceiptJobService,
    ReceiptJobServiceError,
)
from src.services.receipt_queue_service import ReceiptQueueError


FIXED_NOW = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


class MemoryReceiptJobs:
    def __init__(self):
        self.records = {}
        self.created_records = []
        self.calls = []
        self.due_records = []
        self.conflict_next_state = None
        self.conflict_enqueue_attempt_count = None

    def create_if_absent(self, record):
        self.calls.append(("create", record["job_id"]))
        if record["job_id"] in self.records:
            return False
        validate_receipt_job_record(record)
        self.records[record["job_id"]] = deepcopy(record)
        self.created_records.append(deepcopy(record))
        return True

    def get(self, job_id):
        self.calls.append(("get", job_id))
        record = self.records.get(job_id)
        return deepcopy(record) if record else None

    def transition(
        self,
        job_id,
        *,
        expected_states,
        expected_version,
        next_state,
        updated_at,
        values=None,
        remove=(),
        expected_lease_owner=None,
    ):
        self.calls.append(("transition", next_state))
        record = self.records[job_id]
        if self.conflict_next_state is not None:
            record["state"] = self.conflict_next_state
            record["version"] += 1
            if self.conflict_enqueue_attempt_count is not None:
                record["enqueue_attempt_count"] = (
                    self.conflict_enqueue_attempt_count
                )
            self.conflict_next_state = None
            self.conflict_enqueue_attempt_count = None
            raise ReceiptJobConditionFailed("RECEIPT_JOB_CONDITION_FAILED")
        if (
            record["state"] not in expected_states
            or record["version"] != expected_version
            or (
                expected_lease_owner is not None
                and record.get("lease_owner") != expected_lease_owner
            )
        ):
            raise ReceiptJobConditionFailed("RECEIPT_JOB_CONDITION_FAILED")
        updated = deepcopy(record)
        updated.update(values or {})
        for field in remove:
            updated.pop(field, None)
        updated["state"] = next_state
        updated["version"] = expected_version + 1
        updated["updated_at"] = updated_at
        validate_receipt_job_record(updated)
        self.records[job_id] = updated
        return deepcopy(updated)

    def acquire_lease(
        self,
        job_id,
        *,
        owner,
        now_epoch,
        lease_expires_at,
        updated_at,
    ):
        record = self.records[job_id]
        if (
            record.get("lease_owner") not in {None, owner}
            and record.get("lease_expires_at", 0) > now_epoch
        ):
            raise ReceiptJobConditionFailed("RECEIPT_JOB_LEASE_HELD")
        record["lease_owner"] = owner
        record["lease_expires_at"] = lease_expires_at
        record["updated_at"] = updated_at
        record["attempt_count"] += 1
        return deepcopy(record)

    def extend_lease(self, job_id, *, owner, lease_expires_at, updated_at):
        record = self.records[job_id]
        if record.get("lease_owner") != owner:
            return False
        record["lease_expires_at"] = lease_expires_at
        record["updated_at"] = updated_at
        return True

    def release_lease(self, job_id, *, owner, updated_at):
        record = self.records[job_id]
        if record.get("lease_owner") != owner:
            return False
        record.pop("lease_owner")
        record.pop("lease_expires_at")
        record["updated_at"] = updated_at
        return True

    def query_due(self, **kwargs):
        self.calls.append(("query_due", deepcopy(kwargs)))
        return [deepcopy(self.records[job_id]) for job_id in self.due_records]

    def scan(self):
        raise AssertionError("Receipt outbox recovery must not scan")


class FakeQueue:
    def __init__(self, repository=None, *, fail=False, private_error=False):
        self.repository = repository
        self.fail = fail
        self.private_error = private_error
        self.sent = []
        self.persisted_before_send = []

    def send(self, job_id):
        self.sent.append(job_id)
        if self.repository is not None:
            record = self.repository.records.get(job_id)
            self.persisted_before_send.append(
                bool(record and record["state"] == "pending_enqueue")
            )
        if self.fail:
            error = ReceiptQueueError()
            if self.private_error:
                error.args = ("private AWS exception response body",)
            raise error


def make_service(repository=None, queue=None, *, clock=None):
    repository = repository or MemoryReceiptJobs()
    queue = queue or FakeQueue(repository)
    service = ReceiptJobService(
        repository,
        queue,
        job_ttl_hours=24,
        enqueue_retry_seconds=30,
    )
    current = clock or [FIXED_NOW]
    service._now = lambda: current[0]
    return service, repository, queue, current


def submit(service, **overrides):
    values = {
        "order_id": "ORD-123",
        "receipt_version": 1,
        "customer_number": "  +923001234567  ",
        "sender_id": "  sender-private  ",
        "conversation_id": "  conversation-private  ",
        "request_id": "  request-private  ",
    }
    values.update(overrides)
    return service.submit(**values)


def pending_record(order_id, *, state="pending_enqueue", due_epoch=None):
    job_id = receipt_job_id(order_id, 1)
    epoch = due_epoch if due_epoch is not None else int(FIXED_NOW.timestamp())
    record = {
        "PK": f"JOB#{job_id}",
        "SK": "METADATA",
        "job_id": job_id,
        "order_id": order_id,
        "receipt_version": 1,
        "state": state,
        "version": 1,
        "customer_number": "+923001234567",
        "sender_id": "sender-private",
        "conversation_id": "conversation-private",
        "request_id": "request-private",
        "attempt_count": 0,
        "enqueue_attempt_count": 0,
        "created_at": FIXED_NOW.isoformat(),
        "updated_at": FIXED_NOW.isoformat(),
        "expires_at": int((FIXED_NOW + timedelta(hours=24)).timestamp()),
        "GSI1PK": "RECEIPT_OUTBOX",
        "GSI1SK": epoch,
    }
    if state == "retryable_failure":
        record.update(
            generic_failure_code="RECEIPT_QUEUE_OPERATION_FAILED",
            next_retry_at=epoch,
        )
    validate_receipt_job_record(record)
    return record


def test_new_submission_persists_before_send_and_transitions_to_queued():
    service, repository, queue, _ = make_service()

    result = submit(service)

    job_id = receipt_job_id("ORD-123", 1)
    initial = repository.created_records[0]
    final = repository.records[job_id]
    assert result.job_id == job_id
    assert result.accepted is True
    assert result.queued is True
    assert result.duplicate is False
    assert queue.persisted_before_send == [True]
    assert repository.calls[0] == ("create", job_id)
    assert queue.sent == [job_id]
    assert initial == {
        "PK": f"JOB#{job_id}",
        "SK": "METADATA",
        "job_id": job_id,
        "order_id": "ORD-123",
        "receipt_version": 1,
        "state": "pending_enqueue",
        "version": 1,
        "customer_number": "+923001234567",
        "sender_id": "sender-private",
        "conversation_id": "conversation-private",
        "request_id": "request-private",
        "attempt_count": 0,
        "enqueue_attempt_count": 0,
        "created_at": FIXED_NOW.isoformat(),
        "updated_at": FIXED_NOW.isoformat(),
        "expires_at": int((FIXED_NOW + timedelta(hours=24)).timestamp()),
        "GSI1PK": "RECEIPT_OUTBOX",
        "GSI1SK": int(FIXED_NOW.timestamp()),
    }
    assert initial["created_at"] == initial["updated_at"]
    assert final["state"] == "queued"
    assert final["enqueue_attempt_count"] == 1
    for field in ("GSI1PK", "GSI1SK", "next_retry_at", "generic_failure_code"):
        assert field not in final
    forbidden = {
        "receipt_snapshot",
        "customer_name",
        "delivery_address",
        "items",
        "subtotal",
        "total",
        "pdf",
        "base64",
    }
    assert forbidden.isdisjoint(initial)


def test_queue_failure_is_durable_retryable_and_privacy_safe(caplog):
    repository = MemoryReceiptJobs()
    queue = FakeQueue(repository, fail=True, private_error=True)
    service, _, _, _ = make_service(repository, queue)

    result = submit(service)

    record = repository.records[result.job_id]
    retry_at = int((FIXED_NOW + timedelta(seconds=30)).timestamp())
    assert result.accepted is True
    assert result.queued is False
    assert record["state"] == "retryable_failure"
    assert record["enqueue_attempt_count"] == 1
    assert record["generic_failure_code"] == "RECEIPT_QUEUE_OPERATION_FAILED"
    assert record["next_retry_at"] == retry_at
    assert record["GSI1PK"] == "RECEIPT_OUTBOX"
    assert record["GSI1SK"] == retry_at
    assert "private AWS exception response body" not in str(record)
    for private_value in (
        "ORD-123",
        "+923001234567",
        "sender-private",
        "conversation-private",
        "request-private",
        "private AWS exception response body",
    ):
        assert private_value not in caplog.text


def test_repeated_due_failure_checkpoints_each_uncontended_enqueue_attempt():
    repository = MemoryReceiptJobs()
    queue = FakeQueue(repository, fail=True)
    service, _, _, clock = make_service(repository, queue)

    first = submit(service)
    clock[0] = FIXED_NOW + timedelta(seconds=30)
    second = submit(
        service,
        customer_number="different-number",
        sender_id="different-sender",
        conversation_id="different-conversation",
        request_id="different-request",
    )

    record = repository.records[first.job_id]
    assert second.job_id == first.job_id
    assert second.duplicate is True
    assert second.queued is False
    assert queue.sent == [first.job_id, first.job_id]
    assert record["enqueue_attempt_count"] == 2
    assert record["customer_number"] == "+923001234567"
    assert record["sender_id"] == "sender-private"
    assert record["conversation_id"] == "conversation-private"
    assert record["request_id"] == "request-private"


def test_duplicate_advanced_job_does_not_send_again():
    service, _, queue, _ = make_service()
    first = submit(service)

    duplicate = submit(service)

    assert duplicate.job_id == first.job_id
    assert duplicate.accepted is True
    assert duplicate.queued is True
    assert duplicate.duplicate is True
    assert queue.sent == [first.job_id]


def test_duplicate_retryable_job_does_not_bypass_future_backoff():
    repository = MemoryReceiptJobs()
    queue = FakeQueue(repository, fail=True)
    service, _, _, _ = make_service(repository, queue)
    first = submit(service)

    duplicate = submit(service)

    assert duplicate.job_id == first.job_id
    assert duplicate.duplicate is True
    assert duplicate.queued is False
    assert queue.sent == [first.job_id]


def test_due_duplicate_retryable_job_assists_successful_enqueue_recovery():
    repository = MemoryReceiptJobs()
    queue = FakeQueue(repository, fail=True)
    service, _, _, clock = make_service(repository, queue)
    first = submit(service)
    clock[0] = FIXED_NOW + timedelta(seconds=30)
    queue.fail = False

    recovered = submit(service)

    record = repository.records[first.job_id]
    assert recovered.duplicate is True
    assert recovered.queued is True
    assert queue.sent == [first.job_id, first.job_id]
    assert record["state"] == "queued"
    assert record["enqueue_attempt_count"] == 2
    assert "GSI1PK" not in record
    assert "GSI1SK" not in record


def test_duplicate_create_with_missing_consistent_read_fails_explicitly():
    class MissingDuplicate(MemoryReceiptJobs):
        def create_if_absent(self, record):
            return False

    service, _, queue, _ = make_service(MissingDuplicate())

    with pytest.raises(ReceiptJobServiceError) as error:
        submit(service)

    assert error.value.error_code == "RECEIPT_JOB_INTERNAL_ERROR"
    assert queue.sent == []


@pytest.mark.parametrize(
    ("latest_state", "expected_queued"),
    [("processing", True), ("pending_enqueue", True)],
)
def test_successful_send_transition_race_reloads_without_downgrade(
    latest_state,
    expected_queued,
):
    repository = MemoryReceiptJobs()
    repository.conflict_next_state = latest_state
    repository.conflict_enqueue_attempt_count = 7
    service, _, queue, _ = make_service(repository)

    result = submit(service)

    assert queue.sent == [result.job_id]
    assert result.queued is expected_queued
    assert repository.records[result.job_id]["state"] == latest_state
    assert repository.records[result.job_id]["enqueue_attempt_count"] == 7


def test_failed_send_checkpoint_race_reloads_without_downgrade():
    repository = MemoryReceiptJobs()
    repository.conflict_next_state = "generated"
    repository.conflict_enqueue_attempt_count = 9
    queue = FakeQueue(repository, fail=True)
    service, _, _, _ = make_service(repository, queue)

    result = submit(service)

    assert result.accepted is True
    assert result.queued is True
    assert repository.records[result.job_id]["state"] == "generated"
    assert repository.records[result.job_id]["enqueue_attempt_count"] == 9


def test_receipt_job_logs_exclude_submission_and_routing_values(caplog):
    service, _, _, _ = make_service()

    with caplog.at_level("INFO"):
        submit(service)
        submit(service)

    assert {
        getattr(record, "event", None)
        for record in caplog.records
    } == {
        "receipt_job_accepted",
        "receipt_queue_submitted",
        "receipt_duplicate",
    }
    for private_value in (
        "ORD-123",
        "+923001234567",
        "sender-private",
        "conversation-private",
        "request-private",
    ):
        assert private_value not in caplog.text


def test_recover_outbox_queries_index_and_processes_only_enqueue_states():
    service, repository, queue, _ = make_service()
    records = [
        pending_record("ORD-PENDING"),
        pending_record("ORD-RETRY", state="retryable_failure"),
        pending_record("ORD-ADVANCED", state="generated"),
    ]
    for record in records:
        repository.records[record["job_id"]] = deepcopy(record)
    repository.due_records = [record["job_id"] for record in records]

    recovered = service.recover_outbox(limit=7)

    assert recovered == 2
    assert repository.calls[0] == (
        "query_due",
        {
            "due_partition": "RECEIPT_OUTBOX",
            "now_epoch": int(FIXED_NOW.timestamp()),
            "limit": 7,
        },
    )
    assert queue.sent == [records[0]["job_id"], records[1]["job_id"]]
    assert repository.records[records[0]["job_id"]]["state"] == "queued"
    assert repository.records[records[1]["job_id"]]["state"] == "queued"
    assert repository.records[records[2]["job_id"]]["state"] == "generated"


@pytest.mark.parametrize("limit", [0, -1, True, 1.0, "1", None])
def test_recover_outbox_rejects_invalid_limit(limit):
    service, repository, queue, _ = make_service()

    with pytest.raises(ValueError, match="RECEIPT_OUTBOX_LIMIT_INVALID"):
        service.recover_outbox(limit=limit)

    assert repository.calls == []
    assert queue.sent == []


@pytest.mark.parametrize(
    ("job_ttl_hours", "retry_seconds"),
    [(0, 30), (True, 30), (24, 0), (24, False)],
)
def test_service_rejects_invalid_time_policy(job_ttl_hours, retry_seconds):
    with pytest.raises(ValueError):
        ReceiptJobService(
            MemoryReceiptJobs(),
            FakeQueue(),
            job_ttl_hours=job_ttl_hours,
            enqueue_retry_seconds=retry_seconds,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("order_id", " ORD-123 "),
        ("receipt_version", True),
        ("customer_number", " "),
        ("sender_id", None),
        ("conversation_id", ""),
        ("request_id", 123),
    ],
)
def test_submission_rejects_invalid_inputs_before_persistence(field, value):
    service, repository, queue, _ = make_service()

    with pytest.raises(ValueError):
        submit(service, **{field: value})

    assert repository.calls == []
    assert queue.sent == []


def test_worker_lifecycle_helpers_use_explicit_lease_owner_and_duration():
    service, repository, _, _ = make_service()
    record = pending_record("ORD-WORKER", state="queued")
    repository.records[record["job_id"]] = deepcopy(record)

    leased = service.acquire_lease(
        record["job_id"],
        " worker-a ",
        lease_seconds=60,
    )
    processing = service.transition(
        leased,
        "processing",
        lease_owner="worker-a",
        remove=("GSI1PK", "GSI1SK"),
    )

    assert processing["state"] == "processing"
    assert processing["lease_owner"] == "worker-a"
    assert service.extend_lease(
        record["job_id"],
        "worker-a",
        lease_seconds=60,
    ) is True
    assert service.release_lease(record["job_id"], "worker-a") is True


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, None])
def test_worker_lease_helpers_reject_invalid_duration(value):
    service, _, _, _ = make_service()
    with pytest.raises(ValueError, match="RECEIPT_JOB_LEASE_INVALID"):
        service.acquire_lease("unused", "worker", lease_seconds=value)


def test_terminal_jobs_have_no_legal_worker_transition():
    service, _, _, _ = make_service()
    for state in ("sent", "permanent_failure", "manual_review"):
        with pytest.raises(ValueError, match="RECEIPT_JOB_TRANSITION_INVALID"):
            service.transition(
                {"job_id": "unused", "state": state, "version": 1},
                "processing",
                lease_owner="worker",
            )
