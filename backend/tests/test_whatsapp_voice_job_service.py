from copy import deepcopy

import pytest

from src.api.whatsapp import WhatsAppInboundAudioMessage
from src.models.whatsapp_voice_job import VoiceJobState, voice_job_id
from src.services.whatsapp_voice_job_service import WhatsAppVoiceJobService
from test_config import make_test_settings


class MemoryJobs:
    def __init__(self):
        self.records = {}
        self.due = []
        self.receipt_calls = []

    def create_if_absent(self, record):
        if record["job_id"] in self.records:
            return False
        self.records[record["job_id"]] = deepcopy(record)
        return True

    def get(self, job_id):
        return deepcopy(self.records.get(job_id))

    def transition(self, job_id, *, expected_states, expected_version, next_state, updated_at, values=None, remove=()):
        record = self.records[job_id]
        assert record["state"] in expected_states
        assert record["version"] == expected_version
        record.update(values or {})
        for field in remove:
            record.pop(field, None)
        record.update(state=next_state, version=expected_version + 1, updated_at=updated_at)
        return deepcopy(record)

    def query_due(self, **_kwargs):
        return [deepcopy(self.records[job_id]) for job_id in self.due]

    def checkpoint_receipt_pending(
        self,
        job_id,
        *,
        expected_version,
        updated_at,
    ):
        self.receipt_calls.append((
            "checkpoint",
            job_id,
            expected_version,
            updated_at,
        ))
        record = self.records[job_id]
        assert record["state"] == VoiceJobState.OUTBOUND_SENDING.value
        assert record["version"] == expected_version
        record["receipt_activation_state"] = "pending"
        record["version"] += 1
        record["updated_at"] = updated_at
        return deepcopy(record)

    def transition_receipt_activation(
        self,
        job_id,
        *,
        expected_version,
        next_state,
        updated_at,
    ):
        self.receipt_calls.append((
            "transition",
            job_id,
            expected_version,
            next_state,
            updated_at,
        ))
        record = self.records[job_id]
        assert record["state"] == VoiceJobState.OUTBOUND_SENDING.value
        assert record["receipt_activation_state"] == "pending"
        assert record["version"] == expected_version
        record["receipt_activation_state"] = next_state
        record["version"] += 1
        record["updated_at"] = updated_at
        return deepcopy(record)


class FakeQueue:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.sent = []

    def send(self, job_id):
        if self.fail:
            raise RuntimeError("private queue error")
        self.sent.append(job_id)


def inbound():
    return WhatsAppInboundAudioMessage(
        customer_number="+15550100000", customer_name="Private Name",
        sender_id="sender-private", message_id="provider-private-message-id",
        audio_id="media-private-id", media_url="https://media.example.test/private",
    )


def settings():
    return make_test_settings(voice_job_ttl_hours=24)


def test_create_is_deterministic_deduplicated_and_excludes_forbidden_fields():
    repository, queue = MemoryJobs(), FakeQueue()
    service = WhatsAppVoiceJobService(repository, queue, settings())

    first = service.submit_audio(inbound())
    second = service.submit_audio(inbound())

    assert first.job_id == voice_job_id("provider-private-message-id")
    assert first.queued is True and first.duplicate is False
    assert second.duplicate is True
    assert queue.sent == [first.job_id]
    record = repository.records[first.job_id]
    assert record["state"] == VoiceJobState.QUEUED.value
    assert record["audio_id"] == "media-private-id"
    forbidden = {"customer_name", "webhook_payload", "provider_message_id", "transcript"}
    assert forbidden.isdisjoint(record)


def test_missing_audio_id_is_rejected_before_persistence():
    repository, queue = MemoryJobs(), FakeQueue()
    service = WhatsAppVoiceJobService(repository, queue, settings())
    message = WhatsAppInboundAudioMessage(
        customer_number="private-customer",
        customer_name=None,
        sender_id="private-sender",
        message_id="private-message",
        audio_id=None,
        media_url="https://media.example.test/private",
    )

    with pytest.raises(ValueError, match="VOICE_AUDIO_FIELDS_REQUIRED"):
        service.submit_audio(message)

    assert repository.records == {}
    assert queue.sent == []


def test_enqueue_failure_keeps_durable_retryable_job_and_outbox_recovers():
    repository, queue = MemoryJobs(), FakeQueue(fail=True)
    service = WhatsAppVoiceJobService(repository, queue, settings())

    result = service.submit_audio(inbound())
    record = repository.records[result.job_id]

    assert result.accepted is True and result.queued is False
    assert record["state"] == VoiceJobState.RETRYABLE_FAILURE.value
    assert record["GSI1PK"] == "VOICE_OUTBOX"
    repository.due = [result.job_id]
    queue.fail = False
    assert service.recover_outbox() == 1
    assert repository.records[result.job_id]["state"] == VoiceJobState.QUEUED.value


def outbound_record(**overrides):
    record = {
        "job_id": "wv1_" + "c" * 64,
        "state": VoiceJobState.OUTBOUND_SENDING.value,
        "version": 7,
        "submitted_order_id": "ORD-123",
    }
    record.update(overrides)
    return record


def test_receipt_service_wrappers_use_record_version_clock_and_keep_main_state(
    monkeypatch,
):
    repository = MemoryJobs()
    record = outbound_record()
    repository.records[record["job_id"]] = deepcopy(record)
    service = WhatsAppVoiceJobService(repository, FakeQueue(), settings())
    fixed = service._now().replace(microsecond=0)
    monkeypatch.setattr(service, "_now", lambda: fixed)

    pending = service.checkpoint_receipt_pending(record)
    completed = service.complete_receipt_activation(pending)

    assert repository.receipt_calls == [
        ("checkpoint", record["job_id"], 7, fixed.isoformat()),
        ("transition", record["job_id"], 8, "completed", fixed.isoformat()),
    ]
    assert pending["state"] == VoiceJobState.OUTBOUND_SENDING.value
    assert pending["receipt_activation_state"] == "pending"
    assert completed["state"] == VoiceJobState.OUTBOUND_SENDING.value
    assert completed["receipt_activation_state"] == "completed"
    assert completed["version"] == 9


def test_receipt_manual_review_wrapper_keeps_main_state(monkeypatch):
    repository = MemoryJobs()
    record = outbound_record(receipt_activation_state="pending", version=8)
    repository.records[record["job_id"]] = deepcopy(record)
    service = WhatsAppVoiceJobService(repository, FakeQueue(), settings())
    fixed = service._now().replace(microsecond=0)
    monkeypatch.setattr(service, "_now", lambda: fixed)

    reviewed = service.mark_receipt_manual_review(record)

    assert reviewed["state"] == VoiceJobState.OUTBOUND_SENDING.value
    assert reviewed["receipt_activation_state"] == "manual_review"
    assert repository.receipt_calls == [(
        "transition",
        record["job_id"],
        8,
        "manual_review",
        fixed.isoformat(),
    )]


@pytest.mark.parametrize(
    ("method", "record"),
    [
        ("checkpoint_receipt_pending", outbound_record(state="response_ready")),
        (
            "checkpoint_receipt_pending",
            outbound_record(receipt_activation_state="completed"),
        ),
        (
            "complete_receipt_activation",
            outbound_record(receipt_activation_state="completed"),
        ),
        (
            "mark_receipt_manual_review",
            outbound_record(state="completed", receipt_activation_state="pending"),
        ),
    ],
)
def test_unsupported_receipt_service_transitions_fail_before_repository(
    method,
    record,
):
    repository = MemoryJobs()
    repository.records[record["job_id"]] = deepcopy(record)
    service = WhatsAppVoiceJobService(repository, FakeQueue(), settings())

    with pytest.raises(ValueError, match="VOICE_RECEIPT"):
        getattr(service, method)(record)

    assert repository.receipt_calls == []
