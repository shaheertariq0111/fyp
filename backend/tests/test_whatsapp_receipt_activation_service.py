from __future__ import annotations

from copy import deepcopy

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from src.services.receipt_job_service import (
    ReceiptJobServiceError,
    ReceiptJobSubmission,
)
from src.services.whatsapp_receipt_activation_service import (
    WhatsAppReceiptActivationResult,
    WhatsAppReceiptActivationService,
)


MESSAGE_ID = "wamid.private-message"
ORDER_ID = "ORD-PRIVATE-123"
CUSTOMER_NUMBER = "+923001234567"
SENDER_ID = "private-sender"


def pending_marker(**overrides):
    marker = {
        "delivery_state": "completed",
        "receipt_activation_state": "pending",
        "submitted_order_id": ORDER_ID,
        "request_id": "private-request",
        "session_id": "private-conversation",
        "reply": "private reply text",
    }
    marker.update(overrides)
    return marker


class FakeAgentRequests:
    def __init__(self):
        self.complete_calls = []
        self.manual_calls = []
        self.complete_results = [True]
        self.complete_error = None
        self.manual_result = True

    def complete_agentflo_whatsapp_receipt_activation(self, message_id):
        self.complete_calls.append(message_id)
        if self.complete_error is not None:
            raise self.complete_error
        return self.complete_results.pop(0)

    def mark_agentflo_whatsapp_receipt_manual_review(self, message_id):
        self.manual_calls.append(message_id)
        return self.manual_result


class FakeReceiptJobs:
    def __init__(self, submissions=None):
        self.calls = []
        self.submissions = list(submissions or [
            ReceiptJobSubmission("opaque-job", True, True, False)
        ])

    def submit(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        outcome = self.submissions.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def activation(receipt_jobs=None):
    agent_requests = FakeAgentRequests()
    receipt_jobs = receipt_jobs or FakeReceiptJobs()
    service = WhatsAppReceiptActivationService(
        agent_requests=agent_requests,
        receipt_jobs=receipt_jobs,
    )
    return service, agent_requests, receipt_jobs


@pytest.mark.parametrize(
    ("submission", "expected_duplicate"),
    [
        (ReceiptJobSubmission("job", True, True, False), False),
        (ReceiptJobSubmission("job", True, False, False), False),
        (ReceiptJobSubmission("job", True, True, True), True),
        (ReceiptJobSubmission("job", True, False, True), True),
    ],
)
def test_valid_pending_marker_submits_exact_values_and_completes(
    submission,
    expected_duplicate,
    caplog,
):
    service, agent_requests, receipt_jobs = activation(
        FakeReceiptJobs([submission])
    )

    with caplog.at_level("INFO"):
        result = service.activate_pending(
            message_id=MESSAGE_ID,
            marker=pending_marker(),
            customer_number=CUSTOMER_NUMBER,
            sender_id=SENDER_ID,
        )

    assert result == WhatsAppReceiptActivationResult("activated", False)
    assert receipt_jobs.calls == [{
        "order_id": ORDER_ID,
        "receipt_version": 1,
        "customer_number": CUSTOMER_NUMBER,
        "sender_id": SENDER_ID,
        "conversation_id": "private-conversation",
        "request_id": "private-request",
    }]
    assert agent_requests.complete_calls == [MESSAGE_ID]
    assert caplog.records[-1].duplicate is expected_duplicate


@pytest.mark.parametrize(
    ("marker", "expected_status"),
    [
        (pending_marker(receipt_activation_state="completed"), "already_activated"),
        (pending_marker(receipt_activation_state="manual_review"), "manual_review"),
        (pending_marker(delivery_state="outbound_sending"), "not_required"),
        (pending_marker(delivery_state="response_ready"), "not_required"),
        ({"delivery_state": "completed"}, "not_required"),
        (None, "not_required"),
    ],
)
def test_nonpending_or_text_ambiguous_markers_never_submit(marker, expected_status):
    service, agent_requests, receipt_jobs = activation()

    result = service.activate_pending(
        message_id=MESSAGE_ID,
        marker=marker,
        customer_number=CUSTOMER_NUMBER,
        sender_id=SENDER_ID,
    )

    assert result.status == expected_status
    assert result.retryable is False
    assert receipt_jobs.calls == []
    assert agent_requests.complete_calls == []
    assert agent_requests.manual_calls == []


@pytest.mark.parametrize(
    ("marker_overrides", "customer_number", "sender_id"),
    [
        ({"submitted_order_id": None}, CUSTOMER_NUMBER, SENDER_ID),
        ({"submitted_order_id": ""}, CUSTOMER_NUMBER, SENDER_ID),
        ({"submitted_order_id": " ORD "}, CUSTOMER_NUMBER, SENDER_ID),
        ({"request_id": None}, CUSTOMER_NUMBER, SENDER_ID),
        ({"request_id": " request "}, CUSTOMER_NUMBER, SENDER_ID),
        ({"session_id": ""}, CUSTOMER_NUMBER, SENDER_ID),
        ({"session_id": " session "}, CUSTOMER_NUMBER, SENDER_ID),
        ({}, None, SENDER_ID),
        ({}, " ", SENDER_ID),
        ({}, CUSTOMER_NUMBER, None),
        ({}, CUSTOMER_NUMBER, " sender "),
    ],
)
def test_invalid_pending_marker_moves_to_manual_review_without_submit(
    marker_overrides,
    customer_number,
    sender_id,
):
    service, agent_requests, receipt_jobs = activation()

    result = service.activate_pending(
        message_id=MESSAGE_ID,
        marker=pending_marker(**marker_overrides),
        customer_number=customer_number,
        sender_id=sender_id,
    )

    assert result == WhatsAppReceiptActivationResult("manual_review", False)
    assert agent_requests.manual_calls == [MESSAGE_ID]
    assert receipt_jobs.calls == []


def aws_client_error():
    return ClientError(
        {"Error": {"Code": "InternalError", "Message": "private AWS body"}},
        "PutItem",
    )


@pytest.mark.parametrize(
    "error",
    [
        ReceiptJobServiceError("RECEIPT_JOB_INTERNAL_ERROR"),
        aws_client_error(),
        EndpointConnectionError(endpoint_url="https://private.invalid"),
    ],
)
def test_retryable_receipt_job_failures_leave_marker_pending(error):
    service, agent_requests, receipt_jobs = activation(FakeReceiptJobs([error]))
    marker = pending_marker()

    result = service.activate_pending(
        message_id=MESSAGE_ID,
        marker=marker,
        customer_number=CUSTOMER_NUMBER,
        sender_id=SENDER_ID,
    )

    assert result == WhatsAppReceiptActivationResult("retryable_failure", True)
    assert marker["receipt_activation_state"] == "pending"
    assert agent_requests.complete_calls == agent_requests.manual_calls == []
    assert len(receipt_jobs.calls) == 1


def test_local_receipt_validation_failure_moves_to_manual_review():
    service, agent_requests, receipt_jobs = activation(
        FakeReceiptJobs([ValueError("private invalid value")])
    )

    result = service.activate_pending(
        message_id=MESSAGE_ID,
        marker=pending_marker(),
        customer_number=CUSTOMER_NUMBER,
        sender_id=SENDER_ID,
    )

    assert result == WhatsAppReceiptActivationResult("manual_review", False)
    assert agent_requests.manual_calls == [MESSAGE_ID]
    assert len(receipt_jobs.calls) == 1


@pytest.mark.parametrize(
    "error",
    [RuntimeError("programming error"), TypeError("programming error")],
)
def test_programming_errors_propagate_and_leave_marker_pending(error):
    service, agent_requests, receipt_jobs = activation(FakeReceiptJobs([error]))
    marker = pending_marker()

    with pytest.raises(type(error)) as raised:
        service.activate_pending(
            message_id=MESSAGE_ID,
            marker=marker,
            customer_number=CUSTOMER_NUMBER,
            sender_id=SENDER_ID,
        )

    assert raised.value is error
    assert marker["receipt_activation_state"] == "pending"
    assert agent_requests.complete_calls == agent_requests.manual_calls == []
    assert len(receipt_jobs.calls) == 1


def test_job_created_but_marker_conflict_stays_recoverable_then_duplicate_completes():
    first = ReceiptJobSubmission("job", True, True, False)
    duplicate = ReceiptJobSubmission("job", True, True, True)
    service, agent_requests, receipt_jobs = activation(
        FakeReceiptJobs([first, duplicate])
    )
    agent_requests.complete_results = [False, True]
    marker = pending_marker()

    initial = service.activate_pending(
        message_id=MESSAGE_ID,
        marker=marker,
        customer_number=CUSTOMER_NUMBER,
        sender_id=SENDER_ID,
    )
    recovered = service.activate_pending(
        message_id=MESSAGE_ID,
        marker=marker,
        customer_number=CUSTOMER_NUMBER,
        sender_id=SENDER_ID,
    )

    assert initial == WhatsAppReceiptActivationResult(
        "retryable_failure",
        True,
    )
    assert recovered == WhatsAppReceiptActivationResult("activated", False)
    assert len(receipt_jobs.calls) == 2
    assert receipt_jobs.calls[0] == receipt_jobs.calls[1]
    assert not hasattr(receipt_jobs, "delete")


def test_marker_checkpoint_aws_failure_after_job_creation_is_retryable():
    service, agent_requests, receipt_jobs = activation()
    agent_requests.complete_error = aws_client_error()

    result = service.activate_pending(
        message_id=MESSAGE_ID,
        marker=pending_marker(),
        customer_number=CUSTOMER_NUMBER,
        sender_id=SENDER_ID,
    )

    assert result == WhatsAppReceiptActivationResult("retryable_failure", True)
    assert len(receipt_jobs.calls) == 1
    assert agent_requests.complete_calls == [MESSAGE_ID]


def test_unaccepted_submission_is_retryable_without_false_completion():
    rejected = ReceiptJobSubmission("job", False, False, False)
    service, agent_requests, _ = activation(FakeReceiptJobs([rejected]))

    result = service.activate_pending(
        message_id=MESSAGE_ID,
        marker=pending_marker(),
        customer_number=CUSTOMER_NUMBER,
        sender_id=SENDER_ID,
    )

    assert result == WhatsAppReceiptActivationResult("retryable_failure", True)
    assert agent_requests.complete_calls == []


def test_logs_and_result_exclude_activation_private_data(caplog):
    service, _, _ = activation()
    marker = pending_marker()
    with caplog.at_level("INFO"):
        result = service.activate_pending(
            message_id=MESSAGE_ID,
            marker=marker,
            customer_number=CUSTOMER_NUMBER,
            sender_id=SENDER_ID,
        )

    assert set(result.__dataclass_fields__) == {"status", "retryable"}
    structured = " ".join(
        str(value)
        for record in caplog.records
        for key, value in vars(record).items()
        if key not in {"args", "exc_info", "exc_text", "stack_info"}
    )
    for private in (
        MESSAGE_ID,
        ORDER_ID,
        CUSTOMER_NUMBER,
        SENDER_ID,
        marker["request_id"],
        marker["session_id"],
        marker["reply"],
        str(marker),
    ):
        assert private not in structured
