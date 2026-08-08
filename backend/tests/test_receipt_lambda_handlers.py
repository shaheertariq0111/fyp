from __future__ import annotations

from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from src.lambda_handlers import receipt_outbox_recovery, receipt_processing


def scheduled_event():
    return {
        "version": "0",
        "id": "scheduled-event-id",
        "detail-type": "Scheduled Event",
        "source": "aws.events",
        "time": "2026-08-07T00:00:00Z",
        "resources": ["arn:aws:events:us-west-2:123456789012:rule/receipt"],
        "detail": {},
    }


class FakeSqsHandler:
    def __init__(self):
        self.calls = []
        self.response = {"batchItemFailures": [{"itemIdentifier": "message-1"}]}

    def handle(self, event, context):
        self.calls.append((event, context))
        return self.response


class FakeRecoveryJobs:
    def __init__(self, recovered=3, error=None):
        self.recovered = recovered
        self.error = error
        self.calls = []

    def recover_outbox(self, *, limit):
        self.calls.append(limit)
        if self.error is not None:
            raise self.error
        return self.recovered


def test_processing_handler_delegates_event_and_context_unchanged(monkeypatch, caplog):
    sqs_handler = FakeSqsHandler()
    runtime = SimpleNamespace(handler=sqs_handler)
    monkeypatch.setattr(
        receipt_processing,
        "get_cached_processing_runtime",
        lambda: runtime,
    )
    event = {"Records": [{"body": "private raw body"}]}
    context = object()

    response = receipt_processing.handler(event, context)

    assert response is sqs_handler.response
    assert sqs_handler.calls == [(event, context)]
    assert "private raw body" not in caplog.text


def test_processing_runtime_is_cached_across_invocations(monkeypatch):
    runtime = SimpleNamespace(handler=FakeSqsHandler())
    builds = []
    receipt_processing.get_cached_processing_runtime.cache_clear()
    monkeypatch.setattr(
        receipt_processing,
        "build_receipt_processing_runtime",
        lambda: builds.append(True) or runtime,
    )

    first = receipt_processing.get_cached_processing_runtime()
    second = receipt_processing.get_cached_processing_runtime()

    assert first is second is runtime
    assert builds == [True]
    receipt_processing.get_cached_processing_runtime.cache_clear()


def test_recovery_handler_calls_outbox_once_and_returns_sanitized_count(caplog, monkeypatch):
    jobs = FakeRecoveryJobs(recovered=4)
    monkeypatch.setattr(
        receipt_outbox_recovery,
        "get_cached_recovery_runtime",
        lambda: SimpleNamespace(jobs=jobs),
    )
    event = scheduled_event()
    event["private"] = "private scheduled data"

    response = receipt_outbox_recovery.handler(event, object())

    assert response == {"recovered": 4}
    assert jobs.calls == [25]
    assert "private scheduled data" not in caplog.text


def test_recovery_handler_sanitizes_expected_aws_failure(monkeypatch, caplog):
    jobs = FakeRecoveryJobs(
        error=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "private DynamoDB response",
                }
            },
            "Query",
        )
    )
    monkeypatch.setattr(
        receipt_outbox_recovery,
        "get_cached_recovery_runtime",
        lambda: SimpleNamespace(jobs=jobs),
    )

    with pytest.raises(
        receipt_outbox_recovery.ReceiptOutboxRecoveryError,
        match="RECEIPT_OUTBOX_RECOVERY_FAILED",
    ) as raised:
        receipt_outbox_recovery.handler(scheduled_event(), None)

    assert str(raised.value) == "RECEIPT_OUTBOX_RECOVERY_FAILED"
    assert "private DynamoDB response" not in str(raised.value)
    assert "private DynamoDB response" not in caplog.text


@pytest.mark.parametrize(
    "event",
    [
        None,
        {},
        {"source": "aws.events"},
        {
            **scheduled_event(),
            "source": "untrusted.source",
        },
        {
            **scheduled_event(),
            "detail": {"private": "value"},
        },
        {
            **scheduled_event(),
            "resources": "not-a-list",
        },
    ],
)
def test_recovery_handler_rejects_malformed_scheduled_events(event):
    with pytest.raises(ValueError, match="RECEIPT_RECOVERY_EVENT_INVALID"):
        receipt_outbox_recovery.handler(event, None)


def test_recovery_runtime_is_cached_across_invocations(monkeypatch):
    runtime = SimpleNamespace(jobs=FakeRecoveryJobs())
    builds = []
    receipt_outbox_recovery.get_cached_recovery_runtime.cache_clear()
    monkeypatch.setattr(
        receipt_outbox_recovery,
        "build_receipt_recovery_runtime",
        lambda: builds.append(True) or runtime,
    )

    first = receipt_outbox_recovery.get_cached_recovery_runtime()
    second = receipt_outbox_recovery.get_cached_recovery_runtime()

    assert first is second is runtime
    assert builds == [True]
    receipt_outbox_recovery.get_cached_recovery_runtime.cache_clear()
