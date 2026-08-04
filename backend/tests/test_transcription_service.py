import json

import httpx
import pytest
from botocore.exceptions import ClientError

from src.services.transcription_service import (
    TranscriptionService,
    VoiceTranscriptionError,
)


MEDIA_URI = "s3://voice-bucket/voice-input/opaque.ogg"
TRANSCRIPT_URI = "https://s3.us-east-1.amazonaws.com/transcribe-output/result.json"


class FakeTranscribeClient:
    def __init__(self, jobs=None):
        self.jobs = list(jobs or [])
        self.started = []
        self.gotten = []
        self.deleted = []
        self.start_error = None
        self.delete_error = None

    def start_transcription_job(self, **kwargs):
        self.started.append(kwargs)
        if self.start_error is not None:
            raise self.start_error

    def get_transcription_job(self, **kwargs):
        self.gotten.append(kwargs)
        job = self.jobs.pop(0) if len(self.jobs) > 1 else self.jobs[0]
        return {"TranscriptionJob": job}

    def delete_transcription_job(self, **kwargs):
        self.deleted.append(kwargs)
        if self.delete_error is not None:
            raise self.delete_error


def transcript_http_client(transcript="spoken order", *, body=None, headers=None):
    response_body = body
    if response_body is None:
        response_body = json.dumps(
            {"results": {"transcripts": [{"transcript": transcript}]}}
        ).encode()

    def handler(request):
        return httpx.Response(
            200,
            content=response_body,
            headers=headers,
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def completed_job(uri=TRANSCRIPT_URI):
    return {
        "TranscriptionJobStatus": "COMPLETED",
        "Media": {"MediaFileUri": MEDIA_URI},
        "Transcript": {"TranscriptFileUri": uri},
    }


def make_service(client, **overrides):
    return TranscriptionService(
        client=client,
        aws_region="us-east-1",
        transcript_client=overrides.pop("transcript_client", transcript_http_client()),
        sleep=overrides.pop("sleep", lambda _seconds: None),
        **overrides,
    )


def transcribe(service, **overrides):
    values = {
        "media_s3_uri": MEDIA_URI,
        "media_format": "ogg",
        "job_name": "fyp-dev-whatsapp-voice-safehash",
        "language_code": "en-US",
        "identify_language": False,
        "timeout_seconds": 180,
    }
    values.update(overrides)
    return service.transcribe(**values)


def test_transcription_job_name_is_deterministic_safe_and_opaque():
    first = TranscriptionService.build_job_name("raw-message-123", "fyp-dev")
    second = TranscriptionService.build_job_name("raw-message-123", "fyp-dev")

    assert first == second
    assert first.startswith("fyp-dev-whatsapp-voice-")
    assert "raw-message-123" not in first
    assert len(first.removeprefix("fyp-dev-whatsapp-voice-")) == 64


def test_transcription_starts_with_explicit_language_and_cleans_up():
    client = FakeTranscribeClient([completed_job()])
    result = transcribe(make_service(client))

    assert result == "spoken order"
    assert client.started == [
        {
            "TranscriptionJobName": "fyp-dev-whatsapp-voice-safehash",
            "Media": {"MediaFileUri": MEDIA_URI},
            "MediaFormat": "ogg",
            "LanguageCode": "en-US",
        }
    ]
    assert client.deleted == [
        {"TranscriptionJobName": "fyp-dev-whatsapp-voice-safehash"}
    ]


def test_transcription_supports_automatic_language_identification():
    client = FakeTranscribeClient([completed_job()])

    transcribe(
        make_service(client),
        language_code="",
        identify_language=True,
    )

    assert client.started[0]["IdentifyLanguage"] is True
    assert "LanguageCode" not in client.started[0]


@pytest.mark.parametrize(
    "overrides",
    [
        {"media_s3_uri": "https://voice-bucket.example.test/file.ogg"},
        {"media_s3_uri": "s3://voice-bucket/file.ogg?token=secret"},
        {"timeout_seconds": 181},
        {"language_code": "", "identify_language": False},
        {"language_code": "en-US", "identify_language": True},
    ],
)
def test_transcription_rejects_invalid_configuration(overrides):
    client = FakeTranscribeClient([completed_job()])

    with pytest.raises(VoiceTranscriptionError) as error:
        transcribe(make_service(client), **overrides)

    assert error.value.error_code == "VOICE_TRANSCRIPTION_CONFIGURATION_INVALID"
    assert client.started == []


def test_transcription_polls_queued_in_progress_and_completed():
    client = FakeTranscribeClient(
        [
            {"TranscriptionJobStatus": "QUEUED"},
            {"TranscriptionJobStatus": "IN_PROGRESS"},
            completed_job(),
        ]
    )

    assert transcribe(make_service(client)) == "spoken order"
    assert len(client.gotten) == 3


def conflict_error():
    return ClientError(
        {"Error": {"Code": "ConflictException", "Message": "exists"}},
        "StartTranscriptionJob",
    )


def test_transcription_recovers_matching_deterministic_conflict():
    client = FakeTranscribeClient([completed_job(), completed_job()])
    client.start_error = conflict_error()

    assert transcribe(make_service(client)) == "spoken order"
    assert len(client.gotten) == 2


def test_transcription_rejects_conflict_with_different_media_input():
    client = FakeTranscribeClient(
        [
            {
                "TranscriptionJobStatus": "COMPLETED",
                "Media": {"MediaFileUri": "s3://other-bucket/unexpected.ogg"},
                "Transcript": {"TranscriptFileUri": TRANSCRIPT_URI},
            }
        ]
    )
    client.start_error = conflict_error()

    with pytest.raises(VoiceTranscriptionError) as error:
        transcribe(make_service(client))

    assert error.value.error_code == "VOICE_TRANSCRIPTION_CONFLICT"
    assert MEDIA_URI not in str(error.value)


def test_transcription_reports_failed_job_without_failure_reason():
    sensitive_reason = "provider failure containing transcript or URI"
    client = FakeTranscribeClient(
        [{"TranscriptionJobStatus": "FAILED", "FailureReason": sensitive_reason}]
    )

    with pytest.raises(VoiceTranscriptionError) as error:
        transcribe(make_service(client))

    assert error.value.error_code == "VOICE_TRANSCRIPTION_FAILED"
    assert sensitive_reason not in str(error.value)
    assert client.deleted


def test_transcription_timeout_uses_injected_monotonic_clock():
    values = iter([0.0, 181.0])
    client = FakeTranscribeClient([{"TranscriptionJobStatus": "IN_PROGRESS"}])

    with pytest.raises(VoiceTranscriptionError) as error:
        transcribe(make_service(client, clock=lambda: next(values)))

    assert error.value.error_code == "VOICE_TRANSCRIPTION_TIMEOUT"
    assert client.deleted == []


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"{}",
        b'{"results":{"transcripts":[]}}',
        b'{"results":{"transcripts":[{"transcript":"  "}]}}',
    ],
)
def test_transcription_rejects_invalid_or_empty_transcript(body):
    client = FakeTranscribeClient([completed_job()])

    with pytest.raises(VoiceTranscriptionError) as error:
        transcribe(
            make_service(client, transcript_client=transcript_http_client(body=body))
        )

    assert error.value.error_code == "VOICE_TRANSCRIPT_INVALID"
    assert client.deleted


def test_transcription_enforces_bounded_transcript_response():
    client = FakeTranscribeClient([completed_job()])

    with pytest.raises(VoiceTranscriptionError) as error:
        transcribe(
            make_service(
                client,
                transcript_client=transcript_http_client(body=b"x" * 11),
                transcript_max_bytes=10,
            )
        )

    assert error.value.error_code == "VOICE_TRANSCRIPT_DOWNLOAD_FAILED"
    assert client.deleted


def test_transcription_rejects_non_aws_transcript_host_without_request():
    requested = []

    def handler(request):
        requested.append(request)
        return httpx.Response(200, content=b"{}", request=request)

    client = FakeTranscribeClient(
        [completed_job("https://attacker.example.test/transcript.json")]
    )

    with pytest.raises(VoiceTranscriptionError) as error:
        transcribe(
            make_service(
                client,
                transcript_client=httpx.Client(transport=httpx.MockTransport(handler)),
            )
        )

    assert error.value.error_code == "VOICE_TRANSCRIPT_DOWNLOAD_FAILED"
    assert requested == []


def test_transcription_cleanup_failure_does_not_expose_sensitive_values():
    client = FakeTranscribeClient([completed_job()])
    client.delete_error = RuntimeError("sensitive provider details")

    with pytest.raises(VoiceTranscriptionError) as error:
        transcribe(make_service(client))

    assert error.value.error_code == "VOICE_TRANSCRIPTION_CLEANUP_FAILED"
    assert "sensitive provider details" not in str(error.value)
    assert "spoken order" not in str(error.value)
    assert MEDIA_URI not in str(error.value)


def test_transcription_cleanup_warning_contains_metadata_only(caplog):
    sensitive_reason = "sensitive transcript failure reason"
    client = FakeTranscribeClient(
        [{"TranscriptionJobStatus": "FAILED", "FailureReason": sensitive_reason}]
    )
    client.delete_error = RuntimeError("sensitive cleanup detail")

    with pytest.raises(VoiceTranscriptionError):
        transcribe(make_service(client))

    cleanup_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "voice_transcription_cleanup_failed"
    ]
    assert len(cleanup_records) == 1
    assert cleanup_records[0].error_code == "VOICE_TRANSCRIPTION_CLEANUP_FAILED"
    assert sensitive_reason not in caplog.text
    assert "sensitive cleanup detail" not in caplog.text
    assert MEDIA_URI not in caplog.text
