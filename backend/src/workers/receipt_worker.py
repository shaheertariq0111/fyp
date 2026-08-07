from __future__ import annotations

import logging
from dataclasses import dataclass

from botocore.exceptions import BotoCoreError, ClientError

from src.models.receipt_job import ReceiptJobState, validate_receipt_job_id
from src.repositories.receipt_job_repository import ReceiptJobConditionFailed
from src.services.agentflo_gateway_service import DocumentSendDisposition
from src.services.receipt_pdf_renderer import ReceiptPdfRenderError
from src.services.receipt_storage_service import (
    ReceiptStorageError,
    ReceiptStorageService,
)


OUTBOX_RETRY_FIELDS = (
    "GSI1PK",
    "GSI1SK",
    "next_retry_at",
    "generic_failure_code",
)


@dataclass(frozen=True, slots=True)
class ReceiptWorkerResult:
    job_id: str
    state: str
    terminal: bool
    retryable: bool


class ReceiptWorker:
    def __init__(
        self,
        *,
        jobs,
        orders,
        renderer,
        storage,
        gateway,
        lease_seconds: int,
        logger: logging.Logger | None = None,
    ) -> None:
        if type(lease_seconds) is not int or lease_seconds < 1:
            raise ValueError("RECEIPT_WORKER_LEASE_INVALID")
        self.jobs = jobs
        self.orders = orders
        self.renderer = renderer
        self.storage = storage
        self.gateway = gateway
        self.lease_seconds = lease_seconds
        self.logger = logger or logging.getLogger(__name__)

    def process(self, job_id: str, *, owner: str) -> ReceiptWorkerResult:
        validated_job_id = validate_receipt_job_id(job_id)
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("RECEIPT_JOB_LEASE_INVALID")
        owner = owner.strip()

        record = self.jobs.get(validated_job_id)
        if record is None:
            return self._result(
                validated_job_id,
                state="missing",
                terminal=False,
                retryable=False,
            )
        if self.jobs.is_terminal(record):
            return self._result(
                validated_job_id,
                state=record["state"],
                terminal=True,
                retryable=False,
            )

        try:
            record = self.jobs.acquire_lease(
                validated_job_id,
                owner,
                lease_seconds=self.lease_seconds,
            )
        except ReceiptJobConditionFailed:
            return self._result(
                validated_job_id,
                state=record["state"],
                terminal=False,
                retryable=True,
            )

        try:
            if self.jobs.is_terminal(record):
                return self._result(
                    validated_job_id,
                    state=record["state"],
                    terminal=True,
                    retryable=False,
                )
            return self._process_owned(record, owner)
        finally:
            self.jobs.release_lease(validated_job_id, owner)

    def _process_owned(self, record: dict, owner: str) -> ReceiptWorkerResult:
        state = record["state"]
        if state == ReceiptJobState.OUTBOUND_SENDING.value:
            record = self._transition(
                record,
                ReceiptJobState.MANUAL_REVIEW.value,
                owner,
                values={
                    "generic_failure_code": (
                        "RECEIPT_OUTBOUND_OUTCOME_AMBIGUOUS"
                    )
                },
                remove=("next_retry_at",),
            )
            return self._terminal_result(record)

        if state in {
            ReceiptJobState.PENDING_ENQUEUE.value,
            ReceiptJobState.QUEUED.value,
        }:
            record = self._transition(
                record,
                ReceiptJobState.PROCESSING.value,
                owner,
                remove=OUTBOX_RETRY_FIELDS,
            )
        elif state == ReceiptJobState.RETRYABLE_FAILURE.value:
            next_state = (
                ReceiptJobState.GENERATED.value
                if record.get("s3_key")
                else ReceiptJobState.PROCESSING.value
            )
            record = self._transition(
                record,
                next_state,
                owner,
                remove=OUTBOX_RETRY_FIELDS,
            )

        if record["state"] == ReceiptJobState.PROCESSING.value:
            generated = self._generate(record, owner)
            if isinstance(generated, ReceiptWorkerResult):
                return generated
            record = generated

        if record["state"] != ReceiptJobState.GENERATED.value:
            raise RuntimeError("RECEIPT_WORKER_STATE_UNSUPPORTED")
        return self._deliver_generated(record, owner)

    def _generate(
        self,
        record: dict,
        owner: str,
    ) -> dict | ReceiptWorkerResult:
        order = None
        lookup_failed = False
        try:
            order = self.orders.get_by_order_id(record["order_id"])
        except (ClientError, BotoCoreError):
            lookup_failed = True
        if lookup_failed:
            return self._failure(
                record,
                owner,
                error_code="RECEIPT_ORDER_LOOKUP_FAILED",
                failure_stage="order_lookup",
                retryable=True,
            )
        if order is None:
            return self._failure(
                record,
                owner,
                error_code="RECEIPT_ORDER_LOOKUP_PENDING",
                failure_stage="order_lookup",
                retryable=True,
            )

        snapshot = order.get("receipt_snapshot")
        if not isinstance(snapshot, dict):
            return self._failure(
                record,
                owner,
                error_code="RECEIPT_SNAPSHOT_UNAVAILABLE",
                failure_stage="snapshot",
                retryable=False,
            )
        if (
            snapshot.get("order_id") != record["order_id"]
            or type(snapshot.get("schema_version")) is not int
            or snapshot["schema_version"] != record["receipt_version"]
        ):
            return self._failure(
                record,
                owner,
                error_code="RECEIPT_SNAPSHOT_IDENTITY_INVALID",
                failure_stage="snapshot",
                retryable=False,
            )

        try:
            rendered = self.renderer.render(snapshot)
        except ReceiptPdfRenderError as exc:
            return self._failure(
                record,
                owner,
                error_code=exc.error_code,
                failure_stage="render",
                retryable=False,
            )

        try:
            stored = self.storage.store_pdf(
                record["job_id"],
                record["receipt_version"],
                rendered.content,
            )
        except ReceiptStorageError as exc:
            return self._failure(
                record,
                owner,
                error_code=exc.error_code,
                failure_stage="storage_write",
                retryable=exc.retryable,
            )

        expected_key = ReceiptStorageService.object_key(
            record["job_id"],
            record["receipt_version"],
        )
        if stored.key != expected_key:
            return self._failure(
                record,
                owner,
                error_code="RECEIPT_STORAGE_KEY_MISMATCH",
                failure_stage="storage_write",
                retryable=False,
            )
        return self._transition(
            record,
            ReceiptJobState.GENERATED.value,
            owner,
            values={"s3_key": stored.key},
            remove=("next_retry_at", "generic_failure_code"),
        )

    def _deliver_generated(
        self,
        record: dict,
        owner: str,
    ) -> ReceiptWorkerResult:
        expected_key = ReceiptStorageService.object_key(
            record["job_id"],
            record["receipt_version"],
        )
        if record.get("s3_key") != expected_key:
            return self._failure(
                record,
                owner,
                error_code="RECEIPT_STORAGE_KEY_MISMATCH",
                failure_stage="storage_read",
                retryable=False,
            )

        try:
            stored = self.storage.load_pdf(
                record["job_id"],
                record["receipt_version"],
            )
        except ReceiptStorageError as exc:
            return self._failure(
                record,
                owner,
                error_code=exc.error_code,
                failure_stage="storage_read",
                retryable=exc.retryable,
            )
        if not isinstance(stored.content, bytes):
            raise RuntimeError("RECEIPT_STORAGE_CONTENT_MISSING")

        if not self.jobs.extend_lease(
            record["job_id"],
            owner,
            lease_seconds=self.lease_seconds,
        ):
            return self._result(
                record["job_id"],
                state=record["state"],
                terminal=False,
                retryable=True,
            )
        record = self._transition(
            record,
            ReceiptJobState.OUTBOUND_SENDING.value,
            owner,
            remove=("next_retry_at", "generic_failure_code"),
        )

        outbound = self.gateway.send_document_classified(
            customer_number=record["customer_number"],
            conversation_id=record["conversation_id"],
            sender_id=record["sender_id"],
            document=stored.content,
            filename=self.renderer.filename_for_order_id(record["order_id"]),
            caption=None,
            request_id=record["request_id"],
        )
        if outbound.disposition is DocumentSendDisposition.SENT:
            values = {}
            provider_id = outbound.provider_message_id
            if isinstance(provider_id, str) and provider_id.strip():
                values["provider_message_id"] = provider_id.strip()
            record = self._transition(
                record,
                ReceiptJobState.SENT.value,
                owner,
                values=values,
                remove=("next_retry_at", "generic_failure_code"),
            )
            return self._terminal_result(record)
        if outbound.disposition is DocumentSendDisposition.RETRYABLE_FAILURE:
            record = self._transition(
                record,
                ReceiptJobState.GENERATED.value,
                owner,
                values={
                    "generic_failure_code": "RECEIPT_OUTBOUND_RETRYABLE"
                },
                remove=("next_retry_at",),
            )
            return self._result(
                record["job_id"],
                state=record["state"],
                terminal=False,
                retryable=True,
            )
        if outbound.disposition is DocumentSendDisposition.PERMANENT_FAILURE:
            return self._failure(
                record,
                owner,
                error_code="RECEIPT_OUTBOUND_PERMANENT",
                failure_stage="outbound",
                retryable=False,
            )
        if outbound.disposition is DocumentSendDisposition.MANUAL_REVIEW:
            record = self._transition(
                record,
                ReceiptJobState.MANUAL_REVIEW.value,
                owner,
                values={
                    "generic_failure_code": (
                        "RECEIPT_OUTBOUND_OUTCOME_AMBIGUOUS"
                    )
                },
                remove=("next_retry_at",),
            )
            return self._terminal_result(record)
        raise RuntimeError("RECEIPT_OUTBOUND_DISPOSITION_INVALID")

    def _failure(
        self,
        record: dict,
        owner: str,
        *,
        error_code: str,
        failure_stage: str,
        retryable: bool,
    ) -> ReceiptWorkerResult:
        next_state = (
            ReceiptJobState.RETRYABLE_FAILURE.value
            if retryable
            else ReceiptJobState.PERMANENT_FAILURE.value
        )
        record = self._transition(
            record,
            next_state,
            owner,
            values={"generic_failure_code": error_code},
            remove=("next_retry_at",),
        )
        self.logger.warning(
            "Receipt worker operation failed",
            extra={
                "event": "receipt_worker_failed",
                "receipt_job_id": record["job_id"],
                "receipt_job_state": record["state"],
                "failure_stage": failure_stage,
                "error_code": error_code,
                "retryable": retryable,
            },
        )
        return self._result(
            record["job_id"],
            state=record["state"],
            terminal=not retryable,
            retryable=retryable,
        )

    def _transition(
        self,
        record: dict,
        next_state: str,
        owner: str,
        *,
        values: dict | None = None,
        remove: tuple[str, ...] = (),
    ) -> dict:
        return self.jobs.transition(
            record,
            next_state,
            values=values,
            remove=remove,
            lease_owner=owner,
        )

    def _terminal_result(self, record: dict) -> ReceiptWorkerResult:
        return self._result(
            record["job_id"],
            state=record["state"],
            terminal=True,
            retryable=False,
        )

    def _result(
        self,
        job_id: str,
        *,
        state: str,
        terminal: bool,
        retryable: bool,
    ) -> ReceiptWorkerResult:
        self.logger.info(
            "Receipt worker result",
            extra={
                "event": "receipt_worker_result",
                "receipt_job_id": job_id,
                "receipt_job_state": state,
                "retryable": retryable,
            },
        )
        return ReceiptWorkerResult(
            job_id=job_id,
            state=state,
            terminal=terminal,
            retryable=retryable,
        )
