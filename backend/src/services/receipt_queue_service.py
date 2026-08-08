from __future__ import annotations

from botocore.exceptions import BotoCoreError, ClientError

from src.models.receipt_job import ReceiptQueueMessage


class ReceiptQueueError(RuntimeError):
    def __init__(self) -> None:
        self.error_code = "RECEIPT_QUEUE_OPERATION_FAILED"
        self.retryable = True
        super().__init__(self.error_code)


class ReceiptQueueService:
    def __init__(self, client, queue_url: str):
        if not isinstance(queue_url, str) or not queue_url.strip():
            raise ValueError("RECEIPT_QUEUE_URL_REQUIRED")
        self.client = client
        self.queue_url = queue_url.strip()

    def send(self, job_id: str) -> None:
        body = ReceiptQueueMessage(job_id=job_id).serialize()
        queue_failed = False
        try:
            self.client.send_message(
                QueueUrl=self.queue_url,
                MessageBody=body,
            )
        except (ClientError, BotoCoreError):
            queue_failed = True
        if queue_failed:
            raise ReceiptQueueError()
