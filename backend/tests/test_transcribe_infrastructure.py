from types import SimpleNamespace
import logging

import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
)

from src.infrastructure.transcribe import (
    ASSUME_ROLE_DURATION_SECONDS,
    ASSUME_ROLE_SESSION_NAME,
    TranscribeRoleAssumptionError,
    get_transcribe_client_provider,
)
from src.infrastructure.logging import JsonFormatter
from src.services.transcription_service import (
    TranscriptionService,
    VoiceTranscriptionError,
)


ROLE_ARN = "arn:aws:iam::769377364291:role/fyp-cross-account-transcribe"
CREDENTIALS = {
    "AccessKeyId": "temporary-access-key",
    "SecretAccessKey": "temporary-secret-key",
    "SessionToken": "temporary-session-token",
}


def settings(role_arn=""):
    return SimpleNamespace(
        aws_region="us-east-1",
        voice_transcribe_role_arn=role_arn,
    )


class FakeStsClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def assume_role(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class RecordingClientFactory:
    def __init__(self, sts_client=None):
        self.sts_client = sts_client
        self.transcribe_client = object()
        self.calls = []

    def __call__(self, service_name, **kwargs):
        self.calls.append((service_name, kwargs))
        if service_name == "sts":
            if isinstance(self.sts_client, Exception):
                raise self.sts_client
            return self.sts_client
        if service_name == "transcribe":
            return self.transcribe_client
        raise AssertionError(service_name)


def client_error(code, status_code, message="private role and credential detail"):
    return ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status_code},
        },
        "AssumeRole",
    )


def test_empty_role_arn_reuses_the_existing_same_account_client():
    factory = RecordingClientFactory()

    provider = get_transcribe_client_provider(
        settings(),
        client_factory=factory,
    )

    assert provider() is factory.transcribe_client
    assert provider() is factory.transcribe_client
    assert [name for name, _kwargs in factory.calls] == ["transcribe"]
    _, kwargs = factory.calls[0]
    assert kwargs["region_name"] == "us-east-1"
    assert kwargs["config"].retries == {"max_attempts": 3, "mode": "standard"}
    assert "aws_access_key_id" not in kwargs
    assert "aws_secret_access_key" not in kwargs
    assert "aws_session_token" not in kwargs


def test_configured_role_is_assumed_once_and_credentials_build_client():
    sts = FakeStsClient(response={"Credentials": CREDENTIALS})
    factory = RecordingClientFactory(sts)
    provider = get_transcribe_client_provider(
        settings(ROLE_ARN),
        client_factory=factory,
    )

    assert provider() is factory.transcribe_client

    assert sts.calls == [{
        "RoleArn": ROLE_ARN,
        "RoleSessionName": "fyp-whatsapp-voice-transcribe",
        "DurationSeconds": 900,
    }]
    assert ASSUME_ROLE_SESSION_NAME == "fyp-whatsapp-voice-transcribe"
    assert ASSUME_ROLE_DURATION_SECONDS == 900
    assert [name for name, _kwargs in factory.calls] == ["sts", "transcribe"]
    _, transcribe_kwargs = factory.calls[1]
    assert transcribe_kwargs["config"].retries == {
        "max_attempts": 3,
        "mode": "standard",
    }
    assert transcribe_kwargs == {
        "region_name": "us-east-1",
        "config": transcribe_kwargs["config"],
        "aws_access_key_id": CREDENTIALS["AccessKeyId"],
        "aws_secret_access_key": CREDENTIALS["SecretAccessKey"],
        "aws_session_token": CREDENTIALS["SessionToken"],
    }


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"Credentials": None},
        {"Credentials": {}},
        *[
            {
                "Credentials": {
                    **CREDENTIALS,
                    field: missing_value,
                }
            }
            for field in CREDENTIALS
            for missing_value in (None, "", "   ")
        ],
    ],
)
def test_malformed_or_incomplete_assume_role_response_fails_closed(response):
    provider = get_transcribe_client_provider(
        settings(ROLE_ARN),
        client_factory=RecordingClientFactory(FakeStsClient(response=response)),
    )

    with pytest.raises(TranscribeRoleAssumptionError) as error:
        provider()

    assert error.value.exception_type == "MalformedAssumeRoleResponse"
    assert error.value.retryable is False


@pytest.mark.parametrize(
    ("error", "retryable", "aws_error_code", "status_code"),
    [
        (client_error("AccessDenied", 403), False, "AccessDenied", 403),
        (client_error("ValidationError", 400), False, "ValidationError", 400),
        (client_error("ThrottlingException", 400), True, "ThrottlingException", 400),
        (client_error("OtherError", 429), True, "OtherError", 429),
        (client_error("InternalFailure", 500), True, "InternalFailure", 500),
        (
            EndpointConnectionError(endpoint_url="https://private-role.example"),
            True,
            None,
            None,
        ),
        (NoCredentialsError(), False, None, None),
        (RuntimeError("private runtime detail"), False, None, None),
    ],
)
def test_assume_role_failure_classification(
    error,
    retryable,
    aws_error_code,
    status_code,
):
    provider = get_transcribe_client_provider(
        settings(ROLE_ARN),
        client_factory=RecordingClientFactory(FakeStsClient(error=error)),
    )

    with pytest.raises(TranscribeRoleAssumptionError) as raised:
        provider()

    assert raised.value.retryable is retryable
    assert raised.value.aws_error_code == aws_error_code
    assert raised.value.status_code == status_code
    assert str(raised.value) == "Cross-account Transcribe authorization failed."


def test_assume_role_failure_diagnostics_never_log_sensitive_values(caplog):
    sensitive_values = (
        ROLE_ARN,
        CREDENTIALS["AccessKeyId"],
        CREDENTIALS["SecretAccessKey"],
        CREDENTIALS["SessionToken"],
        "s3://private-bucket/voice-input/private-object.ogg",
        "private-transcription-job-name",
        "private-audio-id",
    )
    sts_error = client_error(
        "AccessDenied",
        403,
        " ".join(sensitive_values),
    )
    provider = get_transcribe_client_provider(
        settings(ROLE_ARN),
        client_factory=RecordingClientFactory(FakeStsClient(error=sts_error)),
    )
    service = TranscriptionService(
        client_provider=provider,
        aws_region="us-east-1",
    )

    with caplog.at_level(logging.WARNING), pytest.raises(
        VoiceTranscriptionError
    ) as error:
        service.transcribe(
            media_s3_uri="s3://voice-bucket/voice-input/opaque.ogg",
            media_format="ogg",
            job_name="fyp-dev-whatsapp-voice-safehash",
            language_code="en-US",
            identify_language=False,
            timeout_seconds=180,
        )

    assert error.value.error_code == "VOICE_TRANSCRIBE_ROLE_ASSUME_FAILED"
    assert error.value.retryable is False
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None)
        == "voice_transcribe_role_assume_failed"
    )
    formatted = JsonFormatter().format(record)
    for forbidden in sensitive_values:
        assert forbidden not in caplog.text
        assert forbidden not in formatted
