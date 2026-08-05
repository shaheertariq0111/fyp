from __future__ import annotations

import logging

from src.api.whatsapp import WhatsAppInboundAudioMessage
from src.services.transcription_service import TranscriptionService


logger = logging.getLogger(__name__)


class WhatsAppVoiceProcessingError(Exception):
    error_code = "WHATSAPP_VOICE_PROCESSING_FAILED"

    retryable = False

    def __init__(self, *, error_code: str | None = None, retryable: bool | None = None) -> None:
        if error_code:
            self.error_code = error_code
        if retryable is not None:
            self.retryable = retryable
        super().__init__("The WhatsApp voice message could not be processed.")


class WhatsAppVoiceService:
    def __init__(
        self,
        *,
        media_service,
        storage_service,
        transcription_service: TranscriptionService,
        transcription_job_prefix: str,
        language_code: str,
        identify_language: bool,
        transcription_timeout_seconds: float,
    ) -> None:
        self.media_service = media_service
        self.storage_service = storage_service
        self.transcription_service = transcription_service
        self.transcription_job_prefix = transcription_job_prefix
        self.language_code = language_code
        self.identify_language = identify_language
        self.transcription_timeout_seconds = transcription_timeout_seconds

    def transcribe(self, inbound: WhatsAppInboundAudioMessage) -> str:
        message_id = (inbound.message_id or "").strip()
        if not message_id:
            raise WhatsAppVoiceProcessingError()

        downloaded_media = None
        stored_media = None
        primary_error: Exception | None = None
        transcript: str | None = None
        try:
            audio_id = (inbound.audio_id or "").strip()
            if not audio_id:
                raise WhatsAppVoiceProcessingError(
                    error_code="AGENTFLO_MEDIA_AUDIO_ID_REQUIRED",
                    retryable=False,
                )
            downloaded_media = self.media_service.download_media(
                audio_id,
                request_id=message_id,
            )
            stored_media = self.storage_service.upload(
                downloaded_media,
                message_id=message_id,
            )
            job_name = self.transcription_service.build_job_name(
                message_id,
                self.transcription_job_prefix,
            )
            transcript = self.transcription_service.transcribe(
                media_s3_uri=stored_media.s3_uri,
                media_format=downloaded_media.media_format,
                job_name=job_name,
                language_code=self.language_code,
                identify_language=self.identify_language,
                timeout_seconds=self.transcription_timeout_seconds,
            )
            return transcript
        except Exception as exc:
            primary_error = exc
            if hasattr(exc, "error_code"):
                raise WhatsAppVoiceProcessingError(
                    error_code=exc.error_code,
                    retryable=bool(getattr(exc, "retryable", False)),
                ) from exc
            raise WhatsAppVoiceProcessingError(retryable=True) from exc
        finally:
            cleanup_error: Exception | None = None
            if stored_media is not None:
                try:
                    self.storage_service.delete(stored_media)
                except Exception as exc:
                    cleanup_error = exc
            if downloaded_media is not None:
                try:
                    downloaded_media.close()
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            # Cleanup is best effort. A valid transcript must survive a cleanup
            # failure, which is recorded only as metadata.
            if cleanup_error is not None:
                logger.warning(
                    "Voice media cleanup failed",
                    extra={
                        "event": "voice_cleanup_failure",
                        "error_code": "VOICE_MEDIA_CLEANUP_FAILED",
                        "cleanup_after_valid_transcript": transcript is not None,
                    },
                )
