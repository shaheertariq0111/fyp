from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.models.whatsapp_voice_job import validate_voice_audio_id


@dataclass(frozen=True)
class WhatsAppInboundMessage:
    text: str
    customer_number: str | None = None
    customer_name: str | None = None
    sender_id: str | None = None
    message_id: str | None = None


@dataclass(frozen=True)
class WhatsAppInboundAudioMessage:
    customer_number: str | None
    customer_name: str | None
    sender_id: str | None
    message_id: str | None
    audio_id: str | None
    media_url: str | None


def extract_whatsapp_message(
    payload: dict[str, Any],
) -> WhatsAppInboundMessage | None:
    meta_message = _extract_meta_message(payload)
    if meta_message is not None:
        return meta_message

    candidates = [payload]
    for key in ("data", "payload", "event"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        simple_message = _extract_simple_message(candidate)
        if simple_message is not None:
            return simple_message
    return None


def extract_whatsapp_audio_message(
    payload: dict[str, Any],
) -> WhatsAppInboundAudioMessage | None:
    if not isinstance(payload, dict):
        return None
    candidates = [payload]
    for key in ("data", "payload", "event"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        audio_message = _extract_meta_audio_message(candidate)
        if audio_message is not None:
            return audio_message
    return None


def _extract_meta_audio_message(
    payload: dict[str, Any],
) -> WhatsAppInboundAudioMessage | None:
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata")
            sender_id = (
                _clean_string(metadata.get("phone_number_id"))
                if isinstance(metadata, dict)
                else None
            )
            contacts = value.get("contacts")
            contact_items = (
                [item for item in contacts if isinstance(item, dict)]
                if isinstance(contacts, list)
                else []
            )
            messages = value.get("messages")
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict):
                    continue
                audio = message.get("audio")
                if not isinstance(audio, dict):
                    continue
                media_url = _first_string(audio, ("url", "link"))
                audio_id = _validated_audio_id(audio.get("id"))
                customer_number = _first_string(message, ("from", "wa_id"))
                contact = _matching_contact(contact_items, customer_number)
                if customer_number is None and contact is not None:
                    customer_number = _clean_string(contact.get("wa_id"))
                return WhatsAppInboundAudioMessage(
                    customer_number=customer_number,
                    customer_name=_contact_name(contact),
                    sender_id=sender_id,
                    message_id=_first_string(message, ("id", "message_id")),
                    audio_id=audio_id,
                    media_url=media_url,
                )
    return None


def _extract_meta_message(
    payload: dict[str, Any],
) -> WhatsAppInboundMessage | None:
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata")
            sender_id = (
                _clean_string(metadata.get("phone_number_id"))
                if isinstance(metadata, dict)
                else None
            )
            contacts = value.get("contacts")
            contact_items = (
                [item for item in contacts if isinstance(item, dict)]
                if isinstance(contacts, list)
                else []
            )
            messages = value.get("messages")
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict):
                    continue
                text = _message_text(message.get("text"))
                if text is None:
                    continue
                customer_number = _first_string(message, ("from", "wa_id"))
                contact = _matching_contact(contact_items, customer_number)
                if customer_number is None and contact is not None:
                    customer_number = _clean_string(contact.get("wa_id"))
                return WhatsAppInboundMessage(
                    text=text,
                    customer_number=customer_number,
                    customer_name=_contact_name(contact),
                    sender_id=sender_id,
                    message_id=_first_string(message, ("id", "message_id")),
                )
    return None


def _extract_simple_message(
    payload: dict[str, Any],
) -> WhatsAppInboundMessage | None:
    text = None
    for key in ("message", "text", "body", "content"):
        text = _message_text(payload.get(key))
        if text is not None:
            break
    if text is None:
        return None
    return WhatsAppInboundMessage(
        text=text,
        customer_number=_first_string(
            payload,
            ("from", "user_number", "phone", "phone_number", "wa_id"),
        ),
        customer_name=_first_string(
            payload,
            ("name", "customer_name", "profile_name"),
        ),
        sender_id=_first_string(
            payload,
            ("sender_id", "phone_number_id"),
        ),
        message_id=_first_string(payload, ("message_id", "id")),
    )


def _message_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None
    for key in ("body", "text", "content", "message"):
        nested = value.get(key)
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


def _matching_contact(
    contacts: list[dict[str, Any]],
    customer_number: str | None,
) -> dict[str, Any] | None:
    if customer_number is not None:
        for contact in contacts:
            if _clean_string(contact.get("wa_id")) == customer_number:
                return contact
    return contacts[0] if len(contacts) == 1 else None


def _contact_name(contact: dict[str, Any] | None) -> str | None:
    if not isinstance(contact, dict):
        return None
    profile = contact.get("profile")
    if isinstance(profile, dict):
        return _clean_string(profile.get("name"))
    return _first_string(contact, ("name", "profile_name"))


def _first_string(
    value: dict[str, Any],
    keys: tuple[str, ...],
) -> str | None:
    for key in keys:
        candidate = _clean_string(value.get(key))
        if candidate is not None:
            return candidate
    return None


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _validated_audio_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return validate_voice_audio_id(value)
    except ValueError:
        return None
