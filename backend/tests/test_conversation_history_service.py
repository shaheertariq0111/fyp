from copy import deepcopy
from datetime import datetime, timezone

from src.services.conversation_history_service import ConversationHistoryService


class MemoryConversationRepository:
    def __init__(self):
        self.saved = []
        self.recent = []
        self.by_conversation = {}

    def save(self, message):
        self.saved.append(deepcopy(message))

    def list_recent_whatsapp_messages(self, *, limit):
        return deepcopy(self.recent[:limit])

    def list_for_conversation(self, conversation_id):
        return deepcopy(self.by_conversation.get(conversation_id, []))


def service(now):
    repository = MemoryConversationRepository()
    history = ConversationHistoryService(repository, ttl_days=90)
    history._now = lambda: now
    return history, repository


def test_conversation_history_service_builds_inbound_record_correctly():
    now = datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)
    history, repository = service(now)

    item = history.store_inbound_whatsapp_message(
        conversation_id="whatsapp-session",
        customer_id="whatsapp-customer",
        message_text="Hello from WhatsApp",
        inbound_message_id="wamid.synthetic-1",
        customer_number="+10000001234",
    )

    assert repository.saved == [item]
    assert item["PK"] == "CONVERSATION#whatsapp-session"
    assert item["SK"] == (
        "MSG#2026-07-30T10:00:00+00:00#inbound#wamid.synthetic-1"
    )
    assert item["GSI1PK"] == "CHANNEL#whatsapp"
    assert item["GSI1SK"] == "2026-07-30T10:00:00+00:00#whatsapp-session"
    assert item["channel"] == "whatsapp"
    assert item["direction"] == "inbound"
    assert item["sender_type"] == "customer"
    assert item["message_text"] == "Hello from WhatsApp"
    assert item["inbound_whatsapp_message_id"] == "wamid.synthetic-1"
    assert item["masked_customer_phone"] == "****1234"
    assert item["duplicate"] is False


def test_conversation_history_service_builds_outbound_record_correctly():
    now = datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)
    history, _repository = service(now)

    item = history.store_outbound_whatsapp_message(
        conversation_id="whatsapp-session",
        customer_id="whatsapp-customer",
        message_text="Agent reply",
        request_id="req-1",
        inbound_message_id="wamid.synthetic-1",
        outbound={
            "sent": True,
            "status": "accepted",
            "providerMessageId": "provider-synthetic-1",
        },
    )

    assert item["SK"] == (
        "MSG#2026-07-30T10:00:00+00:00#outbound#provider-synthetic-1"
    )
    assert item["direction"] == "outbound"
    assert item["sender_type"] == "agent"
    assert item["message_text"] == "Agent reply"
    assert item["request_id"] == "req-1"
    assert item["inbound_whatsapp_message_id"] == "wamid.synthetic-1"
    assert item["outbound_provider_message_id"] == "provider-synthetic-1"
    assert item["outbound_status"] == "accepted"
    assert item["delivery_status"] == "accepted"


def test_conversation_history_ttl_is_around_90_days():
    now = datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)
    history, _repository = service(now)

    item = history.store_inbound_whatsapp_message(
        conversation_id="whatsapp-session",
        customer_id="whatsapp-customer",
        message_text="Hello",
        inbound_message_id="wamid.synthetic-1",
    )

    assert item["expires_at"] - int(now.timestamp()) == 90 * 24 * 60 * 60


def conversation_message(**overrides):
    message = {
        "conversation_id": "conv-1",
        "channel": "whatsapp",
        "timestamp_utc": "2026-07-30T10:00:00+00:00",
        "direction": "inbound",
        "sender_type": "customer",
        "message_text": "Hello",
        "masked_customer_phone": "****1234",
    }
    message.update(overrides)
    return message


def test_admin_list_conversations_groups_and_counts_recent_conversations():
    history, repository = service(datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc))
    repository.recent = [
        conversation_message(conversation_id="conv-2"),
        conversation_message(conversation_id="conv-1"),
        conversation_message(conversation_id="conv-1"),
    ]
    repository.by_conversation = {
        "conv-1": [
            conversation_message(
                conversation_id="conv-1",
                timestamp_utc="2026-07-30T09:00:00+00:00",
                message_text="Customer says session_token=abc123",
            ),
            conversation_message(
                conversation_id="conv-1",
                timestamp_utc="2026-07-30T09:01:00+00:00",
                direction="outbound",
                sender_type="agent",
                message_text="Agent reply",
                delivery_status="accepted",
            ),
        ],
        "conv-2": [
            conversation_message(
                conversation_id="conv-2",
                timestamp_utc="2026-07-30T10:00:00+00:00",
                message_text="Latest message",
            )
        ],
    }

    result = history.admin_list_conversations(limit=10)

    assert [item["conversation_id"] for item in result["conversations"]] == [
        "conv-2",
        "conv-1",
    ]
    conv_1 = result["conversations"][1]
    assert conv_1["latest_message_preview"] == "Agent reply"
    assert conv_1["message_count"] == 2
    assert conv_1["customer_message_count"] == 1
    assert conv_1["agent_message_count"] == 1
    assert conv_1["masked_customer_phone"] == "****1234"
    assert conv_1["latest_delivery_status"] == "accepted"
    assert "abc123" not in str(result)


def test_admin_list_messages_returns_chronological_redacted_transcript():
    history, repository = service(datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc))
    repository.by_conversation = {
        "conv-1": [
            conversation_message(
                timestamp_utc="2026-07-30T10:02:00+00:00",
                direction="outbound",
                sender_type="agent",
                message_text='Menu link {"session_token":"secret-token"}',
                outbound_status="accepted",
                delivery_status="delivered",
            ),
            conversation_message(
                timestamp_utc="2026-07-30T10:01:00+00:00",
                message_text="session_token=abc123 phone +10000001234",
                customer_number="+10000001234",
            ),
        ],
    }

    result = history.admin_list_messages("conv-1")

    assert [message["timestamp_utc"] for message in result["messages"]] == [
        "2026-07-30T10:01:00+00:00",
        "2026-07-30T10:02:00+00:00",
    ]
    assert result["messages"][0]["message_text"] == (
        "session_token=[REDACTED] phone [REDACTED_PHONE]"
    )
    assert result["messages"][1]["message_text"] == (
        'Menu link {"session_token":"[REDACTED]"}'
    )
    assert result["messages"][1]["outbound_status"] == "accepted"
    assert result["messages"][1]["delivery_status"] == "delivered"
    assert "customer_number" not in result["messages"][0]
    assert "+10000001234" not in str(result)


def test_admin_conversation_empty_results():
    history, _repository = service(datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc))

    assert history.admin_list_conversations() == {"conversations": []}
    assert history.admin_list_messages("missing") == {
        "conversation_id": "missing",
        "channel": "whatsapp",
        "messages": [],
    }
