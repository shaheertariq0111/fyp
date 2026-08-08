from __future__ import annotations

import logging

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from src.infrastructure.secrets import (
    SECRET_LOAD_FAILED,
    SECRET_VALUE_INVALID,
    SecretLoadError,
    SecretsManagerSecretLoader,
)


SECRET_ARN = "arn:aws:secretsmanager:us-west-2:123456789012:secret:receipt"
SECRET_VALUE = "private-agentflo-api-key"


class FakeSecretsManager:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get_secret_value(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def test_secret_loader_uses_exact_arn_and_returns_secret_string():
    client = FakeSecretsManager({"SecretString": SECRET_VALUE})

    result = SecretsManagerSecretLoader(client, SECRET_ARN).load()

    assert result == SECRET_VALUE
    assert client.calls == [{"SecretId": SECRET_ARN}]


@pytest.mark.parametrize("response", [{}, {"SecretBinary": b"private"}, {"SecretString": "  "}])
def test_secret_loader_rejects_missing_or_blank_secret_string(response):
    with pytest.raises(SecretLoadError, match=SECRET_VALUE_INVALID):
        SecretsManagerSecretLoader(
            FakeSecretsManager(response),
            SECRET_ARN,
        ).load()


@pytest.mark.parametrize(
    "error",
    [
        ClientError(
            {
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": "private backend response",
                }
            },
            "GetSecretValue",
        ),
        EndpointConnectionError(endpoint_url="https://private.example.test"),
    ],
)
def test_expected_aws_failures_are_sanitized(error, caplog):
    with caplog.at_level(logging.INFO), pytest.raises(
        SecretLoadError,
        match=SECRET_LOAD_FAILED,
    ) as raised:
        SecretsManagerSecretLoader(
            FakeSecretsManager(error=error),
            SECRET_ARN,
        ).load()

    assert str(raised.value) == SECRET_LOAD_FAILED
    assert SECRET_VALUE not in caplog.text
    assert "private backend response" not in str(raised.value)
    assert "private.example.test" not in str(raised.value)
    assert caplog.records == []


@pytest.mark.parametrize("error", [RuntimeError("programming failure"), TypeError("bad fake")])
def test_unexpected_programming_failures_propagate(error):
    with pytest.raises(type(error), match=str(error)):
        SecretsManagerSecretLoader(
            FakeSecretsManager(error=error),
            SECRET_ARN,
        ).load()
