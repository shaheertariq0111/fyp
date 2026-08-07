from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.models.receipt_job import (
    ReceiptJobState,
    receipt_job_id,
    validate_receipt_job_record,
)
from src.repositories.receipt_job_repository import ReceiptJobConditionFailed
from src.services.receipt_queue_service import ReceiptQueueError


ENQUEUE_STATES = frozenset({
    ReceiptJobState.PENDING_ENQUEUE.value,
    ReceiptJobState.RETRYABLE_FAILURE.value,
})


class ReceiptJobServiceError(RuntimeError):
    def __init__(self, error_code: str = "RECEIPT_JOB_INTERNAL_ERROR") -> None:
        self.error_code = error_code
        self.retryable = True
        super().__init__(error_code)


@dataclass(frozen=True, slots=True)
class ReceiptJobSubmission:
    job_id: str
    accepted: bool
    queued: bool
    duplicate: bool


class ReceiptJobService:
    def __init__(
        self,
        repository,
        queue_service,
        *,
        job_ttl_hours: int,
        enqueue_retry_seconds: int,
        logger: logging.Logger | None = None,
    ):
        if type(job_ttl_hours) is not int or job_ttl_hours < 1:
            raise ValueError("RECEIPT_JOB_TTL_INVALID")
        if type(enqueue_retry_seconds) is not int or enqueue_retry_seconds < 1:
            raise ValueError("RECEIPT_ENQUEUE_RETRY_INVALID")
        self.repository = repository
        self.queue_service = queue_service
        self.job_ttl_hours = job_ttl_hours
        self.enqueue_retry_seconds = enqueue_retry_seconds
        self.logger = logger or logging.getLogger(__name__)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def submit(
        self,
        *,
        order_id: str,
        receipt_version: int,
        customer_number: str,
        sender_id: str,
        conversation_id: str,
        request_id: str,
    ) -> ReceiptJobSubmission:
        normalized_order_id = self._canonical_order_id(order_id)
        routing = {
            "customer_number": self._required_string(
                customer_number,
                "RECEIPT_CUSTOMER_NUMBER_REQUIRED",
            ),
            "sender_id": self._required_string(
                sender_id,
                "RECEIPT_SENDER_ID_REQUIRED",
            ),
            "conversation_id": self._required_string(
                conversation_id,
                "RECEIPT_CONVERSATION_ID_REQUIRED",
            ),
            "request_id": self._required_string(
                request_id,
                "RECEIPT_REQUEST_ID_REQUIRED",
            ),
        }
        job_id = receipt_job_id(normalized_order_id, receipt_version)
        now = self._now()
        now_epoch = int(now.timestamp())
        timestamp = now.isoformat()
        record = {
            "PK": f"JOB#{job_id}",
            "SK": "METADATA",
            "job_id": job_id,
            "order_id": normalized_order_id,
            "receipt_version": receipt_version,
            "state": ReceiptJobState.PENDING_ENQUEUE.value,
            "version": 1,
            **routing,
            "attempt_count": 0,
            "enqueue_attempt_count": 0,
            "created_at": timestamp,
            "updated_at": timestamp,
            "expires_at": int(
                (now + timedelta(hours=self.job_ttl_hours)).timestamp()
            ),
            "GSI1PK": "RECEIPT_OUTBOX",
            "GSI1SK": now_epoch,
        }
        validate_receipt_job_record(record)
        created = self.repository.create_if_absent(record)
        if not created:
            existing = self._load_existing(job_id)
            self.logger.info(
                "Receipt job duplicate",
                extra={
                    "event": "receipt_duplicate",
                    "receipt_job_id": job_id,
                    "receipt_job_state": existing["state"],
                    "duplicate": True,
                },
            )
            if existing["state"] not in ENQUEUE_STATES:
                return ReceiptJobSubmission(job_id, True, True, True)
            if not self._is_due(existing, int(self._now().timestamp())):
                return ReceiptJobSubmission(job_id, True, False, True)
            return self._enqueue(existing, duplicate=True)

        self.logger.info(
            "Receipt job accepted",
            extra={
                "event": "receipt_job_accepted",
                "receipt_job_id": job_id,
                "receipt_job_state": record["state"],
                "duplicate": False,
            },
        )
        return self._enqueue(record, duplicate=False)

    def _enqueue(self, record: dict, *, duplicate: bool) -> ReceiptJobSubmission:
        job_id = record["job_id"]
        # Observability only: this increment becomes durable only when the
        # optimistic checkpoint wins. It is not a physical SQS-call counter.
        next_count = record["enqueue_attempt_count"] + 1
        try:
            self.queue_service.send(job_id)
        except ReceiptQueueError:
            return self._checkpoint_enqueue_failure(
                record,
                next_count=next_count,
                duplicate=duplicate,
            )

        try:
            updated = self.repository.transition(
                job_id,
                expected_states={record["state"]},
                expected_version=record["version"],
                next_state=ReceiptJobState.QUEUED.value,
                updated_at=self._now().isoformat(),
                values={"enqueue_attempt_count": next_count},
                remove=(
                    "GSI1PK",
                    "GSI1SK",
                    "next_retry_at",
                    "generic_failure_code",
                ),
            )
        except ReceiptJobConditionFailed:
            self._load_existing(job_id)
            return ReceiptJobSubmission(job_id, True, True, duplicate)

        self.logger.info(
            "Receipt queue submitted",
            extra={
                "event": "receipt_queue_submitted",
                "receipt_job_id": job_id,
                "receipt_job_state": updated["state"],
                "duplicate": duplicate,
                "queued": True,
            },
        )
        return ReceiptJobSubmission(job_id, True, True, duplicate)

    def _checkpoint_enqueue_failure(
        self,
        record: dict,
        *,
        next_count: int,
        duplicate: bool,
    ) -> ReceiptJobSubmission:
        job_id = record["job_id"]
        now = self._now()
        retry_at = int(
            (now + timedelta(seconds=self.enqueue_retry_seconds)).timestamp()
        )
        try:
            self.repository.transition(
                job_id,
                expected_states={record["state"]},
                expected_version=record["version"],
                next_state=ReceiptJobState.RETRYABLE_FAILURE.value,
                updated_at=now.isoformat(),
                values={
                    "generic_failure_code": "RECEIPT_QUEUE_OPERATION_FAILED",
                    "next_retry_at": retry_at,
                    "GSI1PK": "RECEIPT_OUTBOX",
                    "GSI1SK": retry_at,
                    "enqueue_attempt_count": next_count,
                },
            )
        except ReceiptJobConditionFailed:
            latest = self._load_existing(job_id)
            queued = latest["state"] not in ENQUEUE_STATES
            return ReceiptJobSubmission(job_id, True, queued, duplicate)

        self.logger.warning(
            "Receipt enqueue deferred",
            extra={
                "event": "receipt_enqueue_deferred",
                "receipt_job_id": job_id,
                "receipt_job_state": ReceiptJobState.RETRYABLE_FAILURE.value,
                "failure_stage": "enqueue",
                "duplicate": duplicate,
                "queued": False,
            },
        )
        return ReceiptJobSubmission(job_id, True, False, duplicate)

    def recover_outbox(self, *, limit: int = 25) -> int:
        if type(limit) is not int or limit < 1:
            raise ValueError("RECEIPT_OUTBOX_LIMIT_INVALID")
        now_epoch = int(self._now().timestamp())
        recovered = 0
        records = self.repository.query_due(
            due_partition="RECEIPT_OUTBOX",
            now_epoch=now_epoch,
            limit=limit,
        )
        for record in records:
            if record.get("state") not in ENQUEUE_STATES:
                continue
            if self._enqueue(record, duplicate=True).queued:
                recovered += 1
        self.logger.info(
            "Receipt outbox recovery completed",
            extra={
                "event": "receipt_outbox_recovered",
                "queued": recovered,
            },
        )
        return recovered

    def _load_existing(self, job_id: str) -> dict:
        existing = self.repository.get(job_id)
        if existing is None:
            raise ReceiptJobServiceError()
        return existing

    @staticmethod
    def _is_due(record: dict, now_epoch: int) -> bool:
        due_values = [
            record[field]
            for field in ("GSI1SK", "next_retry_at")
            if field in record
        ]
        return bool(due_values) and max(due_values) <= now_epoch

    @staticmethod
    def _canonical_order_id(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("RECEIPT_ORDER_ID_REQUIRED")
        if value != value.strip():
            raise ValueError("RECEIPT_ORDER_ID_INVALID")
        return value

    @staticmethod
    def _required_string(value: str, error_code: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(error_code)
        return value.strip()
