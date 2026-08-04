import json

import pytest

from src.models.whatsapp_voice_job import VoiceQueueMessage, voice_job_id
from src.services.voice_queue_service import VoiceQueueError, VoiceQueueService


class FakeSqs:
    def __init__(self):
        self.calls = []
        self.messages = []

    def send_message(self, **kwargs):
        self.calls.append(("send", kwargs))
        return {"MessageId": "opaque"}

    def receive_message(self, **kwargs):
        self.calls.append(("receive", kwargs))
        return {"Messages": self.messages}

    def delete_message(self, **kwargs):
        self.calls.append(("delete", kwargs))

    def change_message_visibility(self, **kwargs):
        self.calls.append(("visibility", kwargs))


def test_standard_sqs_send_uses_only_queue_url_and_compact_body():
    client = FakeSqs()
    service = VoiceQueueService(client, "queue-url")
    job_id = voice_job_id("provider-message-sensitive")

    service.send(job_id)

    operation, parameters = client.calls[0]
    assert operation == "send"
    assert set(parameters) == {"QueueUrl", "MessageBody"}
    assert len(parameters["MessageBody"].encode()) <= 512
    assert json.loads(parameters["MessageBody"]) == {
        "v": 1, "kind": "agentflo_whatsapp_voice", "job_id": job_id,
    }
    assert "provider-message-sensitive" not in parameters["MessageBody"]
    assert "MessageGroupId" not in parameters
    assert "MessageDeduplicationId" not in parameters


@pytest.mark.parametrize("body", [
    '{"v":1,"kind":"agentflo_whatsapp_voice","job_id":"bad"}',
    '{"v":1,"v":1,"kind":"agentflo_whatsapp_voice","job_id":"wv1_' + "0" * 64 + '"}',
    '{"v":1,"kind":"agentflo_whatsapp_voice","job_id":"wv1_' + "0" * 64 + '","extra":true}',
    '{"v":2,"kind":"agentflo_whatsapp_voice","job_id":"wv1_' + "0" * 64 + '"}',
])
def test_queue_schema_rejects_invalid_duplicate_and_unknown_keys(body):
    with pytest.raises(ValueError):
        VoiceQueueMessage.parse(body)


def test_receive_is_one_message_long_poll_and_visibility_is_metadata_only():
    client = FakeSqs()
    job_id = voice_job_id("message-1")
    client.messages = [{
        "Body": VoiceQueueMessage(job_id).serialize(),
        "ReceiptHandle": "sensitive-receipt",
        "Attributes": {"ApproximateReceiveCount": "2"},
    }]
    service = VoiceQueueService(client, "queue-url", wait_time_seconds=20, visibility_timeout_seconds=300)

    received = service.receive_one()
    service.change_visibility(received.receipt_handle)
    service.delete(received.receipt_handle)

    assert received.queue_message.job_id == job_id
    assert received.receive_count == 2
    assert client.calls[0][1] == {
        "QueueUrl": "queue-url", "MaxNumberOfMessages": 1,
        "WaitTimeSeconds": 20, "VisibilityTimeout": 300,
        "AttributeNames": ["ApproximateReceiveCount"],
    }


def test_queue_errors_are_sanitized():
    class Broken:
        def send_message(self, **_kwargs):
            raise RuntimeError("secret receipt and body")

    with pytest.raises(VoiceQueueError) as error:
        VoiceQueueService(Broken(), "queue-url").send(voice_job_id("message"))
    assert str(error.value) == "VOICE_QUEUE_OPERATION_FAILED"
