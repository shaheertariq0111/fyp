from types import SimpleNamespace

import pytest

from src.models.whatsapp_voice_job import VoiceQueueMessage, voice_job_id
from src.workers.whatsapp_voice_worker import VoiceWorkerHeartbeat


class LeaseJobs:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def extend_lease(self, job_id, owner):
        self.calls.append((job_id, owner))
        return next(self.results)


class Queue:
    def __init__(self):
        self.receipts = []

    def change_visibility(self, receipt):
        self.receipts.append(receipt)


def test_worker_heartbeat_extends_lease_and_sqs_visibility_without_exposing_receipt():
    jobs, queue = LeaseJobs([True]), Queue()
    heartbeat = VoiceWorkerHeartbeat(
        jobs=jobs, queue=queue, job_id=voice_job_id("provider-id"),
        owner="worker-opaque", receipt_handle="private-receipt", interval_seconds=1,
    )
    heartbeat._run = lambda: None
    assert heartbeat.job_id.startswith("wv1_")
    assert not hasattr(heartbeat, "message_body")


def test_heartbeat_loss_blocks_irreversible_actions():
    heartbeat = VoiceWorkerHeartbeat(
        jobs=LeaseJobs([]), queue=Queue(), job_id=voice_job_id("provider-id"),
        owner="worker", receipt_handle="receipt", interval_seconds=1,
    )
    heartbeat.lost_event.set()
    with pytest.raises(RuntimeError, match="VOICE_WORKER_HEARTBEAT_LOST"):
        heartbeat.assert_owned()


def test_worker_module_queue_schema_has_no_sensitive_fields():
    job_id = voice_job_id("raw-provider-id")
    body = VoiceQueueMessage(job_id).serialize()
    assert set(__import__("json").loads(body)) == {"v", "kind", "job_id"}
    assert "raw-provider-id" not in body
