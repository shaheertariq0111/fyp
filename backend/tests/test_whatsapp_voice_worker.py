from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from src.models.whatsapp_voice_job import VoiceQueueMessage, voice_job_id
from src.repositories.base import to_dynamodb
from src.repositories.whatsapp_voice_job_repository import WhatsAppVoiceJobRepository
from src.services.whatsapp_voice_service import WhatsAppVoiceProcessingError
from src.services.whatsapp_voice_service import WhatsAppVoiceService
from src.services.whatsapp_conversation_service import (
    WhatsAppConversationReply,
    WhatsAppDeliveryOutcome,
)
from src.services.whatsapp_voice_reply_service import VoiceReplyOutcome
from src.workers.whatsapp_voice_worker import VoiceWorkerHeartbeat, WhatsAppVoiceWorker


class LeaseJobs:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def extend_lease(self, job_id, owner):
        self.calls.append((job_id, owner))
        return next(self.results)


class Queue:
    def __init__(self):
        self.receipts = []

    def change_visibility(self, receipt):
        self.receipts.append(receipt)


def test_worker_heartbeat_extends_lease_and_sqs_visibility_without_exposing_receipt():
    jobs, queue = LeaseJobs([True]), Queue()
    heartbeat = VoiceWorkerHeartbeat(
        jobs=jobs, queue=queue, job_id=voice_job_id("provider-id"),
        owner="worker-opaque", receipt_handle="private-receipt", interval_seconds=1,
    )
    heartbeat._run = lambda: None
    assert heartbeat.job_id.startswith("wv1_")
    assert not hasattr(heartbeat, "message_body")


def test_heartbeat_loss_blocks_irreversible_actions():
    heartbeat = VoiceWorkerHeartbeat(
        jobs=LeaseJobs([]), queue=Queue(), job_id=voice_job_id("provider-id"),
        owner="worker", receipt_handle="receipt", interval_seconds=1,
    )
    heartbeat.lost_event.set()
    with pytest.raises(RuntimeError, match="VOICE_WORKER_HEARTBEAT_LOST"):
        heartbeat.assert_owned()


def test_worker_module_queue_schema_has_no_sensitive_fields():
    job_id = voice_job_id("raw-provider-id")
    body = VoiceQueueMessage(job_id).serialize()
    assert set(__import__("json").loads(body)) == {"v", "kind", "job_id"}
    assert "raw-provider-id" not in body


class ProcessingJobs:
    def transition(self, record, next_state, **_kwargs):
        return {**record, "state": next_state, "version": record["version"] + 1}


class OwnedHeartbeat:
    def assert_owned(self):
        return None


class CapturingVoice:
    def __init__(self):
        self.messages = []

    def transcribe(self, message):
        self.messages.append(message)
        if not message.audio_id:
            raise WhatsAppVoiceProcessingError(
                error_code="AGENTFLO_MEDIA_AUDIO_ID_REQUIRED",
                retryable=False,
            )
        raise WhatsAppVoiceProcessingError(
            error_code="AGENTFLO_MEDIA_NOT_FOUND",
            retryable=False,
        )


def make_processing_worker(voice):
    agent_requests = SimpleNamespace(get=lambda _request_id: None)
    conversations = SimpleNamespace(
        services_provider=lambda: SimpleNamespace(agent_requests=agent_requests)
    )
    return WhatsAppVoiceWorker(
        settings=SimpleNamespace(),
        jobs=ProcessingJobs(),
        queue=SimpleNamespace(),
        voice=voice,
        conversations=conversations,
    )


def test_worker_passes_audio_id_and_does_not_use_lookaside_url_as_identifier():
    voice = CapturingVoice()
    worker = make_processing_worker(voice)
    record = {
        "job_id": voice_job_id("provider-id"),
        "state": "queued",
        "version": 1,
        "customer_number": "private-customer",
        "sender_id": "private-sender",
        "audio_id": "agentflo-audio-id",
        "media_url": "https://lookaside.example.test/private?mid=forbidden",
    }

    with pytest.raises(WhatsAppVoiceProcessingError) as error:
        worker._process(record, OwnedHeartbeat())

    assert error.value.error_code == "AGENTFLO_MEDIA_NOT_FOUND"
    assert voice.messages[0].audio_id == "agentflo-audio-id"


def test_worker_legacy_job_without_audio_id_fails_terminally():
    job_id = voice_job_id("legacy-provider-id")
    legacy_dynamo_record = {
        "PK": f"JOB#{job_id}",
        "SK": "METADATA",
        "job_id": job_id,
        "state": "queued",
        "version": 1,
        "media_url": "https://lookaside.example.test/private?mid=forbidden",
        "customer_number": "+15550100000",
        "sender_id": "sender-private",
        "conversation_identity_hash": "c" * 64,
        "attempt_count": 0,
        "enqueue_attempt_count": 1,
        "created_at": "2026-08-05T00:00:00+00:00",
        "updated_at": "2026-08-05T00:00:00+00:00",
        "expires_at": 1785974400,
    }

    class LegacyTable:
        def get_item(self, **kwargs):
            assert kwargs == {
                "Key": {"PK": f"JOB#{job_id}", "SK": "METADATA"},
                "ConsistentRead": True,
            }
            return {"Item": to_dynamodb(legacy_dynamo_record)}

    class LegacyDynamo:
        def Table(self, table_name):
            assert table_name == "voice-jobs"
            return LegacyTable()

    repository = WhatsAppVoiceJobRepository(LegacyDynamo(), "voice-jobs")
    record = repository.get(job_id)
    assert record is not None
    assert "audio_id" not in record

    class MustNotDownload:
        def download_media(self, *_args, **_kwargs):
            raise AssertionError("legacy media_url must not be used as a fallback")

    voice = WhatsAppVoiceService(
        media_service=MustNotDownload(),
        storage_service=SimpleNamespace(),
        transcription_service=SimpleNamespace(),
        transcription_job_prefix="fyp-dev-whatsapp-voice-",
        language_code="en-US",
        identify_language=False,
        transcription_timeout_seconds=180,
    )
    worker = make_processing_worker(voice)

    with pytest.raises(WhatsAppVoiceProcessingError) as error:
        worker._process(record, OwnedHeartbeat())

    assert error.value.error_code == "AGENTFLO_MEDIA_AUDIO_ID_REQUIRED"
    assert error.value.retryable is False


def test_job_scoped_aws_access_denied_is_logged_and_does_not_escape(caplog):
    job_id = voice_job_id("access-denied-provider-id")
    error = ClientError(
        {
            "Error": {
                "Code": "AccessDeniedException",
                "Message": "private infrastructure detail",
            }
        },
        "Scan",
    )
    worker = WhatsAppVoiceWorker(
        settings=SimpleNamespace(),
        jobs=SimpleNamespace(
            repository=SimpleNamespace(get=lambda _job_id: (_ for _ in ()).throw(error))
        ),
        queue=SimpleNamespace(),
        voice=SimpleNamespace(),
        conversations=SimpleNamespace(),
    )
    received = SimpleNamespace(
        queue_message=SimpleNamespace(job_id=job_id),
        receive_count=1,
    )

    with caplog.at_level("ERROR"):
        worker.handle(received)

    record = next(
        item
        for item in caplog.records
        if getattr(item, "event", None) == "voice_worker_aws_operation_failed"
    )
    assert record.voice_job_id == job_id
    assert record.error_code == "VOICE_WORKER_AWS_OPERATION_FAILED"
    assert record.aws_error_code == "AccessDeniedException"
    assert record.aws_operation == "Scan"
    assert record.retryable is True
    assert "private infrastructure detail" not in caplog.text


def test_job_scoped_programming_error_still_terminates_worker_turn():
    worker = WhatsAppVoiceWorker(
        settings=SimpleNamespace(),
        jobs=SimpleNamespace(
            repository=SimpleNamespace(
                get=lambda _job_id: (_ for _ in ()).throw(
                    RuntimeError("programming defect")
                )
            )
        ),
        queue=SimpleNamespace(),
        voice=SimpleNamespace(),
        conversations=SimpleNamespace(),
    )
    received = SimpleNamespace(
        queue_message=SimpleNamespace(job_id=voice_job_id("programming-error")),
        receive_count=1,
    )

    with pytest.raises(RuntimeError, match="programming defect"):
        worker.handle(received)


class RecordingTransitions:
    def __init__(self):
        self.calls = []

    def transition(self, record, next_state, **kwargs):
        self.calls.append((next_state, kwargs))
        return {
            **record,
            **kwargs.get("values", {}),
            "state": next_state,
            "version": record["version"] + 1,
        }


class TextDelivery:
    def __init__(self, events, status="sent"):
        self.events = events
        self.status = status

    def deliver(self, *_args, **_kwargs):
        self.events.append("text")
        return WhatsAppDeliveryOutcome(self.status, {"sent": self.status == "sent"})


class OptionalVoiceDelivery:
    def __init__(self, events, outcome):
        self.events = events
        self.outcome = outcome
        self.calls = []

    def deliver(self, **kwargs):
        self.events.append("audio")
        self.calls.append(kwargs)
        return self.outcome


def _ready_worker(*, enabled, text_status="sent", voice_outcome=None):
    events = []
    jobs = RecordingTransitions()
    voice_replies = OptionalVoiceDelivery(
        events,
        voice_outcome or VoiceReplyOutcome("sent", generated_audio_bytes=128),
    )
    worker = WhatsAppVoiceWorker(
        settings=SimpleNamespace(whatsapp_voice_reply_enabled=enabled),
        jobs=jobs,
        queue=SimpleNamespace(),
        voice=SimpleNamespace(),
        conversations=TextDelivery(events, text_status),
        voice_replies=voice_replies,
    )
    record = {
        "job_id": voice_job_id("ready-provider-id"),
        "state": "response_ready",
        "version": 1,
        "customer_number": "+10000000000",
        "sender_id": "sender-safe",
    }
    inbound = SimpleNamespace(
        customer_number=record["customer_number"],
        sender_id=record["sender_id"],
        message_id=record["job_id"],
    )
    reply = WhatsAppConversationReply(
        "private final text",
        "request-safe",
        "session-safe",
        "customer-safe",
    )
    return worker, jobs, voice_replies, events, record, inbound, reply


def test_voice_input_delivers_text_first_then_optional_audio_and_completes():
    worker, jobs, voice_replies, events, record, inbound, reply = _ready_worker(
        enabled=True
    )
    assert worker._send_ready(
        record,
        OwnedHeartbeat(),
        inbound=inbound,
        reply=reply,
    ) is True
    assert events == ["text", "audio"]
    assert len(voice_replies.calls) == 1
    assert [state for state, _kwargs in jobs.calls] == [
        "outbound_sending",
        "completed",
    ]
    assert jobs.calls[-1][1]["values"] == {
        "voice_reply_status": "sent",
        "voice_reply_audio_bytes": 128,
    }


def test_disabled_audio_reply_preserves_text_only_behavior():
    worker, jobs, voice_replies, events, record, inbound, reply = _ready_worker(
        enabled=False
    )
    assert worker._send_ready(
        record,
        OwnedHeartbeat(),
        inbound=inbound,
        reply=reply,
    ) is True
    assert events == ["text"]
    assert voice_replies.calls == []
    assert jobs.calls[-1][1]["values"] == {"voice_reply_status": "disabled"}


@pytest.mark.parametrize("audio_status", ["failed", "ambiguous"])
def test_optional_audio_failure_never_undoes_text_or_replays_agent(audio_status):
    outcome = VoiceReplyOutcome(
        audio_status,
        error_code="VOICE_REPLY_OUTCOME_AMBIGUOUS",
    )
    worker, jobs, voice_replies, events, record, inbound, reply = _ready_worker(
        enabled=True,
        voice_outcome=outcome,
    )
    assert worker._send_ready(
        record,
        OwnedHeartbeat(),
        inbound=inbound,
        reply=reply,
    ) is True
    assert events == ["text", "audio"]
    assert len(voice_replies.calls) == 1
    assert jobs.calls[-1][0] == "completed"
    assert jobs.calls[-1][1]["values"] == {
        "voice_reply_status": audio_status,
        "voice_reply_error_code": "VOICE_REPLY_OUTCOME_AMBIGUOUS",
    }


def test_optional_audio_is_not_attempted_until_primary_text_is_sent():
    worker, jobs, voice_replies, events, record, inbound, reply = _ready_worker(
        enabled=True,
        text_status="retryable_failure",
    )
    assert worker._send_ready(
        record,
        OwnedHeartbeat(),
        inbound=inbound,
        reply=reply,
    ) is False
    assert events == ["text"]
    assert voice_replies.calls == []
    assert [state for state, _kwargs in jobs.calls] == [
        "outbound_sending",
        "response_ready",
    ]
