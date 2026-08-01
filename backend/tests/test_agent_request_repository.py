from copy import deepcopy

import pytest
from botocore.exceptions import ClientError

from src.repositories.agent_request_repository import AgentRequestRepository


class FakeTable:
    def __init__(self):
        self.put_calls = []
        self.put_error = None
        self.get_calls = []
        self.get_response = {}
        self.delete_calls = []

    def put_item(self, **kwargs):
        self.put_calls.append(deepcopy(kwargs))
        if self.put_error:
            raise self.put_error

    def get_item(self, **kwargs):
        self.get_calls.append(deepcopy(kwargs))
        return deepcopy(self.get_response)

    def delete_item(self, **kwargs):
        self.delete_calls.append(deepcopy(kwargs))


class FakeDynamo:
    def __init__(self):
        self.table = FakeTable()

    def Table(self, table_name):
        assert table_name == "agent-requests"
        return self.table


def client_error(code):
    return ClientError(
        {"Error": {"Code": code, "Message": "private failure"}},
        "PutItem",
    )


def test_claim_idempotency_key_uses_conditional_ttl_write():
    dynamo = FakeDynamo()
    repository = AgentRequestRepository(dynamo, "agent-requests")
    marker = {
        "PK": "agentflo-whatsapp-message:wamid.synthetic-1",
        "SK": "IDEMPOTENCY",
        "record_type": "agentflo_whatsapp_message_idempotency",
        "created_at": "2026-07-29T10:00:00+00:00",
        "expires_at": 1785405600,
    }

    assert repository.claim_idempotency_key(
        marker,
        now_epoch=1785319200,
    )
    assert dynamo.table.put_calls == [{
        "Item": marker,
        "ConditionExpression": (
            "attribute_not_exists(PK) OR expires_at <= :now"
        ),
        "ExpressionAttributeValues": {
            ":now": 1785319200,
        },
    }]


def test_claim_idempotency_key_classifies_duplicate_condition():
    dynamo = FakeDynamo()
    dynamo.table.put_error = client_error("ConditionalCheckFailedException")
    repository = AgentRequestRepository(dynamo, "agent-requests")

    assert not repository.claim_idempotency_key(
        {
            "PK": "agentflo-whatsapp-message:wamid.synthetic-1",
            "SK": "IDEMPOTENCY",
            "expires_at": 1785405600,
        },
        now_epoch=1785319200,
    )


def test_claim_idempotency_key_propagates_unrelated_aws_failure():
    dynamo = FakeDynamo()
    dynamo.table.put_error = client_error("ProvisionedThroughputExceededException")
    repository = AgentRequestRepository(dynamo, "agent-requests")

    with pytest.raises(ClientError) as raised:
        repository.claim_idempotency_key(
            {
                "PK": "agentflo-whatsapp-message:wamid.synthetic-1",
                "SK": "IDEMPOTENCY",
                "expires_at": 1785405600,
            },
            now_epoch=1785319200,
        )

    assert (
        raised.value.response["Error"]["Code"]
        == "ProvisionedThroughputExceededException"
    )


def test_whatsapp_idempotency_marker_read_save_and_delete_use_exact_key():
    dynamo = FakeDynamo()
    repository = AgentRequestRepository(dynamo, "agent-requests")
    marker = {
        "PK": "agentflo-whatsapp-message:wamid.synthetic-1",
        "SK": "IDEMPOTENCY",
        "delivery_state": "response_ready",
    }
    dynamo.table.get_response = {"Item": marker}

    assert repository.get_idempotency_key("wamid.synthetic-1") == marker
    repository.save_idempotency_key(marker)
    repository.delete_idempotency_key("wamid.synthetic-1")

    key = {
        "PK": "agentflo-whatsapp-message:wamid.synthetic-1",
        "SK": "IDEMPOTENCY",
    }
    assert dynamo.table.get_calls == [{"Key": key, "ConsistentRead": True}]
    assert dynamo.table.put_calls == [{"Item": marker}]
    assert dynamo.table.delete_calls == [{"Key": key}]
