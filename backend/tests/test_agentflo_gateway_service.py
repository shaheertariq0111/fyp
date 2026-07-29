from __future__ import annotations

import json
import logging
from io import BytesIO
from urllib.error import HTTPError

from src.services.agentflo_gateway_service import AgentfloGatewayService


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
