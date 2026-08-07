from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from typing import Any

from src.models.receipt_job import ReceiptQueueMessage


EVENT_INVALID = "RECEIPT_SQS_EVENT_INVALID"
WORKER_UNEXPECTED_FAILURE = "RECEIPT_WORKER_UNEXPECTED_FAILURE"
SAFE_PARSE_ERROR_CODES = frozenset({
    "RECEIPT_JOB_ID_INVALID",
    "RECEIPT_QUEUE_BODY_TOO_LARGE",
    "RECEIPT_QUEUE_DUPLICATE_KEY",
    "RECEIPT_QUEUE_JSON_INVALID",
    "RECEIPT_QUEUE_SCHEMA_INVALID",
})


class ReceiptSqsHandler:
    def __init__(
        self,
        worker,
        *,
        logger: logging.Logger | None = None,
        owner_factory: Callable[[], str] | None = None,
    ) -> None:
        self.worker = worker
        self.logger = logger or logging.getLogger(__name__)
        self.owner_factory = owner_factory or (lambda: uuid.uuid4().hex)

    def handle(self, event: Any, context: Any = None) -> dict[str, list[dict[str, str]]]:
        records = self._validated_records(event)
        invocation_identity = None
        failures: list[dict[str, str]] = []

        for index, record in enumerate(records):
            message_id = record["messageId"]
            try:
                message = ReceiptQueueMessage.parse(record.get("body"))
            except ValueError as exc:
                error_code = str(exc)
                if error_code not in SAFE_PARSE_ERROR_CODES:
                    error_code = "RECEIPT_QUEUE_MESSAGE_INVALID"
                self.logger.warning(
                    "Receipt SQS message discarded",
                    extra={
                        "event": "receipt_sqs_message_discarded",
                        "failure_stage": "queue_parse",
                        "error_code": error_code,
                        "retryable": False,
                    },
                )
                continue

            if invocation_identity is None:
                invocation_identity = self._invocation_identity(context)
            owner = self._owner(invocation_identity, index)
            try:
                result = self.worker.process(message.job_id, owner=owner)
            except Exception:
                failures.append({"itemIdentifier": message_id})
                self.logger.warning(
                    "Receipt SQS worker failed",
                    extra={
                        "event": "receipt_sqs_worker_failed",
                        "receipt_job_id": message.job_id,
                        "failure_stage": "worker",
                        "error_code": WORKER_UNEXPECTED_FAILURE,
                        "retryable": True,
                    },
                )
                continue
            if result.retryable is True:
                failures.append({"itemIdentifier": message_id})

        return {"batchItemFailures": failures}

    @staticmethod
    def _validated_records(event: Any) -> list[dict[str, Any]]:
        if (
            not isinstance(event, dict)
            or "Records" not in event
            or not isinstance(event["Records"], list)
        ):
            raise ValueError(EVENT_INVALID)
        records = event["Records"]
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(EVENT_INVALID)
            message_id = record.get("messageId")
            if not isinstance(message_id, str) or not message_id.strip():
                raise ValueError(EVENT_INVALID)
        return records

    def _invocation_identity(self, context: Any) -> str:
        request_id = getattr(context, "aws_request_id", None)
        if not isinstance(request_id, str) or not request_id.strip():
            request_id = self.owner_factory()
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("RECEIPT_SQS_OWNER_INVALID")
        normalized = re.sub(r"[^A-Za-z0-9_-]", "-", request_id.strip())
        normalized = normalized.strip("-")
        if not normalized:
            raise ValueError("RECEIPT_SQS_OWNER_INVALID")
        return normalized[:80]

    @staticmethod
    def _owner(invocation_identity: str, record_index: int) -> str:
        return f"receipt-lambda-{invocation_identity}-{record_index}"
