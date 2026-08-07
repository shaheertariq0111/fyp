from copy import deepcopy

import pytest

from src.models.whatsapp_voice_job import validate_voice_job_record


def voice_record(**overrides):
    job_id = "wv1_" + "a" * 64
    record = {
        "PK": f"JOB#{job_id}",
        "SK": "METADATA",
        "job_id": job_id,
        "state": "outbound_sending",
        "version": 7,
        "audio_id": "audio-private",
        "media_url": "https://media.example.test/private",
        "customer_number": "+15550100000",
        "sender_id": "sender-private",
        "conversation_identity_hash": "b" * 64,
        "attempt_count": 1,
        "enqueue_attempt_count": 1,
        "created_at": "2026-08-08T00:00:00+00:00",
        "updated_at": "2026-08-08T00:00:00+00:00",
        "expires_at": 1786060800,
    }
    record.update(overrides)
    return record


def test_legacy_voice_record_without_receipt_fields_remains_valid():
    record = voice_record()

    validate_voice_job_record(record)

    assert "submitted_order_id" not in record
    assert "receipt_activation_state" not in record


def test_canonical_submitted_order_id_is_valid_without_receipt_state():
    record = voice_record(submitted_order_id="ORD-123")

    validate_voice_job_record(record)

    assert record["submitted_order_id"] == "ORD-123"


@pytest.mark.parametrize("order_id", ["", "   ", " ORD-123 ", None, 123])
def test_invalid_submitted_order_id_is_rejected(order_id):
    with pytest.raises(ValueError, match="VOICE_SUBMITTED_ORDER_ID_INVALID"):
        validate_voice_job_record(voice_record(submitted_order_id=order_id))


@pytest.mark.parametrize("state", ["pending", "completed", "manual_review"])
def test_supported_receipt_activation_states_are_valid(state):
    record = voice_record(
        submitted_order_id="ORD-123",
        receipt_activation_state=state,
    )

    validate_voice_job_record(record)

    assert record["state"] == "outbound_sending"
    assert record["receipt_activation_state"] == state


@pytest.mark.parametrize("state", ["", "queued", "retryable_failure", None, 1, {}])
def test_invalid_receipt_activation_state_is_rejected(state):
    with pytest.raises(ValueError, match="VOICE_RECEIPT_ACTIVATION_INVALID"):
        validate_voice_job_record(
            voice_record(
                submitted_order_id="ORD-123",
                receipt_activation_state=state,
            )
        )


def test_receipt_activation_state_requires_submitted_order_id():
    record = deepcopy(voice_record())
    record["receipt_activation_state"] = "pending"

    with pytest.raises(ValueError, match="VOICE_RECEIPT_ACTIVATION_INVALID"):
        validate_voice_job_record(record)
