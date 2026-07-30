from copy import deepcopy
from datetime import datetime, timezone

from src.services.conversation_history_service import ConversationHistoryService


class MemoryConversationRepository:
    def __init__(self):
        self.saved = []

    def save(self, message):
        self.saved.append(deepcopy(message))


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
