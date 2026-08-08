from types import SimpleNamespace

from src.services.agent_request_processor import (
    AgentRequestProcessor,
    PreparedAgentRequest,
)


class ForbiddenCall:
    def __call__(self, *_args, **_kwargs):
        raise AssertionError("completed AgentRequest must not invoke dependencies")


def test_completed_request_returns_persisted_structured_response_without_agentcore():
    response = {
        "text": "Persisted reply",
        "tool_calls": [{
            "tool_name": "confirm_order",
            "success": True,
            "is_write": True,
            "result": {
                "success": True,
                "data": {
                    "status": "submitted_to_restaurant",
                    "order_id": "ORD-PERSISTED-123",
                },
            },
        }],
    }
    record = {
        "request_id": "request-safe",
        "status": "completed",
        "response": response,
    }
    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(agent_requests=object()),
        agent_client_provider=ForbiddenCall(),
        identity_resolver=ForbiddenCall(),
        response_builder=ForbiddenCall(),
    )
    prepared = PreparedAgentRequest(
        payload=SimpleNamespace(),
        record=record,
        context=SimpleNamespace(),
        identity_state={},
    )

    result = processor.invoke_prepared(prepared)

    assert result.outcome == "completed"
    assert result.record is record
    assert result.record["response"] is response


def test_search_results_persist_expected_item_selection_write_and_options():
    class Sessions:
        def __init__(self):
            self.state = {}

        def save_whatsapp_order_state(self, customer_id, session_id, **kwargs):
            self.state = {"offered_menu_items": kwargs["offered_menu_items"]}

        def get_whatsapp_order_state(self, customer_id, session_id):
            return self.state

        def clear_whatsapp_order_state(self, customer_id, session_id):
            self.state = {}

    sessions = Sessions()
    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(agent_sessions=sessions),
        agent_client_provider=ForbiddenCall(),
        identity_resolver=ForbiddenCall(),
        response_builder=ForbiddenCall(),
    )
    context = SimpleNamespace(
        channel="whatsapp", customer_id="customer-1",
        user_id="user-1", agent_session_id="session-1",
    )
    invocation = SimpleNamespace(raw_result={
        "expected_write_tool": "start_cart_item_customization",
        "tool_calls": [{
            "tool_name": "search_menu", "success": True,
            "result": {
                "success": True,
                "data": {"items": [
                    {"product_id": "item-1", "name": "First Item"},
                    {"product_id": "item-2", "name": "Second Item"},
                ]},
            },
        }],
    })

    processor._persist_whatsapp_grounding_state(context, invocation)
    state = processor._whatsapp_grounding_state(context)

    assert state == {
        "expected_write_tool": "start_cart_item_customization",
        "available_options": [
            {"id": "item-1", "label": "First Item"},
            {"id": "item-2", "label": "Second Item"},
        ],
    }


def test_successful_expected_write_clears_persisted_menu_selection_state():
    class Sessions:
        state = {"offered_menu_items": [{"product_id": "item-1", "name": "First"}]}

        def clear_whatsapp_order_state(self, customer_id, session_id):
            self.state = {}

    sessions = Sessions()
    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(agent_sessions=sessions),
        agent_client_provider=ForbiddenCall(),
        identity_resolver=ForbiddenCall(),
        response_builder=ForbiddenCall(),
    )
    processor._persist_whatsapp_grounding_state(
        SimpleNamespace(
            channel="whatsapp", customer_id="customer-1",
            user_id="user-1", agent_session_id="session-1",
        ),
        SimpleNamespace(raw_result={"tool_calls": [{
            "tool_name": "start_cart_item_customization",
            "success": True,
            "result": {"success": True, "user_message": "Choose a size."},
        }]}),
    )

    assert sessions.state == {}


def test_informational_search_does_not_replace_pending_item_choices():
    original = [{"product_id": "item-1", "name": "Original Item"}]

    class Sessions:
        def __init__(self):
            self.state = {"offered_menu_items": original}

        def save_whatsapp_order_state(self, customer_id, session_id, **kwargs):
            self.state = {"offered_menu_items": kwargs["offered_menu_items"]}

    sessions = Sessions()
    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(agent_sessions=sessions),
        agent_client_provider=ForbiddenCall(),
        identity_resolver=ForbiddenCall(),
        response_builder=ForbiddenCall(),
    )
    processor._persist_whatsapp_grounding_state(
        SimpleNamespace(
            channel="whatsapp", customer_id="customer-1",
            user_id="user-1", agent_session_id="session-1",
        ),
        SimpleNamespace(raw_result={
            "expected_write_tool": "start_cart_item_customization",
            "tool_calls": [{
                "tool_name": "search_menu", "success": True,
                "result": {"success": True, "data": {"items": [
                    {"product_id": "clarification-item", "name": "Clarification"},
                ]}},
            }],
        }),
        prior_expected_write_tool="start_cart_item_customization",
    )

    assert sessions.state["offered_menu_items"] == original
