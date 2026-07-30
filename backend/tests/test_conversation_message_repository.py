from copy import deepcopy

from src.repositories.conversation_message_repository import ConversationMessageRepository


class FakeTable:
    def __init__(self):
        self.put_calls = []
        self.query_calls = []
        self.query_responses = []

    def put_item(self, **kwargs):
        self.put_calls.append(deepcopy(kwargs))

    def query(self, **kwargs):
        self.query_calls.append(deepcopy(kwargs))
        return self.query_responses.pop(0)


class FakeDynamo:
    def __init__(self):
        self.table = FakeTable()

    def Table(self, table_name):
        assert table_name == "conversation-messages"
        return self.table


def test_conversation_message_repository_writes_expected_item_shape():
    dynamo = FakeDynamo()
    repository = ConversationMessageRepository(dynamo, "conversation-messages")
    item = {
        "PK": "CONVERSATION#whatsapp-session",
        "SK": "MSG#2026-07-30T10:00:00+00:00#inbound#wamid-1",
        "GSI1PK": "CHANNEL#whatsapp",
        "GSI1SK": "2026-07-30T10:00:00+00:00#whatsapp-session",
        "message_text": "hello",
        "expires_at": 1790762400,
    }

    repository.save(item)

    assert dynamo.table.put_calls == [{"Item": item}]


def test_conversation_message_repository_queries_recent_whatsapp_gsi():
    dynamo = FakeDynamo()
    dynamo.table.query_responses = [{"Items": [{"conversation_id": "conv-1"}]}]
    repository = ConversationMessageRepository(dynamo, "conversation-messages")

    items = repository.list_recent_whatsapp_messages(limit=25)

    assert items == [{"conversation_id": "conv-1"}]
    assert dynamo.table.query_calls[0]["IndexName"] == "GSI1"
    assert dynamo.table.query_calls[0]["ScanIndexForward"] is False
    assert dynamo.table.query_calls[0]["Limit"] == 25


def test_conversation_message_repository_queries_conversation_partition():
    dynamo = FakeDynamo()
    dynamo.table.query_responses = [{"Items": [{"conversation_id": "conv-1"}]}]
    repository = ConversationMessageRepository(dynamo, "conversation-messages")

    items = repository.list_for_conversation("conv-1")

    assert items == [{"conversation_id": "conv-1"}]
    assert dynamo.table.query_calls[0]["ScanIndexForward"] is True
