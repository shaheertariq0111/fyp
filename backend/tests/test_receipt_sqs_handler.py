from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.models.receipt_job import ReceiptQueueMessage, receipt_job_id
from src.workers.receipt_sqs_handler import ReceiptSqsHandler
from src.workers.receipt_worker import ReceiptWorkerResult


JOB_ID = receipt_job_id("private-order-123", 1)
VALID_BODY = ReceiptQueueMessage(job_id=JOB_ID).serialize()


class FakeWorker:
    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = list(outcomes or [
            ReceiptWorkerResult(JOB_ID, "sent", True, False)
        ])
        self.orders = ForbiddenDependency()
        self.renderer = ForbiddenDependency()
        self.storage = ForbiddenDependency()
        self.gateway = ForbiddenDependency()

    def process(self, job_id, *, owner):
        self.calls.append((job_id, owner))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def submit(self, *_args, **_kwargs):
        raise AssertionError("handler must not submit receipt jobs")


class ForbiddenDependency:
    def __getattr__(self, _name):
        raise AssertionError("handler crossed the worker responsibility boundary")


def record(message_id="message-1", body=VALID_BODY):
    return {"messageId": message_id, "body": body}


def handler(worker=None, identities=None):
    worker = worker or FakeWorker()
    identity_values = iter(identities or ["local-attempt"])
    return (
        ReceiptSqsHandler(worker, owner_factory=lambda: next(identity_values)),
        worker,
    )


@pytest.mark.parametrize("event", [None, [], "event", 1])
def test_event_must_be_dict(event):
    receipt_handler, worker = handler()
    with pytest.raises(ValueError, match="RECEIPT_SQS_EVENT_INVALID"):
        receipt_handler.handle(event)
    assert worker.calls == []


@pytest.mark.parametrize("event", [{}, {"Records": None}, {"Records": {}}, {"Records": "x"}])
def test_records_are_required_and_must_be_list(event):
    receipt_handler, worker = handler()
    with pytest.raises(ValueError, match="RECEIPT_SQS_EVENT_INVALID"):
        receipt_handler.handle(event)
    assert worker.calls == []


@pytest.mark.parametrize(
    "bad_record",
    [None, "record", [], {}, {"messageId": None}, {"messageId": "   "}],
)
def test_invalid_record_or_message_identity_raises(bad_record):
    receipt_handler, worker = handler()
    with pytest.raises(ValueError, match="RECEIPT_SQS_EVENT_INVALID"):
        receipt_handler.handle({"Records": [bad_record]})
    assert worker.calls == []


def test_all_record_identities_are_validated_before_processing():
    receipt_handler, worker = handler()
    with pytest.raises(ValueError, match="RECEIPT_SQS_EVENT_INVALID"):
        receipt_handler.handle({"Records": [record(), {"body": VALID_BODY}]})
    assert worker.calls == []


@pytest.mark.parametrize(
    "body",
    [None, 123, {}, [], True, b"\xff"],
)
def test_missing_or_invalid_body_is_discarded_when_message_is_identifiable(body):
    receipt_handler, worker = handler()
    response = receipt_handler.handle({"Records": [record(body=body)]})
    assert response == {"batchItemFailures": []}
    assert worker.calls == []


def test_valid_queue_message_reaches_worker_with_nonempty_owner():
    receipt_handler, worker = handler()
    response = receipt_handler.handle({"Records": [record()]})
    assert response == {"batchItemFailures": []}
    assert worker.calls == [(JOB_ID, "receipt-lambda-local-attempt-0")]


@pytest.mark.parametrize(
    "body",
    [
        "not-json-private-body",
        "x" * 513,
        f'{{"job_id":"{JOB_ID}","kind":"wrong","v":1}}',
        f'{{"job_id":"{JOB_ID}","kind":"whatsapp_pdf_receipt","v":2}}',
        f'{{"extra":true,"job_id":"{JOB_ID}","kind":"whatsapp_pdf_receipt","v":1}}',
        f'{{"job_id":"{JOB_ID}","job_id":"{JOB_ID}","kind":"whatsapp_pdf_receipt","v":1}}',
        '{"job_id":"bad","kind":"whatsapp_pdf_receipt","v":1}',
    ],
)
def test_poison_queue_bodies_are_discarded_without_worker_call(body):
    receipt_handler, worker = handler()
    response = receipt_handler.handle({"Records": [record(body=body)]})
    assert response == {"batchItemFailures": []}
    assert worker.calls == []


def test_invalid_body_and_exception_contents_never_appear_in_logs(caplog):
    private_body = "not-json-private-customer-routing-snapshot-pdf-s3"
    receipt_handler, _ = handler()
    with caplog.at_level("WARNING"):
        receipt_handler.handle({"Records": [record(body=private_body)]})

    assert private_body not in caplog.text
    log_values = " ".join(
        str(value)
        for log_record in caplog.records
        for value in vars(log_record).values()
        if not isinstance(value, tuple)
    )
    assert private_body not in log_values
    assert caplog.records[0].event == "receipt_sqs_message_discarded"
    assert caplog.records[0].failure_stage == "queue_parse"
    assert caplog.records[0].retryable is False


@pytest.mark.parametrize(
    "result",
    [
        ReceiptWorkerResult(JOB_ID, "sent", True, False),
        ReceiptWorkerResult(JOB_ID, "permanent_failure", True, False),
        ReceiptWorkerResult(JOB_ID, "manual_review", True, False),
        ReceiptWorkerResult(JOB_ID, "missing", False, False),
    ],
)
def test_nonretryable_worker_results_are_acknowledged(result):
    receipt_handler, _ = handler(FakeWorker([result]))
    assert receipt_handler.handle({"Records": [record()]}) == {
        "batchItemFailures": []
    }


def test_retryable_worker_result_creates_minimal_batch_failure():
    result = ReceiptWorkerResult(JOB_ID, "generated", False, True)
    receipt_handler, _ = handler(FakeWorker([result]))
    assert receipt_handler.handle({"Records": [record("retry-me")]}) == {
        "batchItemFailures": [{"itemIdentifier": "retry-me"}]
    }


@pytest.mark.parametrize("error", [RuntimeError("private provider body"), TypeError("private PDF data")])
def test_worker_exception_is_retryable_sanitized_and_not_returned(error, caplog):
    receipt_handler, _ = handler(FakeWorker([error]))
    with caplog.at_level("WARNING"):
        response = receipt_handler.handle({"Records": [record("failed-id")]})

    assert response == {
        "batchItemFailures": [{"itemIdentifier": "failed-id"}]
    }
    assert str(error) not in str(response)
    assert str(error) not in caplog.text
    logged = caplog.records[0]
    assert logged.event == "receipt_sqs_worker_failed"
    assert logged.receipt_job_id == JOB_ID
    assert logged.error_code == "RECEIPT_WORKER_UNEXPECTED_FAILURE"
    assert logged.retryable is True


def test_worker_exception_does_not_abort_later_records():
    success = ReceiptWorkerResult(JOB_ID, "sent", True, False)
    worker = FakeWorker([RuntimeError("bug"), success])
    receipt_handler, _ = handler(worker)

    response = receipt_handler.handle({
        "Records": [record("first"), record("second")]
    })

    assert response == {
        "batchItemFailures": [{"itemIdentifier": "first"}]
    }
    assert len(worker.calls) == 2


def test_mixed_batch_returns_only_retryable_valid_ids_in_input_order():
    retry = ReceiptWorkerResult(JOB_ID, "generated", False, True)
    success = ReceiptWorkerResult(JOB_ID, "sent", True, False)
    worker = FakeWorker([retry, success, RuntimeError("bug")])
    receipt_handler, _ = handler(worker)

    response = receipt_handler.handle({"Records": [
        record("retry-1"),
        record("poison", "not-json"),
        record("success"),
        record("retry-2"),
    ]})

    assert response == {"batchItemFailures": [
        {"itemIdentifier": "retry-1"},
        {"itemIdentifier": "retry-2"},
    ]}
    assert len(worker.calls) == 3


def test_empty_batch_returns_minimal_response():
    receipt_handler, worker = handler()
    assert receipt_handler.handle({"Records": []}) == {"batchItemFailures": []}
    assert worker.calls == []


def test_poison_only_batch_does_not_require_worker_owner_identity():
    worker = FakeWorker()
    receipt_handler = ReceiptSqsHandler(
        worker,
        owner_factory=lambda: (_ for _ in ()).throw(
            AssertionError("owner is only needed for worker processing")
        ),
    )

    assert receipt_handler.handle({
        "Records": [record("poison", "not-json")]
    }) == {"batchItemFailures": []}
    assert worker.calls == []


def test_duplicate_job_messages_are_both_delegated_sequentially():
    first = ReceiptWorkerResult(JOB_ID, "sent", True, False)
    second = ReceiptWorkerResult(JOB_ID, "sent", True, False)
    worker = FakeWorker([first, second])
    receipt_handler, _ = handler(worker)

    receipt_handler.handle({
        "Records": [record("duplicate-1"), record("duplicate-2")]
    })

    assert [call[0] for call in worker.calls] == [JOB_ID, JOB_ID]
    assert worker.calls[0][1] != worker.calls[1][1]


def test_context_request_id_produces_bounded_owner_without_body_data():
    receipt_handler, worker = handler()
    context = SimpleNamespace(aws_request_id="aws-request-123")
    private_body_values = (JOB_ID, "private-order", "customer", "routing")

    receipt_handler.handle({"Records": [record()]}, context)

    owner = worker.calls[0][1]
    assert owner == "receipt-lambda-aws-request-123-0"
    assert len(owner) <= 128
    for private in private_body_values:
        assert private not in owner


def test_owner_factory_is_injected_and_varies_between_local_attempts():
    success = ReceiptWorkerResult(JOB_ID, "sent", True, False)
    worker = FakeWorker([success, success])
    receipt_handler, _ = handler(worker, identities=["attempt-a", "attempt-b"])

    receipt_handler.handle({"Records": [record("one")]})
    receipt_handler.handle({"Records": [record("two")]})

    assert [call[1] for call in worker.calls] == [
        "receipt-lambda-attempt-a-0",
        "receipt-lambda-attempt-b-0",
    ]


def test_response_contains_no_worker_state_or_private_data():
    retry = ReceiptWorkerResult(JOB_ID, "private-state", False, True)
    receipt_handler, _ = handler(FakeWorker([retry]))
    response = receipt_handler.handle({"Records": [record("message-only")]})

    assert response == {
        "batchItemFailures": [{"itemIdentifier": "message-only"}]
    }
    response_text = str(response)
    assert JOB_ID not in response_text
    assert "private-state" not in response_text
