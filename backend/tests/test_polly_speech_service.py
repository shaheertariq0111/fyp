from __future__ import annotations

import logging
from concurrent.futures import TimeoutError as FutureTimeoutError
from io import BytesIO

import pytest
from botocore.exceptions import ClientError

from src.infrastructure.polly import get_polly_client
from src.services.polly_speech_service import (
    PollySpeechSynthesisService,
    PollySynthesisError,
)
from test_config import make_test_settings


class ClosingStream(BytesIO):
    closed_by_service = False

    def close(self):
        self.closed_by_service = True
        super().close()


class FailingStream:
    def __init__(self):
        self.closed_by_service = False

    def read(self, _size):
        raise OSError("private stream detail")

    def close(self):
        self.closed_by_service = True


class PollyClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def synthesize_speech(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def service(client, **overrides):
    return PollySpeechSynthesisService(
        client=client,
        voice_id=overrides.get("voice_id", "Joanna"),
        engine=overrides.get("engine", "neural"),
        language_code=overrides.get("language_code", "en-US"),
        max_text_chars=overrides.get("max_text_chars", 100),
        max_audio_bytes=overrides.get("max_audio_bytes", 1024),
        timeout_seconds=overrides.get("timeout_seconds", 5),
        executor_factory=overrides.get("executor_factory", __import__(
            "concurrent.futures", fromlist=["ThreadPoolExecutor"]
        ).ThreadPoolExecutor),
    )


def test_polly_client_uses_primary_region_and_standard_credentials_only():
    calls = []
    expected = object()

    def factory(name, **kwargs):
        calls.append((name, kwargs))
        return expected

    settings = make_test_settings(
        voice_reply_synthesis_timeout_seconds=17,
    )
    assert get_polly_client(settings, client_factory=factory) is expected
    assert calls[0][0] == "polly"
    assert calls[0][1]["region_name"] == "us-west-2"
    assert calls[0][1]["config"].connect_timeout == 17
    assert calls[0][1]["config"].read_timeout == 17
    assert not {
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_session_token",
        "role_arn",
    } & set(calls[0][1])


def test_synthesis_uses_selected_voice_engine_language_and_closes_stream():
    stream = ClosingStream(b"synthetic-mp3")
    client = PollyClient({"AudioStream": stream})

    assert service(client).synthesize("Synthetic reply") == b"synthetic-mp3"
    assert client.calls == [{
        "Text": "Synthetic reply",
        "TextType": "text",
        "OutputFormat": "mp3",
        "VoiceId": "Joanna",
        "Engine": "neural",
        "LanguageCode": "en-US",
    }]
    assert stream.closed_by_service is True


def test_synthesis_omits_unconfigured_language():
    client = PollyClient({"AudioStream": ClosingStream(b"mp3")})
    service(client, language_code="").synthesize("reply")
    assert "LanguageCode" not in client.calls[0]


@pytest.mark.parametrize(
    ("text", "error_code"),
    [("", "VOICE_REPLY_TEXT_EMPTY"), ("   ", "VOICE_REPLY_TEXT_EMPTY")],
)
def test_synthesis_rejects_empty_text(text, error_code):
    client = PollyClient()
    with pytest.raises(PollySynthesisError) as error:
        service(client).synthesize(text)
    assert error.value.error_code == error_code
    assert client.calls == []


def test_synthesis_enforces_text_and_audio_limits_and_closes_stream():
    with pytest.raises(PollySynthesisError, match="Voice reply synthesis failed") as error:
        service(PollyClient(), max_text_chars=3).synthesize("four")
    assert error.value.error_code == "VOICE_REPLY_TEXT_TOO_LONG"

    stream = ClosingStream(b"12345")
    with pytest.raises(PollySynthesisError) as error:
        service(
            PollyClient({"AudioStream": stream}),
            max_audio_bytes=4,
        ).synthesize("reply")
    assert error.value.error_code == "VOICE_REPLY_SYNTHESIS_TOO_LARGE"
    assert stream.closed_by_service is True


def test_synthesis_closes_stream_when_read_fails():
    stream = FailingStream()
    with pytest.raises(PollySynthesisError) as error:
        service(PollyClient({"AudioStream": stream})).synthesize("reply")
    assert error.value.error_code == "VOICE_REPLY_POLLY_REQUEST_FAILED"
    assert stream.closed_by_service is True


def test_polly_aws_failure_is_sanitized_and_does_not_log_text(caplog):
    private_text = "private generated speech text"
    private_message = "private AWS exception message"
    client = PollyClient(error=ClientError(
        {
            "Error": {"Code": "AccessDeniedException", "Message": private_message},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        },
        "SynthesizeSpeech",
    ))

    with caplog.at_level(logging.INFO), pytest.raises(PollySynthesisError) as error:
        service(client).synthesize(private_text)

    assert error.value.error_code == "VOICE_REPLY_POLLY_REQUEST_FAILED"
    assert error.value.retryable is False
    assert private_text not in caplog.text
    assert private_message not in caplog.text
    assert private_text not in str(error.value)
    assert private_message not in str(error.value)


class TimeoutFuture:
    def result(self, timeout):
        assert timeout == 2
        raise FutureTimeoutError

    def cancel(self):
        return True


class TimeoutExecutor:
    def __init__(self, **kwargs):
        assert kwargs == {"max_workers": 1}
        self.shutdown_call = None

    def submit(self, function, text):
        assert callable(function)
        assert text == "reply"
        return TimeoutFuture()

    def shutdown(self, **kwargs):
        self.shutdown_call = kwargs


def test_synthesis_has_bounded_timeout():
    with pytest.raises(PollySynthesisError) as error:
        service(
            PollyClient(),
            timeout_seconds=2,
            executor_factory=TimeoutExecutor,
        ).synthesize("reply")
    assert error.value.error_code == "VOICE_REPLY_SYNTHESIS_TIMEOUT"
    assert error.value.retryable is True
