import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent_runtime import handler
from agent_runtime.server import app


class FakeMemoryConfig:
    created = []

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.created.append(kwargs)


class FakeMemorySessionManager:
    created = []
    closed = []
    history_by_session = {}

    def __init__(self, *, agentcore_memory_config, region_name):
        self.config = agentcore_memory_config
        self.region_name = region_name
        self.history = self.history_by_session.setdefault(agentcore_memory_config.session_id, [])
        self.created.append(self)

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed.append(self)
        self.exited = True
        return False


def reset_fake_memory():
    FakeMemoryConfig.created = []
    FakeMemorySessionManager.created = []
    FakeMemorySessionManager.closed = []
    FakeMemorySessionManager.history_by_session = {}


def settings(memory_id="memory-1", environment="production"):
    return SimpleNamespace(
        environment=environment,
        agentcore_memory_id=memory_id,
        log_level="INFO",
        session_token_secret_arn="",
        aws_region="us-east-1",
    )


def runtime_payload(**overrides):
    payload = {
        "message": "hello",
        "user_id": "user-1",
        "agent_session_id": "session-1",
        "branch_id": "branch-1",
        "customer_id": "customer-1",
        "customer_name": "Ava",
        "customer_phone": "+923001234567",
        "channel": "web",
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def fake_memory_integration(monkeypatch):
    reset_fake_memory()
    monkeypatch.setattr(
        handler,
        "load_agentcore_memory_integration",
        lambda: (FakeMemoryConfig, FakeMemorySessionManager),
    )


def test_handler_invokes_existing_restaurant_agent_with_agentcore_memory(monkeypatch):
    captured = {}

    class FakeAgent:
        def __init__(self, session_manager):
            self.session_manager = session_manager

    def fake_build_restaurant_agent(*, session_manager):
        captured["session_manager"] = session_manager
        return FakeAgent(session_manager)

    def fake_invoke_restaurant_agent(message, **kwargs):
        captured.update({"message": message, **kwargs})
        kwargs["agent"].session_manager.history.append(message)
        return SimpleNamespace(
            message={"content": [{"text": "Ready."}]},
            tool_calls=[{
                "tool_name": "search_menu",
                "success": True,
                "is_write": False,
                "result": {"success": True},
                "error_code": None,
            }],
        )

    monkeypatch.setattr(handler, "build_restaurant_agent", fake_build_restaurant_agent)
    monkeypatch.setattr(handler, "invoke_restaurant_agent", fake_invoke_restaurant_agent)
    monkeypatch.setattr(handler, "agent_result_text", lambda result: "Ready.")
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings())

    response = handler.invoke(runtime_payload())

    assert response["text"] == "Ready."
    assert response["tool_calls"][0]["tool_name"] == "search_menu"
    assert response["memory"] == {
        "memory_id": "memory-1",
        "actor_id": "customer-1",
        "session_id": "session-1",
    }
    assert FakeMemoryConfig.created == [{
        "memory_id": "memory-1",
        "actor_id": "customer-1",
        "session_id": "session-1",
        "batch_size": 1,
    }]
    assert FakeMemorySessionManager.created[0].region_name == "us-east-1"
    assert captured["session_manager"] is FakeMemorySessionManager.created[0]
    assert captured["agent"].session_manager is FakeMemorySessionManager.created[0]
    assert captured["agent_session_id"] == "session-1"
    assert FakeMemorySessionManager.closed == [FakeMemorySessionManager.created[0]]


def test_handler_uses_user_id_as_actor_when_customer_id_missing(monkeypatch):
    monkeypatch.setattr(handler, "build_restaurant_agent", lambda *, session_manager: lambda message, **kwargs: "ok")
    monkeypatch.setattr(
        handler,
        "invoke_restaurant_agent",
        lambda message, **kwargs: SimpleNamespace(message={"content": [{"text": "ok"}]}, tool_calls=[]),
    )
    monkeypatch.setattr(handler, "agent_result_text", lambda result: "ok")
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings())

    response = handler.invoke(runtime_payload(customer_id=None))

    assert FakeMemoryConfig.created[0]["actor_id"] == "user-1"
    assert response["memory"]["actor_id"] == "user-1"


def test_same_session_id_restores_conversation_history(monkeypatch):
    observed_history_lengths = []

    class FakeAgent:
        def __init__(self, session_manager):
            self.session_manager = session_manager

    def fake_invoke_restaurant_agent(message, **kwargs):
        history = kwargs["agent"].session_manager.history
        observed_history_lengths.append(len(history))
        history.append(message)
        return SimpleNamespace(message={"content": [{"text": f"turn {len(history)}"}]}, tool_calls=[])

    monkeypatch.setattr(
        handler,
        "build_restaurant_agent",
        lambda *, session_manager: FakeAgent(session_manager),
    )
    monkeypatch.setattr(handler, "invoke_restaurant_agent", fake_invoke_restaurant_agent)
    monkeypatch.setattr(handler, "agent_result_text", lambda result: result.message["content"][0]["text"])
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings())

    first = handler.invoke(runtime_payload(message="first", agent_session_id="same-session"))
    second = handler.invoke(runtime_payload(message="second", agent_session_id="same-session"))

    assert observed_history_lengths == [0, 1]
    assert first["text"] == "turn 1"
    assert second["text"] == "turn 2"
    assert FakeMemorySessionManager.history_by_session["same-session"] == ["first", "second"]


def test_file_session_manager_is_not_used_in_agentcore_runtime(monkeypatch):
    def fail_if_file_session_manager_is_used(agent_session_id):
        raise AssertionError("FileSessionManager path must not be used in AgentCore Runtime")

    monkeypatch.setattr("src.agent.restaurant_agent.build_session_manager", fail_if_file_session_manager_is_used)
    monkeypatch.setattr(
        handler,
        "build_restaurant_agent",
        lambda *, session_manager: SimpleNamespace(session_manager=session_manager),
    )
    monkeypatch.setattr(
        handler,
        "invoke_restaurant_agent",
        lambda message, **kwargs: SimpleNamespace(message={"content": [{"text": "ok"}]}, tool_calls=[]),
    )
    monkeypatch.setattr(handler, "agent_result_text", lambda result: "ok")
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings())

    response = handler.invoke(runtime_payload())

    assert response["text"] == "ok"
    assert FakeMemorySessionManager.created


def test_missing_agentcore_memory_id_fails_safely_in_production(monkeypatch):
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings(memory_id=""))

    with pytest.raises(RuntimeError, match="AGENTCORE_MEMORY_ID is required"):
        handler.invoke(runtime_payload())

    assert FakeMemoryConfig.created == []
    assert FakeMemorySessionManager.created == []


def test_session_manager_cleanup_occurs_after_invocation_failure(monkeypatch):
    monkeypatch.setattr(
        handler,
        "build_restaurant_agent",
        lambda *, session_manager: SimpleNamespace(session_manager=session_manager),
    )

    def fail_invoke(message, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(handler, "invoke_restaurant_agent", fail_invoke)
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings())

    with pytest.raises(ValueError, match="boom"):
        handler.invoke(runtime_payload())

    assert FakeMemorySessionManager.closed == [FakeMemorySessionManager.created[0]]


def test_ensure_session_token_secret_loads_secret_arn(monkeypatch):
    monkeypatch.delenv("SESSION_TOKEN_SECRET", raising=False)
    monkeypatch.setattr(
        handler,
        "get_secret_value",
        lambda secret_arn, region_name: f"{secret_arn}:{region_name}:secret",
    )

    handler.ensure_session_token_secret(
        SimpleNamespace(
            session_token_secret_arn="arn:aws:secretsmanager:us-east-1:123:secret:session",
            aws_region="us-east-1",
        )
    )

    assert os.environ["SESSION_TOKEN_SECRET"] == (
        "arn:aws:secretsmanager:us-east-1:123:secret:session:us-east-1:secret"
    )


def test_http_runtime_contract(monkeypatch):
    monkeypatch.setattr(
        "agent_runtime.server.invoke",
        lambda payload: {
            "text": f"handled {payload['message']}",
            "tool_calls": [],
            "memory": {},
        },
    )
    client = TestClient(app)

    ping_response = client.get("/ping")
    invocation_response = client.post(
        "/invocations",
        json={
            "message": "hello",
            "user_id": "user-1",
            "agent_session_id": "session-1",
            "branch_id": "default",
        },
    )

    assert ping_response.status_code == 200
    assert ping_response.json() == {"status": "ok"}
    assert invocation_response.status_code == 200
    assert invocation_response.json()["text"] == "handled hello"
