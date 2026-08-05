from src.api.whatsapp import (
    extract_whatsapp_audio_message,
    extract_whatsapp_message,
)


def meta_payload(message, *, contacts=None):
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "sender-123"},
                            "contacts": contacts or [],
                            "messages": [message],
                        }
                    }
                ]
            }
        ]
    }


def test_existing_text_extraction_is_unchanged():
    inbound = extract_whatsapp_message(
        meta_payload(
            {"from": "customer-123", "id": "message-123", "text": {"body": " Hi "}},
            contacts=[
                {"wa_id": "customer-123", "profile": {"name": "Customer"}}
            ],
        )
    )

    assert inbound is not None
    assert inbound.text == "Hi"
    assert inbound.customer_number == "customer-123"
    assert inbound.customer_name == "Customer"
    assert inbound.sender_id == "sender-123"
    assert inbound.message_id == "message-123"


def test_audio_extraction_preserves_confirmed_identity_fields():
    inbound = extract_whatsapp_audio_message(
        meta_payload(
            {
                "from": "customer-123",
                "id": "message-123",
                "audio": {
                    "id": "media-123",
                    "url": " https://media.example.test/file ",
                },
            },
            contacts=[
                {"wa_id": "customer-123", "profile": {"name": " Customer "}}
            ],
        )
    )

    assert inbound is not None
    assert inbound.customer_number == "customer-123"
    assert inbound.customer_name == "Customer"
    assert inbound.sender_id == "sender-123"
    assert inbound.message_id == "message-123"
    assert inbound.audio_id == "media-123"
    assert inbound.media_url == "https://media.example.test/file"


def test_audio_url_is_preferred_and_link_is_a_fallback():
    preferred = extract_whatsapp_audio_message(
        meta_payload(
            {
                "audio": {
                    "url": "https://media.example.test/url",
                    "link": "https://media.example.test/link",
                }
            }
        )
    )
    fallback = extract_whatsapp_audio_message(
        {"data": meta_payload({"audio": {"link": " https://media.example.test/link "}})}
    )

    assert preferred is not None
    assert preferred.media_url == "https://media.example.test/url"
    assert fallback is not None
    assert fallback.media_url == "https://media.example.test/link"


def test_audio_extractor_uses_contact_fallback_but_not_from_user_id():
    contact_fallback = extract_whatsapp_audio_message(
        meta_payload(
            {"from_user_id": "unconfirmed-id", "audio": {"url": "https://media.example.test/a"}},
            contacts=[{"wa_id": "contact-fallback", "profile": {"name": "Name"}}],
        )
    )
    no_contact = extract_whatsapp_audio_message(
        meta_payload(
            {"from_user_id": "unconfirmed-id", "audio": {"url": "https://media.example.test/a"}},
            contacts=[],
        )
    )

    assert contact_fallback is not None
    assert contact_fallback.customer_number == "contact-fallback"
    assert no_contact is not None
    assert no_contact.customer_number is None


def test_audio_extractor_rejects_malformed_incomplete_and_text_messages():
    assert extract_whatsapp_audio_message(meta_payload({"audio": "invalid"})) is None
    missing_id = extract_whatsapp_audio_message(
        meta_payload({"audio": {"url": "https://media.example.test/a"}})
    )
    oversized_id = extract_whatsapp_audio_message(
        meta_payload({"audio": {"id": "x" * 513}})
    )
    assert missing_id is not None and missing_id.audio_id is None
    assert oversized_id is not None and oversized_id.audio_id is None
    assert extract_whatsapp_audio_message(meta_payload({"text": {"body": "hello"}})) is None
    assert extract_whatsapp_audio_message({"entry": "invalid"}) is None
