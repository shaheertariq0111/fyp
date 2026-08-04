import io

import pytest

from src.api.whatsapp import WhatsAppInboundAudioMessage
from src.services.agentflo_media_service import DownloadedVoiceMedia
from src.services.voice_media_storage_service import StoredVoiceMedia
from src.services.whatsapp_voice_service import (
    WhatsAppVoiceProcessingError,
    WhatsAppVoiceService,
)


class FakeMediaService:
    def __init__(self, media):
        self.media = media
        self.urls = []

    def download(self, url):
        self.urls.append(url)
        return self.media


class FakeStorageService:
    def __init__(self):
        self.uploads = []
        self.deletes = []
        self.delete_error = None

    def upload(self, media, *, message_id):
        self.uploads.append((media, message_id))
        return StoredVoiceMedia(
            bucket_name="voice-bucket",
            object_key="voice-input/opaque.ogg",
            s3_uri="s3://voice-bucket/voice-input/opaque.ogg",
        )

    def delete(self, stored):
        self.deletes.append(stored)
        if self.delete_error is not None:
            raise self.delete_error


class FakeTranscriptionService:
    def __init__(self):
        self.calls = []
        self.error = None

    def build_job_name(self, message_id, prefix):
        assert message_id == "message-123"
        assert prefix == "fyp-dev"
        return "fyp-dev-whatsapp-voice-opaque"

    def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return "one pizza please"


def make_inbound():
    return WhatsAppInboundAudioMessage(
        customer_number="private-customer",
        customer_name="Private Name",
        sender_id="private-sender",
        message_id="message-123",
        media_id="private-media-id",
        media_url="https://media.example.test/private",
    )


def make_service():
    media = DownloadedVoiceMedia(
        file=io.BytesIO(b"OggS synthetic OpusHead"),
        media_format="ogg",
        suffix=".ogg",
        content_type="audio/ogg",
        size_bytes=23,
    )
    media_service = FakeMediaService(media)
    storage_service = FakeStorageService()
    transcription_service = FakeTranscriptionService()
    service = WhatsAppVoiceService(
        media_service=media_service,
        storage_service=storage_service,
        transcription_service=transcription_service,
        transcription_job_prefix="fyp-dev",
        language_code="en-US",
        identify_language=False,
        transcription_timeout_seconds=180,
    )
    return service, media, media_service, storage_service, transcription_service


def test_voice_orchestration_downloads_uploads_transcribes_and_cleans_up():
    service, media, media_service, storage, transcription = make_service()

    assert service.transcribe(make_inbound()) == "one pizza please"
    assert media_service.urls == ["https://media.example.test/private"]
    assert storage.uploads == [(media, "message-123")]
    assert transcription.calls == [
        {
            "media_s3_uri": "s3://voice-bucket/voice-input/opaque.ogg",
            "media_format": "ogg",
            "job_name": "fyp-dev-whatsapp-voice-opaque",
            "language_code": "en-US",
            "identify_language": False,
            "timeout_seconds": 180,
        }
    ]
    assert len(storage.deletes) == 1
    assert media.file.closed


def test_voice_orchestration_cleans_s3_after_transcription_failure():
    service, media, _, storage, transcription = make_service()
    processing_error = RuntimeError("primary processing failure")
    transcription.error = processing_error

    with pytest.raises(RuntimeError) as error:
        service.transcribe(make_inbound())

    assert error.value is processing_error
    assert len(storage.deletes) == 1
    assert media.file.closed


def test_voice_orchestration_preserves_primary_error_when_cleanup_also_fails():
    service, media, _, storage, transcription = make_service()
    processing_error = RuntimeError("primary processing failure")
    transcription.error = processing_error
    storage.delete_error = RuntimeError("cleanup failure")

    with pytest.raises(RuntimeError) as error:
        service.transcribe(make_inbound())

    assert error.value is processing_error
    assert media.file.closed


def test_voice_orchestration_has_no_external_delivery_or_state_dependencies():
    service, _, _, _, _ = make_service()

    assert not set(vars(service)).intersection(
        {"outbound", "agentcore", "history", "sqs", "dynamodb"}
    )


def test_voice_orchestration_reports_cleanup_failure_after_success():
    service, media, _, storage, _ = make_service()
    storage.delete_error = RuntimeError("sensitive cleanup detail")

    with pytest.raises(WhatsAppVoiceProcessingError) as error:
        service.transcribe(make_inbound())

    assert "sensitive cleanup detail" not in str(error.value)
    assert media.file.closed
