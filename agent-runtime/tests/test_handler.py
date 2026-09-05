import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent_runtime import handler
from agent_runtime.schemas import RuntimeRequest
from agent_runtime.server import app
from src.services.whatsapp_conversation_service import (
    UNGROUNDED_ORDER_SUBMISSION_FALLBACK,
    whatsapp_reply_from_response,
)


ORDER_ID = "ORD-MEMORY-123"
SUBMISSION_CONFIRMATION = (
    "Your order has been confirmed and sent to the restaurant.\n"
    f"Order ID: {ORDER_ID}\n"
    "Status: Submitted to restaurant"
)
STATUS_MESSAGE = (
    f"Order ID: {ORDER_ID}\n"
    "Status: Submitted to restaurant"
)


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
        self.history = self.history_by_session.setdefault(
            agentcore_memory_config.session_id,
            [],
        )
        self.created.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed.append(self)
        return False

    def append_message(self, message, agent, **kwargs):
        self.history.append(message)

    def initialize(self, agent):
        return None

    def sync_agent(self, agent):
        return None


def reset_fake_memory():
    FakeMemoryConfig.created = []
    FakeMemorySessionManager.created = []
    FakeMemorySessionManager.closed = []
    FakeMemorySessionManager.history_by_session = {}


def settings(
    memory_id="memory-1",
    environment="production",
    whatsapp_memory_namespace="whatsapp-agent-v3",
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
    monkeypatch.setattr(
        handler,
        "get_agentcore_runtime_settings",
        lambda: settings(),
    )


def install_fake_agent(monkeypatch, *, text, tool_calls=None, capture=None):
    capture = capture if capture is not None else {}

    class FakeAgent:
        def __init__(self, session_manager):
            self.session_manager = session_manager

    def build(*, session_manager):
        capture["session_manager"] = session_manager
        return FakeAgent(session_manager)

    def invoke(message, **kwargs):
        capture.update({"message": message, **kwargs})
        return SimpleNamespace(
            message={"content": [{"text": text}]},
            tool_calls=tool_calls or [],
        )

    monkeypatch.setattr(handler, "build_restaurant_agent", build)
    monkeypatch.setattr(handler, "invoke_restaurant_agent", invoke)
    monkeypatch.setattr(handler, "agent_result_text", lambda result: text)
    return capture


def test_whatsapp_response_selection_log_has_only_safe_tool_summary(monkeypatch):
    class RecordingLogger:
        def __init__(self):
            self.infos = []

        def info(self, message, *, extra):
            self.infos.append((message, extra))

        def exception(self, message, *, extra):
            raise AssertionError(f"unexpected exception log: {message} {extra}")

    private_text = "private customer response"
    private_result = "private backend result"
    authoritative_text = "Safe authoritative menu response."
    tool_calls = [
        {
            "tool_name": "search_menu",
            "success": True,
            "is_write": False,
            "result": {
                "success": True,
                "user_message": private_result,
                "data": {"private": private_result},
                "grounding": {"exact_customer_text": authoritative_text},
            },
        },
        {
            "tool_name": "get_menu_item",
            "success": True,
            "is_write": False,
            "result": {
                "success": True,
                "user_message": private_result,
                "data": {"private": private_result},
                "grounding": {"exact_customer_text": authoritative_text},
            },
        },
    ]
    install_fake_agent(
        monkeypatch,
        text=private_text,
        tool_calls=tool_calls,
    )
    recording_logger = RecordingLogger()
    monkeypatch.setattr(handler, "logger", recording_logger)

    handler.invoke(runtime_payload(channel="whatsapp"))

    selected = next(
        extra
        for _message, extra in recording_logger.infos
        if extra.get("event") == "whatsapp_response_selected"
    )
    assert selected == {
        "event": "whatsapp_response_selected",
        "grounding_source": "exact_artifact",
        "grounding_rejection_reason": None,
        "grounding_retry_count": 0,
        "tool_call_count": 2,
        "tool_names": ["search_menu", "get_menu_item"],
    }
    assert private_text not in repr(selected)
    assert private_result not in repr(selected)
    assert authoritative_text not in repr(selected)


def test_whatsapp_menu_read_uses_authoritative_artifact_without_classifier(
    monkeypatch,
):
    raw = "Imaginary Supreme is available."
    authoritative = "1. Pepperoni Passion - MYR 10"
    install_fake_agent(
        monkeypatch,
        text=raw,
        tool_calls=[
            {
                "tool_name": "search_menu",
                "success": True,
                "is_write": False,
                "result": {
                    "success": True,
                    "data": {
                        "items": [
                            {
                                "item_id": "pepperoni-passion",
                                "name": "Pepperoni Passion",
                                "price": 10,
                            }
                        ]
                    },
                    "grounding": {
                        "authoritative_domains": ["menu"],
                        "exact_customer_text": authoritative,
                    },
                },
            }
        ],
    )
    monkeypatch.setattr(
        handler,
        "assess_assistant_claims",
        lambda **kwargs: pytest.fail("semantic classifier must not run"),
        raising=False,
    )

    response = handler.invoke(runtime_payload(message="Pepperoni", channel="whatsapp"))

    assert response["text"] == authoritative
    history = next(iter(FakeMemorySessionManager.history_by_session.values()))
    assert history == [
        {"role": "assistant", "content": [{"text": authoritative}]},
    ]
    assert raw not in str(history)


def test_whatsapp_ungrounded_menu_recommendation_retries_silently(
    monkeypatch,
):
    class RecordingLogger:
        def __init__(self):
            self.infos = []

        def info(self, message, *, extra):
            self.infos.append((message, extra))

        def exception(self, message, *, extra):
            raise AssertionError(f"unexpected exception log: {message} {extra}")

    authoritative = (
        "Here are the current menu options I found:\n"
        "1. Super Cheese - from PKR 650\n"
        "Which item would you like?"
    )
    hallucinated = (
        "Sure, here are some popular items from our menu:\n"
        "1. *Pepperoni Passion* - A classic favorite."
    )
    messages = []

    class FakeAgent:
        def __init__(self, session_manager):
            self.session_manager = session_manager

    def build(*, session_manager):
        return FakeAgent(session_manager)

    def invoke_agent(message, **kwargs):
        messages.append(message)
        agent = kwargs["agent"]
        if len(messages) == 1:
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
                {
                    "tool_name": "search_menu",
                    "success": True,
                    "is_write": False,
                    "result": {
                        "success": True,
                        "user_message": "I found current menu options.",
                        "grounding": {"exact_customer_text": authoritative},
                    },
                }
            ],
        )

    monkeypatch.setattr(handler, "build_restaurant_agent", build)
    monkeypatch.setattr(handler, "invoke_restaurant_agent", invoke_agent)
    monkeypatch.setattr(
        handler,
        "agent_result_text",
        lambda result: result.message["content"][0]["text"],
    )
    recording_logger = RecordingLogger()
    monkeypatch.setattr(handler, "logger", recording_logger)

    response = handler.invoke(
        runtime_payload(message="recommend something", channel="whatsapp")
    )

    assert response["text"] == authoritative
    assert response["grounding_source"] == "exact_artifact"
    assert "grounding_rejection_reason" not in response
    assert len(messages) == 2
    assert messages[0] == "recommend something"
    assert "Internal retry instruction" in messages[1]
    history = next(iter(FakeMemorySessionManager.history_by_session.values()))
    assert history == [
        {"role": "assistant", "content": [{"text": authoritative}]},
    ]
    assert hallucinated not in str(history)
    selected = next(
        extra
        for _message, extra in recording_logger.infos
        if extra.get("event") == "whatsapp_response_selected"
    )
    assert selected["grounding_retry_count"] == 1
    assert selected["grounding_source"] == "exact_artifact"


def test_whatsapp_general_conversation_survives_without_semantic_classifier(
    monkeypatch,
):
    raw = "Hello! What can I help you order today?"
    install_fake_agent(monkeypatch, text=raw)
    monkeypatch.setattr(
        handler,
        "assess_assistant_claims",
        lambda **kwargs: pytest.fail("semantic classifier must not run"),
        raising=False,
    )

    response = handler.invoke(runtime_payload(channel="whatsapp"))

    assert response["text"] == raw
    history = next(iter(FakeMemorySessionManager.history_by_session.values()))
    assert history[-1]["content"][0]["text"] == raw


def test_failed_whatsapp_write_replaces_model_success_and_memory_draft(monkeypatch):
    raw = "Done, your customization was saved."
    authoritative = "That customization is unavailable. Please choose another option."
    captured = install_fake_agent(
        monkeypatch,
        text=raw,
        tool_calls=[
            {
                "tool_name": "update_cart_item_customization",
                "success": False,
                "is_write": True,
                "result": {
                    "success": False,
                    "user_message": authoritative,
                    "error_code": "OPTION_UNAVAILABLE",
                },
                "error_code": "OPTION_UNAVAILABLE",
            }
        ],
    )
    original_invoke = handler.invoke_restaurant_agent

    def invoke_with_draft(message, **kwargs):
        kwargs["agent"].session_manager.append_message(
            {"role": "assistant", "content": [{"text": raw}]},
            kwargs["agent"],
        )
        return original_invoke(message, **kwargs)

    monkeypatch.setattr(handler, "invoke_restaurant_agent", invoke_with_draft)

    response = handler.invoke(runtime_payload(channel="whatsapp"))

    assert response["text"] == authoritative
    session_manager = captured["session_manager"].session_manager
    assert session_manager.history == [
        {"role": "assistant", "content": [{"text": authoritative}]},
    ]
    assert raw not in str(session_manager.history)


def test_successful_grounded_write_uses_backend_text_in_response_and_memory(
    monkeypatch,
):
    raw = "Saved the cheese and switched you to delivery."
    authoritative = "The customization was saved."
    captured = install_fake_agent(
        monkeypatch,
        text=raw,
        tool_calls=[
            {
                "tool_name": "update_cart_item_customization",
                "success": True,
                "is_write": True,
                "result": {
                    "success": True,
                    "user_message": authoritative,
                    "grounding": {
                        "transactional_effects": ["customization_saved"],
                    },
                },
            }
        ],
    )
    original_invoke = handler.invoke_restaurant_agent

    def invoke_with_draft(message, **kwargs):
        kwargs["agent"].session_manager.append_message(
            {"role": "assistant", "content": [{"text": raw}]},
            kwargs["agent"],
        )
        return original_invoke(message, **kwargs)

    monkeypatch.setattr(handler, "invoke_restaurant_agent", invoke_with_draft)

    response = handler.invoke(runtime_payload(channel="whatsapp"))

    assert response["text"] == authoritative
    session_manager = captured["session_manager"].session_manager
    assert session_manager.history == [
        {"role": "assistant", "content": [{"text": authoritative}]},
    ]
    assert raw not in str(session_manager.history)


def test_false_submission_claim_is_replaced_before_memory_commit(monkeypatch):
    raw = "Your order has been submitted successfully."
    captured = install_fake_agent(monkeypatch, text=raw)
    original_invoke = handler.invoke_restaurant_agent

    def invoke_with_draft(message, **kwargs):
        kwargs["agent"].session_manager.append_message(
            {"role": "assistant", "content": [{"text": raw}]},
            kwargs["agent"],
        )
        return original_invoke(message, **kwargs)

    monkeypatch.setattr(handler, "invoke_restaurant_agent", invoke_with_draft)

    response = handler.invoke(runtime_payload(channel="whatsapp"))
    session_manager = captured["session_manager"].session_manager
    delivered = whatsapp_reply_from_response(
        response,
        request_id="request-safe",
        session_id="session-safe",
        customer_id="customer-safe",
    )

    assert response["text"] == UNGROUNDED_ORDER_SUBMISSION_FALLBACK
    assert session_manager.history == [
        {
            "role": "assistant",
            "content": [{"text": UNGROUNDED_ORDER_SUBMISSION_FALLBACK}],
        },
    ]
    assert raw not in str(session_manager.history)
    assert delivered is not None
    assert delivered.reply == session_manager.history[0]["content"][0]["text"]


def test_valid_submission_proof_uses_exact_confirmation_in_response_and_memory(
    monkeypatch,
):
    raw = "Your order is all set."
    captured = install_fake_agent(
        monkeypatch,
        text=raw,
        tool_calls=[
            {
                "tool_name": "confirm_order",
                "success": True,
                "is_write": True,
                "result": {
                    "success": True,
                    "user_message": "The order was submitted.",
                    "data": {
                        "status": "submitted_to_restaurant",
                        "order_id": ORDER_ID,
                    },
                    "agent": {
                        "submitted_order_id": ORDER_ID,
                        "submission_confirmation": SUBMISSION_CONFIRMATION,
                    },
                    "grounding": {
                        "transactional_effects": ["order_submitted"],
                    },
                },
            }
        ],
    )
    original_invoke = handler.invoke_restaurant_agent

    def invoke_with_draft(message, **kwargs):
        kwargs["agent"].session_manager.append_message(
            {"role": "assistant", "content": [{"text": raw}]},
            kwargs["agent"],
        )
        return original_invoke(message, **kwargs)

    monkeypatch.setattr(handler, "invoke_restaurant_agent", invoke_with_draft)

    response = handler.invoke(runtime_payload(channel="whatsapp"))

    assert response["text"] == SUBMISSION_CONFIRMATION
    session_manager = captured["session_manager"].session_manager
    assert session_manager.history == [
        {
            "role": "assistant",
            "content": [{"text": SUBMISSION_CONFIRMATION}],
        },
    ]
    delivered = whatsapp_reply_from_response(
        response,
        request_id="request-safe",
        session_id="session-safe",
        customer_id="customer-safe",
    )
    assert delivered is not None
    assert delivered.reply == session_manager.history[0]["content"][0]["text"]
    assert delivered.submitted_order_id == ORDER_ID


def test_existing_submitted_order_status_read_is_allowed_before_memory_commit(
    monkeypatch,
):
    captured = install_fake_agent(
        monkeypatch,
        text=STATUS_MESSAGE,
        tool_calls=[
            {
                "tool_name": "get_order_status",
                "success": True,
                "is_write": False,
                "result": {
                    "success": True,
                    "data": {
                        "order": {
                            "order_id": ORDER_ID,
                            "status": "submitted_to_restaurant",
                        }
                    },
                    "user_message": STATUS_MESSAGE,
                    "agent": {
                        "selected_order_id": ORDER_ID,
                        "status_message": STATUS_MESSAGE,
                    },
                },
            }
        ],
    )

    response = handler.invoke(runtime_payload(channel="whatsapp"))

    assert response["text"] == STATUS_MESSAGE
    session_manager = captured["session_manager"].session_manager
    assert session_manager.history[-1]["content"][0]["text"] == STATUS_MESSAGE


def test_failed_confirm_order_cannot_be_committed_as_success(monkeypatch):
    raw = "Your order has been submitted successfully."
    failure = "The order could not be submitted."
    captured = install_fake_agent(
        monkeypatch,
        text=raw,
        tool_calls=[
            {
                "tool_name": "confirm_order",
                "success": False,
                "is_write": True,
                "result": {
                    "success": False,
                    "user_message": failure,
                },
            }
        ],
    )

    response = handler.invoke(runtime_payload(channel="whatsapp"))

    assert response["text"] == failure
    session_manager = captured["session_manager"].session_manager
    assert session_manager.history[-1]["content"][0]["text"] == failure
    assert raw not in str(session_manager.history)


def test_memory_commit_failure_never_persists_raw_assistant_draft(monkeypatch):
    class FailingMemorySessionManager(FakeMemorySessionManager):
        def append_message(self, message, agent, **kwargs):
            if message.get("role") == "assistant":
                raise RuntimeError("memory commit unavailable")
            super().append_message(message, agent, **kwargs)

    monkeypatch.setattr(
        handler,
        "load_agentcore_memory_integration",
        lambda: (FakeMemoryConfig, FailingMemorySessionManager),
    )
    install_fake_agent(monkeypatch, text="Raw undelivered")

    with pytest.raises(RuntimeError, match="memory commit unavailable"):
        handler.invoke(runtime_payload(channel="whatsapp"))

    assert "Raw undelivered" not in str(FakeMemorySessionManager.history_by_session)


def test_handler_invokes_restaurant_agent_with_agentcore_memory(monkeypatch):
    captured = install_fake_agent(
        monkeypatch,
        text="Ready.",
        tool_calls=[
            {
                "tool_name": "search_menu",
                "success": True,
                "is_write": False,
                "result": {"success": True},
            }
        ],
    )

    response = handler.invoke(runtime_payload())

    assert response["text"] == "Ready."
    assert response["tool_calls"][0]["tool_name"] == "search_menu"
    assert response["memory"] == {
        "memory_id": "memory-1",
        "actor_id": "customer-1",
        "session_id": "session-1",
    }
    assert FakeMemoryConfig.created == [
        {
            "memory_id": "memory-1",
            "actor_id": "customer-1",
            "session_id": "session-1",
            "batch_size": 1,
        }
    ]
    assert captured["agent_session_id"] == "session-1"
    assert captured["request_id"] == "req-trusted"
    assert FakeMemorySessionManager.closed == [FakeMemorySessionManager.created[0]]


def test_handler_uses_versioned_agentcore_memory_session_for_whatsapp(monkeypatch):
    captured = install_fake_agent(monkeypatch, text="Ready.")
    monkeypatch.setattr(handler.time, "time", lambda: 216000)
    monkeypatch.setattr(
        handler,
        "get_agentcore_runtime_settings",
        lambda: settings(whatsapp_memory_namespace="wa-clean-v3"),
    )

    response = handler.invoke(
        runtime_payload(
            agent_session_id="whatsapp-session-1",
            channel="whatsapp",
        )
    )

    assert captured["agent_session_id"] == "whatsapp-session-1"
    assert response["memory"]["session_id"] == "wa-clean-v3-whatsapp-session-1-b10"


def test_runtime_request_accepts_missing_request_id():
    payload = runtime_payload()
    payload.pop("request_id")

    request = RuntimeRequest.model_validate(payload)

    assert request.request_id is None


@pytest.mark.parametrize("task", ["classify_order_intent", "classify_whatsapp_turn"])
def test_runtime_request_rejects_removed_classifier_tasks(task):
    with pytest.raises(ValidationError):
        RuntimeRequest.model_validate(runtime_payload(task=task))


def test_handler_forwards_missing_request_id_without_substitute(monkeypatch):
    captured = install_fake_agent(monkeypatch, text="ok")
    payload = runtime_payload()
    payload.pop("request_id")

    handler.invoke(payload)

    assert captured["request_id"] is None


def test_handler_uses_user_id_as_actor_when_customer_id_missing(monkeypatch):
    install_fake_agent(monkeypatch, text="ok")

    response = handler.invoke(runtime_payload(customer_id=None))

    assert FakeMemoryConfig.created[0]["actor_id"] == "user-1"
    assert response["memory"]["actor_id"] == "user-1"


def test_same_session_id_restores_conversation_history(monkeypatch):
    observed_history_lengths = []

    class FakeAgent:
        def __init__(self, session_manager):
            self.session_manager = session_manager

    def invoke(message, **kwargs):
        history = kwargs["agent"].session_manager.history
        observed_history_lengths.append(len(history))
        history.append(message)
        return SimpleNamespace(
            message={"content": [{"text": f"turn {len(history)}"}]},
            tool_calls=[],
        )

    monkeypatch.setattr(
        handler,
        "build_restaurant_agent",
        lambda *, session_manager: FakeAgent(session_manager),
    )
    monkeypatch.setattr(handler, "invoke_restaurant_agent", invoke)
    monkeypatch.setattr(
        handler,
        "agent_result_text",
        lambda result: result.message["content"][0]["text"],
    )

    first = handler.invoke(runtime_payload(message="first", agent_session_id="same"))
    second = handler.invoke(runtime_payload(message="second", agent_session_id="same"))

    assert observed_history_lengths == [0, 1]
    assert first["text"] == "turn 1"
    assert second["text"] == "turn 2"
    assert FakeMemorySessionManager.history_by_session["same"] == ["first", "second"]


def test_missing_agentcore_memory_id_fails_safely_in_production(monkeypatch):
    monkeypatch.setattr(
        handler,
        "get_agentcore_runtime_settings",
        lambda: settings(memory_id=""),
    )

    with pytest.raises(RuntimeError, match="AGENTCORE_MEMORY_ID is required"):
        handler.invoke(runtime_payload())

    assert FakeMemoryConfig.created == []


def test_session_manager_cleanup_occurs_after_invocation_failure(monkeypatch):
    install_fake_agent(monkeypatch, text="unused")

    def fail_invoke(message, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(handler, "invoke_restaurant_agent", fail_invoke)

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

    assert os.environ["SESSION_TOKEN_SECRET"].endswith(":us-east-1:secret")


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

    assert client.get("/ping").json() == {"status": "ok"}
    response = client.post(
        "/invocations",
        json={
            "message": "hello",
            "user_id": "user-1",
            "agent_session_id": "session-1",
            "branch_id": "default",
        },
    )

    assert response.status_code == 200
    assert response.json()["text"] == "handled hello"


def test_whatsapp_no_tool_turn_cannot_claim_unsatisfied_continuation(monkeypatch):
    """The runtime must forward continuation into deterministic grounding."""
    from src.agent.continuation import TransactionalContinuation

    continuation = TransactionalContinuation(
        scope="order",
        resource_id="ORD-1",
        state="awaiting_fulfillment_method",
        required_effect="fulfillment_saved",
        required_input="fulfillment_method",
        valid_next_actions=("update_order_flow:set_takeaway",),
        offered_options=(),
        pending_prompt="Would you like delivery or takeaway?",
    )
    raw = "Fulfillment Method: Takeaway. Your order is ready for pickup."
    messages = []

    def invoke(message, **kwargs):
        messages.append(message)
        return SimpleNamespace(
            message={"content": [{"text": raw}]},
            tool_calls=[],
            continuation=continuation,
        )

    monkeypatch.setattr(
        handler,
        "build_restaurant_agent",
        lambda *, session_manager: SimpleNamespace(),
    )
    monkeypatch.setattr(handler, "invoke_restaurant_agent", invoke)
    monkeypatch.setattr(handler, "agent_result_text", lambda result: raw)

    response = handler.invoke(runtime_payload(channel="whatsapp"))

    assert "ready for pickup" not in response["text"]
    assert response["text"] == "Would you like delivery or takeaway?"
    assert response["grounding_source"] == "authoritative_continuation"
    assert (
        response["grounding_rejection_reason"]
        == "required_effect_not_satisfied"
    )
    assert messages == ["hello"]
    history = next(iter(FakeMemorySessionManager.history_by_session.values()))
    assert history[-1]["content"][0]["text"] == "Would you like delivery or takeaway?"
