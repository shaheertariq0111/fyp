from __future__ import annotations

from functools import lru_cache
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from src.composition.receipt_dependencies import (
    ReceiptRecoveryRuntime,
    build_receipt_recovery_runtime,
)
from src.services.receipt_job_service import ReceiptJobServiceError


RECOVERY_EVENT_INVALID = "RECEIPT_RECOVERY_EVENT_INVALID"
RECOVERY_FAILED = "RECEIPT_OUTBOX_RECOVERY_FAILED"


class ReceiptOutboxRecoveryError(RuntimeError):
    """Sanitized external-service failure for the scheduled invocation."""


@lru_cache
def get_cached_recovery_runtime() -> ReceiptRecoveryRuntime:
    return build_receipt_recovery_runtime()


def _validate_scheduled_event(event: Any) -> None:
    if (
        not isinstance(event, dict)
        or event.get("source") != "aws.events"
        or event.get("detail-type") != "Scheduled Event"
        or not isinstance(event.get("id"), str)
        or not event["id"].strip()
        or not isinstance(event.get("time"), str)
        or not event["time"].strip()
        or event.get("detail") != {}
        or not isinstance(event.get("resources"), list)
    ):
        raise ValueError(RECOVERY_EVENT_INVALID)


def handler(event, context):
    del context
    _validate_scheduled_event(event)
    runtime = get_cached_recovery_runtime()
    try:
        recovered = runtime.jobs.recover_outbox(limit=25)
    except (ClientError, BotoCoreError, ReceiptJobServiceError):
        raise ReceiptOutboxRecoveryError(RECOVERY_FAILED) from None
    return {"recovered": recovered}
