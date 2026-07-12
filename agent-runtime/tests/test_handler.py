import os
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_runtime import handler
from agent_runtime.server import app


def test_handler_invokes_existing_restaurant_agent(monkeypatch):
    captured = {}

    def fake_invoke_restaurant_agent(message, **kwargs):
        captured.update({"message": message, **kwargs})
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

    monkeypatch.setattr(handler, "invoke_restaurant_agent", fake_invoke_restaurant_agent)
    monkeypatch.setattr(handler, "agent_result_text", lambda result: "Ready.")
    monkeypatch.setattr(
        handler,
        "get_agentcore_runtime_settings",
        lambda: SimpleNamespace(
            agentcore_memory_id="memory-1",
            log_level="INFO",
            session_token_secret_arn="",
            aws_region="us-east-1",
        ),
    )

    response = handler.invoke({
        "message": "hello",
        "user_id": "user-1",
        "agent_session_id": "session-1",
        "branch_id": "branch-1",
        "customer_id": "customer-1",
        "customer_name": "Ava",
        "customer_phone": "+923001234567",
        "channel": "web",
    })

    assert response["text"] == "Ready."
    assert response["tool_calls"][0]["tool_name"] == "search_menu"
    assert response["memory"] == {
        "memory_id": "memory-1",
        "actor_id": "customer-1",
        "session_id": "session-1",
    }
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
