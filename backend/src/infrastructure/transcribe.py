import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    HTTPClientError,
    ProxyConnectionError,
    ReadTimeoutError,
)

from .config import Settings


AWS_CLIENT_CONFIG = Config(retries={"max_attempts": 3, "mode": "standard"})
ASSUME_ROLE_SESSION_NAME = "fyp-whatsapp-voice-transcribe"
ASSUME_ROLE_DURATION_SECONDS = 900
RETRYABLE_STS_ERROR_CODES = frozenset({
    "InternalFailure",
    "InternalFailureException",
    "PriorRequestNotComplete",
    "RequestLimitExceeded",
    "RequestTimeout",
    "RequestTimeoutException",
    "ServiceUnavailable",
    "ServiceUnavailableException",
    "Throttling",
    "ThrottlingException",
    "TooManyRequestsException",
})
RETRYABLE_STS_TRANSPORT_ERRORS = (
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    HTTPClientError,
    ProxyConnectionError,
    ReadTimeoutError,
)


class TranscribeRoleAssumptionError(Exception):
    """Sanitized metadata for a failed cross-account client acquisition."""

    def __init__(
        self,
        *,
        exception_type: str,
        retryable: bool,
        aws_error_code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.exception_type = exception_type
        self.retryable = retryable
        self.aws_error_code = aws_error_code
        self.status_code = status_code
        super().__init__("Cross-account Transcribe authorization failed.")


def get_transcribe_client(settings: Settings, *, client_factory=None):
    factory = client_factory or boto3.client
    return factory(
        "transcribe",
        region_name=settings.aws_region,
        config=AWS_CLIENT_CONFIG,
    )


class AssumeRoleTranscribeClientProvider:
    def __init__(
        self,
        *,
        role_arn: str,
        region_name: str,
        client_factory=None,
    ) -> None:
        self.role_arn = role_arn
        self.region_name = region_name
        self.client_factory = client_factory

    def __call__(self):
        factory = self.client_factory or boto3.client
        try:
            sts_client = factory(
                "sts",
                region_name=self.region_name,
                config=AWS_CLIENT_CONFIG,
            )
            response = sts_client.assume_role(
                RoleArn=self.role_arn,
                RoleSessionName=ASSUME_ROLE_SESSION_NAME,
                DurationSeconds=ASSUME_ROLE_DURATION_SECONDS,
            )
            credentials = self._validated_credentials(response)
            return factory(
                "transcribe",
                region_name=self.region_name,
                config=AWS_CLIENT_CONFIG,
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretAccessKey"],
                aws_session_token=credentials["SessionToken"],
            )
        except TranscribeRoleAssumptionError:
            raise
        except ClientError as exc:
            aws_error_code = exc.response.get("Error", {}).get("Code")
            aws_error_code = (
                aws_error_code if isinstance(aws_error_code, str) else None
            )
            status_code = exc.response.get("ResponseMetadata", {}).get(
                "HTTPStatusCode"
            )
            status_code = status_code if isinstance(status_code, int) else None
            raise TranscribeRoleAssumptionError(
                exception_type=type(exc).__name__,
                aws_error_code=aws_error_code,
                status_code=status_code,
                retryable=_retryable_sts_client_error(
                    aws_error_code,
                    status_code,
                ),
            ) from None
        except BotoCoreError as exc:
            raise TranscribeRoleAssumptionError(
                exception_type=type(exc).__name__,
                retryable=isinstance(exc, RETRYABLE_STS_TRANSPORT_ERRORS),
            ) from None
        except Exception as exc:
            raise TranscribeRoleAssumptionError(
                exception_type=type(exc).__name__,
                retryable=False,
            ) from None

    @staticmethod
    def _validated_credentials(response: object) -> dict[str, str]:
        if not isinstance(response, dict):
            raise TranscribeRoleAssumptionError(
                exception_type="MalformedAssumeRoleResponse",
                retryable=False,
            )
        credentials = response.get("Credentials")
        if not isinstance(credentials, dict):
            raise TranscribeRoleAssumptionError(
                exception_type="MalformedAssumeRoleResponse",
                retryable=False,
            )
        required = ("AccessKeyId", "SecretAccessKey", "SessionToken")
        if any(
            not isinstance(credentials.get(name), str)
            or not credentials[name].strip()
            for name in required
        ):
            raise TranscribeRoleAssumptionError(
                exception_type="MalformedAssumeRoleResponse",
                retryable=False,
            )
        return {name: credentials[name] for name in required}


def _retryable_sts_client_error(
    aws_error_code: str | None,
    status_code: int | None,
) -> bool:
    return bool(
        status_code == 429
        or (status_code is not None and status_code >= 500)
        or aws_error_code in RETRYABLE_STS_ERROR_CODES
    )


def get_transcribe_client_provider(settings: Settings, *, client_factory=None):
    role_arn = settings.voice_transcribe_role_arn.strip()
    if not role_arn:
        client = get_transcribe_client(settings, client_factory=client_factory)
        return lambda: client
    return AssumeRoleTranscribeClientProvider(
        role_arn=role_arn,
        region_name=settings.aws_region,
        client_factory=client_factory,
    )
