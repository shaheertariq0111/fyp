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
