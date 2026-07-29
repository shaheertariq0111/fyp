from copy import deepcopy

import pytest
from botocore.exceptions import ClientError

from src.repositories.agent_request_repository import AgentRequestRepository


class FakeTable:
    def __init__(self):
        self.put_calls = []
        self.put_error = None

    def put_item(self, **kwargs):
        self.put_calls.append(deepcopy(kwargs))
        if self.put_error:
            raise self.put_error


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
