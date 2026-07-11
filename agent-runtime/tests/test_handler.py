from types import SimpleNamespace

from agent_runtime import handler


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
        lambda: SimpleNamespace(agentcore_memory_id="memory-1", log_level="INFO"),
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
