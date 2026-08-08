from __future__ import annotations

from copy import deepcopy

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from src.repositories.whatsapp_voice_job_repository import VoiceJobConditionFailed
from src.services.receipt_job_service import (
    ReceiptJobServiceError,
    ReceiptJobSubmission,
)
from src.services.whatsapp_voice_receipt_activation_service import (
    WhatsAppVoiceReceiptActivationResult,
    WhatsAppVoiceReceiptActivationService,
)


ORDER_ID = "ORD-PRIVATE-VOICE-1"
CUSTOMER_NUMBER = "+923001234567"
SENDER_ID = "sender-private"
JOB_ID = "wv1_" + "d" * 64


def pending_record(**overrides):
    record = {
        "job_id": JOB_ID,
        "state": "outbound_sending",
        "version": 9,
        "submitted_order_id": ORDER_ID,
        "receipt_activation_state": "pending",
        "customer_number": CUSTOMER_NUMBER,
        "sender_id": SENDER_ID,
        "session_id": "session-private",
        "request_id": "request-private",
    }
    record.update(overrides)
    return record


class FakeVoiceJobs:
    def __init__(self, *, complete_outcomes=None, manual_outcomes=None):
        self.complete_calls = []
        self.manual_calls = []
        self.complete_outcomes = list(complete_outcomes or [None])
        self.manual_outcomes = list(manual_outcomes or [None])

    def complete_receipt_activation(self, record):
        self.complete_calls.append(deepcopy(record))
        outcome = self.complete_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        updated = deepcopy(record)
        updated["receipt_activation_state"] = "completed"
        updated["version"] += 1
        return updated

    def mark_receipt_manual_review(self, record):
        self.manual_calls.append(deepcopy(record))
        outcome = self.manual_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        updated = deepcopy(record)
        updated["receipt_activation_state"] = "manual_review"
        updated["version"] += 1
        return updated


class FakeReceiptJobs:
    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = list(outcomes or [
            ReceiptJobSubmission("receipt-job", True, True, False)
        ])

    def submit(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def coordinator(receipt_jobs=None, voice_jobs=None):
    voice_jobs = voice_jobs or FakeVoiceJobs()
    receipt_jobs = receipt_jobs or FakeReceiptJobs()
    service = WhatsAppVoiceReceiptActivationService(
        voice_jobs=voice_jobs,
        receipt_jobs=receipt_jobs,
    )
    return service, voice_jobs, receipt_jobs


@pytest.mark.parametrize(
    "submission",
    [
        ReceiptJobSubmission("job", True, True, False),
        ReceiptJobSubmission("job", True, False, False),
        ReceiptJobSubmission("job", True, True, True),
        ReceiptJobSubmission("job", True, False, True),
    ],
)
def test_pending_voice_receipt_submits_exact_values_and_completes(submission):
    service, voice_jobs, receipt_jobs = coordinator(
        FakeReceiptJobs([submission])
    )
    record = pending_record()

    result = service.activate_pending(record)

    assert result == WhatsAppVoiceReceiptActivationResult("activated", False)
    assert receipt_jobs.calls == [{
        "order_id": ORDER_ID,
        "receipt_version": 1,
        "customer_number": CUSTOMER_NUMBER,
        "sender_id": SENDER_ID,
        "conversation_id": "session-private",
        "request_id": "request-private",
    }]
    assert voice_jobs.complete_calls == [record]
    assert voice_jobs.manual_calls == []


@pytest.mark.parametrize(
    ("record", "expected_status"),
    [
        (pending_record(receipt_activation_state="completed"), "already_activated"),
        (pending_record(receipt_activation_state="manual_review"), "manual_review"),
        (pending_record(state="response_ready"), "not_required"),
        (pending_record(state="completed"), "not_required"),
        (pending_record(receipt_activation_state=None), "not_required"),
        (None, "not_required"),
    ],
)
def test_ineligible_voice_receipt_records_never_submit(record, expected_status):
    service, voice_jobs, receipt_jobs = coordinator()

    result = service.activate_pending(record)

    assert result == WhatsAppVoiceReceiptActivationResult(
        expected_status,
        False,
    )
    assert receipt_jobs.calls == []
    assert voice_jobs.complete_calls == []
    assert voice_jobs.manual_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("submitted_order_id", ""),
        ("submitted_order_id", " ORD-1 "),
        ("customer_number", None),
        ("customer_number", " customer "),
        ("sender_id", ""),
        ("session_id", " session "),
        ("request_id", None),
    ],
)
def test_invalid_durable_routing_moves_to_manual_review_without_submit(
    field,
    value,
):
    service, voice_jobs, receipt_jobs = coordinator()
    record = pending_record(**{field: value})

    result = service.activate_pending(record)

    assert result == WhatsAppVoiceReceiptActivationResult(
        "manual_review",
        False,
    )
    assert voice_jobs.manual_calls == [record]
    assert voice_jobs.complete_calls == []
    assert receipt_jobs.calls == []


def aws_error():
    return ClientError(
        {"Error": {"Code": "InternalError", "Message": "private AWS body"}},
        "PutItem",
    )


@pytest.mark.parametrize(
    "error",
    [
        ReceiptJobServiceError("RECEIPT_JOB_INTERNAL_ERROR"),
        aws_error(),
        EndpointConnectionError(endpoint_url="https://private.invalid"),
    ],
)
def test_retryable_receipt_submission_failures_leave_voice_receipt_pending(error):
    service, voice_jobs, receipt_jobs = coordinator(FakeReceiptJobs([error]))
    record = pending_record()

    result = service.activate_pending(record)

    assert result == WhatsAppVoiceReceiptActivationResult(
        "retryable_failure",
        True,
    )
    assert record["receipt_activation_state"] == "pending"
    assert len(receipt_jobs.calls) == 1
    assert voice_jobs.complete_calls == voice_jobs.manual_calls == []


def test_nonretryable_receipt_service_failure_moves_to_manual_review():
    error = ReceiptJobServiceError("RECEIPT_JOB_INVALID")
    error.retryable = False
    service, voice_jobs, receipt_jobs = coordinator(FakeReceiptJobs([error]))
    record = pending_record()

    result = service.activate_pending(record)

    assert result == WhatsAppVoiceReceiptActivationResult(
        "manual_review",
        False,
    )
    assert len(receipt_jobs.calls) == 1
    assert voice_jobs.manual_calls == [record]


def test_receipt_submission_value_error_moves_to_manual_review():
    service, voice_jobs, receipt_jobs = coordinator(
        FakeReceiptJobs([ValueError("private invalid input")])
    )
    record = pending_record()

    result = service.activate_pending(record)

    assert result == WhatsAppVoiceReceiptActivationResult(
        "manual_review",
        False,
    )
    assert len(receipt_jobs.calls) == 1
    assert voice_jobs.manual_calls == [record]


@pytest.mark.parametrize(
    "error",
    [RuntimeError("programming error"), TypeError("programming error")],
)
def test_unexpected_programming_failure_propagates_and_leaves_pending(error):
    service, voice_jobs, receipt_jobs = coordinator(FakeReceiptJobs([error]))
    record = pending_record()

    with pytest.raises(type(error)) as raised:
        service.activate_pending(record)

    assert raised.value is error
    assert record["receipt_activation_state"] == "pending"
    assert len(receipt_jobs.calls) == 1
    assert voice_jobs.complete_calls == voice_jobs.manual_calls == []


@pytest.mark.parametrize(
    "completion_error",
    [aws_error(), EndpointConnectionError(endpoint_url="https://private.invalid"), VoiceJobConditionFailed("conflict")],
)
def test_completion_checkpoint_failure_does_not_rollback_receipt_job(
    completion_error,
):
    voice_jobs = FakeVoiceJobs(complete_outcomes=[completion_error])
    receipt_jobs = FakeReceiptJobs()
    service, _, _ = coordinator(receipt_jobs, voice_jobs)
    record = pending_record()

    result = service.activate_pending(record)

    assert result == WhatsAppVoiceReceiptActivationResult(
        "retryable_failure",
        True,
    )
    assert len(receipt_jobs.calls) == 1
    assert voice_jobs.complete_calls == [record]
    assert not hasattr(receipt_jobs, "delete")
    assert record["receipt_activation_state"] == "pending"


def test_duplicate_receipt_submission_recovers_after_completion_failure():
    receipt_jobs = FakeReceiptJobs([
        ReceiptJobSubmission("job", True, True, False),
        ReceiptJobSubmission("job", True, True, True),
    ])
    voice_jobs = FakeVoiceJobs(
        complete_outcomes=[aws_error(), None],
    )
    service, _, _ = coordinator(receipt_jobs, voice_jobs)
    record = pending_record()

    first = service.activate_pending(record)
    recovered = service.activate_pending(record)

    assert first == WhatsAppVoiceReceiptActivationResult(
        "retryable_failure",
        True,
    )
    assert recovered == WhatsAppVoiceReceiptActivationResult(
        "activated",
        False,
    )
    assert len(receipt_jobs.calls) == 2
    assert receipt_jobs.calls[0] == receipt_jobs.calls[1]
    assert len(voice_jobs.complete_calls) == 2


def test_unaccepted_receipt_submission_is_retryable_without_completion():
    service, voice_jobs, receipt_jobs = coordinator(
        FakeReceiptJobs([
            ReceiptJobSubmission("job", False, False, False)
        ])
    )

    result = service.activate_pending(pending_record())

    assert result == WhatsAppVoiceReceiptActivationResult(
        "retryable_failure",
        True,
    )
    assert len(receipt_jobs.calls) == 1
    assert voice_jobs.complete_calls == []


def test_voice_receipt_logs_exclude_private_values(caplog):
    service, _, _ = coordinator()
    record = pending_record()

    with caplog.at_level("INFO"):
        result = service.activate_pending(record)

    assert result.status == "activated"
    serialized = repr([vars(log_record) for log_record in caplog.records])
    for private in (
        ORDER_ID,
        CUSTOMER_NUMBER,
        SENDER_ID,
        "session-private",
        "request-private",
    ):
        assert private not in serialized
    log_record = caplog.records[-1]
    assert log_record.event == "whatsapp_voice_receipt_activation"
    assert log_record.voice_receipt_activation_status == "activated"
    assert log_record.retryable is False
