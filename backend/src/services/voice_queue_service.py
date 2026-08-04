from __future__ import annotations

from dataclasses import dataclass

from src.models.whatsapp_voice_job import VoiceQueueMessage


class VoiceQueueError(RuntimeError):
    def __init__(self, error_code: str = "VOICE_QUEUE_OPERATION_FAILED"):
        super().__init__(error_code)
        self.error_code = error_code
        self.retryable = True


@dataclass(frozen=True, slots=True)
class ReceivedVoiceMessage:
    queue_message: VoiceQueueMessage
    receipt_handle: str
    receive_count: int


class VoiceQueueService:
    def __init__(self, client, queue_url: str, *, wait_time_seconds: int = 20, visibility_timeout_seconds: int = 300):
        self.client = client
        self.queue_url = queue_url
        self.wait_time_seconds = wait_time_seconds
        self.visibility_timeout_seconds = visibility_timeout_seconds

    def send(self, job_id: str) -> None:
        try:
            self.client.send_message(
                QueueUrl=self.queue_url,
                MessageBody=VoiceQueueMessage(job_id=job_id).serialize(),
            )
        except Exception as exc:
            raise VoiceQueueError() from exc

    def receive_one(self) -> ReceivedVoiceMessage | None:
        try:
            response = self.client.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=self.wait_time_seconds,
                VisibilityTimeout=self.visibility_timeout_seconds,
                AttributeNames=["ApproximateReceiveCount"],
            )
        except Exception as exc:
            raise VoiceQueueError("VOICE_QUEUE_RECEIVE_FAILED") from exc
        messages = response.get("Messages", [])
        if not messages:
            return None
        message = messages[0]
        try:
            parsed = VoiceQueueMessage.parse(message["Body"])
            receipt_handle = message["ReceiptHandle"]
            count = int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
        except (KeyError, TypeError, ValueError) as exc:
            raise VoiceQueueError("VOICE_QUEUE_MESSAGE_INVALID") from exc
        return ReceivedVoiceMessage(parsed, receipt_handle, count)

    def delete(self, receipt_handle: str) -> None:
        try:
            self.client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt_handle)
        except Exception as exc:
            raise VoiceQueueError("VOICE_QUEUE_DELETE_FAILED") from exc

    def change_visibility(self, receipt_handle: str, timeout_seconds: int | None = None) -> None:
        try:
            self.client.change_message_visibility(
                QueueUrl=self.queue_url,
                ReceiptHandle=receipt_handle,
                VisibilityTimeout=timeout_seconds or self.visibility_timeout_seconds,
            )
        except Exception as exc:
            raise VoiceQueueError("VOICE_QUEUE_VISIBILITY_FAILED") from exc
