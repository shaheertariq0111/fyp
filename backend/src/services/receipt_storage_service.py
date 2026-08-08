from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from src.models.receipt_job import validate_receipt_job_id


DEFAULT_MAX_PDF_BYTES = 5 * 1024 * 1024
PDF_CONTENT_TYPE = "application/pdf"
STORAGE_OPERATION_FAILED = "RECEIPT_STORAGE_OPERATION_FAILED"
STORAGE_ARTIFACT_MISSING = "RECEIPT_STORAGE_ARTIFACT_MISSING"
STORAGE_ARTIFACT_INVALID = "RECEIPT_STORAGE_ARTIFACT_INVALID"


@dataclass(frozen=True, slots=True)
class StoredReceiptArtifact:
    key: str
    content: bytes | None
    size_bytes: int
    content_type: str = PDF_CONTENT_TYPE


class ReceiptStorageError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        self.error_code = error_code
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(error_code)


class ReceiptStorageService:
    def __init__(
        self,
        *,
        client: Any,
        bucket_name: str,
        max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES,
    ) -> None:
        if not isinstance(bucket_name, str) or not bucket_name.strip():
            raise ValueError("RECEIPT_STORAGE_BUCKET_REQUIRED")
        if type(max_pdf_bytes) is not int or max_pdf_bytes < 1:
            raise ValueError("RECEIPT_STORAGE_MAX_PDF_BYTES_INVALID")
        self.client = client
        self.bucket_name = bucket_name.strip()
        self.max_pdf_bytes = max_pdf_bytes

    @staticmethod
    def object_key(job_id: str, receipt_version: int) -> str:
        validated_job_id = validate_receipt_job_id(job_id)
        if type(receipt_version) is not int or receipt_version < 1:
            raise ValueError("RECEIPT_VERSION_INVALID")
        return f"receipts/{validated_job_id}/v{receipt_version}.pdf"

    def store_pdf(
        self,
        job_id: str,
        receipt_version: int,
        content: bytes,
    ) -> StoredReceiptArtifact:
        key = self.object_key(job_id, receipt_version)
        self._validate_local_pdf(content)

        failure: tuple[str, bool, int | None] | None = None
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=content,
                ContentType=PDF_CONTENT_TYPE,
                ServerSideEncryption="AES256",
            )
        except ClientError as exc:
            failure = self._classify_client_error(exc, loading=False)
        except BotoCoreError:
            failure = (STORAGE_OPERATION_FAILED, True, None)
        if failure is not None:
            raise ReceiptStorageError(
                failure[0],
                retryable=failure[1],
                status_code=failure[2],
            )

        return StoredReceiptArtifact(
            key=key,
            content=None,
            size_bytes=len(content),
        )

    def load_pdf(
        self,
        job_id: str,
        receipt_version: int,
    ) -> StoredReceiptArtifact:
        key = self.object_key(job_id, receipt_version)
        response = None
        failure: tuple[str, bool, int | None] | None = None
        try:
            response = self.client.get_object(
                Bucket=self.bucket_name,
                Key=key,
            )
        except ClientError as exc:
            failure = self._classify_client_error(exc, loading=True)
        except BotoCoreError:
            failure = (STORAGE_OPERATION_FAILED, True, None)
        if failure is not None:
            raise ReceiptStorageError(
                failure[0],
                retryable=failure[1],
                status_code=failure[2],
            )

        body = response.get("Body")
        if body is None:
            raise ReceiptStorageError(
                STORAGE_ARTIFACT_INVALID,
                retryable=False,
            )

        declared_size = response.get("ContentLength")
        if type(declared_size) is int and declared_size > self.max_pdf_bytes:
            self._close_body(body)
            raise ReceiptStorageError(
                STORAGE_ARTIFACT_INVALID,
                retryable=False,
            )

        content = None
        failure = None
        try:
            content = body.read(self.max_pdf_bytes + 1)
        except ClientError as exc:
            failure = self._classify_client_error(exc, loading=True)
        except BotoCoreError:
            failure = (STORAGE_OPERATION_FAILED, True, None)
        finally:
            self._close_body(body)
        if failure is not None:
            raise ReceiptStorageError(
                failure[0],
                retryable=failure[1],
                status_code=failure[2],
            )
        if not self._is_valid_pdf(content):
            raise ReceiptStorageError(
                STORAGE_ARTIFACT_INVALID,
                retryable=False,
            )

        return StoredReceiptArtifact(
            key=key,
            content=content,
            size_bytes=len(content),
        )

    def _validate_local_pdf(self, content: bytes) -> None:
        if not self._is_valid_pdf(content):
            raise ValueError("RECEIPT_STORAGE_PDF_INVALID")

    def _is_valid_pdf(self, content: Any) -> bool:
        return (
            isinstance(content, bytes)
            and bool(content)
            and content.startswith(b"%PDF-")
            and len(content) <= self.max_pdf_bytes
        )

    @staticmethod
    def _close_body(body: Any) -> None:
        close = getattr(body, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _classify_client_error(
        exc: ClientError,
        *,
        loading: bool,
    ) -> tuple[str, bool, int | None]:
        response = exc.response if isinstance(exc.response, dict) else {}
        metadata = response.get("ResponseMetadata", {})
        status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
        if type(status) is not int:
            status = None
        error = response.get("Error", {})
        code = error.get("Code") if isinstance(error, dict) else None
        if loading and (status == 404 or code == "NoSuchKey"):
            return STORAGE_ARTIFACT_MISSING, False, status
        retryable = status is None or status == 429 or status >= 500
        return STORAGE_OPERATION_FAILED, retryable, status
