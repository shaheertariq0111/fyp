from copy import deepcopy

import pytest

from src.models.receipt_job import (
    RECEIPT_QUEUE_KIND,
    RECEIPT_QUEUE_MAX_BYTES,
    RECEIPT_QUEUE_VERSION,
    TERMINAL_RECEIPT_JOB_STATES,
    ReceiptJobState,
    ReceiptQueueMessage,
    receipt_job_id,
    validate_receipt_job_id,
    validate_receipt_job_record,
)


def receipt_record(**overrides):
    order_id = overrides.pop("order_id", "ORD-123")
    receipt_version = overrides.pop("receipt_version", 1)
    job_id = overrides.pop("job_id", receipt_job_id(order_id, receipt_version))
    record = {
        "PK": f"JOB#{job_id}",
        "SK": "METADATA",
        "job_id": job_id,
        "order_id": order_id,
        "receipt_version": receipt_version,
        "state": ReceiptJobState.PENDING_ENQUEUE.value,
        "version": 1,
        "customer_number": "+923001234567",
        "sender_id": "sender-private",
        "conversation_id": "conversation-private",
        "request_id": "request-safe",
        "attempt_count": 0,
        "enqueue_attempt_count": 0,
        "created_at": "2026-08-07T00:00:00+00:00",
        "updated_at": "2026-08-07T00:00:00+00:00",
        "expires_at": 1786060800,
        "GSI1PK": "RECEIPT_OUTBOX",
        "GSI1SK": 1786060000,
    }
    record.update(overrides)
    return record


def test_receipt_job_id_is_deterministic_and_opaque():
    first = receipt_job_id("ORD-123", 1)
    second = receipt_job_id("ORD-123", 1)

    assert first == second
    assert first.startswith("rj1_")
    assert len(first) == 68
    assert "ORD-123" not in first
    assert validate_receipt_job_id(first) == first


def test_receipt_job_id_changes_with_order_or_version():
    baseline = receipt_job_id("ORD-123", 1)

    assert receipt_job_id("ORD-124", 1) != baseline
    assert receipt_job_id("ORD-123", 2) != baseline


@pytest.mark.parametrize("order_id", ["", "   ", None, 123])
def test_receipt_job_id_rejects_empty_or_invalid_order_id(order_id):
    with pytest.raises(ValueError, match="RECEIPT_ORDER_ID_REQUIRED"):
        receipt_job_id(order_id, 1)


@pytest.mark.parametrize("version", [0, -1, True, False, 1.0, "1", None])
def test_receipt_job_id_rejects_invalid_receipt_version(version):
    with pytest.raises(ValueError, match="RECEIPT_VERSION_INVALID"):
        receipt_job_id("ORD-123", version)


@pytest.mark.parametrize(
    "value",
    [
        "rj1_" + "A" * 64,
        "rj2_" + "a" * 64,
        "rj1_" + "a" * 63,
        "ORD-123",
        "",
        None,
        123,
    ],
)
def test_receipt_job_id_validation_is_strict(value):
    with pytest.raises(ValueError, match="RECEIPT_JOB_ID_INVALID"):
        validate_receipt_job_id(value)


def test_receipt_queue_message_is_exact_minimal_and_round_trips():
    job_id = receipt_job_id("private-order-id", 1)
    message = ReceiptQueueMessage(job_id)
    body = message.serialize()

    assert body == (
        '{"job_id":"' + job_id
        + '","kind":"whatsapp_pdf_receipt","v":1}'
    )
    assert len(body.encode("utf-8")) <= RECEIPT_QUEUE_MAX_BYTES
    assert ReceiptQueueMessage.parse(body) == message
    assert ReceiptQueueMessage.parse(body.encode("utf-8")) == message
    assert "private-order-id" not in body
    for private_value in (
        "+923001234567",
        "Private Customer",
        "Private Address",
        "sender-private",
        "conversation-private",
    ):
        assert private_value not in body


@pytest.mark.parametrize(
    "body",
    [
        "not-json",
        b"\xff",
        "[]",
        "{}",
        '{"v":1,"kind":"whatsapp_pdf_receipt"}',
        (
            '{"v":1,"kind":"whatsapp_pdf_receipt","job_id":"rj1_'
            + "0" * 64
            + '","extra":true}'
        ),
        (
            '{"v":1,"v":1,"kind":"whatsapp_pdf_receipt","job_id":"rj1_'
            + "0" * 64
            + '"}'
        ),
        (
            '{"v":2,"kind":"whatsapp_pdf_receipt","job_id":"rj1_'
            + "0" * 64
            + '"}'
        ),
        (
            '{"v":1,"kind":"wrong","job_id":"rj1_'
            + "0" * 64
            + '"}'
        ),
        (
            '{"v":true,"kind":"whatsapp_pdf_receipt","job_id":"rj1_'
            + "0" * 64
            + '"}'
        ),
        '{"v":1,"kind":"whatsapp_pdf_receipt","job_id":"bad"}',
        123,
    ],
)
def test_receipt_queue_parser_rejects_invalid_bodies(body):
    with pytest.raises(ValueError):
        ReceiptQueueMessage.parse(body)


def test_receipt_queue_parser_rejects_oversized_body():
    with pytest.raises(ValueError, match="RECEIPT_QUEUE_BODY_TOO_LARGE"):
        ReceiptQueueMessage.parse(b" " * (RECEIPT_QUEUE_MAX_BYTES + 1))


def test_receipt_queue_constants_and_terminal_states_are_exact():
    assert RECEIPT_QUEUE_VERSION == 1
    assert RECEIPT_QUEUE_KIND == "whatsapp_pdf_receipt"
    assert [state.value for state in ReceiptJobState] == [
        "pending_enqueue",
        "queued",
        "processing",
        "generated",
        "outbound_sending",
        "sent",
        "retryable_failure",
        "permanent_failure",
        "manual_review",
    ]
    assert TERMINAL_RECEIPT_JOB_STATES == {
        "sent",
        "permanent_failure",
        "manual_review",
    }


def test_receipt_job_record_validation_accepts_valid_record():
    validate_receipt_job_record(receipt_record())


def test_receipt_job_record_rejects_noncanonical_order_id():
    record = receipt_record()
    record["order_id"] = "  ORD-123  "

    with pytest.raises(ValueError, match="RECEIPT_ORDER_ID_INVALID"):
        validate_receipt_job_record(record)


def test_receipt_job_record_accepts_known_optional_checkpoints():
    validate_receipt_job_record(receipt_record(
        s3_key="receipts/opaque.pdf",
        provider_message_id="provider-safe",
        generic_failure_code="RECEIPT_GENERATION_FAILED",
        next_retry_at=1786060100,
    ))


def test_receipt_job_record_rejects_arbitrary_extra_field():
    record = receipt_record(customer_name="Private Customer")

    with pytest.raises(ValueError, match="RECEIPT_JOB_RECORD_INVALID"):
        validate_receipt_job_record(record)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("PK", "JOB#wrong", "RECEIPT_JOB_RECORD_INVALID"),
        ("SK", "PRIVATE", "RECEIPT_JOB_RECORD_INVALID"),
        ("order_id", " ", "RECEIPT_ORDER_ID_REQUIRED"),
        ("receipt_version", 0, "RECEIPT_VERSION_INVALID"),
        ("receipt_version", True, "RECEIPT_VERSION_INVALID"),
        ("state", "unknown", "RECEIPT_JOB_STATE_INVALID"),
        ("state", 1, "RECEIPT_JOB_STATE_INVALID"),
        ("version", 0, "RECEIPT_JOB_VERSION_INVALID"),
        ("version", True, "RECEIPT_JOB_VERSION_INVALID"),
        ("attempt_count", -1, "RECEIPT_JOB_COUNTER_INVALID"),
        ("attempt_count", False, "RECEIPT_JOB_COUNTER_INVALID"),
        ("enqueue_attempt_count", -1, "RECEIPT_JOB_COUNTER_INVALID"),
        ("expires_at", 0, "RECEIPT_JOB_EXPIRY_INVALID"),
        ("expires_at", True, "RECEIPT_JOB_EXPIRY_INVALID"),
        ("customer_number", "", "RECEIPT_JOB_ROUTING_INVALID"),
        ("sender_id", " ", "RECEIPT_JOB_ROUTING_INVALID"),
        ("conversation_id", None, "RECEIPT_JOB_ROUTING_INVALID"),
        ("request_id", "", "RECEIPT_JOB_ROUTING_INVALID"),
        ("created_at", "", "RECEIPT_JOB_TIMESTAMP_INVALID"),
        ("updated_at", None, "RECEIPT_JOB_TIMESTAMP_INVALID"),
        ("GSI1PK", "PRIVATE", "RECEIPT_JOB_OUTBOX_INVALID"),
        ("GSI1SK", True, "RECEIPT_JOB_OUTBOX_INVALID"),
    ],
)
def test_receipt_job_record_validation_rejects_invalid_fields(field, value, error):
    record = receipt_record()
    record[field] = value

    with pytest.raises(ValueError, match=error):
        validate_receipt_job_record(record)


def test_receipt_job_record_rejects_snapshot_and_malformed_lease_pairs():
    with_snapshot = receipt_record(receipt_snapshot={"private": "order"})
    with pytest.raises(ValueError, match="RECEIPT_JOB_RECORD_INVALID"):
        validate_receipt_job_record(with_snapshot)

    incomplete_lease = deepcopy(receipt_record())
    incomplete_lease["lease_owner"] = "worker"
    with pytest.raises(ValueError, match="RECEIPT_JOB_LEASE_INVALID"):
        validate_receipt_job_record(incomplete_lease)


def test_receipt_job_record_requires_identity_to_match_order_and_version():
    record = receipt_record()
    record["order_id"] = "ORD-DIFFERENT"

    with pytest.raises(ValueError, match="RECEIPT_JOB_IDENTITY_INVALID"):
        validate_receipt_job_record(record)
