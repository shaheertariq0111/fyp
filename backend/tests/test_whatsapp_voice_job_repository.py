from copy import deepcopy

import pytest
from botocore.exceptions import ClientError

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


def test_create_if_absent_is_conditional():
    table = Table()
    repository = WhatsAppVoiceJobRepository(Dynamo(table), "jobs")

    assert repository.create_if_absent({"PK": "JOB#opaque", "SK": "METADATA"})
    assert table.calls[0][1]["ConditionExpression"] == "attribute_not_exists(PK)"
    table.fail = True
    assert repository.create_if_absent({"PK": "JOB#opaque", "SK": "METADATA"}) is False


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
