from __future__ import annotations

import logging
import signal
import threading
import uuid
from contextlib import contextmanager

from botocore.exceptions import ClientError

from src.agent.dependencies import get_services
from src.agent_client.factory import get_agent_runtime_client
from src.api.whatsapp import WhatsAppInboundAudioMessage, WhatsAppInboundMessage
from src.composition.receipt_dependencies import build_receipt_submission_runtime
from src.infrastructure.config import get_settings
from src.infrastructure.dynamodb import get_dynamodb_resource
from src.infrastructure.logging import configure_logging
from src.infrastructure.polly import get_polly_client
from src.infrastructure.s3 import get_s3_client
from src.infrastructure.sqs import create_sqs_client
from src.infrastructure.transcribe import get_transcribe_client_provider
from src.models.whatsapp_voice_job import VoiceJobState
from src.repositories.whatsapp_voice_job_repository import WhatsAppVoiceJobRepository, VoiceJobConditionFailed
from src.services.agent_request_processor import AgentRequestProcessor, build_identity_resolver, build_response_builder
from src.services.agentflo_gateway_service import AgentfloGatewayService
from src.services.polly_speech_service import PollySpeechSynthesisService
from src.services.transcription_service import TranscriptionService
from src.services.voice_media_storage_service import VoiceMediaStorageService
from src.services.voice_queue_service import VoiceQueueError, VoiceQueueService
from src.services.voice_reply_audio_converter import VoiceReplyAudioConverter
from src.services.whatsapp_conversation_service import WhatsAppConversationReply, WhatsAppConversationService, build_whatsapp_identity
from src.services.whatsapp_voice_reply_service import (
    VoiceReplyOutcome,
    WhatsAppVoiceReplyService,
)
from src.services.whatsapp_voice_job_service import WhatsAppVoiceJobService
from src.services.whatsapp_voice_receipt_activation_service import (
    WhatsAppVoiceReceiptActivationService,
)
from src.services.whatsapp_voice_service import WhatsAppVoiceProcessingError, WhatsAppVoiceService


GENERIC_FAILURE_REPLY = "I couldn’t process that voice note. Please send a shorter voice note or type your message."
logger = logging.getLogger(__name__)


class HeartbeatLost(RuntimeError):
    pass


class VoiceWorkerHeartbeat:
    def __init__(self, *, jobs, queue, job_id: str, owner: str, receipt_handle: str, interval_seconds: int):
        self.jobs, self.queue = jobs, queue
        self.job_id, self.owner, self.receipt_handle = job_id, owner, receipt_handle
        self.interval_seconds = interval_seconds
        self.stop_event = threading.Event()
        self.lost_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="voice-heartbeat", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=max(1, self.interval_seconds + 1))

    def assert_owned(self) -> None:
        if self.lost_event.is_set():
            raise HeartbeatLost("VOICE_WORKER_HEARTBEAT_LOST")

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            try:
                if not self.jobs.extend_lease(self.job_id, self.owner):
                    self.lost_event.set()
                    return
                self.queue.change_visibility(self.receipt_handle)
            except Exception:
                self.lost_event.set()
                return


class WhatsAppVoiceWorker:
    def __init__(
        self,
        *,
        settings,
        jobs,
        queue,
        voice,
        conversations,
        voice_replies=None,
        receipt_activation=None,
    ):
        self.settings, self.jobs, self.queue = settings, jobs, queue
        self.voice, self.conversations = voice, conversations
        self.voice_replies = voice_replies
        self.receipt_activation = receipt_activation
        self.worker_id = f"voice-worker-{uuid.uuid4()}"
        self.stopping = threading.Event()

    def request_stop(self, *_args) -> None:
        self.stopping.set()

    def run(self) -> None:
        self.jobs.recover_outbox(limit=25)
        while not self.stopping.is_set():
            try:
                received = self.queue.receive_one()
            except VoiceQueueError:
                logger.warning("Voice queue receive failed", extra={"event": "voice_retryable_failure", "failure_stage": "receive", "retryable": True})
                continue
            if received is not None:
                self.handle(received)

    def handle(self, received) -> None:
        try:
            self._handle(received)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            logger.error(
                "Voice worker AWS operation failed; message left for bounded queue retry",
                extra={
                    "event": "voice_worker_aws_operation_failed",
                    "voice_job_id": received.queue_message.job_id,
                    "error_code": "VOICE_WORKER_AWS_OPERATION_FAILED",
                    "aws_error_code": error.get("Code"),
                    "aws_operation": exc.operation_name,
                    "retryable": True,
                },
            )

    def _handle(self, received) -> None:
        job_id = received.queue_message.job_id
        record = self.jobs.repository.get(job_id)
        logger.info("Voice worker job received", extra={"event": "voice_worker_job_received", "voice_job_id": job_id, "receive_count": received.receive_count})
        if record is None:
            return
        if self.jobs.is_terminal(record):
            self._delete(received.receipt_handle, job_id)
            return
        try:
            record = self.jobs.acquire_lease(job_id, self.worker_id)
        except VoiceJobConditionFailed:
            return
        heartbeat = VoiceWorkerHeartbeat(
            jobs=self.jobs, queue=self.queue, job_id=job_id, owner=self.worker_id,
            receipt_handle=received.receipt_handle,
            interval_seconds=self.settings.voice_sqs_heartbeat_seconds,
        )
        heartbeat.start()
        terminal = False
        try:
            terminal = self._process(record, heartbeat)
            if terminal:
                self._delete(received.receipt_handle, job_id)
        except HeartbeatLost:
            logger.warning("Voice heartbeat lost", extra={"event": "voice_retryable_failure", "voice_job_id": job_id, "failure_stage": "heartbeat", "retryable": True})
        except WhatsAppVoiceProcessingError as exc:
            terminal = self._handle_voice_failure(record, exc, heartbeat)
            if terminal:
                self._delete(received.receipt_handle, job_id)
        except VoiceJobConditionFailed:
            pass
        finally:
            heartbeat.stop()
            self.jobs.release_lease(job_id, self.worker_id)

    def _process(self, record: dict, heartbeat: VoiceWorkerHeartbeat) -> bool:
        state = record["state"]
        if state == VoiceJobState.PERMANENT_FAILURE.value:
            heartbeat.assert_owned()
            identity_message = WhatsAppInboundMessage(
                text="", customer_number=record["customer_number"], customer_name=None,
                sender_id=record["sender_id"], message_id=record["job_id"],
            )
            customer_id, session_id = build_whatsapp_identity(
                identity_message, self.conversations.services_provider,
            )
            record = self.jobs.transition(record, VoiceJobState.RESPONSE_READY.value, values={
                "generic_response_code": "VOICE_PROCESSING_FAILED", "customer_id": customer_id,
                "session_id": session_id, "request_id": f"voice-generic-{record['job_id'][4:]}",
            })
            return self._send_ready(record, heartbeat, inbound=identity_message)
        if state == VoiceJobState.RESPONSE_READY.value:
            return self._send_ready(record, heartbeat)
        if (
            state == VoiceJobState.OUTBOUND_SENDING.value
            and self._receipt_activation_enabled()
            and record.get("receipt_activation_state") in {
                "pending",
                "completed",
                "manual_review",
            }
        ):
            return self._recover_receipt_outbound(record, heartbeat)
        if state == VoiceJobState.OUTBOUND_SENDING.value or state == VoiceJobState.AGENT_INVOKING.value:
            record = self.jobs.transition(record, VoiceJobState.MANUAL_REVIEW.value, values={"generic_failure_code": "VOICE_OPERATION_OUTCOME_AMBIGUOUS"})
            logger.error("Voice job requires manual review", extra={"event": "voice_manual_review", "voice_job_id": record["job_id"], "failure_stage": state})
            return True
        if state in {VoiceJobState.QUEUED.value, VoiceJobState.RETRYABLE_FAILURE.value}:
            record = self.jobs.transition(record, VoiceJobState.PROCESSING.value, remove=("next_retry_at", "generic_failure_code", "GSI1PK", "GSI1SK"))
        if record["state"] != VoiceJobState.PROCESSING.value:
            return False
        heartbeat.assert_owned()
        deterministic_request_id = "req-voice-" + record["job_id"][4:]
        existing_request = self.conversations.services_provider().agent_requests.get(deterministic_request_id)
        existing_transcript = existing_request.get("message") if isinstance(existing_request, dict) else None
        if isinstance(existing_transcript, str) and existing_transcript.strip():
            transcript = existing_transcript
        else:
            transcript = None
        audio = WhatsAppInboundAudioMessage(
            customer_number=record["customer_number"], customer_name=None,
            sender_id=record["sender_id"], message_id=record["job_id"],
            audio_id=record.get("audio_id"), media_url=record.get("media_url"),
        )
        if transcript is None:
            transcript = self.voice.transcribe(audio)
            logger.info("Voice transcription completed", extra={"event": "voice_transcription_completed", "voice_job_id": record["job_id"]})
        heartbeat.assert_owned()
        inbound = WhatsAppInboundMessage(
            text=transcript, customer_number=record["customer_number"], customer_name=None,
            sender_id=record["sender_id"], message_id=record["job_id"],
        )
        prepared = self.conversations.prepare_text(
            inbound, deterministic_request_id=deterministic_request_id,
            history_identifier=f"{record['job_id']}:inbound",
        )
        agent_record = prepared.prepared_agent_request.record
        record = self.jobs.transition(record, VoiceJobState.TRANSCRIBED.value, values={
            "request_id": agent_record["request_id"],
            "customer_id": prepared.prepared_agent_request.context.customer_id,
            "session_id": prepared.prepared_agent_request.context.agent_session_id,
        })
        logger.info("Transcript routed", extra={"event": "voice_transcript_routed", "voice_job_id": record["job_id"], "request_id": agent_record["request_id"]})
        record = self.jobs.transition(record, VoiceJobState.AGENT_INVOKING.value)
        heartbeat.assert_owned()
        reply = self.conversations.invoke_prepared(prepared, ambiguous_on_failure=True)
        if reply is None:
            self.jobs.transition(record, VoiceJobState.MANUAL_REVIEW.value, values={"generic_failure_code": "VOICE_AGENT_OUTCOME_AMBIGUOUS"})
            logger.error("Voice agent outcome ambiguous", extra={"event": "voice_manual_review", "voice_job_id": record["job_id"], "failure_stage": "agent_invoking"})
            return True
        response_values = None
        if (
            getattr(self.settings, "receipt_activation_enabled", False)
            and reply.submitted_order_id is not None
        ):
            response_values = {"submitted_order_id": reply.submitted_order_id}
        if response_values is None:
            record = self.jobs.transition(
                record,
                VoiceJobState.RESPONSE_READY.value,
            )
        else:
            record = self.jobs.transition(
                record,
                VoiceJobState.RESPONSE_READY.value,
                values=response_values,
            )
        return self._send_ready(record, heartbeat, inbound=inbound, reply=reply)

    def _send_ready(self, record: dict, heartbeat: VoiceWorkerHeartbeat, *, inbound=None, reply=None) -> bool:
        heartbeat.assert_owned()
        if inbound is None:
            inbound = WhatsAppInboundMessage(
                text="", customer_number=record["customer_number"], customer_name=None,
                sender_id=record["sender_id"], message_id=record["job_id"],
            )
        if reply is None:
            request = self.conversations.services_provider().agent_requests.get(record.get("request_id", ""))
            response = request.get("response") if isinstance(request, dict) else None
            text = response.get("text") if isinstance(response, dict) else None
            if not isinstance(text, str) or not text.strip():
                text = GENERIC_FAILURE_REPLY if record.get("generic_response_code") else None
            if text is None:
                return False
            reply = WhatsAppConversationReply(text, record.get("request_id") or f"voice-generic-{record['job_id'][4:]}", record["session_id"], record["customer_id"], resumed=True)
        record = self.jobs.transition(record, VoiceJobState.OUTBOUND_SENDING.value)
        heartbeat.assert_owned()
        delivery = self.conversations.deliver(
            inbound, reply, history_identifier=f"{record['job_id']}:outbound",
            ambiguous_on_exception=True,
        )
        if delivery.status == "ambiguous":
            self.jobs.transition(record, VoiceJobState.MANUAL_REVIEW.value, values={"generic_failure_code": "VOICE_OUTBOUND_OUTCOME_AMBIGUOUS"})
            logger.error("Voice outbound outcome ambiguous", extra={"event": "voice_manual_review", "voice_job_id": record["job_id"], "failure_stage": "outbound_sending"})
            return True
        if not delivery.successful:
            # A definite validation failure is terminal; a definite non-send can
            # be retried from response_ready without replaying AgentCore.
            if delivery.status == "permanent_failure":
                self.jobs.transition(record, VoiceJobState.MANUAL_REVIEW.value, values={"generic_failure_code": "VOICE_OUTBOUND_INVALID"})
                return True
            self.jobs.transition(record, VoiceJobState.RESPONSE_READY.value)
            return False

        receipt_recovery = False
        receipt_required = (
            self._receipt_activation_enabled()
            and "submitted_order_id" in record
            and delivery.status == "sent"
            and delivery.outbound.get("sent") is True
        )
        if receipt_required:
            heartbeat.assert_owned()
            try:
                record = self.jobs.checkpoint_receipt_pending(record)
            except VoiceJobConditionFailed:
                heartbeat.assert_owned()
                latest = self.jobs.repository.get(record["job_id"])
                if latest is None:
                    return False
                if latest.get("state") != VoiceJobState.OUTBOUND_SENDING.value:
                    return self.jobs.is_terminal(latest)
                receipt_state = latest.get("receipt_activation_state")
                if receipt_state not in {
                    "pending",
                    "completed",
                    "manual_review",
                }:
                    heartbeat.assert_owned()
                    self.jobs.transition(
                        latest,
                        VoiceJobState.MANUAL_REVIEW.value,
                        values={
                            "generic_failure_code": (
                                "VOICE_RECEIPT_CHECKPOINT_AMBIGUOUS"
                            )
                        },
                    )
                    return True
                record = latest
                receipt_recovery = True

            if record.get("receipt_activation_state") == "pending":
                heartbeat.assert_owned()
                activation = self.receipt_activation.activate_pending(record)
                heartbeat.assert_owned()
                latest = self.jobs.repository.get(record["job_id"])
                if latest is None:
                    return False
                record = latest
                if activation.retryable or record.get(
                    "receipt_activation_state"
                ) == "pending":
                    if not receipt_recovery:
                        self._deliver_optional_voice_reply(
                            record=record,
                            inbound=inbound,
                            reply=reply,
                            text_delivery_status=delivery.status,
                            heartbeat=heartbeat,
                        )
                    return False

            if record.get("receipt_activation_state") not in {
                "completed",
                "manual_review",
            }:
                return False

        if receipt_recovery:
            return self._complete_receipt_recovery(record, heartbeat)
        voice_reply = self._deliver_optional_voice_reply(
            record=record,
            inbound=inbound,
            reply=reply,
            text_delivery_status=delivery.status,
            heartbeat=heartbeat,
        )
        heartbeat.assert_owned()
        record = self.jobs.transition(
            record,
            VoiceJobState.COMPLETED.value,
            values=voice_reply.persistence_values(),
        )
        logger.info("Voice outbound completed", extra={"event": "voice_outbound_completed", "voice_job_id": record["job_id"]})
        return True

    def _recover_receipt_outbound(
        self,
        record: dict,
        heartbeat: VoiceWorkerHeartbeat,
    ) -> bool:
        if record.get("receipt_activation_state") == "pending":
            heartbeat.assert_owned()
            activation = self.receipt_activation.activate_pending(record)
            heartbeat.assert_owned()
            latest = self.jobs.repository.get(record["job_id"])
            if latest is None:
                return False
            record = latest
            if activation.retryable or record.get(
                "receipt_activation_state"
            ) == "pending":
                return False
        if record.get("receipt_activation_state") not in {
            "completed",
            "manual_review",
        }:
            return False
        return self._complete_receipt_recovery(record, heartbeat)

    def _complete_receipt_recovery(
        self,
        record: dict,
        heartbeat: VoiceWorkerHeartbeat,
    ) -> bool:
        heartbeat.assert_owned()
        record = self.jobs.transition(
            record,
            VoiceJobState.COMPLETED.value,
            values=VoiceReplyOutcome(
                "skipped",
                error_code="VOICE_REPLY_RECOVERY_NOT_REPLAYED",
            ).persistence_values(),
        )
        logger.info(
            "Voice outbound recovery completed",
            extra={
                "event": "voice_outbound_recovery_completed",
                "voice_job_id": record["job_id"],
                "voice_job_state": record["state"],
                "recovery": True,
            },
        )
        return True

    def _receipt_activation_enabled(self) -> bool:
        return bool(
            getattr(self.settings, "receipt_activation_enabled", False)
            and self.receipt_activation is not None
        )

    def _deliver_optional_voice_reply(
        self,
        *,
        record: dict,
        inbound: WhatsAppInboundMessage,
        reply: WhatsAppConversationReply,
        text_delivery_status: str,
        heartbeat: VoiceWorkerHeartbeat,
    ) -> VoiceReplyOutcome:
        if not getattr(self.settings, "whatsapp_voice_reply_enabled", False):
            return VoiceReplyOutcome("disabled")
        if text_delivery_status != "sent":
            return VoiceReplyOutcome("skipped", error_code="VOICE_REPLY_TEXT_NOT_SENT")
        if self.voice_replies is None:
            logger.warning(
                "Optional voice reply is enabled but unavailable",
                extra={
                    "event": "voice_reply_failed",
                    "voice_job_id": record["job_id"],
                    "request_id": reply.request_id,
                    "failure_stage": "configuration",
                    "error_code": "VOICE_REPLY_SERVICE_UNAVAILABLE",
                    "retryable": False,
                },
            )
            return VoiceReplyOutcome(
                "failed",
                error_code="VOICE_REPLY_SERVICE_UNAVAILABLE",
            )
        heartbeat.assert_owned()
        return self.voice_replies.deliver(
            text=reply.reply,
            customer_number=inbound.customer_number,
            conversation_id=reply.session_id,
            sender_id=inbound.sender_id,
            request_id=reply.request_id,
            voice_job_id=record["job_id"],
        )

    def _handle_voice_failure(self, record: dict, exc: WhatsAppVoiceProcessingError, heartbeat: VoiceWorkerHeartbeat) -> bool:
        current = self.jobs.repository.get(record["job_id"]) or record
        if exc.retryable:
            self.jobs.transition(current, VoiceJobState.RETRYABLE_FAILURE.value, values={"generic_failure_code": exc.error_code})
            logger.warning("Voice processing will retry", extra={"event": "voice_retryable_failure", "voice_job_id": record["job_id"], "failure_stage": "media_transcription", "retryable": True, "error_code": exc.error_code})
            return False
        failed = self.jobs.transition(current, VoiceJobState.PERMANENT_FAILURE.value, values={"generic_failure_code": exc.error_code})
        logger.info("Voice processing permanently failed", extra={"event": "voice_permanent_failure", "voice_job_id": record["job_id"], "failure_stage": "media_transcription", "retryable": False, "error_code": exc.error_code})
        heartbeat.assert_owned()
        customer_id, session_id = build_whatsapp_identity(
            WhatsAppInboundMessage(text="", customer_number=record["customer_number"], sender_id=record["sender_id"], message_id=record["job_id"]),
            self.conversations.services_provider,
        )
        ready = self.jobs.transition(failed, VoiceJobState.RESPONSE_READY.value, values={
            "generic_response_code": "VOICE_PROCESSING_FAILED", "customer_id": customer_id,
            "session_id": session_id, "request_id": f"voice-generic-{record['job_id'][4:]}",
        })
        return self._send_ready(ready, heartbeat)

    def _delete(self, receipt_handle: str, job_id: str) -> None:
        self.queue.delete(receipt_handle)
        logger.info("Voice SQS message deleted", extra={"event": "voice_sqs_message_deleted", "voice_job_id": job_id})


def build_worker(settings=None) -> WhatsAppVoiceWorker:
    settings = settings or get_settings()
    settings.validate_voice_worker_settings()
    dynamodb = get_dynamodb_resource(settings)
    queue = VoiceQueueService(
        create_sqs_client(region_name=settings.aws_region), settings.voice_job_queue_url,
        wait_time_seconds=settings.voice_sqs_wait_time_seconds,
        visibility_timeout_seconds=settings.voice_sqs_visibility_timeout_seconds,
    )
    jobs = WhatsAppVoiceJobService(
        WhatsAppVoiceJobRepository(dynamodb, settings.whatsapp_voice_jobs_table_name), queue, settings,
    )
    voice = WhatsAppVoiceService(
        media_service=AgentfloGatewayService(
            base_url=settings.agentflo_gateway_base_url,
            api_key=settings.agentflo_gateway_api_key,
            tenant_id=settings.agentflo_gateway_tenant_id,
            agent_id=settings.agentflo_gateway_agent_id,
            actor_id=settings.agentflo_gateway_actor_id,
            timeout_seconds=settings.voice_download_timeout_seconds,
            max_media_bytes=settings.voice_max_media_bytes,
        ),
        storage_service=VoiceMediaStorageService(
            client=get_s3_client(settings), bucket_name=settings.voice_media_bucket_name,
            input_prefix=settings.voice_media_input_prefix,
        ),
        transcription_service=TranscriptionService(
            client_provider=get_transcribe_client_provider(settings),
            aws_region=settings.aws_region,
        ),
        transcription_job_prefix=settings.voice_transcription_job_prefix,
        language_code=settings.voice_transcription_language_code,
        identify_language=settings.voice_transcription_identify_language,
        transcription_timeout_seconds=settings.voice_transcription_timeout_seconds,
    )
    processor = AgentRequestProcessor(
        services_provider=get_services, agent_client_provider=get_agent_runtime_client,
        identity_resolver=build_identity_resolver(get_services),
        response_builder=build_response_builder(get_services),
    )
    conversations = WhatsAppConversationService(
        services_provider=get_services, processor=processor,
        identity_builder=lambda inbound: build_whatsapp_identity(inbound, get_services),
        gateway_provider=lambda: AgentfloGatewayService(
            base_url=settings.agentflo_gateway_base_url, api_key=settings.agentflo_gateway_api_key,
            tenant_id=settings.agentflo_gateway_tenant_id, agent_id=settings.agentflo_gateway_agent_id,
            actor_id=settings.agentflo_gateway_actor_id,
        ),
    )
    voice_replies = None
    if settings.whatsapp_voice_reply_enabled:
        voice_replies = WhatsAppVoiceReplyService(
            synthesizer=PollySpeechSynthesisService(
                client=get_polly_client(settings),
                voice_id=settings.polly_voice_id,
                engine=settings.polly_engine,
                language_code=settings.polly_language_code,
                max_text_chars=settings.polly_max_text_chars,
                max_audio_bytes=settings.voice_reply_max_audio_bytes,
                timeout_seconds=settings.voice_reply_synthesis_timeout_seconds,
            ),
            converter=VoiceReplyAudioConverter(
                timeout_seconds=settings.voice_reply_conversion_timeout_seconds,
                max_audio_bytes=settings.voice_reply_max_audio_bytes,
            ),
            gateway_provider=lambda: AgentfloGatewayService(
                base_url=settings.agentflo_gateway_base_url,
                api_key=settings.agentflo_gateway_api_key,
                tenant_id=settings.agentflo_gateway_tenant_id,
                agent_id=settings.agentflo_gateway_agent_id,
                actor_id=settings.agentflo_gateway_actor_id,
                max_audio_bytes=settings.voice_reply_max_audio_bytes,
                audio_firestore=settings.agentflo_audio_firestore,
                audio_kinesis=settings.agentflo_audio_kinesis,
            ),
        )
    receipt_activation = None
    if settings.receipt_activation_enabled:
        receipt_runtime = build_receipt_submission_runtime(
            settings,
            dynamodb=dynamodb,
        )
        receipt_activation = WhatsAppVoiceReceiptActivationService(
            voice_jobs=jobs,
            receipt_jobs=receipt_runtime.jobs,
        )
    return WhatsAppVoiceWorker(
        settings=settings,
        jobs=jobs,
        queue=queue,
        voice=voice,
        conversations=conversations,
        voice_replies=voice_replies,
        receipt_activation=receipt_activation,
    )


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker = build_worker(settings)
    signal.signal(signal.SIGTERM, worker.request_stop)
    signal.signal(signal.SIGINT, worker.request_stop)
    worker.run()


if __name__ == "__main__":
    main()
