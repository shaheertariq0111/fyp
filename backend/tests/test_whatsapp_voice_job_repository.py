from copy import deepcopy

import pytest
from botocore.exceptions import ClientError

from src.repositories.base import to_dynamodb
from src.repositories.whatsapp_voice_job_repository import WhatsAppVoiceJobRepository, VoiceJobConditionFailed


def condition_failure():
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "private"}},
        "UpdateItem",
    )


class Table:
    def __init__(self):
        self.calls = []
        self.fail = False

    def put_item(self, **kwargs):
        self.calls.append(("put", kwargs))
        if self.fail:
            raise condition_failure()

    def update_item(self, **kwargs):
        self.calls.append(("update", kwargs))
        if self.fail:
            raise condition_failure()
        return {"Attributes": {
            "PK": kwargs["Key"]["PK"], "SK": "METADATA", "job_id": kwargs["Key"]["PK"][4:],
            "state": kwargs.get("ExpressionAttributeValues", {}).get(":next", "processing"),
            "version": 2,
        }}


class Dynamo:
    def __init__(self, table):
        self.table = table

    def Table(self, _name):
        return self.table


def durable_record(*, audio_id="agentflo-audio-id"):
    job_id = "wv1_" + "a" * 64
    record = {
        "PK": f"JOB#{job_id}",
        "SK": "METADATA",
        "job_id": job_id,
        "state": "queued",
        "version": 1,
        "media_url": "https://lookaside.example.test/private",
        "customer_number": "+15550100000",
        "sender_id": "sender-private",
        "conversation_identity_hash": "b" * 64,
        "attempt_count": 0,
        "enqueue_attempt_count": 1,
        "created_at": "2026-08-05T00:00:00+00:00",
        "updated_at": "2026-08-05T00:00:00+00:00",
        "expires_at": 1785974400,
    }
    if audio_id is not None:
        record["audio_id"] = audio_id
    return record


def test_create_if_absent_is_conditional():
    table = Table()
    repository = WhatsAppVoiceJobRepository(Dynamo(table), "jobs")
    record = durable_record()

    assert repository.create_if_absent(record)
    assert table.calls[0][1]["ConditionExpression"] == "attribute_not_exists(PK)"
    table.fail = True
    assert repository.create_if_absent(record) is False


def test_new_durable_job_write_requires_valid_audio_id():
    repository = WhatsAppVoiceJobRepository(Dynamo(Table()), "jobs")

    with pytest.raises(ValueError, match="VOICE_JOB_RECORD_INVALID"):
        repository.create_if_absent(durable_record(audio_id=None))
    with pytest.raises(ValueError, match="VOICE_AUDIO_ID_INVALID"):
        repository.create_if_absent(durable_record(audio_id=""))


def test_audio_id_survives_durable_job_serialization_and_deserialization():
    class RoundTripTable(Table):
        def put_item(self, **kwargs):
            super().put_item(**kwargs)
            self.item = deepcopy(kwargs["Item"])

        def get_item(self, **_kwargs):
            return {"Item": deepcopy(self.item)}

    table = RoundTripTable()
    repository = WhatsAppVoiceJobRepository(Dynamo(table), "jobs")
    record = durable_record()

    assert repository.create_if_absent(record) is True
    restored = repository.get("wv1_" + "a" * 64)

    assert restored["audio_id"] == "agentflo-audio-id"
    assert restored["media_url"] == "https://lookaside.example.test/private"


def test_legacy_outbox_record_without_audio_id_deserializes():
    legacy = durable_record(audio_id=None)

    class LegacyQueryTable(Table):
        def query(self, **_kwargs):
            return {"Items": [to_dynamodb(legacy)]}

    repository = WhatsAppVoiceJobRepository(Dynamo(LegacyQueryTable()), "jobs")

    records = repository.query_due(
        due_partition="VOICE_OUTBOX",
        now_epoch=1785974400,
    )

    assert records == [legacy]
    assert "audio_id" not in records[0]


def test_state_transition_requires_expected_state_and_version():
    table = Table()
    repository = WhatsAppVoiceJobRepository(Dynamo(table), "jobs")

    repository.transition(
        "wv1_" + "0" * 64, expected_states={"queued"}, expected_version=7,
        next_state="processing", updated_at="timestamp",
    )
    call = table.calls[-1][1]
    assert "#version = :version" in call["ConditionExpression"]
    assert "#state IN" in call["ConditionExpression"]
    assert call["ExpressionAttributeValues"][":version"] == 7

    table.fail = True
    with pytest.raises(VoiceJobConditionFailed):
        repository.transition(
            "wv1_" + "0" * 64, expected_states={"queued"}, expected_version=7,
            next_state="processing", updated_at="timestamp",
        )


def test_live_lease_is_rejected_and_stale_lease_condition_is_explicit():
    table = Table()
    repository = WhatsAppVoiceJobRepository(Dynamo(table), "jobs")
    repository.acquire_lease(
        "wv1_" + "0" * 64, owner="worker", now_epoch=100,
        lease_expires_at=200, updated_at="timestamp",
    )
    condition = table.calls[-1][1]["ConditionExpression"]
    assert "lease_expires_at <= :now" in condition
    assert "lease_owner = :owner" in condition

    table.fail = True
    with pytest.raises(VoiceJobConditionFailed, match="VOICE_JOB_LEASE_HELD"):
        repository.acquire_lease(
            "wv1_" + "0" * 64, owner="other", now_epoch=100,
            lease_expires_at=200, updated_at="timestamp",
        )
