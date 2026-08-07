from copy import deepcopy
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from src.models.whatsapp_voice_job import VoiceQueueMessage, voice_job_id
from src.repositories.base import to_dynamodb
from src.repositories.whatsapp_voice_job_repository import (
    VoiceJobConditionFailed,
    WhatsAppVoiceJobRepository,
)
from src.services.whatsapp_voice_service import WhatsAppVoiceProcessingError
from src.services.whatsapp_voice_service import WhatsAppVoiceService
from src.services.whatsapp_conversation_service import (
    WhatsAppConversationReply,
    WhatsAppDeliveryOutcome,
)
from src.services.whatsapp_voice_reply_service import VoiceReplyOutcome
from src.services.whatsapp_voice_receipt_activation_service import (
    WhatsAppVoiceReceiptActivationResult,
)
from src.workers import whatsapp_voice_worker as worker_module
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


class DurableReceiptJobs:
    def __init__(self, events, record):
        self.events = events
        self.record = deepcopy(record)
        self.calls = []
        self.repository = SimpleNamespace(get=self.get)

    def get(self, _job_id):
        return deepcopy(self.record)

    def transition(self, record, next_state, **kwargs):
        self.events.append(f"transition:{next_state}")
        self.calls.append((next_state, deepcopy(kwargs)))
        self.record = {
            **record,
            **(kwargs.get("values") or {}),
            "state": next_state,
            "version": record["version"] + 1,
        }
        return deepcopy(self.record)

    def checkpoint_receipt_pending(self, record):
        self.events.append("receipt_checkpoint")
        self.record = {
            **record,
            "receipt_activation_state": "pending",
            "version": record["version"] + 1,
        }
        return deepcopy(self.record)

    @staticmethod
    def is_terminal(record):
        return record.get("state") in {"completed", "manual_review"}


class CheckpointConflictJobs(DurableReceiptJobs):
    def __init__(self, events, record, latest):
        super().__init__(events, record)
        self.latest = deepcopy(latest)

    def checkpoint_receipt_pending(self, _record):
        self.events.append("receipt_checkpoint")
        self.record = deepcopy(self.latest)
        raise VoiceJobConditionFailed("conflict")


class CheckpointAwsFailureJobs(DurableReceiptJobs):
    def checkpoint_receipt_pending(self, _record):
        self.events.append("receipt_checkpoint")
        raise ClientError(
            {
                "Error": {
                    "Code": "InternalError",
                    "Message": "private backend detail",
                }
            },
            "UpdateItem",
        )


class ReceiptActivation:
    def __init__(self, events, jobs, outcomes):
        self.events = events
        self.jobs = jobs
        self.outcomes = list(outcomes)
        self.calls = []

    def activate_pending(self, record):
        self.events.append("receipt_activation")
        self.calls.append(deepcopy(record))
        status = self.outcomes.pop(0)
        if status == "activated":
            self.jobs.record = {
                **record,
                "receipt_activation_state": "completed",
                "version": record["version"] + 1,
            }
        elif status == "manual_review":
            self.jobs.record = {
                **record,
                "receipt_activation_state": "manual_review",
                "version": record["version"] + 1,
            }
        return WhatsAppVoiceReceiptActivationResult(
            status,
            status == "retryable_failure",
        )


class ProvenTextDelivery:
    def __init__(self, events, outcome):
        self.events = events
        self.outcome = outcome
        self.calls = []

    def deliver(self, *_args, **kwargs):
        self.events.append("text_deliver")
        self.calls.append(kwargs)
        return self.outcome


class SequencedVoiceDelivery(OptionalVoiceDelivery):
    def deliver(self, **kwargs):
        self.events.append("optional_audio")
        self.calls.append(kwargs)
        return self.outcome


def receipt_ready_record(**overrides):
    record = {
        "job_id": voice_job_id("receipt-ready-provider-id"),
        "state": "response_ready",
        "version": 4,
        "customer_number": "+15550100000",
        "sender_id": "sender-safe",
        "customer_id": "customer-safe",
        "session_id": "session-safe",
        "request_id": "request-safe",
        "submitted_order_id": "ORD-VOICE-1",
    }
    record.update(overrides)
    return record


def receipt_ready_worker(
    *,
    delivery=None,
    activation_outcomes=("activated",),
    voice_enabled=True,
    record=None,
):
    events = []
    record = record or receipt_ready_record()
    jobs = DurableReceiptJobs(events, record)
    receipt_activation = ReceiptActivation(
        events,
        jobs,
        activation_outcomes,
    )
    voice_replies = SequencedVoiceDelivery(
        events,
        VoiceReplyOutcome("sent", provider_message_id="wamid-safe", generated_audio_bytes=64),
    )
    conversations = ProvenTextDelivery(
        events,
        delivery or WhatsAppDeliveryOutcome("sent", {"sent": True}),
    )
    worker = WhatsAppVoiceWorker(
        settings=SimpleNamespace(
            receipt_activation_enabled=True,
            whatsapp_voice_reply_enabled=voice_enabled,
        ),
        jobs=jobs,
        queue=SimpleNamespace(),
        voice=SimpleNamespace(),
        conversations=conversations,
        voice_replies=voice_replies,
        receipt_activation=receipt_activation,
    )
    inbound = SimpleNamespace(
        customer_number=record["customer_number"],
        sender_id=record["sender_id"],
        message_id=record["job_id"],
    )
    reply = WhatsAppConversationReply(
        "private order confirmation",
        record["request_id"],
        record["session_id"],
        record["customer_id"],
        submitted_order_id=record.get("submitted_order_id"),
    )
    return (
        worker,
        jobs,
        receipt_activation,
        voice_replies,
        conversations,
        events,
        record,
        inbound,
        reply,
    )


def test_fresh_order_sequences_text_checkpoint_receipt_audio_and_completion():
    (
        worker,
        jobs,
        receipt_activation,
        voice_replies,
        _conversations,
        events,
        record,
        inbound,
        reply,
    ) = receipt_ready_worker()

    assert worker._send_ready(
        record,
        OwnedHeartbeat(),
        inbound=inbound,
        reply=reply,
    ) is True

    assert events == [
        "transition:outbound_sending",
        "text_deliver",
        "receipt_checkpoint",
        "receipt_activation",
        "optional_audio",
        "transition:completed",
    ]
    assert len(receipt_activation.calls) == 1
    assert len(voice_replies.calls) == 1
    assert jobs.record["receipt_activation_state"] == "completed"
    assert jobs.record["voice_reply_provider_message_id"] == "wamid-safe"
    assert jobs.record["voice_reply_audio_bytes"] == 64


@pytest.mark.parametrize(
    ("latest_receipt_state", "activation_calls"),
    [("pending", 1), ("completed", 0), ("manual_review", 0)],
)
def test_checkpoint_conflict_reloads_and_classifies_durable_receipt_state(
    latest_receipt_state,
    activation_calls,
):
    events = []
    record = receipt_ready_record()
    latest = {
        **record,
        "state": "outbound_sending",
        "version": record["version"] + 2,
        "receipt_activation_state": latest_receipt_state,
    }
    jobs = CheckpointConflictJobs(events, record, latest)
    receipt_activation = ReceiptActivation(events, jobs, ("activated",))
    voice_replies = SequencedVoiceDelivery(
        events,
        VoiceReplyOutcome("sent", generated_audio_bytes=64),
    )
    conversations = ProvenTextDelivery(
        events,
        WhatsAppDeliveryOutcome("sent", {"sent": True}),
    )
    worker = WhatsAppVoiceWorker(
        settings=SimpleNamespace(
            receipt_activation_enabled=True,
            whatsapp_voice_reply_enabled=True,
        ),
        jobs=jobs,
        queue=SimpleNamespace(),
        voice=SimpleNamespace(),
        conversations=conversations,
        voice_replies=voice_replies,
        receipt_activation=receipt_activation,
    )
    inbound = SimpleNamespace(
        customer_number=record["customer_number"],
        sender_id=record["sender_id"],
        message_id=record["job_id"],
    )
    reply = WhatsAppConversationReply(
        "private confirmation",
        record["request_id"],
        record["session_id"],
        record["customer_id"],
        submitted_order_id=record["submitted_order_id"],
    )

    assert worker._send_ready(
        record,
        OwnedHeartbeat(),
        inbound=inbound,
        reply=reply,
    ) is True
    assert len(receipt_activation.calls) == activation_calls
    assert voice_replies.calls == []
    assert jobs.record["state"] == "completed"


def test_checkpoint_conflict_without_receipt_proof_uses_manual_review():
    events = []
    record = receipt_ready_record()
    latest = {
        **record,
        "state": "outbound_sending",
        "version": record["version"] + 2,
    }
    jobs = CheckpointConflictJobs(events, record, latest)
    receipt_activation = ReceiptActivation(events, jobs, ("activated",))
    worker = WhatsAppVoiceWorker(
        settings=SimpleNamespace(
            receipt_activation_enabled=True,
            whatsapp_voice_reply_enabled=True,
        ),
        jobs=jobs,
        queue=SimpleNamespace(),
        voice=SimpleNamespace(),
        conversations=ProvenTextDelivery(
            events,
            WhatsAppDeliveryOutcome("sent", {"sent": True}),
        ),
        voice_replies=SequencedVoiceDelivery(
            events,
            VoiceReplyOutcome("sent"),
        ),
        receipt_activation=receipt_activation,
    )
    inbound = SimpleNamespace(
        customer_number=record["customer_number"],
        sender_id=record["sender_id"],
        message_id=record["job_id"],
    )
    reply = WhatsAppConversationReply(
        "private confirmation",
        record["request_id"],
        record["session_id"],
        record["customer_id"],
        submitted_order_id=record["submitted_order_id"],
    )

    assert worker._send_ready(
        record,
        OwnedHeartbeat(),
        inbound=inbound,
        reply=reply,
    ) is True
    assert jobs.record["state"] == "manual_review"
    assert receipt_activation.calls == []
    assert "optional_audio" not in events


def test_checkpoint_aws_failure_never_activates_or_replays_definitely_sent_text():
    events = []
    record = receipt_ready_record()
    jobs = CheckpointAwsFailureJobs(events, record)
    receipt_activation = ReceiptActivation(events, jobs, ("activated",))
    conversations = ProvenTextDelivery(
        events,
        WhatsAppDeliveryOutcome("sent", {"sent": True}),
    )
    voice_replies = SequencedVoiceDelivery(
        events,
        VoiceReplyOutcome("sent"),
    )
    worker = WhatsAppVoiceWorker(
        settings=SimpleNamespace(
            receipt_activation_enabled=True,
            whatsapp_voice_reply_enabled=True,
        ),
        jobs=jobs,
        queue=SimpleNamespace(),
        voice=SimpleNamespace(),
        conversations=conversations,
        voice_replies=voice_replies,
        receipt_activation=receipt_activation,
    )
    inbound = SimpleNamespace(
        customer_number=record["customer_number"],
        sender_id=record["sender_id"],
        message_id=record["job_id"],
    )
    reply = WhatsAppConversationReply(
        "private confirmation",
        record["request_id"],
        record["session_id"],
        record["customer_id"],
        submitted_order_id=record["submitted_order_id"],
    )

    with pytest.raises(ClientError):
        worker._send_ready(
            record,
            OwnedHeartbeat(),
            inbound=inbound,
            reply=reply,
        )

    assert len(conversations.calls) == 1
    assert receipt_activation.calls == []
    assert voice_replies.calls == []
    assert jobs.record["state"] == "outbound_sending"
    assert "receipt_activation_state" not in jobs.record


@pytest.mark.parametrize(
    "delivery",
    [
        WhatsAppDeliveryOutcome("skipped", {"sent": False, "skipped": True}),
        WhatsAppDeliveryOutcome("retryable_failure", {"sent": False}),
        WhatsAppDeliveryOutcome("permanent_failure", {"sent": False}),
        WhatsAppDeliveryOutcome("ambiguous", {"sent": False}),
        WhatsAppDeliveryOutcome("sent", {"sent": False}),
        WhatsAppDeliveryOutcome("sent", {}),
    ],
)
def test_receipt_checkpoint_requires_exact_definite_text_proof(delivery):
    (
        worker,
        jobs,
        receipt_activation,
        _voice_replies,
        _conversations,
        events,
        record,
        inbound,
        reply,
    ) = receipt_ready_worker(delivery=delivery)

    worker._send_ready(
        record,
        OwnedHeartbeat(),
        inbound=inbound,
        reply=reply,
    )

    assert "receipt_checkpoint" not in events
    assert receipt_activation.calls == []
    assert "receipt_activation_state" not in jobs.record


def test_retryable_receipt_attempts_audio_once_and_leaves_main_state_pending():
    (
        worker,
        jobs,
        receipt_activation,
        voice_replies,
        conversations,
        events,
        record,
        inbound,
        reply,
    ) = receipt_ready_worker(
        activation_outcomes=("retryable_failure", "activated"),
    )

    assert worker._send_ready(
        record,
        OwnedHeartbeat(),
        inbound=inbound,
        reply=reply,
    ) is False
    assert jobs.record["state"] == "outbound_sending"
    assert jobs.record["receipt_activation_state"] == "pending"
    assert len(conversations.calls) == 1
    assert len(voice_replies.calls) == 1
    assert "transition:completed" not in events

    assert worker._process(jobs.get(record["job_id"]), OwnedHeartbeat()) is True
    assert len(conversations.calls) == 1
    assert len(receipt_activation.calls) == 2
    assert len(voice_replies.calls) == 1
    assert jobs.record["state"] == "completed"
    assert jobs.record["receipt_activation_state"] == "completed"
    assert jobs.record["voice_reply_status"] == "skipped"
    assert jobs.record["voice_reply_error_code"] == (
        "VOICE_REPLY_RECOVERY_NOT_REPLAYED"
    )
    assert "voice_reply_provider_message_id" not in jobs.record
    assert "voice_reply_audio_bytes" not in jobs.record


class ImmediateHeartbeat:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def assert_owned(self):
        pass


def test_retryable_recovery_is_nonterminal_and_queue_message_is_not_deleted(
    monkeypatch,
):
    events = []
    record = receipt_ready_record(
        state="outbound_sending",
        receipt_activation_state="pending",
    )
    jobs = DurableReceiptJobs(events, record)
    jobs.acquire_lease = lambda _job_id, _owner: jobs.get(record["job_id"])
    jobs.release_lease = lambda _job_id, _owner: True
    receipt_activation = ReceiptActivation(
        events,
        jobs,
        ("retryable_failure",),
    )
    deleted = []
    queue = SimpleNamespace(
        delete=lambda receipt_handle: deleted.append(receipt_handle)
    )
    worker = WhatsAppVoiceWorker(
        settings=SimpleNamespace(
            receipt_activation_enabled=True,
            whatsapp_voice_reply_enabled=True,
            voice_sqs_heartbeat_seconds=1,
        ),
        jobs=jobs,
        queue=queue,
        voice=SimpleNamespace(),
        conversations=SimpleNamespace(),
        voice_replies=SimpleNamespace(
            deliver=lambda **_kwargs: pytest.fail("audio must not replay")
        ),
        receipt_activation=receipt_activation,
    )
    monkeypatch.setattr(worker_module, "VoiceWorkerHeartbeat", ImmediateHeartbeat)
    received = SimpleNamespace(
        queue_message=SimpleNamespace(job_id=record["job_id"]),
        receipt_handle="receipt-safe",
        receive_count=2,
    )

    worker._handle(received)

    assert deleted == []
    assert jobs.record["state"] == "outbound_sending"
    assert jobs.record["receipt_activation_state"] == "pending"


@pytest.mark.parametrize(
    "crash_point",
    ["before_optional_audio", "after_audio_may_have_been_accepted"],
)
def test_audio_crash_recovery_never_replays_or_fabricates_audio(crash_point):
    record = receipt_ready_record(
        state="outbound_sending",
        receipt_activation_state="completed",
        crash_point=crash_point,
    )
    (
        worker,
        jobs,
        _receipt_activation,
        voice_replies,
        conversations,
        _events,
        _record,
        _inbound,
        _reply,
    ) = receipt_ready_worker(record=record)

    assert worker._process(record, OwnedHeartbeat()) is True
    assert conversations.calls == []
    assert voice_replies.calls == []
    assert jobs.record["voice_reply_status"] == "skipped"
    assert "voice_reply_provider_message_id" not in jobs.record
    assert "voice_reply_audio_bytes" not in jobs.record


def test_receipt_recovery_log_excludes_private_routing(caplog):
    record = receipt_ready_record(
        state="outbound_sending",
        receipt_activation_state="completed",
    )
    (
        worker,
        _jobs,
        _receipt_activation,
        _voice_replies,
        _conversations,
        _events,
        _record,
        _inbound,
        _reply,
    ) = receipt_ready_worker(record=record)

    with caplog.at_level("INFO"):
        assert worker._process(record, OwnedHeartbeat()) is True

    serialized = repr([vars(log_record) for log_record in caplog.records])
    for private in (
        record["submitted_order_id"],
        record["customer_number"],
        record["sender_id"],
        record["session_id"],
        record["request_id"],
    ):
        assert private not in serialized


class OwnershipHeartbeat:
    def __init__(self, events):
        self.events = events

    def assert_owned(self):
        self.events.append("heartbeat")


def test_heartbeat_is_asserted_before_receipt_audio_and_final_transition():
    (
        worker,
        jobs,
        _receipt_activation,
        _voice_replies,
        _conversations,
        events,
        record,
        inbound,
        reply,
    ) = receipt_ready_worker()

    assert worker._send_ready(
        record,
        OwnershipHeartbeat(events),
        inbound=inbound,
        reply=reply,
    ) is True

    for operation in (
        "receipt_checkpoint",
        "receipt_activation",
        "optional_audio",
        "transition:completed",
    ):
        index = events.index(operation)
        assert "heartbeat" in events[:index]
    completed_index = events.index("transition:completed")
    assert events[completed_index - 1] == "heartbeat"


@pytest.mark.parametrize("receipt_state", ["completed", "manual_review"])
def test_recovered_terminal_receipt_completes_without_text_agent_or_audio(
    receipt_state,
):
    record = receipt_ready_record(
        state="outbound_sending",
        receipt_activation_state=receipt_state,
    )
    (
        worker,
        jobs,
        receipt_activation,
        voice_replies,
        conversations,
        _events,
        _record,
        _inbound,
        _reply,
    ) = receipt_ready_worker(record=record)
    worker.conversations.invoke_prepared = lambda *_args, **_kwargs: (
        pytest.fail("AgentCore must not be replayed")
    )

    assert worker._process(record, OwnedHeartbeat()) is True
    assert conversations.calls == []
    assert receipt_activation.calls == []
    assert voice_replies.calls == []
    assert jobs.record["state"] == "completed"
    assert jobs.record["voice_reply_status"] == "skipped"
    assert "voice_reply_provider_message_id" not in jobs.record
    assert "voice_reply_audio_bytes" not in jobs.record


def test_submitted_order_id_without_receipt_state_keeps_ambiguous_policy():
    record = receipt_ready_record(state="outbound_sending")
    (
        worker,
        jobs,
        receipt_activation,
        voice_replies,
        conversations,
        _events,
        _record,
        _inbound,
        _reply,
    ) = receipt_ready_worker(record=record)

    assert worker._process(record, OwnedHeartbeat()) is True
    assert jobs.record["state"] == "manual_review"
    assert receipt_activation.calls == []
    assert conversations.calls == []
    assert voice_replies.calls == []


def test_feature_disabled_preserves_outbound_ambiguity_even_with_receipt_fields():
    record = receipt_ready_record(
        state="outbound_sending",
        receipt_activation_state="pending",
    )
    events = []
    jobs = DurableReceiptJobs(events, record)
    worker = WhatsAppVoiceWorker(
        settings=SimpleNamespace(receipt_activation_enabled=False),
        jobs=jobs,
        queue=SimpleNamespace(),
        voice=SimpleNamespace(),
        conversations=SimpleNamespace(),
    )

    assert worker._process(record, OwnedHeartbeat()) is True
    assert jobs.record["state"] == "manual_review"


def test_feature_enabled_non_order_keeps_normal_text_audio_completion():
    record = receipt_ready_record()
    record.pop("submitted_order_id")
    (
        worker,
        jobs,
        receipt_activation,
        voice_replies,
        _conversations,
        events,
        _record,
        inbound,
        _reply,
    ) = receipt_ready_worker(record=record)
    reply = WhatsAppConversationReply(
        "private chat reply",
        record["request_id"],
        record["session_id"],
        record["customer_id"],
    )

    assert worker._send_ready(
        record,
        OwnedHeartbeat(),
        inbound=inbound,
        reply=reply,
    ) is True
    assert "receipt_checkpoint" not in events
    assert receipt_activation.calls == []
    assert len(voice_replies.calls) == 1
    assert jobs.record["state"] == "completed"


class InvokingConversation:
    def __init__(self, reply):
        self.reply = reply
        self.invoke_calls = 0
        self.services = SimpleNamespace(
            agent_requests=SimpleNamespace(
                get=lambda _request_id: {"message": "private transcript"}
            )
        )

    def services_provider(self):
        return self.services

    def prepare_text(self, *_args, **_kwargs):
        request = SimpleNamespace(
            record={"request_id": "request-safe"},
            context=SimpleNamespace(
                customer_id="customer-safe",
                agent_session_id="session-safe",
            ),
        )
        return SimpleNamespace(prepared_agent_request=request)

    def invoke_prepared(self, *_args, **_kwargs):
        self.invoke_calls += 1
        return self.reply


@pytest.mark.parametrize(
    ("feature_enabled", "submitted_order_id", "expected_persisted"),
    [
        (True, "ORD-VOICE-1", True),
        (True, None, False),
        (False, "ORD-VOICE-1", False),
    ],
)
def test_authoritative_submission_signal_persistence_is_feature_gated(
    feature_enabled,
    submitted_order_id,
    expected_persisted,
):
    events = []
    record = receipt_ready_record(state="processing", version=1)
    record.pop("submitted_order_id")
    jobs = DurableReceiptJobs(events, record)
    reply = WhatsAppConversationReply(
        "private response",
        "request-safe",
        "session-safe",
        "customer-safe",
        submitted_order_id=submitted_order_id,
    )
    conversations = InvokingConversation(reply)
    conversations.deliver = lambda *_args, **_kwargs: WhatsAppDeliveryOutcome(
        "retryable_failure",
        {"sent": False},
    )
    receipt_activation = (
        ReceiptActivation(events, jobs, ("activated",))
        if feature_enabled
        else None
    )
    worker = WhatsAppVoiceWorker(
        settings=SimpleNamespace(
            receipt_activation_enabled=feature_enabled,
            whatsapp_voice_reply_enabled=False,
        ),
        jobs=jobs,
        queue=SimpleNamespace(),
        voice=SimpleNamespace(
            transcribe=lambda *_args: pytest.fail(
                "persisted transcript must be reused"
            )
        ),
        conversations=conversations,
        receipt_activation=receipt_activation,
    )

    assert worker._process(record, OwnedHeartbeat()) is False
    response_ready = next(
        kwargs
        for state, kwargs in jobs.calls
        if state == "response_ready"
    )
    if expected_persisted:
        assert response_ready["values"] == {
            "submitted_order_id": "ORD-VOICE-1"
        }
    else:
        assert response_ready.get("values") is None


def test_resumed_response_ready_uses_durable_order_id_without_agent_replay():
    (
        worker,
        jobs,
        receipt_activation,
        _voice_replies,
        conversations,
        _events,
        record,
        _inbound,
        _reply,
    ) = receipt_ready_worker(voice_enabled=False)
    conversations.services_provider = lambda: SimpleNamespace(
        agent_requests=SimpleNamespace(
            get=lambda _request_id: {
                "response": {"text": "private cached response"}
            }
        )
    )
    conversations.invoke_prepared = lambda *_args, **_kwargs: pytest.fail(
        "AgentCore must not be replayed"
    )

    assert worker._process(record, OwnedHeartbeat()) is True
    assert len(receipt_activation.calls) == 1
    assert jobs.record["receipt_activation_state"] == "completed"


def _patch_build_worker_dependencies(monkeypatch, *, settings, receipt_calls):
    dynamodb = object()
    jobs = SimpleNamespace(name="voice-jobs")
    queue = SimpleNamespace(name="voice-queue")
    monkeypatch.setattr(worker_module, "get_dynamodb_resource", lambda _s: dynamodb)
    monkeypatch.setattr(worker_module, "create_sqs_client", lambda **_kwargs: object())
    monkeypatch.setattr(worker_module, "VoiceQueueService", lambda *_a, **_k: queue)
    monkeypatch.setattr(worker_module, "WhatsAppVoiceJobRepository", lambda *_a, **_k: object())
    monkeypatch.setattr(worker_module, "WhatsAppVoiceJobService", lambda *_a, **_k: jobs)
    monkeypatch.setattr(worker_module, "AgentfloGatewayService", lambda **_k: object())
    monkeypatch.setattr(worker_module, "VoiceMediaStorageService", lambda **_k: object())
    monkeypatch.setattr(worker_module, "TranscriptionService", lambda **_k: object())
    monkeypatch.setattr(worker_module, "WhatsAppVoiceService", lambda **_k: object())
    monkeypatch.setattr(worker_module, "build_identity_resolver", lambda *_a: object())
    monkeypatch.setattr(worker_module, "build_response_builder", lambda *_a: object())
    monkeypatch.setattr(worker_module, "AgentRequestProcessor", lambda **_k: object())
    monkeypatch.setattr(worker_module, "WhatsAppConversationService", lambda **_k: object())
    monkeypatch.setattr(worker_module, "get_s3_client", lambda _s: object())
    monkeypatch.setattr(worker_module, "get_transcribe_client_provider", lambda _s: object())

    receipt_jobs = object()

    def build_receipt(received_settings, *, dynamodb=None):
        receipt_calls.append((received_settings, dynamodb))
        return SimpleNamespace(jobs=receipt_jobs)

    activations = []

    def activation_factory(**kwargs):
        activation = SimpleNamespace(**kwargs)
        activations.append(activation)
        return activation

    monkeypatch.setattr(
        worker_module,
        "build_receipt_submission_runtime",
        build_receipt,
    )
    monkeypatch.setattr(
        worker_module,
        "WhatsAppVoiceReceiptActivationService",
        activation_factory,
    )
    return dynamodb, jobs, receipt_jobs, activations


def build_worker_settings(*, receipt_enabled):
    return SimpleNamespace(
        validate_voice_worker_settings=lambda: None,
        aws_region="us-west-2",
        voice_job_queue_url="voice-queue-url",
        voice_sqs_wait_time_seconds=1,
        voice_sqs_visibility_timeout_seconds=30,
        whatsapp_voice_jobs_table_name="voice-jobs",
        agentflo_gateway_base_url="https://gateway.invalid",
        agentflo_gateway_api_key="synthetic-key",
        agentflo_gateway_tenant_id="tenant",
        agentflo_gateway_agent_id="agent",
        agentflo_gateway_actor_id="actor",
        voice_download_timeout_seconds=5,
        voice_max_media_bytes=1024,
        voice_media_bucket_name="voice-bucket",
        voice_media_input_prefix="voice/input/",
        voice_transcription_job_prefix="voice-test-",
        voice_transcription_language_code="en-US",
        voice_transcription_identify_language=False,
        voice_transcription_timeout_seconds=30,
        whatsapp_voice_reply_enabled=False,
        receipt_activation_enabled=receipt_enabled,
    )


def test_build_worker_disabled_does_not_construct_receipt_runtime(monkeypatch):
    settings = build_worker_settings(receipt_enabled=False)
    receipt_calls = []
    _patch_build_worker_dependencies(
        monkeypatch,
        settings=settings,
        receipt_calls=receipt_calls,
    )

    worker = worker_module.build_worker(settings)

    assert receipt_calls == []
    assert worker.receipt_activation is None


def test_build_worker_enabled_constructs_submission_runtime_once_and_reuses_dynamo(
    monkeypatch,
):
    settings = build_worker_settings(receipt_enabled=True)
    receipt_calls = []
    dynamodb, jobs, receipt_jobs, activations = _patch_build_worker_dependencies(
        monkeypatch,
        settings=settings,
        receipt_calls=receipt_calls,
    )

    worker = worker_module.build_worker(settings)

    assert receipt_calls == [(settings, dynamodb)]
    assert len(activations) == 1
    assert activations[0].voice_jobs is jobs
    assert activations[0].receipt_jobs is receipt_jobs
    assert worker.receipt_activation is activations[0]
