import asyncio
import io
import json
from types import SimpleNamespace

import pytest

from src.agent_client.agentcore import AgentCoreRuntimeClient
from src.agent.order_intent import OrderIntentClassification, OrderIntentRequest
from src.agent.whatsapp_turn_intent import (
    WhatsAppTurnIntentRequest,
    WhatsAppTurnInterpretation,
)
from src.agent_client.factory import get_agent_runtime_client
from src.agent_client.local import LocalStrandsAgentRuntimeClient
from src.agent_client.schemas import AgentInvocationRequest


def test_agent_invocation_request_supports_optional_request_id():
    request = AgentInvocationRequest(
        message="hello",
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-trusted",
    )
    legacy_request = AgentInvocationRequest(
        message="hello",
        user_id="user-1",
        agent_session_id="session-1",
    )

    assert request.request_id == "req-trusted"
    assert legacy_request.request_id is None


def test_local_agent_runtime_client_invokes_existing_strands_agent(monkeypatch):
    captured = {}

    def fake_invoke_restaurant_agent(message, **kwargs):
        captured.update({"message": message, **kwargs})
        return {"agent": "result"}

    monkeypatch.setattr(
        "src.agent_client.local.invoke_restaurant_agent",
        fake_invoke_restaurant_agent,
    )
    monkeypatch.setattr("src.agent_client.local.agent_result_text", lambda result: "Agent response")

    result = LocalStrandsAgentRuntimeClient().invoke(
        AgentInvocationRequest(
            message="hello",
            user_id="user-1",
            agent_session_id="session-1",
            branch_id="branch-1",
            customer_id="customer-1",
            customer_name="Ava",
            customer_phone="+923001234567",
            channel="web",
            request_id="req-trusted",
        )
    )

    assert result.text == "Agent response"
    assert result.raw_result == {"agent": "result"}
    assert captured == {
        "message": "hello",
        "user_id": "user-1",
        "agent_session_id": "session-1",
        "branch_id": "branch-1",
        "customer_id": "customer-1",
        "customer_name": "Ava",
        "customer_phone": "+923001234567",
        "channel": "web",
        "request_id": "req-trusted",
    }


def test_local_agent_runtime_client_preserves_missing_request_id(monkeypatch):
    captured = {}

    def fake_invoke_restaurant_agent(message, **kwargs):
        captured.update({"message": message, **kwargs})
        return {"agent": "result"}

    monkeypatch.setattr(
        "src.agent_client.local.invoke_restaurant_agent",
        fake_invoke_restaurant_agent,
    )
    monkeypatch.setattr(
        "src.agent_client.local.agent_result_text",
        lambda result: "Agent response",
    )

    LocalStrandsAgentRuntimeClient().invoke(
        AgentInvocationRequest(
            message="hello",
            user_id="user-1",
            agent_session_id="session-1",
        )
    )

    assert captured["request_id"] is None


def test_local_agent_runtime_client_async_methods_are_agentcore_boundary():
    client = LocalStrandsAgentRuntimeClient()
    request = AgentInvocationRequest(
        message="hello",
        user_id="user-1",
        agent_session_id="session-1",
    )

    with pytest.raises(NotImplementedError, match="AgentCore"):
        asyncio.run(client.start_request(request))

    with pytest.raises(NotImplementedError, match="AgentCore"):
        asyncio.run(client.get_request_status("request-1"))


def test_agentcore_runtime_client_invokes_bedrock_agentcore_runtime():
    captured = {}

    class FakeAgentCoreClient:
        def invoke_agent_runtime(self, **kwargs):
            captured.update(kwargs)
            return {
                "statusCode": 200,
                "response": io.BytesIO(
                    json.dumps(
                        {
                            "text": "AgentCore response",
                            "tool_calls": [],
                            "memory": {"session_id": "session-1"},
                        }
                    ).encode("utf-8")
                ),
            }

    result = AgentCoreRuntimeClient(
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example",
        aws_region="us-east-1",
        client=FakeAgentCoreClient(),
    ).invoke(
        AgentInvocationRequest(
            message="hello",
            user_id="user-1",
            agent_session_id="session-1",
            branch_id="branch-1",
            customer_id="customer-1",
            customer_name="Ava",
            customer_phone="+923001234567",
            channel="web",
            request_id="req-trusted",
        )
    )

    assert result.text == "AgentCore response"
    assert result.raw_result["memory"] == {"session_id": "session-1"}
    assert captured["agentRuntimeArn"] == "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example"
    assert captured["runtimeSessionId"] == "req-trusted"
    assert captured["runtimeUserId"] == "user-1"
    assert captured["contentType"] == "application/json"
    assert captured["accept"] == "application/json"
    assert json.loads(captured["payload"].decode("utf-8")) == {
        "message": "hello",
        "user_id": "user-1",
        "agent_session_id": "session-1",
        "branch_id": "branch-1",
        "customer_id": "customer-1",
        "customer_name": "Ava",
        "customer_phone": "+923001234567",
        "channel": "web",
        "request_id": "req-trusted",
    }


def test_agentcore_runtime_payload_preserves_missing_request_id():
    payload = AgentCoreRuntimeClient._payload(
        AgentInvocationRequest(
            message="hello",
            user_id="user-1",
            agent_session_id="session-1",
        )
    )

    assert "request_id" in payload
    assert payload["request_id"] is None


def test_agentcore_runtime_client_requests_strict_order_intent_classification():
    captured = {}

    class FakeAgentCoreClient:
        def invoke_agent_runtime(self, **kwargs):
            captured.update(kwargs)
            return {
                "statusCode": 200,
                "response": io.BytesIO(json.dumps({
                    "text": "",
                    "intent": {
                        "action": "checkout",
                        "confidence": 0.95,
                        "selected_option": None,
                    },
                }).encode("utf-8")),
            }

    result = AgentCoreRuntimeClient(
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example",
        aws_region="us-east-1",
        client=FakeAgentCoreClient(),
    ).classify_order_intent(OrderIntentRequest(
        message="checkouttt",
        state="cart_ready",
        allowed_actions=["checkout"],
        available_options=[],
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
    ))

    payload = json.loads(captured["payload"].decode("utf-8"))
    assert result == OrderIntentClassification(action="checkout", confidence=0.95)
    assert captured["runtimeSessionId"] == "req-1"
    assert payload == {
        "task": "classify_order_intent",
        "message": "checkouttt",
        "state": "cart_ready",
        "allowed_actions": ["checkout"],
        "available_options": [],
        "user_id": "user-1",
        "agent_session_id": "session-1",
        "request_id": "req-1",
        "channel": "whatsapp",
    }


def test_local_runtime_order_intent_path_does_not_invoke_transactional_agent(monkeypatch):
    monkeypatch.setattr(
        "src.agent_client.local.invoke_restaurant_agent",
        lambda *args, **kwargs: pytest.fail("transactional agent must not run"),
    )
    monkeypatch.setattr(
        "src.agent_client.local.classify_order_intent",
        lambda **kwargs: OrderIntentClassification(
            action="confirm",
            confidence=0.92,
        ),
    )

    result = LocalStrandsAgentRuntimeClient().classify_order_intent(
        OrderIntentRequest(
            message="yeah go ahead",
            state="pending_confirmation",
            allowed_actions=["confirm", "cancel"],
            available_options=[],
            user_id="user-1",
            agent_session_id="session-1",
        )
    )

    assert result.action == "confirm"
    assert result.confidence == 0.92


def test_agentcore_runtime_sends_structured_whatsapp_turn_task():
    captured = {}

    class FakeAgentCoreClient:
        def invoke_agent_runtime(self, **kwargs):
            captured.update(kwargs)
            return {
                "statusCode": 200,
                "response": io.BytesIO(json.dumps({
                    "text": "",
                    "turn_intent": {
                        "action": "menu_item_detail",
                        "confidence": 0.96,
                        "informational_only": True,
                        "wants_to_order": False,
                        "question_type": "contents",
                        "target_items": ["choco bread"],
                    },
                }).encode("utf-8")),
            }

    result = AgentCoreRuntimeClient(
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example",
        aws_region="us-east-1",
        client=FakeAgentCoreClient(),
    ).classify_whatsapp_turn(WhatsAppTurnIntentRequest(
        message="what is choco bread?",
        state="conversation",
        allowed_actions=["menu_item_detail"],
        available_options=[],
        user_id="user-1",
        agent_session_id="session-1",
        request_id="req-1",
    ))

    payload = json.loads(captured["payload"].decode("utf-8"))
    assert result == WhatsAppTurnInterpretation(
        action="menu_item_detail",
        confidence=0.96,
        informational_only=True,
        wants_to_order=False,
        question_type="contents",
        target_items=["choco bread"],
    )
    assert payload["task"] == "classify_whatsapp_turn"
    assert captured["runtimeSessionId"] == "req-1"


def test_agentcore_runtime_session_falls_back_to_agent_session_without_request_id():
    captured = {}

    class FakeAgentCoreClient:
        def invoke_agent_runtime(self, **kwargs):
            captured.update(kwargs)
            return {
                "statusCode": 200,
                "response": io.BytesIO(json.dumps({"text": "ok"}).encode("utf-8")),
            }

    AgentCoreRuntimeClient(
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example",
        aws_region="us-east-1",
        client=FakeAgentCoreClient(),
    ).invoke(
        AgentInvocationRequest(
            message="hello",
            user_id="user-1",
            agent_session_id="session-1",
        )
    )

    assert captured["runtimeSessionId"] == "session-1"


def test_agent_runtime_factory_uses_agentcore_when_runtime_arn_is_set(monkeypatch):
    get_agent_runtime_client.cache_clear()
    monkeypatch.setattr(
        "src.agent_client.factory.get_settings",
        lambda: SimpleNamespace(
            agentcore_runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example",
            aws_region="us-east-1",
        ),
    )

    client = get_agent_runtime_client()

    assert isinstance(client, AgentCoreRuntimeClient)
    assert client.runtime_arn == "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example"
    get_agent_runtime_client.cache_clear()


def test_agent_runtime_factory_uses_local_client_without_runtime_arn(monkeypatch):
    get_agent_runtime_client.cache_clear()
    monkeypatch.setattr(
        "src.agent_client.factory.get_settings",
        lambda: SimpleNamespace(agentcore_runtime_arn="", aws_region="us-east-1"),
    )

    assert isinstance(get_agent_runtime_client(), LocalStrandsAgentRuntimeClient)
    get_agent_runtime_client.cache_clear()
