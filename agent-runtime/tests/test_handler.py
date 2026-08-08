import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent_runtime import handler
from agent_runtime.server import app
from agent_runtime.schemas import RuntimeRequest
from src.agent import order_intent, restaurant_agent
from src.agent.order_intent import OrderIntentClassification
from src.agent.whatsapp_turn_intent import WhatsAppTurnInterpretation
from src.agent.response_grounding import (
    AssistantClaimAssessment,
    UNGROUNDED_TRANSACTION_FALLBACK,
)
from src.agent_client.schemas import AgentInvocationResult
from src.agent.context import AgentRequestContext
from src.api import main as api_main
from src.services.whatsapp_conversation_service import whatsapp_reply_from_response


class FakeMemoryConfig:
    created = []

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.created.append(kwargs)


class FakeMemorySessionManager:
    created = []
    closed = []
    history_by_session = {}
    redactions = []

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

    def redact_latest_message(self, redact_message, agent):
        self.redactions.append(redact_message)

    def append_message(self, message, agent, **kwargs):
        self.history.append(message)


def reset_fake_memory():
    FakeMemoryConfig.created = []
    FakeMemorySessionManager.created = []
    FakeMemorySessionManager.closed = []
    FakeMemorySessionManager.history_by_session = {}
    FakeMemorySessionManager.redactions = []


def test_whatsapp_search_response_and_memory_share_grounded_next_question(monkeypatch):
    class FakeAgent:
        def __init__(self, session_manager):
            self.session_manager = session_manager
            self.messages = []

    monkeypatch.setattr(
        handler,
        "build_restaurant_agent",
        lambda *, session_manager: FakeAgent(session_manager),
    )
    monkeypatch.setattr(
        handler,
        "invoke_restaurant_agent",
        lambda message, **kwargs: SimpleNamespace(
            message={"content": [{"text": "Choose the item and size."}]},
            tool_calls=[{
                "tool_name": "search_menu",
                "success": True,
                "is_write": False,
                "result": {
                    "success": True,
                    "data": {"items": [
                        {"item_id": "item-1", "name": "First Item", "price": 10},
                        {"item_id": "item-2", "name": "Second Item", "price": 12},
                    ]},
                },
                "error_code": None,
            }],
        ),
    )
    monkeypatch.setattr(
        handler,
        "agent_result_text",
        lambda result: "Choose the item and size.",
    )
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings())

    response = handler.invoke(runtime_payload(channel="whatsapp"))

    assert response["text"].endswith("Which item would you like?")
    assert "size" not in response["text"].lower()
    assert FakeMemorySessionManager.redactions == []
    assert FakeMemorySessionManager.created[0].history[-1] == {
        "role": "assistant",
        "content": [{"text": response["text"]}],
    }


def test_transactional_customer_can_receive_conversational_continuation(monkeypatch):
    text = "Of course. What would you like to order today?"

    class FakeAgent:
        def __init__(self, session_manager):
            self.session_manager = session_manager

    def fake_invoke(message, **kwargs):
        agent = kwargs["agent"]
        agent.session_manager.append_message(
            {"role": "assistant", "content": [{"text": text}]},
            agent,
        )
        return SimpleNamespace(
            message={"content": [{"text": text}]},
            tool_calls=[],
        )

    monkeypatch.setattr(
        handler,
        "build_restaurant_agent",
        lambda *, session_manager: FakeAgent(session_manager),
    )
    monkeypatch.setattr(handler, "invoke_restaurant_agent", fake_invoke)
    monkeypatch.setattr(handler, "agent_result_text", lambda result: text)
    monkeypatch.setattr(
        handler,
        "classify_whatsapp_turn",
        lambda **kwargs: WhatsAppTurnInterpretation(
            action="transactional_change",
            confidence=0.99,
            informational_only=False,
            wants_to_order=True,
        ),
    )
    monkeypatch.setattr(
        handler,
        "assess_assistant_claims",
        lambda **kwargs: AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
    )
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings())

    response = handler.invoke(runtime_payload(
        message="I want to place an order",
        channel="whatsapp",
    ))

    assert response["text"] == text
    assert response["claim_assessment"]["claims_transactional_progression"] is False
    assert FakeMemorySessionManager.created[0].history[-1] == {
        "role": "assistant",
        "content": [{"text": text}],
    }


def test_pending_write_survives_conversational_detour(monkeypatch):
    text = "Would you like a quick description before choosing?"

    class FakeAgent:
        def __init__(self, session_manager):
            self.session_manager = session_manager

    def fake_invoke(message, **kwargs):
        agent = kwargs["agent"]
        agent.session_manager.append_message(
            {"role": "assistant", "content": [{"text": text}]},
            agent,
        )
        return SimpleNamespace(
            message={"content": [{"text": text}]},
            tool_calls=[],
        )

    monkeypatch.setattr(
        handler,
        "build_restaurant_agent",
        lambda *, session_manager: FakeAgent(session_manager),
    )
    monkeypatch.setattr(handler, "invoke_restaurant_agent", fake_invoke)
    monkeypatch.setattr(handler, "agent_result_text", lambda result: text)
    monkeypatch.setattr(
        handler,
        "classify_whatsapp_turn",
        lambda **kwargs: WhatsAppTurnInterpretation(
            action="clarify",
            confidence=0.99,
            informational_only=False,
            wants_to_order=False,
        ),
    )
    monkeypatch.setattr(
        handler,
        "assess_assistant_claims",
        lambda **kwargs: AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
    )
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings())

    response = handler.invoke(runtime_payload(
        message="Could you explain the difference first?",
        channel="whatsapp",
        expected_write_tool="start_cart_item_customization",
        available_options=[{"id": "item-1", "label": "First Item"}],
    ))

    assert response["text"] == text
    assert response["expected_write_tool"] == "start_cart_item_customization"
    assert response["tool_calls"] == []


def test_two_turn_search_selection_blocks_prose_progression_across_production_path(monkeypatch):
    class FakeAgent:
        def __init__(self, session_manager):
            self.session_manager = session_manager

    observed_histories = []

    def fake_invoke(message, **kwargs):
        agent = kwargs["agent"]
        observed_histories.append(list(agent.session_manager.history))
        agent.session_manager.append_message({"role": "user", "content": [{"text": message}]}, agent)
        if message == "show me the menu":
            raw = "Choose the item and size."
            calls = [{
                "tool_name": "search_menu",
                "success": True,
                "is_write": False,
                "result": {
                    "success": True,
                    "data": {"items": [
                        {"product_id": "item-1", "name": "Pepperoni Hot", "price": 10},
                        {"product_id": "item-2", "name": "Second Item", "price": 12},
                    ]},
                },
                "error_code": None,
            }]
        else:
            raw = "You selected Pepperoni Hot. Which size would you like?"
            calls = []
        agent.session_manager.append_message(
            {"role": "assistant", "content": [{"text": raw}]},
            agent,
        )
        return SimpleNamespace(message={"content": [{"text": raw}]}, tool_calls=calls)

    def classify_turn(**kwargs):
        if kwargs["message"] == "show me the menu":
            return WhatsAppTurnInterpretation(
                action="menu_browse", confidence=0.99,
                informational_only=True, wants_to_order=False,
            )
        return WhatsAppTurnInterpretation(
            action="select_menu_item", confidence=0.99,
            informational_only=False, wants_to_order=True,
            selected_option="item-1",
        )

    monkeypatch.setattr(handler, "build_restaurant_agent", lambda *, session_manager: FakeAgent(session_manager))
    monkeypatch.setattr(handler, "invoke_restaurant_agent", fake_invoke)
    monkeypatch.setattr(handler, "agent_result_text", lambda result: result.message["content"][0]["text"])
    monkeypatch.setattr(handler, "classify_whatsapp_turn", classify_turn)
    monkeypatch.setattr(
        handler,
        "assess_assistant_claims",
        lambda **kwargs: AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
    )
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings())

    first = handler.invoke(runtime_payload(message="show me the menu", channel="whatsapp"))
    second = handler.invoke(runtime_payload(
        message="Pepperoni Hot",
        channel="whatsapp",
        expected_write_tool="start_cart_item_customization",
        available_options=[
            {"id": "item-1", "label": "Pepperoni Hot"},
            {"id": "item-2", "label": "Second Item"},
        ],
    ))

    context = AgentRequestContext(
        user_id="user-1", agent_session_id="session-1",
        customer_id="customer-1", channel="whatsapp",
    )
    identity = {
        "customer": {"customer_id": "customer-1", "phone_verified": True},
        "session": {"session_id": "session-1", "channel": "whatsapp"},
    }
    built = api_main._chat_response_from_invocation(
        context,
        identity,
        AgentInvocationResult(text=second["text"], raw_result=second),
    ).model_dump(exclude_none=True)
    reply = whatsapp_reply_from_response(
        built,
        request_id="request-2",
        session_id="session-1",
        customer_id="customer-1",
    )

    assert first["text"].endswith("Which item would you like?")
    assert first["expected_write_tool"] == "start_cart_item_customization"
    assert observed_histories[1][-1]["content"][0]["text"] == first["text"]
    assert second["tool_calls"] == []
    assert second["text"] == UNGROUNDED_TRANSACTION_FALLBACK
    assert reply.reply == UNGROUNDED_TRANSACTION_FALLBACK
    history = FakeMemorySessionManager.created[-1].history
    assert all("You selected Pepperoni Hot" not in str(message) for message in history)
    assert history[-1]["content"][0]["text"] == UNGROUNDED_TRANSACTION_FALLBACK


def test_assistant_classifier_exception_fails_closed_before_memory_commit(monkeypatch):
    class FakeAgent:
        def __init__(self, session_manager):
            self.session_manager = session_manager

    def fake_invoke(message, **kwargs):
        agent = kwargs["agent"]
        raw = "Your selections are now locked in."
        agent.session_manager.append_message(
            {"role": "assistant", "content": [{"text": raw}]}, agent
        )
        return SimpleNamespace(message={"content": [{"text": raw}]}, tool_calls=[])

    monkeypatch.setattr(handler, "build_restaurant_agent", lambda *, session_manager: FakeAgent(session_manager))
    monkeypatch.setattr(handler, "invoke_restaurant_agent", fake_invoke)
    monkeypatch.setattr(handler, "agent_result_text", lambda result: result.message["content"][0]["text"])
    monkeypatch.setattr(
        handler,
        "classify_whatsapp_turn",
        lambda **kwargs: WhatsAppTurnInterpretation(
            action="general_chat", confidence=0.99,
            informational_only=False, wants_to_order=False,
        ),
    )
    monkeypatch.setattr(
        handler,
        "assess_assistant_claims",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("malformed")),
    )
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings())

    response = handler.invoke(runtime_payload(channel="whatsapp"))

    assert response["text"] == UNGROUNDED_TRANSACTION_FALLBACK
    assert FakeMemorySessionManager.created[0].history[-1]["content"][0]["text"] == (
        UNGROUNDED_TRANSACTION_FALLBACK
    )
    assert "selections are now locked" not in str(FakeMemorySessionManager.created[0].history)


@pytest.mark.parametrize(
    ("success", "user_message"),
    [
        (True, "Which size would you like?"),
        (False, "Please choose one of the available items."),
    ],
)
def test_write_outcome_commits_only_authoritative_message(monkeypatch, success, user_message):
    class FakeAgent:
        def __init__(self, session_manager):
            self.session_manager = session_manager

    def fake_invoke(message, **kwargs):
        agent = kwargs["agent"]
        raw = "The item and every customization were saved successfully."
        agent.session_manager.append_message(
            {"role": "assistant", "content": [{"text": raw}]}, agent
        )
        return SimpleNamespace(
            message={"content": [{"text": raw}]},
            tool_calls=[{
                "tool_name": "start_cart_item_customization",
                "success": success,
                "is_write": True,
                "result": {"success": success, "user_message": user_message},
                "error_code": None if success else "INVALID_ITEM",
            }],
        )

    monkeypatch.setattr(handler, "build_restaurant_agent", lambda *, session_manager: FakeAgent(session_manager))
    monkeypatch.setattr(handler, "invoke_restaurant_agent", fake_invoke)
    monkeypatch.setattr(handler, "agent_result_text", lambda result: result.message["content"][0]["text"])
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings())

    response = handler.invoke(runtime_payload(channel="whatsapp"))

    assert response["text"] == user_message
    assert FakeMemorySessionManager.created[0].history[-1]["content"][0]["text"] == user_message
    assert "every customization" not in str(FakeMemorySessionManager.created[0].history)


def test_memory_commit_failure_leaves_no_raw_turn_for_next_invocation(monkeypatch):
    class FailingMemory(FakeMemorySessionManager):
        fail_assistant = True
        shared_history = []

        def __init__(self, *, agentcore_memory_config, region_name):
            self.config = agentcore_memory_config
            self.region_name = region_name
            self.history = self.shared_history
            self.created.append(self)

        def append_message(self, message, agent, **kwargs):
            if message["role"] == "assistant" and self.fail_assistant:
                raise RuntimeError("memory commit unavailable")
            self.history.append(message)

        def redact_latest_message(self, redact_message, agent):
            raise AssertionError("redaction must not be called")

    class FakeAgent:
        def __init__(self, session_manager):
            self.session_manager = session_manager

    def fake_invoke(message, **kwargs):
        agent = kwargs["agent"]
        raw = "Raw undelivered assistant progression."
        agent.session_manager.append_message(
            {"role": "assistant", "content": [{"text": raw}]}, agent
        )
        return SimpleNamespace(message={"content": [{"text": raw}]}, tool_calls=[])

    monkeypatch.setattr(
        handler,
        "load_agentcore_memory_integration",
        lambda: (FakeMemoryConfig, FailingMemory),
    )
    monkeypatch.setattr(handler, "build_restaurant_agent", lambda *, session_manager: FakeAgent(session_manager))
    monkeypatch.setattr(handler, "invoke_restaurant_agent", fake_invoke)
    monkeypatch.setattr(handler, "agent_result_text", lambda result: result.message["content"][0]["text"])
    monkeypatch.setattr(
        handler,
        "classify_whatsapp_turn",
        lambda **kwargs: WhatsAppTurnInterpretation(
            action="select_menu_item", confidence=0.99,
            informational_only=False, wants_to_order=True,
            selected_option="item-1",
        ),
    )
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings())
    payload = runtime_payload(
        channel="whatsapp",
        expected_write_tool="start_cart_item_customization",
        available_options=[{"id": "item-1", "label": "First Item"}],
    )

    with pytest.raises(RuntimeError, match="memory commit unavailable"):
        handler.invoke(payload)
    assert "Raw undelivered" not in str(FailingMemory.shared_history)

    FailingMemory.fail_assistant = False
    response = handler.invoke(payload)

    assert response["text"] == UNGROUNDED_TRANSACTION_FALLBACK
    assert "Raw undelivered" not in str(FailingMemory.shared_history)
    assert FailingMemory.shared_history[-1]["content"][0]["text"] == (
        UNGROUNDED_TRANSACTION_FALLBACK
    )


def settings(
    memory_id="memory-1",
    environment="production",
    whatsapp_memory_namespace="whatsapp-agent-v2",
    whatsapp_memory_ttl_hours=6,
):
    return SimpleNamespace(
        environment=environment,
        agentcore_memory_id=memory_id,
        whatsapp_agentcore_memory_namespace=whatsapp_memory_namespace,
        whatsapp_agentcore_memory_ttl_hours=whatsapp_memory_ttl_hours,
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
        "request_id": "req-trusted",
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
    assert captured["request_id"] == "req-trusted"
    assert FakeMemorySessionManager.closed == [FakeMemorySessionManager.created[0]]


def test_handler_uses_versioned_agentcore_memory_session_for_whatsapp(monkeypatch):
    captured = {}

    class FakeAgent:
        def __init__(self, session_manager):
            self.session_manager = session_manager

    def fake_build_restaurant_agent(*, session_manager):
        return FakeAgent(session_manager)

    def fake_invoke_restaurant_agent(message, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(message={"content": [{"text": "Ready."}]}, tool_calls=[])

    monkeypatch.setattr(handler, "build_restaurant_agent", fake_build_restaurant_agent)
    monkeypatch.setattr(handler, "invoke_restaurant_agent", fake_invoke_restaurant_agent)
    monkeypatch.setattr(handler, "agent_result_text", lambda result: "Ready.")
    monkeypatch.setattr(handler.time, "time", lambda: 216000)
    monkeypatch.setattr(
        handler,
        "get_agentcore_runtime_settings",
        lambda: settings(whatsapp_memory_namespace="wa-clean-v3"),
    )

    response = handler.invoke(runtime_payload(
        agent_session_id="whatsapp-session-1",
        channel="whatsapp",
    ))

    assert captured["agent_session_id"] == "whatsapp-session-1"
    assert response["memory"] == {
        "memory_id": "memory-1",
        "actor_id": "customer-1",
        "session_id": "wa-clean-v3-whatsapp-session-1-b10",
    }
    assert FakeMemoryConfig.created == [{
        "memory_id": "memory-1",
        "actor_id": "customer-1",
        "session_id": "wa-clean-v3-whatsapp-session-1-b10",
        "batch_size": 1,
    }]


def test_runtime_request_accepts_missing_request_id():
    payload = runtime_payload()
    payload.pop("request_id")

    request = RuntimeRequest.model_validate(payload)

    assert request.request_id is None


def test_handler_classifies_order_intent_without_tools_or_conversation_memory(monkeypatch):
    captured = {}

    def fake_classify_order_intent(**kwargs):
        captured.update(kwargs)
        return OrderIntentClassification(
            action="checkout",
            confidence=0.95,
        )

    monkeypatch.setattr(handler, "classify_order_intent", fake_classify_order_intent)
    monkeypatch.setattr(
        handler,
        "build_restaurant_agent",
        lambda **kwargs: pytest.fail("transactional agent must not be built"),
    )
    monkeypatch.setattr(
        handler,
        "get_agentcore_runtime_settings",
        lambda: settings(),
    )

    response = handler.invoke(runtime_payload(
        task="classify_order_intent",
        message="checkouttt",
        state="cart_ready",
        allowed_actions=["checkout"],
        available_options=[],
        channel="whatsapp",
    ))

    assert response == {
        "text": "",
        "tool_calls": [],
        "memory": {},
        "intent": {
            "action": "checkout",
            "confidence": 0.95,
        },
    }
    assert captured == {
        "message": "checkouttt",
        "state": "cart_ready",
        "allowed_actions": ["checkout"],
        "available_options": [],
    }
    assert FakeMemoryConfig.created == []
    assert FakeMemorySessionManager.created == []


def test_handler_classifies_whatsapp_turn_without_tools_or_memory(monkeypatch):
    monkeypatch.setattr(
        handler,
        "classify_whatsapp_turn",
        lambda **kwargs: WhatsAppTurnInterpretation(
            action="menu_item_detail",
            confidence=0.96,
            informational_only=True,
            wants_to_order=False,
            question_type="contents",
            target_items=["choco bread"],
        ),
    )
    monkeypatch.setattr(
        handler,
        "build_restaurant_agent",
        lambda **kwargs: pytest.fail("transactional agent must not be built"),
    )
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings())

    response = handler.invoke(runtime_payload(
        task="classify_whatsapp_turn",
        message="what is choco bread?",
        state="conversation",
        allowed_actions=["menu_item_detail"],
        available_options=[],
        channel="whatsapp",
    ))

    assert response["turn_intent"]["action"] == "menu_item_detail"
    assert response["turn_intent"]["informational_only"] is True
    assert FakeMemoryConfig.created == []
    assert FakeMemorySessionManager.created == []


def test_classifier_model_build_does_not_require_session_token_secret(monkeypatch):
    captured = {}

    class FakeBedrockModel:
        def __init__(self, **kwargs):
            captured["model"] = kwargs

    class FakeClassifier:
        def __init__(self, **kwargs):
            captured["classifier"] = kwargs

        def __call__(self, prompt, **kwargs):
            return SimpleNamespace(
                structured_output=OrderIntentClassification(
                    action="latest_order_eta",
                    confidence=0.96,
                )
            )

    monkeypatch.delenv("SESSION_TOKEN_SECRET", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "configured-model")
    monkeypatch.setattr(restaurant_agent, "BedrockModel", FakeBedrockModel)
    monkeypatch.setattr(order_intent, "Agent", FakeClassifier)
    monkeypatch.setattr(handler, "get_agentcore_runtime_settings", lambda: settings())
    restaurant_agent.get_bedrock_model_settings.cache_clear()

    response = handler.invoke(runtime_payload(
        task="classify_order_intent",
        message="when will I receive my order",
        state="conversation",
        allowed_actions=["latest_order_eta", "latest_order_status"],
        channel="whatsapp",
    ))

    assert response["intent"] == {
        "action": "latest_order_eta",
        "confidence": 0.96,
    }
    assert captured["model"]["model_id"] == "configured-model"
    assert "SESSION_TOKEN_SECRET" not in os.environ
    restaurant_agent.get_bedrock_model_settings.cache_clear()


def test_handler_forwards_missing_request_id_without_substitute(monkeypatch):
    captured = {}
    payload = runtime_payload()
    payload.pop("request_id")

    monkeypatch.setattr(
        handler,
        "build_restaurant_agent",
        lambda *, session_manager: SimpleNamespace(
            session_manager=session_manager
        ),
    )

    def fake_invoke_restaurant_agent(message, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            message={"content": [{"text": "ok"}]},
            tool_calls=[],
        )

    monkeypatch.setattr(
        handler,
        "invoke_restaurant_agent",
        fake_invoke_restaurant_agent,
    )
    monkeypatch.setattr(handler, "agent_result_text", lambda result: "ok")
    monkeypatch.setattr(
        handler,
        "get_agentcore_runtime_settings",
        lambda: settings(),
    )

    handler.invoke(payload)

    assert captured["request_id"] is None


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
