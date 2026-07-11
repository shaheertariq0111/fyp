import asyncio

import pytest

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
