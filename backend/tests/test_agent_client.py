import asyncio
import io
import json
from types import SimpleNamespace

import pytest

from src.agent_client.agentcore import AgentCoreRuntimeClient
from src.agent_client.factory import get_agent_runtime_client
from src.agent_client.local import LocalStrandsAgentRuntimeClient
from src.agent_client.schemas import AgentInvocationRequest


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
    }


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
        )
    )

    assert result.text == "AgentCore response"
    assert result.raw_result["memory"] == {"session_id": "session-1"}
    assert captured["agentRuntimeArn"] == "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example"
    assert captured["runtimeSessionId"] == "session-1"
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
    }


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
