from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from src.services.polly_speech_service import PollySynthesisError
from src.services.voice_reply_audio_converter import VoiceReplyConversionError


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VoiceReplyOutcome:
    status: str
    error_code: str | None = None
    provider_message_id: str | None = None
    generated_audio_bytes: int | None = None

    def persistence_values(self) -> dict[str, object]:
        values: dict[str, object] = {"voice_reply_status": self.status}
        if self.error_code:
            values["voice_reply_error_code"] = self.error_code
        if self.provider_message_id:
            values["voice_reply_provider_message_id"] = self.provider_message_id
        if self.generated_audio_bytes is not None:
            values["voice_reply_audio_bytes"] = self.generated_audio_bytes
        return values


class WhatsAppVoiceReplyService:
    def __init__(self, *, synthesizer, converter, gateway_provider) -> None:
        self.synthesizer = synthesizer
        self.converter = converter
        self.gateway_provider = gateway_provider

    def deliver(
        self,
        *,
        text: str,
        customer_number: str,
        conversation_id: str,
        sender_id: str,
        request_id: str,
        voice_job_id: str,
    ) -> VoiceReplyOutcome:
        logger.info(
            "Voice reply synthesis started",
            extra={
                "event": "voice_reply_synthesis_started",
                "voice_job_id": voice_job_id,
                "request_id": request_id,
            },
        )
        try:
            started = time.perf_counter()
            source = self.synthesizer.synthesize(text)
            synthesis_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "Voice reply synthesis completed",
                extra={
                    "event": "voice_reply_synthesis_completed",
                    "voice_job_id": voice_job_id,
                    "request_id": request_id,
                    "source_audio_bytes": len(source),
                    "synthesis_duration_ms": synthesis_ms,
                },
            )

            started = time.perf_counter()
            converted = self.converter.convert(source)
            conversion_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "Voice reply conversion completed",
                extra={
                    "event": "voice_reply_conversion_completed",
                    "voice_job_id": voice_job_id,
                    "request_id": request_id,
                    "generated_audio_bytes": len(converted),
                    "conversion_duration_ms": conversion_ms,
                },
            )
            outbound = self.gateway_provider().send_audio(
                customer_number=customer_number,
                conversation_id=conversation_id,
                sender_id=sender_id,
                audio=converted,
                request_id=request_id,
            )
            if not outbound.get("sent"):
                return self._failed(
                    voice_job_id=voice_job_id,
                    request_id=request_id,
                    stage="outbound",
                    error_code=str(
                        outbound.get("error_code")
                        or "AGENTFLO_AUDIO_OUTBOUND_FAILED"
                    ),
                    retryable=False,
                    generated_audio_bytes=len(converted),
                )
            provider_message_id = outbound.get("providerMessageId")
            provider_message_id = (
                provider_message_id
                if isinstance(provider_message_id, str) and provider_message_id
                else None
            )
            logger.info(
                "Voice reply outbound completed",
                extra={
                    "event": "voice_reply_outbound_completed",
                    "voice_job_id": voice_job_id,
                    "request_id": request_id,
                    "generated_audio_bytes": len(converted),
                    "outbound_status": outbound.get("status") or "accepted",
                    "provider_message_id": provider_message_id,
                },
            )
            return VoiceReplyOutcome(
                "sent",
                provider_message_id=provider_message_id,
                generated_audio_bytes=len(converted),
            )
        except (PollySynthesisError, VoiceReplyConversionError) as exc:
            return self._failed(
                voice_job_id=voice_job_id,
                request_id=request_id,
                stage=exc.stage,
                error_code=exc.error_code,
                retryable=exc.retryable,
            )
        except Exception:
            # Text delivery already succeeded. Treat an unknown optional-audio
            # outcome as ambiguous and never replay the agent or primary text.
            return self._failed(
                voice_job_id=voice_job_id,
                request_id=request_id,
                stage="outbound",
                error_code="VOICE_REPLY_OUTCOME_AMBIGUOUS",
                retryable=False,
                status="ambiguous",
            )

    @staticmethod
    def _failed(
        *,
        voice_job_id: str,
        request_id: str,
        stage: str,
        error_code: str,
        retryable: bool,
        generated_audio_bytes: int | None = None,
        status: str = "failed",
    ) -> VoiceReplyOutcome:
        logger.warning(
            "Optional voice reply failed",
            extra={
                "event": "voice_reply_failed",
                "voice_job_id": voice_job_id,
                "request_id": request_id,
                "failure_stage": stage,
                "error_code": error_code,
                "retryable": retryable,
                "generated_audio_bytes": generated_audio_bytes,
                "outbound_status": status,
            },
        )
        return VoiceReplyOutcome(
            status,
            error_code=error_code,
            generated_audio_bytes=generated_audio_bytes,
        )
