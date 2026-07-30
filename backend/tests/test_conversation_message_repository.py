from copy import deepcopy

from src.repositories.conversation_message_repository import ConversationMessageRepository


class FakeTable:
    def __init__(self):
        self.put_calls = []

    def put_item(self, **kwargs):
        self.put_calls.append(deepcopy(kwargs))


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
