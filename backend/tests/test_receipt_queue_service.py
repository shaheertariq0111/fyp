import pytest
from botocore.exceptions import ClientError

from src.models.receipt_job import ReceiptQueueMessage, receipt_job_id
from src.services.receipt_queue_service import (
    ReceiptQueueError,
    ReceiptQueueService,
)


class FakeSqs:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def send_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"MessageId": "opaque"}


def test_receipt_queue_send_uses_exact_url_and_minimal_body():
    client = FakeSqs()
    service = ReceiptQueueService(client, "  receipt-queue-url  ")
    job_id = receipt_job_id("ORD-123", 1)

    service.send(job_id)

    assert client.calls == [{
        "QueueUrl": "receipt-queue-url",
        "MessageBody": ReceiptQueueMessage(job_id=job_id).serialize(),
    }]
    assert set(client.calls[0]) == {"QueueUrl", "MessageBody"}
    body = client.calls[0]["MessageBody"]
    assert body == (
        '{"job_id":"' + job_id
        + '","kind":"whatsapp_pdf_receipt","v":1}'
    )
    for private_value in (
        "ORD-123",
        "+923001234567",
        "sender-private",
        "conversation-private",
        "request-private",
    ):
        assert private_value not in body


def test_receipt_queue_wraps_low_level_error_without_exposing_message():
    aws_error = ClientError(
        {
            "Error": {
                "Code": "AccessDeniedException",
                "Message": "private AWS response body",
            }
        },
        "SendMessage",
    )
    client = FakeSqs(aws_error)
    service = ReceiptQueueService(client, "receipt-queue-url")

    with pytest.raises(ReceiptQueueError) as error:
        service.send(receipt_job_id("ORD-123", 1))

    assert error.value.error_code == "RECEIPT_QUEUE_OPERATION_FAILED"
    assert error.value.retryable is True
    assert str(error.value) == "RECEIPT_QUEUE_OPERATION_FAILED"
    assert "private AWS response body" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


@pytest.mark.parametrize(
    "programming_error",
    [RuntimeError("programming defect"), TypeError("invalid client contract")],
)
def test_receipt_queue_does_not_convert_programming_errors(programming_error):
    service = ReceiptQueueService(FakeSqs(programming_error), "receipt-queue-url")

    with pytest.raises(type(programming_error), match=str(programming_error)) as error:
        service.send(receipt_job_id("ORD-123", 1))

    assert error.value is programming_error


@pytest.mark.parametrize("queue_url", ["", "  ", None, 123])
def test_receipt_queue_requires_non_empty_url(queue_url):
    with pytest.raises(ValueError, match="RECEIPT_QUEUE_URL_REQUIRED"):
        ReceiptQueueService(FakeSqs(), queue_url)
