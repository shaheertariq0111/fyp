from __future__ import annotations

import logging
from types import SimpleNamespace

from src.services.polly_speech_service import PollySynthesisError
from src.services.whatsapp_voice_reply_service import WhatsAppVoiceReplyService


class Synthesizer:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def synthesize(self, text):
        self.calls.append(text)
        if self.error:
            raise self.error
        return b"mp3"


class Converter:
    def __init__(self):
        self.calls = []

    def convert(self, audio):
        self.calls.append(audio)
        return b"ogg-audio"


class Gateway:
    def __init__(self, result=None, error=None):
        self.result = result or {
            "sent": True,
            "status": "accepted",
            "providerMessageId": "provider-safe",
        }
        self.error = error
        self.calls = []

    def send_audio(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def deliver(service):
    return service.deliver(
        text="private final reply",
        customer_number="+10000000000",
        conversation_id="session-safe",
        sender_id="sender-safe",
        request_id="request-safe",
        voice_job_id="job-safe",
    )


def test_voice_reply_synthesizes_converts_and_sends_exactly_once(caplog):
    synth, converter, gateway = Synthesizer(), Converter(), Gateway()
    service = WhatsAppVoiceReplyService(
        synthesizer=synth,
        converter=converter,
        gateway_provider=lambda: gateway,
    )
    with caplog.at_level(logging.INFO):
        result = deliver(service)

    assert result.status == "sent"
    assert result.provider_message_id == "provider-safe"
    assert result.generated_audio_bytes == len(b"ogg-audio")
    assert synth.calls == ["private final reply"]
    assert converter.calls == [b"mp3"]
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["audio"] == b"ogg-audio"
    assert "private final reply" not in caplog.text
    assert "+10000000000" not in caplog.text
    assert {getattr(record, "event", None) for record in caplog.records} >= {
        "voice_reply_synthesis_started",
        "voice_reply_synthesis_completed",
        "voice_reply_conversion_completed",
        "voice_reply_outbound_completed",
    }


def test_synthesis_failure_is_nonfatal_and_stops_before_conversion_or_send():
    synth = Synthesizer(PollySynthesisError(
        "VOICE_REPLY_POLLY_REQUEST_FAILED",
        retryable=True,
        stage="synthesis",
    ))
    converter, gateway = Converter(), Gateway()
    result = deliver(WhatsAppVoiceReplyService(
        synthesizer=synth,
        converter=converter,
        gateway_provider=lambda: gateway,
    ))
    assert result.status == "failed"
    assert result.error_code == "VOICE_REPLY_POLLY_REQUEST_FAILED"
    assert converter.calls == []
    assert gateway.calls == []


def test_ambiguous_audio_delivery_is_not_retried():
    gateway = Gateway(error=RuntimeError("private ambiguous detail"))
    result = deliver(WhatsAppVoiceReplyService(
        synthesizer=Synthesizer(),
        converter=Converter(),
        gateway_provider=lambda: gateway,
    ))
    assert result.status == "ambiguous"
    assert result.error_code == "VOICE_REPLY_OUTCOME_AMBIGUOUS"
    assert len(gateway.calls) == 1
