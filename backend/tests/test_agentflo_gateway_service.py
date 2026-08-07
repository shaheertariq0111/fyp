from __future__ import annotations

import base64
import json
import logging
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

from src.services.agentflo_gateway_service import (
    AgentfloGatewayService,
    AgentfloMediaDownloadError,
    DocumentSendDisposition,
)


OGG_OPUS = b"OggS" + (b"\x00" * 24) + b"OpusHead" + (b"\x00" * 32)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._body


class FakeRawResponse(FakeResponse):
    def __init__(self, body, status=200):
        self.status = status
        self._body = body


class FakeMediaResponse:
    def __init__(self, body=OGG_OPUS, *, status=200, headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {"Content-Type": "audio/ogg"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]


def make_media_gateway(*, media_response=None, media_error=None, max_media_bytes=1024):
    auth_calls = []
    media_calls = []

    def open_auth(request, timeout):
        auth_calls.append((request, timeout))
        return FakeResponse({"success": True, "token": "synthetic-media-jwt"})

    def open_media(request, timeout):
        media_calls.append((request, timeout))
        if media_error is not None:
            raise media_error
        return media_response or FakeMediaResponse()

    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key="synthetic-api-key",
        tenant_id="fyp-dev",
        agent_id="restaurant-agent",
        actor_id="restaurant-agent",
        open_request=open_auth,
        open_media_request=open_media,
        timeout_seconds=7,
        max_media_bytes=max_media_bytes,
    )
    return service, auth_calls, media_calls


def test_gateway_defaults_preserve_text_transport_and_disable_media_redirects():
    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key="synthetic-api-key",
        tenant_id="fyp-dev",
        agent_id="restaurant-agent",
        actor_id="restaurant-agent",
    )

    assert service.open_request is urlopen
    assert service.open_media_request is not urlopen


def test_gateway_auth_and_outbound_requests_use_exact_contract():
    calls = []
    responses = iter([
        FakeResponse({"success": True, "token": "synthetic-jwt", "expiresIn": "30h"}),
        FakeResponse({
            "requestId": "gateway-request-synthetic-1",
            "status": "accepted",
            "product": "whatsapp",
            "routedService": "whatsapp",
            "conversationId": "whatsapp-synthetic-session",
            "tenantId": "fyp-dev",
            "downstream": {
                "httpStatus": 200,
                "accepted": True,
                "providerMessageId": "provider-synthetic-1",
            },
            "storage": {
                "uploaded": False,
                "artifacts": [],
            },
            "latencyMs": 312,
        }),
    ])

    def open_request(request, timeout):
        calls.append((request, timeout))
        return next(responses)

    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com/",
        api_key="synthetic-api-key",
        tenant_id="fyp-dev",
        agent_id="restaurant-agent",
        actor_id="",
        open_request=open_request,
        timeout_seconds=7,
    )

    result = service.send_text(
        customer_number="+10000000000",
        conversation_id="whatsapp-synthetic-session",
        sender_id="sender-synthetic-1",
        text="Synthetic agent reply.",
        request_id="req-synthetic-1",
    )

    assert result == {
        "sent": True,
        "status": "accepted",
        "providerMessageId": "provider-synthetic-1",
    }
    assert len(calls) == 2

    auth_request, auth_timeout = calls[0]
    assert auth_request.full_url == (
        "https://communicationgateway.agentflo.com/auth/token"
    )
    assert auth_request.method == "POST"
    assert json.loads(auth_request.data) == {"apiKey": "synthetic-api-key"}
    assert auth_timeout == 7

    outbound_request, outbound_timeout = calls[1]
    assert outbound_request.full_url == (
        "https://communicationgateway.agentflo.com/whatsapp/outbound"
    )
    assert outbound_request.method == "POST"
    assert outbound_request.get_header("Authorization") == (
        "Bearer synthetic-jwt"
    )
    assert outbound_request.get_header("Content-type") == "application/json"
    assert outbound_timeout == 7
    assert json.loads(outbound_request.data) == {
        "tenantId": "fyp-dev",
        "agentId": "restaurant-agent",
        "userId": "10000000000",
        "conversationId": "whatsapp-synthetic-session",
        "actorId": "restaurant-agent",
        "actorType": "agent",
        "recipient": {
            "type": "phone",
            "value": "10000000000",
        },
        "sender": {
            "phoneNumberId": "sender-synthetic-1",
        },
        "message": {
            "type": "text",
            "text": "Synthetic agent reply.",
        },
        "firestore": False,
        "kinesis": False,
    }


def test_gateway_audio_uses_exact_confirmed_contract_and_standard_base64():
    calls = []
    responses = iter([
        FakeResponse({"success": True, "token": "synthetic-jwt"}),
        FakeResponse({
            "status": "accepted",
            "downstream": {
                "accepted": True,
                "providerMessageId": "provider-audio-safe",
            },
        }),
    ])

    def open_request(request, timeout):
        calls.append((request, timeout))
        return next(responses)

    audio = b"OggS\x00OpusHead\xfb\xff"
    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key="synthetic-api-key",
        tenant_id="tenant-safe",
        agent_id="agent-safe",
        actor_id="actor-safe",
        open_request=open_request,
        max_audio_bytes=1024,
        audio_firestore=False,
        audio_kinesis=True,
    )
    result = service.send_audio(
        customer_number="+10000000000",
        conversation_id="conversation-safe",
        sender_id="phone-number-id-safe",
        audio=audio,
        request_id="request-safe",
    )

    assert result == {
        "sent": True,
        "status": "accepted",
        "providerMessageId": "provider-audio-safe",
    }
    payload = json.loads(calls[1][0].data)
    assert payload == {
        "tenantId": "tenant-safe",
        "agentId": "agent-safe",
        "userId": "10000000000",
        "conversationId": "conversation-safe",
        "actorId": "actor-safe",
        "actorType": "agent",
        "recipient": {"type": "phone", "value": "10000000000"},
        "sender": {"phoneNumberId": "phone-number-id-safe"},
        "source": "agent",
        "firestore": False,
        "kinesis": True,
        "message": {
            "type": "audio",
            "base64": base64.b64encode(audio).decode("ascii"),
        },
        "log": {},
    }


def test_gateway_audio_rejection_and_size_validation_are_sanitized(caplog):
    private_audio = b"private-audio"
    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key="private-key",
        tenant_id="tenant-safe",
        agent_id="agent-safe",
        actor_id="actor-safe",
        max_audio_bytes=4,
    )
    with caplog.at_level(logging.WARNING):
        result = service.send_audio(
            customer_number="+10000000000",
            conversation_id="conversation-safe",
            sender_id="sender-safe",
            audio=private_audio,
            request_id="request-safe",
        )
    assert result == {
        "sent": False,
        "error_code": "AGENTFLO_AUDIO_OUTBOUND_FAILED",
    }
    assert base64.b64encode(private_audio).decode("ascii") not in caplog.text
    assert private_audio.decode() not in caplog.text
    assert "+10000000000" not in caplog.text


def test_gateway_audio_http_accepted_and_rejected_responses_match_text_parser():
    for response, expected in (
        (
            {"downstream": {"accepted": True, "providerMessageId": "safe-id"}},
            {
                "sent": True,
                "status": "accepted",
                "providerMessageId": "safe-id",
            },
        ),
        (
            {"status": "rejected", "downstream": {"accepted": False}},
            {
                "sent": False,
                "error_code": "AGENTFLO_AUDIO_OUTBOUND_FAILED",
            },
        ),
    ):
        responses = iter([
            FakeResponse({"success": True, "token": "safe-token"}),
            FakeResponse(response),
        ])
        service = AgentfloGatewayService(
            base_url="https://communicationgateway.agentflo.com",
            api_key="safe-key",
            tenant_id="tenant-safe",
            agent_id="agent-safe",
            actor_id="actor-safe",
            open_request=lambda _request, timeout: next(responses),
            max_audio_bytes=1024,
        )
        assert service.send_audio(
            customer_number="10000000000",
            conversation_id="conversation-safe",
            sender_id="sender-safe",
            audio=OGG_OPUS,
            request_id="request-safe",
        ) == expected


def test_gateway_document_uses_exact_confirmed_contract_and_standard_base64():
    calls = []
    responses = iter([
        FakeResponse({"success": True, "token": "synthetic-jwt"}),
        FakeResponse({
            "status": "accepted",
            "downstream": {
                "accepted": True,
                "providerMessageId": "provider-document-safe",
            },
        }),
    ])

    def open_request(request, timeout):
        calls.append((request, timeout))
        return next(responses)

    document = b"%PDF-1.7\x00\xfb\xff"
    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key="synthetic-api-key",
        tenant_id="tenant-safe",
        agent_id="agent-safe",
        actor_id="actor-safe",
        open_request=open_request,
        max_media_bytes=1024,
    )
    result = service.send_document(
        customer_number="+10000000000",
        conversation_id="conversation-safe",
        sender_id="phone-number-id-safe",
        document=document,
        filename="  Order-Receipt-ORD-123.pdf  ",
        caption="  Order receipt - ORD-123  ",
        request_id="request-safe",
    )

    assert result == {
        "sent": True,
        "status": "accepted",
        "providerMessageId": "provider-document-safe",
    }
    assert len(calls) == 2
    outbound_request, outbound_timeout = calls[1]
    assert outbound_request.full_url.endswith("/whatsapp/outbound")
    assert outbound_request.get_header("Authorization") == "Bearer synthetic-jwt"
    assert outbound_timeout == 10
    assert json.loads(outbound_request.data) == {
        "tenantId": "tenant-safe",
        "agentId": "agent-safe",
        "userId": "10000000000",
        "conversationId": "conversation-safe",
        "actorId": "actor-safe",
        "actorType": "agent",
        "recipient": {"type": "phone", "value": "10000000000"},
        "sender": {"phoneNumberId": "phone-number-id-safe"},
        "source": "agent",
        "firestore": False,
        "kinesis": False,
        "message": {
            "type": "document",
            "base64": base64.b64encode(document).decode("ascii"),
            "filename": "Order-Receipt-ORD-123.pdf",
            "caption": "Order receipt - ORD-123",
        },
    }
    assert not json.loads(outbound_request.data)["message"]["base64"].startswith(
        "data:"
    )


def test_gateway_document_omits_blank_caption():
    calls = []
    responses = iter([
        FakeResponse({"success": True, "token": "safe-token"}),
        FakeResponse({"status": "accepted"}),
    ])

    def open_request(request, timeout):
        calls.append(request)
        return next(responses)

    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key="safe-key",
        tenant_id="tenant-safe",
        agent_id="agent-safe",
        actor_id="actor-safe",
        open_request=open_request,
    )

    assert service.send_document(
        customer_number="10000000000",
        conversation_id="conversation-safe",
        sender_id="sender-safe",
        document=b"safe-document",
        filename="receipt.pdf",
        caption="  \t ",
        request_id="request-safe",
    ) == {"sent": True, "status": "accepted"}
    assert "caption" not in json.loads(calls[1].data)["message"]


@pytest.mark.parametrize(
    ("document", "filename", "caption", "max_media_bytes"),
    [
        (b"", "receipt.pdf", None, 1024),
        (b"oversized", "receipt.pdf", None, 4),
        (b"safe", "", None, 1024),
        (b"safe", "  ", None, 1024),
        (b"safe", None, None, 1024),
        (b"safe", "receipt.pdf", 123, 1024),
    ],
)
def test_gateway_document_rejects_invalid_content_filename_and_caption(
    document,
    filename,
    caption,
    max_media_bytes,
):
    calls = []
    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key="safe-key",
        tenant_id="tenant-safe",
        agent_id="agent-safe",
        actor_id="actor-safe",
        open_request=lambda request, timeout: calls.append(request),
        max_media_bytes=max_media_bytes,
    )

    assert service.send_document(
        customer_number="10000000000",
        conversation_id="conversation-safe",
        sender_id="sender-safe",
        document=document,
        filename=filename,
        caption=caption,
        request_id="request-safe",
    ) == {
        "sent": False,
        "error_code": "AGENTFLO_DOCUMENT_OUTBOUND_FAILED",
    }
    assert calls == []


def test_gateway_document_rejection_and_failure_logs_are_sanitized(caplog):
    api_key = "private-document-api-key"
    token = "private-document-jwt"
    phone = "+19999999999"
    caption = "Private document caption"
    document = b"private-pdf-document-content"
    encoded = base64.b64encode(document).decode("ascii")
    responses = iter([
        FakeResponse({"success": True, "token": token}),
        FakeResponse({"status": "rejected", "downstream": {"accepted": False}}),
    ])
    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key=api_key,
        tenant_id="tenant-safe",
        agent_id="agent-safe",
        actor_id="actor-safe",
        open_request=lambda request, timeout: next(responses),
        max_media_bytes=1024,
    )

    with caplog.at_level(logging.WARNING):
        result = service.send_document(
            customer_number=phone,
            conversation_id="conversation-safe",
            sender_id="sender-safe",
            document=document,
            filename="receipt.pdf",
            caption=caption,
            request_id="request-safe",
        )

    assert result == {
        "sent": False,
        "error_code": "AGENTFLO_DOCUMENT_OUTBOUND_FAILED",
    }
    assert document.decode() not in caplog.text
    assert encoded not in caplog.text
    assert phone not in caplog.text
    assert caption not in caplog.text
    assert api_key not in caplog.text
    assert token not in caplog.text


def make_classified_document_gateway(*responses, max_media_bytes=1024):
    calls = []
    remaining = iter(responses)

    def open_request(request, timeout):
        calls.append((request, timeout))
        response = next(remaining)
        if isinstance(response, BaseException):
            raise response
        return response

    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key="private-api-key",
        tenant_id="tenant-safe",
        agent_id="agent-safe",
        actor_id="actor-safe",
        open_request=open_request,
        max_media_bytes=max_media_bytes,
    )
    return service, calls


def send_classified_document(service, **overrides):
    values = {
        "customer_number": "+19999999999",
        "conversation_id": "conversation-private",
        "sender_id": "sender-private",
        "document": b"%PDF-private-document",
        "filename": "  Order-Receipt-ORD-123.pdf  ",
        "caption": "  Private receipt caption  ",
        "request_id": "request-safe",
    }
    values.update(overrides)
    return service.send_document_classified(**values)


@pytest.mark.parametrize(
    ("outbound", "provider_message_id"),
    [
        (
            {
                "status": "accepted",
                "downstream": {
                    "accepted": True,
                    "providerMessageId": "provider-safe",
                },
            },
            "provider-safe",
        ),
        ({"status": "accepted"}, None),
        ({"downstream": {"accepted": True}}, None),
    ],
)
def test_classified_document_acceptance_is_definite_sent(
    outbound,
    provider_message_id,
):
    service, calls = make_classified_document_gateway(
        FakeResponse({"success": True, "token": "private-token"}),
        FakeResponse(outbound),
    )

    result = send_classified_document(service)

    assert result.disposition is DocumentSendDisposition.SENT
    assert result.sent is True
    assert result.retryable is False
    assert result.provider_message_id == provider_message_id
    assert result.stage is None
    assert result.status_code is None
    assert result.error_code is None
    assert len(calls) == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"document": b""},
        {"document": b"oversized"},
        {"filename": "  "},
        {"caption": 123},
        {"customer_number": ""},
        {"conversation_id": ""},
        {"sender_id": ""},
    ],
)
def test_classified_document_local_validation_is_permanent_without_http(
    overrides,
):
    max_media_bytes = 4 if overrides.get("document") == b"oversized" else 1024
    service, calls = make_classified_document_gateway(
        max_media_bytes=max_media_bytes,
    )

    result = send_classified_document(service, **overrides)

    assert result.disposition is DocumentSendDisposition.PERMANENT_FAILURE
    assert result.stage == "document_validation"
    assert result.error_code == "AGENTFLO_DOCUMENT_OUTBOUND_FAILED"
    assert calls == []


def test_classified_document_not_configured_is_permanent_without_http():
    calls = []
    service = AgentfloGatewayService(
        base_url="",
        api_key="",
        tenant_id="tenant-safe",
        agent_id="agent-safe",
        actor_id="actor-safe",
        open_request=lambda request, timeout: calls.append(request),
    )

    result = send_classified_document(service)

    assert result.disposition is DocumentSendDisposition.PERMANENT_FAILURE
    assert result.stage == "configuration"
    assert result.error_code == "AGENTFLO_DOCUMENT_OUTBOUND_FAILED"
    assert calls == []


def gateway_http_error(status):
    return HTTPError(
        "https://private.example.test",
        status,
        "private gateway response message",
        {},
        BytesIO(b'{"private":"response body"}'),
    )


@pytest.mark.parametrize(
    ("status", "disposition"),
    [
        (400, DocumentSendDisposition.PERMANENT_FAILURE),
        (401, DocumentSendDisposition.PERMANENT_FAILURE),
        (403, DocumentSendDisposition.PERMANENT_FAILURE),
        (404, DocumentSendDisposition.PERMANENT_FAILURE),
        (422, DocumentSendDisposition.PERMANENT_FAILURE),
        (429, DocumentSendDisposition.RETRYABLE_FAILURE),
        (500, DocumentSendDisposition.RETRYABLE_FAILURE),
        (503, DocumentSendDisposition.RETRYABLE_FAILURE),
    ],
)
def test_classified_document_auth_http_failure_matrix(status, disposition):
    service, calls = make_classified_document_gateway(gateway_http_error(status))

    result = send_classified_document(service)

    assert result.disposition is disposition
    assert result.retryable is (
        disposition is DocumentSendDisposition.RETRYABLE_FAILURE
    )
    assert result.stage == "auth"
    assert result.status_code == status
    assert result.error_code == "AGENTFLO_DOCUMENT_OUTBOUND_FAILED"
    assert len(calls) == 1
    assert calls[0][0].full_url.endswith("/auth/token")


@pytest.mark.parametrize(
    "network_error",
    [
        URLError("private auth network message"),
        TimeoutError("private auth timeout message"),
        OSError("private auth operating system message"),
    ],
)
def test_classified_document_auth_network_failure_is_retryable(network_error):
    service, calls = make_classified_document_gateway(network_error)

    result = send_classified_document(service)

    assert result.disposition is DocumentSendDisposition.RETRYABLE_FAILURE
    assert result.stage == "auth"
    assert result.status_code is None
    assert len(calls) == 1
    assert str(network_error) not in repr(result)


@pytest.mark.parametrize(
    "auth_response",
    [
        FakeRawResponse(b"not-json"),
        FakeResponse([]),
        FakeResponse({"success": False}),
        FakeResponse({"success": True, "token": "  "}),
    ],
)
def test_classified_document_malformed_auth_2xx_is_retryable_without_outbound(
    auth_response,
):
    service, calls = make_classified_document_gateway(auth_response)

    result = send_classified_document(service)

    assert result.disposition is DocumentSendDisposition.RETRYABLE_FAILURE
    assert result.stage == "auth_response"
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("status", "disposition"),
    [
        (400, DocumentSendDisposition.PERMANENT_FAILURE),
        (401, DocumentSendDisposition.PERMANENT_FAILURE),
        (403, DocumentSendDisposition.PERMANENT_FAILURE),
        (404, DocumentSendDisposition.PERMANENT_FAILURE),
        (422, DocumentSendDisposition.PERMANENT_FAILURE),
        (429, DocumentSendDisposition.RETRYABLE_FAILURE),
        (408, DocumentSendDisposition.MANUAL_REVIEW),
        (500, DocumentSendDisposition.MANUAL_REVIEW),
        (502, DocumentSendDisposition.MANUAL_REVIEW),
        (503, DocumentSendDisposition.MANUAL_REVIEW),
        (504, DocumentSendDisposition.MANUAL_REVIEW),
    ],
)
def test_classified_document_outbound_http_failure_matrix(status, disposition):
    service, calls = make_classified_document_gateway(
        FakeResponse({"success": True, "token": "private-token"}),
        gateway_http_error(status),
    )

    result = send_classified_document(service)

    assert result.disposition is disposition
    assert result.stage == "outbound"
    assert result.status_code == status
    assert len(calls) == 2


@pytest.mark.parametrize(
    "network_error",
    [
        URLError("private outbound network message"),
        TimeoutError("private outbound timeout message"),
        OSError("private outbound operating system message"),
    ],
)
def test_classified_document_outbound_network_failure_requires_manual_review(
    network_error,
):
    service, calls = make_classified_document_gateway(
        FakeResponse({"success": True, "token": "private-token"}),
        network_error,
    )

    result = send_classified_document(service)

    assert result.disposition is DocumentSendDisposition.MANUAL_REVIEW
    assert result.stage == "outbound"
    assert result.status_code is None
    assert len(calls) == 2
    assert str(network_error) not in repr(result)


@pytest.mark.parametrize(
    "outbound",
    [
        {"success": False},
        {"status": "rejected"},
        {"status": "failed"},
        {"downstream": {"accepted": False}},
        {
            "success": False,
            "status": "accepted",
            "downstream": {"accepted": True},
        },
    ],
)
def test_classified_document_explicit_2xx_rejection_is_permanent(outbound):
    service, _ = make_classified_document_gateway(
        FakeResponse({"success": True, "token": "private-token"}),
        FakeResponse(outbound),
    )

    result = send_classified_document(service)

    assert result.disposition is DocumentSendDisposition.PERMANENT_FAILURE
    assert result.stage == "outbound_response"


@pytest.mark.parametrize(
    "outbound_response",
    [
        FakeRawResponse(b"not-json"),
        FakeRawResponse(b""),
        FakeResponse([]),
        FakeResponse({}),
        FakeResponse({"status": "unknown"}),
        FakeResponse({"success": True}),
    ],
)
def test_classified_document_ambiguous_2xx_requires_manual_review(
    outbound_response,
):
    service, _ = make_classified_document_gateway(
        FakeResponse({"success": True, "token": "private-token"}),
        outbound_response,
    )

    result = send_classified_document(service)

    assert result.disposition is DocumentSendDisposition.MANUAL_REVIEW
    assert result.stage == "outbound_response"


@pytest.mark.parametrize("caption", [None, "  "])
def test_classified_document_uses_exact_private_safe_contract(caption):
    service, calls = make_classified_document_gateway(
        FakeResponse({"success": True, "token": "private-token"}),
        FakeResponse({"status": "accepted"}),
    )
    document = b"%PDF-contract-safe\xfb\xff"

    result = send_classified_document(
        service,
        document=document,
        caption=caption,
    )

    assert result.disposition is DocumentSendDisposition.SENT
    payload = json.loads(calls[1][0].data)
    assert payload == {
        "tenantId": "tenant-safe",
        "agentId": "agent-safe",
        "userId": "19999999999",
        "conversationId": "conversation-private",
        "actorId": "actor-safe",
        "actorType": "agent",
        "recipient": {"type": "phone", "value": "19999999999"},
        "sender": {"phoneNumberId": "sender-private"},
        "source": "agent",
        "firestore": False,
        "kinesis": False,
        "message": {
            "type": "document",
            "base64": base64.b64encode(document).decode("ascii"),
            "filename": "Order-Receipt-ORD-123.pdf",
        },
    }
    result_text = repr(result)
    for private_value in (
        document.decode("latin-1"),
        base64.b64encode(document).decode("ascii"),
        "+19999999999",
        "19999999999",
        "sender-private",
        "conversation-private",
        "Order-Receipt-ORD-123.pdf",
        "Private receipt caption",
        "private-token",
        "private-api-key",
    ):
        assert private_value not in result_text


def test_gateway_auth_failure_is_safe_and_does_not_call_outbound():
    calls = []

    def open_request(request, timeout):
        calls.append(request.full_url)
        raise HTTPError(
            request.full_url,
            401,
            "private auth failure",
            {},
            BytesIO(b'{"private":"body"}'),
        )

    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key="synthetic-api-key",
        tenant_id="fyp-dev",
        agent_id="restaurant-agent",
        actor_id="restaurant-actor",
        open_request=open_request,
    )

    result = service.send_text(
        customer_number="10000000000",
        conversation_id="whatsapp-synthetic-session",
        sender_id="sender-synthetic-1",
        text="Private synthetic message",
        request_id="req-synthetic-2",
    )

    assert result == {
        "sent": False,
        "error_code": "AGENTFLO_OUTBOUND_FAILED",
    }
    assert calls == [
        "https://communicationgateway.agentflo.com/auth/token",
    ]


def test_gateway_outbound_failure_is_safe():
    responses = iter([
        FakeResponse({"success": True, "token": "synthetic-jwt"}),
        FakeResponse({"success": False, "private": "failure"}, status=503),
    ])

    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key="synthetic-api-key",
        tenant_id="tenant-synthetic",
        agent_id="agent-synthetic",
        actor_id="actor-synthetic",
        open_request=lambda request, timeout: next(responses),
    )

    result = service.send_text(
        customer_number="10000000000",
        conversation_id="whatsapp-synthetic-session",
        sender_id="sender-synthetic-1",
        text="Synthetic agent reply.",
        request_id="req-synthetic-3",
    )

    assert result == {
        "sent": False,
        "error_code": "AGENTFLO_OUTBOUND_FAILED",
    }


def _send_with_outbound_response(outbound_response):
    responses = iter([
        FakeResponse({"success": True, "token": "synthetic-jwt"}),
        FakeResponse(outbound_response),
    ])
    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key="synthetic-api-key",
        tenant_id="tenant-synthetic",
        agent_id="agent-synthetic",
        actor_id="actor-synthetic",
        open_request=lambda request, timeout: next(responses),
    )
    return service.send_text(
        customer_number="10000000000",
        conversation_id="whatsapp-synthetic-session",
        sender_id="sender-synthetic-1",
        text="Synthetic agent reply.",
        request_id="req-synthetic-response",
    )


def test_gateway_http_200_rejected_status_is_failure():
    assert _send_with_outbound_response({
        "status": "rejected",
        "downstream": {
            "accepted": True,
            "providerMessageId": "provider-synthetic-rejected",
        },
    }) == {
        "sent": False,
        "error_code": "AGENTFLO_OUTBOUND_FAILED",
    }


def test_gateway_http_200_downstream_rejection_is_failure():
    assert _send_with_outbound_response({
        "status": "accepted",
        "downstream": {
            "accepted": False,
            "providerMessageId": "provider-synthetic-rejected",
        },
    }) == {
        "sent": False,
        "error_code": "AGENTFLO_OUTBOUND_FAILED",
    }


def test_gateway_other_explicit_failure_shapes_are_rejected():
    for outbound_response in (
        {"status": "failed", "downstream": {"accepted": True}},
        {
            "success": False,
            "status": "accepted",
            "downstream": {"accepted": True},
        },
        {"requestId": "gateway-request-without-acceptance"},
    ):
        assert _send_with_outbound_response(outbound_response) == {
            "sent": False,
            "error_code": "AGENTFLO_OUTBOUND_FAILED",
        }


def test_gateway_downstream_acceptance_without_status_defaults_to_accepted():
    assert _send_with_outbound_response({
        "downstream": {
            "accepted": True,
            "providerMessageId": "provider-synthetic-accepted",
        },
    }) == {
        "sent": True,
        "status": "accepted",
        "providerMessageId": "provider-synthetic-accepted",
    }


def test_gateway_failure_logs_exclude_sensitive_values(caplog):
    api_key = "private-synthetic-api-key"
    token = "private-synthetic-bearer-token"
    message = "Private synthetic customer message"
    phone = "+10000000000"
    responses = iter([
        FakeResponse({"success": True, "token": token}),
        FakeResponse({"success": False}, status=500),
    ])
    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key=api_key,
        tenant_id="tenant-synthetic",
        agent_id="agent-synthetic",
        actor_id="actor-synthetic",
        open_request=lambda request, timeout: next(responses),
    )

    with caplog.at_level(logging.INFO):
        service.send_text(
            customer_number=phone,
            conversation_id="whatsapp-synthetic-session",
            sender_id="sender-synthetic-1",
            text=message,
            request_id="req-synthetic-4",
        )

    assert api_key not in caplog.text
    assert token not in caplog.text
    assert message not in caplog.text
    assert phone not in caplog.text


def test_authenticated_media_download_uses_encoded_audio_id_and_safe_headers():
    service, auth_calls, media_calls = make_media_gateway()

    media = service.download_media("audio/id with spaces", request_id="wv1_safe")

    try:
        assert media.file.read() == OGG_OPUS
        assert media.media_format == "ogg"
        assert media.suffix == ".ogg"
        assert media.content_type == "audio/ogg"
        assert media.size_bytes == len(OGG_OPUS)
    finally:
        media.close()

    assert len(auth_calls) == 1
    auth_request, auth_timeout = auth_calls[0]
    assert auth_request.full_url.endswith("/auth/token")
    assert json.loads(auth_request.data) == {"apiKey": "synthetic-api-key"}
    assert auth_timeout == 7

    assert len(media_calls) == 1
    media_request, media_timeout = media_calls[0]
    assert media_request.full_url == (
        "https://communicationgateway.agentflo.com/whatsapp/media/"
        "audio%2Fid%20with%20spaces"
    )
    assert media_request.method == "GET"
    assert media_request.get_header("Authorization") == "Bearer synthetic-media-jwt"
    assert media_request.get_header("Accept") == "audio/*,application/octet-stream"
    assert media_timeout == 7


def test_media_download_rejects_invalid_auth_response_without_get():
    media_calls = []
    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key="synthetic-api-key",
        tenant_id="fyp-dev",
        agent_id="restaurant-agent",
        actor_id="restaurant-agent",
        open_request=lambda _request, timeout: FakeResponse({"success": False}),
        open_media_request=lambda request, timeout: media_calls.append((request, timeout)),
    )

    with pytest.raises(AgentfloMediaDownloadError) as error:
        service.download_media("audio-id", request_id="wv1_safe")

    assert error.value.error_code == "AGENTFLO_MEDIA_AUTH_FAILED"
    assert error.value.retryable is False
    assert media_calls == []


@pytest.mark.parametrize(
    ("status", "error_code", "retryable"),
    [
        (401, "AGENTFLO_MEDIA_AUTH_FAILED", False),
        (403, "AGENTFLO_MEDIA_AUTH_FAILED", False),
        (404, "AGENTFLO_MEDIA_NOT_FOUND", False),
        (429, "AGENTFLO_MEDIA_DOWNLOAD_FAILED", True),
        (500, "AGENTFLO_MEDIA_DOWNLOAD_FAILED", True),
        (503, "AGENTFLO_MEDIA_DOWNLOAD_FAILED", True),
    ],
)
def test_media_download_maps_http_statuses(status, error_code, retryable):
    service, _, _ = make_media_gateway(
        media_response=FakeMediaResponse(status=status)
    )

    with pytest.raises(AgentfloMediaDownloadError) as error:
        service.download_media("audio-id", request_id="wv1_safe")

    assert error.value.error_code == error_code
    assert error.value.retryable is retryable


def test_media_download_rejects_redirect_without_following_it():
    service, _, media_calls = make_media_gateway(
        media_response=FakeMediaResponse(
            status=302,
            headers={"Location": "https://private.example/redirect"},
        )
    )

    with pytest.raises(AgentfloMediaDownloadError) as error:
        service.download_media("audio-id", request_id="wv1_safe")

    assert error.value.error_code == "AGENTFLO_MEDIA_REDIRECT_REJECTED"
    assert error.value.retryable is False
    assert len(media_calls) == 1


@pytest.mark.parametrize(
    ("response", "error_code"),
    [
        (FakeMediaResponse(body=b""), "AGENTFLO_MEDIA_EMPTY"),
        (
            FakeMediaResponse(headers={"Content-Type": "text/html"}),
            "AGENTFLO_MEDIA_FORMAT_UNSUPPORTED",
        ),
        (
            FakeMediaResponse(
                body=b"not-ogg",
                headers={"Content-Type": "audio/ogg"},
            ),
            "AGENTFLO_MEDIA_FORMAT_UNSUPPORTED",
        ),
        (
            FakeMediaResponse(headers={"Content-Type": "audio/ogg", "Content-Length": "1025"}),
            "AGENTFLO_MEDIA_TOO_LARGE",
        ),
        (
            FakeMediaResponse(body=b"x" * 1025),
            "AGENTFLO_MEDIA_TOO_LARGE",
        ),
    ],
)
def test_media_download_validates_response_body(response, error_code):
    service, _, _ = make_media_gateway(
        media_response=response,
        max_media_bytes=1024,
    )

    with pytest.raises(AgentfloMediaDownloadError) as error:
        service.download_media("audio-id", request_id="wv1_safe")

    assert error.value.error_code == error_code
    assert error.value.retryable is False


@pytest.mark.parametrize("network_error", [TimeoutError("private"), URLError("private")])
def test_media_download_maps_timeout_and_network_failures(network_error):
    service, _, _ = make_media_gateway(media_error=network_error)

    with pytest.raises(AgentfloMediaDownloadError) as error:
        service.download_media("audio-id", request_id="wv1_safe")

    assert error.value.error_code == "AGENTFLO_MEDIA_DOWNLOAD_FAILED"
    assert error.value.retryable is True


def test_media_failure_logs_exclude_credentials_ids_urls_and_bodies(caplog):
    api_key = "private-api-key"
    token = "private-jwt"
    audio_id = "private-audio-id"
    response_body = b"private-response-body"
    service = AgentfloGatewayService(
        base_url="https://communicationgateway.agentflo.com",
        api_key=api_key,
        tenant_id="fyp-dev",
        agent_id="restaurant-agent",
        actor_id="restaurant-agent",
        open_request=lambda _request, timeout: FakeResponse(
            {"success": True, "token": token}
        ),
        open_media_request=lambda _request, timeout: FakeMediaResponse(
            body=response_body,
            status=500,
        ),
    )

    with caplog.at_level(logging.WARNING), pytest.raises(AgentfloMediaDownloadError):
        service.download_media(audio_id, request_id="wv1_safe")

    assert api_key not in caplog.text
    assert token not in caplog.text
    assert audio_id not in caplog.text
    assert response_body.decode() not in caplog.text
    assert "/whatsapp/media/" not in caplog.text
    assert "Authorization" not in caplog.text
