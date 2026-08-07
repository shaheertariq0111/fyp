from __future__ import annotations

from io import BytesIO

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from src.models.receipt_job import receipt_job_id
from src.services.receipt_storage_service import (
    ReceiptStorageError,
    ReceiptStorageService,
    STORAGE_ARTIFACT_INVALID,
    STORAGE_ARTIFACT_MISSING,
    STORAGE_OPERATION_FAILED,
)


JOB_ID = receipt_job_id("private-order-value", 1)
BUCKET = "private-receipt-artifacts"


class Body(BytesIO):
    def __init__(self, content: bytes):
        super().__init__(content)
        self.read_sizes: list[int] = []
        self.was_closed = False

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)

    def close(self) -> None:
        self.was_closed = True
        super().close()


class FakeS3Client:
    def __init__(self):
        self.put_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self.put_error: BaseException | None = None
        self.get_error: BaseException | None = None
        self.response: dict = {"Body": Body(b"%PDF-loaded")}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        if self.put_error is not None:
            raise self.put_error

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        if self.get_error is not None:
            raise self.get_error
        return self.response


def service(client=None, *, maximum=64) -> ReceiptStorageService:
    return ReceiptStorageService(
        client=client or FakeS3Client(),
        bucket_name=f"  {BUCKET}  ",
        max_pdf_bytes=maximum,
    )


def client_error(status: int, *, code: str = "Failure") -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "raw secret AWS message"},
            "ResponseMetadata": {
                "HTTPStatusCode": status,
                "RequestId": "secret-request-id",
            },
        },
        "S3Operation",
    )


def test_constructor_validates_and_normalizes_configuration():
    storage = service(maximum=32)
    assert storage.bucket_name == BUCKET
    assert storage.max_pdf_bytes == 32


@pytest.mark.parametrize("bucket", ["", "   ", None, 123])
def test_constructor_rejects_invalid_bucket(bucket):
    with pytest.raises(ValueError, match="RECEIPT_STORAGE_BUCKET_REQUIRED"):
        ReceiptStorageService(client=FakeS3Client(), bucket_name=bucket)


@pytest.mark.parametrize("maximum", [0, -1, True, False, 1.5])
def test_constructor_rejects_invalid_maximum(maximum):
    with pytest.raises(ValueError, match="RECEIPT_STORAGE_MAX_PDF_BYTES_INVALID"):
        ReceiptStorageService(
            client=FakeS3Client(),
            bucket_name=BUCKET,
            max_pdf_bytes=maximum,
        )


def test_object_key_is_exact_deterministic_and_versioned():
    first = ReceiptStorageService.object_key(JOB_ID, 1)
    second = ReceiptStorageService.object_key(JOB_ID, 1)
    assert first == f"receipts/{JOB_ID}/v1.pdf"
    assert second == first
    assert ReceiptStorageService.object_key(JOB_ID, 27).endswith("/v27.pdf")
    assert "private-order-value" not in first


@pytest.mark.parametrize("job_id", ["", "rj1_bad", "order-123", None])
def test_object_key_rejects_invalid_job_id(job_id):
    with pytest.raises(ValueError, match="RECEIPT_JOB_ID_INVALID"):
        ReceiptStorageService.object_key(job_id, 1)


@pytest.mark.parametrize("version", [0, -1, True, False, "1", None])
def test_object_key_rejects_invalid_receipt_version(version):
    with pytest.raises(ValueError, match="RECEIPT_VERSION_INVALID"):
        ReceiptStorageService.object_key(JOB_ID, version)


def test_store_uses_exact_private_encrypted_s3_contract():
    client = FakeS3Client()
    pdf = b"%PDF-exact-content"
    result = service(client).store_pdf(JOB_ID, 2, pdf)

    assert client.put_calls == [{
        "Bucket": BUCKET,
        "Key": f"receipts/{JOB_ID}/v2.pdf",
        "Body": pdf,
        "ContentType": "application/pdf",
        "ServerSideEncryption": "AES256",
    }]
    assert result.key == f"receipts/{JOB_ID}/v2.pdf"
    assert result.content is None
    assert result.size_bytes == len(pdf)
    assert result.content_type == "application/pdf"
    assert not hasattr(client, "generate_presigned_url")


@pytest.mark.parametrize("content", [b"", bytearray(b"%PDF-x"), "%PDF-x", b"not-pdf"])
def test_store_rejects_invalid_content_without_s3_call(content):
    client = FakeS3Client()
    with pytest.raises(ValueError, match="RECEIPT_STORAGE_PDF_INVALID"):
        service(client).store_pdf(JOB_ID, 1, content)
    assert client.put_calls == []


def test_store_rejects_oversized_content_and_accepts_exact_limit():
    client = FakeS3Client()
    maximum = 10
    storage = service(client, maximum=maximum)
    exact = b"%PDF-" + b"x" * (maximum - 5)
    result = storage.store_pdf(JOB_ID, 1, exact)
    assert result.size_bytes == maximum

    with pytest.raises(ValueError, match="RECEIPT_STORAGE_PDF_INVALID"):
        storage.store_pdf(JOB_ID, 1, exact + b"x")
    assert len(client.put_calls) == 1


def test_repeated_store_uses_same_key_without_randomness():
    client = FakeS3Client()
    storage = service(client)
    storage.store_pdf(JOB_ID, 4, b"%PDF-same")
    storage.store_pdf(JOB_ID, 4, b"%PDF-same")
    assert client.put_calls[0]["Key"] == client.put_calls[1]["Key"]
    assert client.put_calls[0]["Key"] == f"receipts/{JOB_ID}/v4.pdf"


def test_load_uses_computed_key_and_returns_exact_validated_pdf():
    client = FakeS3Client()
    pdf = b"%PDF-stored-exactly"
    body = Body(pdf)
    client.response = {"Body": body, "ContentLength": len(pdf)}

    result = service(client).load_pdf(JOB_ID, 3)

    assert client.get_calls == [{
        "Bucket": BUCKET,
        "Key": f"receipts/{JOB_ID}/v3.pdf",
    }]
    assert result.content == pdf
    assert result.size_bytes == len(pdf)
    assert result.key == f"receipts/{JOB_ID}/v3.pdf"
    assert body.read_sizes == [65]
    assert body.was_closed is True


@pytest.mark.parametrize("content", [b"", b"not-pdf"])
def test_load_rejects_empty_or_non_pdf_stored_artifact(content):
    client = FakeS3Client()
    body = Body(content)
    client.response = {"Body": body}

    with pytest.raises(ReceiptStorageError) as raised:
        service(client).load_pdf(JOB_ID, 1)

    assert raised.value.error_code == STORAGE_ARTIFACT_INVALID
    assert raised.value.retryable is False
    assert body.was_closed is True


def test_load_rejects_actual_oversize_after_bounded_read():
    client = FakeS3Client()
    body = Body(b"%PDF-" + b"x" * 20)
    client.response = {"Body": body}

    with pytest.raises(ReceiptStorageError) as raised:
        service(client, maximum=10).load_pdf(JOB_ID, 1)

    assert raised.value.error_code == STORAGE_ARTIFACT_INVALID
    assert body.read_sizes == [11]
    assert body.was_closed is True


def test_load_rejects_declared_oversize_without_reading_and_closes_body():
    client = FakeS3Client()
    body = Body(b"%PDF-small")
    client.response = {"Body": body, "ContentLength": 11}

    with pytest.raises(ReceiptStorageError) as raised:
        service(client, maximum=10).load_pdf(JOB_ID, 1)

    assert raised.value.error_code == STORAGE_ARTIFACT_INVALID
    assert body.read_sizes == []
    assert body.was_closed is True


@pytest.mark.parametrize(
    ("operation", "status", "retryable"),
    [
        ("put", 500, True),
        ("put", 429, True),
        ("put", 403, False),
        ("get", 500, True),
        ("get", 403, False),
    ],
)
def test_client_errors_are_safely_classified(operation, status, retryable):
    client = FakeS3Client()
    setattr(client, f"{operation}_error", client_error(status))

    with pytest.raises(ReceiptStorageError) as raised:
        if operation == "put":
            service(client).store_pdf(JOB_ID, 1, b"%PDF-valid")
        else:
            service(client).load_pdf(JOB_ID, 1)

    error = raised.value
    assert error.error_code == STORAGE_OPERATION_FAILED
    assert error.retryable is retryable
    assert error.status_code == status
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize(
    ("status", "code"),
    [(404, "Failure"), (400, "NoSuchKey")],
)
def test_load_missing_artifact_is_stable_and_non_retryable(status, code):
    client = FakeS3Client()
    client.get_error = client_error(status, code=code)

    with pytest.raises(ReceiptStorageError) as raised:
        service(client).load_pdf(JOB_ID, 1)

    assert raised.value.error_code == STORAGE_ARTIFACT_MISSING
    assert raised.value.retryable is False


@pytest.mark.parametrize("operation", ["put", "get"])
def test_botocore_errors_are_retryable(operation):
    client = FakeS3Client()
    setattr(
        client,
        f"{operation}_error",
        EndpointConnectionError(endpoint_url="https://private.invalid"),
    )

    with pytest.raises(ReceiptStorageError) as raised:
        if operation == "put":
            service(client).store_pdf(JOB_ID, 1, b"%PDF-valid")
        else:
            service(client).load_pdf(JOB_ID, 1)

    assert raised.value.error_code == STORAGE_OPERATION_FAILED
    assert raised.value.retryable is True
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_storage_error_text_is_sanitized():
    client = FakeS3Client()
    client.put_error = client_error(403)

    with pytest.raises(ReceiptStorageError) as raised:
        service(client).store_pdf(JOB_ID, 1, b"%PDF-customer-payload")

    text = str(raised.value)
    for secret in (
        "raw secret AWS message",
        "secret-request-id",
        BUCKET,
        JOB_ID,
        "customer",
        "private-order-value",
    ):
        assert secret not in text


@pytest.mark.parametrize("error", [RuntimeError("bug"), TypeError("bug")])
@pytest.mark.parametrize("operation", ["put", "get"])
def test_programming_errors_propagate_unchanged(operation, error):
    client = FakeS3Client()
    setattr(client, f"{operation}_error", error)

    with pytest.raises(type(error)) as raised:
        if operation == "put":
            service(client).store_pdf(JOB_ID, 1, b"%PDF-valid")
        else:
            service(client).load_pdf(JOB_ID, 1)

    assert raised.value is error
