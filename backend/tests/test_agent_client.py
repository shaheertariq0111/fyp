import asyncio
import io
import json
from types import SimpleNamespace

import pytest

from src.agent_client.agentcore import AgentCoreRuntimeClient
from src.agent_client.factory import get_agent_runtime_client
from src.agent_client.local import LocalStrandsAgentRuntimeClient
from src.agent_client.schemas import AgentInvocationRequest
from src.services.whatsapp_conversation_service import (
    UNGROUNDED_ORDER_SUBMISSION_FALLBACK,
)


def request(**overrides):
    values = {
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
    values.update(overrides)
    return AgentInvocationRequest(**values)


def test_agent_invocation_request_supports_optional_request_id():
    assert request().request_id == "req-trusted"
    assert request(request_id=None).request_id is None


def test_local_agent_runtime_client_invokes_existing_strands_agent(monkeypatch):
    captured = {}

    def invoke(message, **kwargs):
        captured.update({"message": message, **kwargs})
        return {"agent": "result"}

    monkeypatch.setattr("src.agent_client.local.invoke_restaurant_agent", invoke)
    monkeypatch.setattr(
        "src.agent_client.local.agent_result_text",
        lambda result: "Agent response",
    )

    result = LocalStrandsAgentRuntimeClient().invoke(request())

    assert result.text == "Agent response"
    assert result.raw_result == {"agent": "result"}
    assert captured == {
        "message": "hello",
        "user_id": "user-1",
        "agent_session_id": "session-1",
        "request_id": "req-trusted",
        "branch_id": "branch-1",
        "customer_id": "customer-1",
        "customer_name": "Ava",
        "customer_phone": "+923001234567",
        "channel": "web",
    }


class MemoryManager:
    def __init__(self):
        self.messages = []

    def append_message(self, message, agent, **kwargs):
        self.messages.append(message)

    def initialize(self, agent):
        return None

    def sync_agent(self, agent):
        return None


def install_local_whatsapp_agent(monkeypatch, *, raw_text, tool_calls):
    manager = MemoryManager()
    monkeypatch.setattr(
        "src.agent_client.local.build_session_manager",
        lambda session_id: manager,
    )
    monkeypatch.setattr(
        "src.agent_client.local.build_restaurant_agent",
        lambda *, session_manager: SimpleNamespace(session_manager=session_manager),
    )

    def invoke(message, **kwargs):
        agent = kwargs["agent"]
        agent.session_manager.append_message(
            {"role": "assistant", "content": [{"text": raw_text}]},
            agent,
        )
        return SimpleNamespace(
            message={"content": [{"text": raw_text}]},
            tool_calls=tool_calls,
        )

    monkeypatch.setattr("src.agent_client.local.invoke_restaurant_agent", invoke)
    monkeypatch.setattr(
        "src.agent_client.local.agent_result_text",
        lambda result: raw_text,
    )
    return manager


def test_local_whatsapp_menu_read_uses_exact_artifact_and_logs_safe_tool_summary(
    monkeypatch,
    caplog,
):
    raw = "Imaginary Supreme is available."
    authoritative = "1. Real Pizza Alpha — MYR 20"
    manager = install_local_whatsapp_agent(
        monkeypatch,
        raw_text=raw,
        tool_calls=[
            SimpleNamespace(
                tool_name="search_menu",
                success=True,
                is_write=False,
                result={
                    "success": True,
                    "user_message": "I found current menu options.",
                    "grounding": {
                        "authoritative_domains": ["menu"],
                        "exact_customer_text": authoritative,
                    },
                },
            )
        ],
    )

    with caplog.at_level("INFO"):
        result = LocalStrandsAgentRuntimeClient().invoke(
            request(message="Pepperoni", channel="whatsapp")
        )

    assert result.text == authoritative
    assert result.grounding_source == "exact_artifact"
    assert result.grounding_rejection_reason is None
    assert manager.messages == [
        {"role": "assistant", "content": [{"text": authoritative}]},
    ]
    assert raw not in str(manager.messages)
    selected = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "whatsapp_response_selected"
    )
    assert selected.grounding_source == "exact_artifact"
    assert selected.grounding_rejection_reason is None
    assert selected.tool_call_count == 1
    assert selected.tool_names == ["search_menu"]


def test_local_whatsapp_ungrounded_menu_recommendation_retries_silently(
    monkeypatch,
    caplog,
):
    manager = MemoryManager()
    authoritative = (
        "Here are the current menu options I found:\n"
        "1. Super Cheese - from PKR 650\n"
        "Which item would you like?"
    )
    hallucinated = (
        "Sure, here are some popular items from our menu:\n"
        "1. *Pepperoni Passion* - A classic favorite."
    )
    calls = []

    monkeypatch.setattr(
        "src.agent_client.local.build_session_manager",
        lambda session_id: manager,
    )
    monkeypatch.setattr(
        "src.agent_client.local.build_restaurant_agent",
        lambda *, session_manager: SimpleNamespace(session_manager=session_manager),
    )

    def invoke(message, **kwargs):
        calls.append(message)
        agent = kwargs["agent"]
        if len(calls) == 1:
            agent.session_manager.append_message(
                {"role": "assistant", "content": [{"text": hallucinated}]},
                agent,
            )
            return SimpleNamespace(
                message={"content": [{"text": hallucinated}]},
                tool_calls=[],
            )
        return SimpleNamespace(
            message={"content": [{"text": "Natural retry draft"}]},
            tool_calls=[
                SimpleNamespace(
                    tool_name="search_menu",
                    success=True,
                    is_write=False,
                    result={
                        "success": True,
                        "user_message": "I found current menu options.",
                        "grounding": {"exact_customer_text": authoritative},
                    },
                )
            ],
        )

    monkeypatch.setattr("src.agent_client.local.invoke_restaurant_agent", invoke)
    monkeypatch.setattr(
        "src.agent_client.local.agent_result_text",
        lambda result: result.message["content"][0]["text"],
    )

    with caplog.at_level("INFO"):
        result = LocalStrandsAgentRuntimeClient().invoke(
            request(message="recommend something", channel="whatsapp")
        )

    assert result.text == authoritative
    assert len(calls) == 2
    assert calls[0] == "recommend something"
    assert "Internal retry instruction" in calls[1]
    assert manager.messages == [
        {"role": "assistant", "content": [{"text": authoritative}]},
    ]
    assert hallucinated not in str(manager.messages)
    selected = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "whatsapp_response_selected"
    )
    assert selected.grounding_retry_count == 1
    assert selected.grounding_source == "exact_artifact"


def test_local_whatsapp_failed_write_replaces_raw_draft_in_memory(monkeypatch):
    raw = "Your customization was saved."
    failure = "That option is unavailable."
    manager = install_local_whatsapp_agent(
        monkeypatch,
        raw_text=raw,
        tool_calls=[
            SimpleNamespace(
                tool_name="update_cart_item_customization",
                success=False,
                is_write=True,
                result={"success": False, "user_message": failure},
            )
        ],
    )

    result = LocalStrandsAgentRuntimeClient().invoke(
        request(message="extra cheese", channel="whatsapp")
    )

    assert result.text == failure
    assert manager.messages == [
        {"role": "assistant", "content": [{"text": failure}]},
    ]
    assert raw not in str(manager.messages)


def test_local_whatsapp_successful_grounded_write_uses_backend_text_in_memory(
    monkeypatch,
):
    raw = "Saved the cheese and switched you to delivery."
    authoritative = "The customization was saved."
    manager = install_local_whatsapp_agent(
        monkeypatch,
        raw_text=raw,
        tool_calls=[
            SimpleNamespace(
                tool_name="update_cart_item_customization",
                success=True,
                is_write=True,
                result={
                    "success": True,
                    "user_message": authoritative,
                    "grounding": {
                        "transactional_effects": ["customization_saved"],
                    },
                },
            )
        ],
    )

    result = LocalStrandsAgentRuntimeClient().invoke(
        request(message="extra cheese", channel="whatsapp")
    )

    assert result.text == authoritative
    assert manager.messages == [
        {"role": "assistant", "content": [{"text": authoritative}]},
    ]
    assert raw not in str(manager.messages)


def test_local_false_submission_claim_is_replaced_before_memory_commit(monkeypatch):
    raw = "Your order has been submitted successfully."
    manager = install_local_whatsapp_agent(
        monkeypatch,
        raw_text=raw,
        tool_calls=[],
    )

    result = LocalStrandsAgentRuntimeClient().invoke(
        request(message="confirm", channel="whatsapp")
    )

    assert result.text == UNGROUNDED_ORDER_SUBMISSION_FALLBACK
    assert manager.messages == [
        {
            "role": "assistant",
            "content": [{"text": UNGROUNDED_ORDER_SUBMISSION_FALLBACK}],
        },
    ]
    assert raw not in str(manager.messages)


def test_local_agent_runtime_client_async_methods_are_agentcore_boundary():
    client = LocalStrandsAgentRuntimeClient()

    with pytest.raises(NotImplementedError, match="AgentCore"):
        asyncio.run(client.start_request(request()))
    with pytest.raises(NotImplementedError, match="AgentCore"):
        asyncio.run(client.get_request_status("request-1"))


def test_agentcore_runtime_client_invokes_only_conversation_runtime_surface():
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
                            "grounding_source": "authoritative_continuation",
                            "grounding_rejection_reason": (
                                "required_effect_not_satisfied"
                            ),
                        }
                    ).encode("utf-8")
                ),
            }

    client = AgentCoreRuntimeClient(
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example",
        aws_region="us-east-1",
        client=FakeAgentCoreClient(),
    )
    result = client.invoke(request())

    assert result.text == "AgentCore response"
    assert result.raw_result["memory"] == {"session_id": "session-1"}
    assert result.grounding_source == "authoritative_continuation"
    assert (
        result.grounding_rejection_reason == "required_effect_not_satisfied"
    )
    assert captured["runtimeSessionId"] == "req-trusted"
    assert captured["runtimeUserId"] == "user-1"
    assert json.loads(captured["payload"].decode("utf-8")) == {
        "message": "hello",
        "user_id": "user-1",
        "agent_session_id": "session-1",
        "request_id": "req-trusted",
        "branch_id": "branch-1",
        "customer_id": "customer-1",
        "customer_name": "Ava",
        "customer_phone": "+923001234567",
        "channel": "web",
    }
    assert not hasattr(client, "classify_order_intent")
    assert not hasattr(client, "classify_whatsapp_turn")


def test_agentcore_runtime_payload_preserves_missing_request_id():
    payload = AgentCoreRuntimeClient._payload(request(request_id=None))

    assert "request_id" in payload
    assert payload["request_id"] is None


def test_agentcore_runtime_session_falls_back_to_agent_session_without_request_id():
    captured = {}

    class FakeAgentCoreClient:
        def invoke_agent_runtime(self, **kwargs):
            captured.update(kwargs)
            return {
                "statusCode": 200,
                "response": io.BytesIO(b'{"text":"ok"}'),
            }

    AgentCoreRuntimeClient(
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example",
        aws_region="us-east-1",
        client=FakeAgentCoreClient(),
    ).invoke(request(request_id=None))

    assert captured["runtimeSessionId"] == "session-1"


def test_agent_runtime_factory_uses_agentcore_when_runtime_arn_is_set(monkeypatch):
    get_agent_runtime_client.cache_clear()
    captured = {}

    class FakeAgentCoreRuntimeClient:
        def __init__(self, *, runtime_arn, aws_region):
            captured["runtime_arn"] = runtime_arn
            captured["aws_region"] = aws_region
            self.runtime_arn = runtime_arn

    monkeypatch.setattr(
        "src.agent_client.factory.get_settings",
        lambda: SimpleNamespace(
            agentcore_runtime_arn=(
                "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example"
            ),
            aws_region="us-east-1",
        ),
    )
    monkeypatch.setattr(
        "src.agent_client.factory.AgentCoreRuntimeClient",
        FakeAgentCoreRuntimeClient,
    )

    client = get_agent_runtime_client()

    assert isinstance(client, FakeAgentCoreRuntimeClient)
    assert client.runtime_arn == "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example"
    assert captured == {
        "runtime_arn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example",
        "aws_region": "us-east-1",
    }
    get_agent_runtime_client.cache_clear()


def test_agent_runtime_factory_uses_local_client_without_runtime_arn(monkeypatch):
    get_agent_runtime_client.cache_clear()
    monkeypatch.setattr(
        "src.agent_client.factory.get_settings",
        lambda: SimpleNamespace(agentcore_runtime_arn="", aws_region="us-east-1"),
    )

    assert isinstance(get_agent_runtime_client(), LocalStrandsAgentRuntimeClient)
    get_agent_runtime_client.cache_clear()
