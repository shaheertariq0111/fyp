from types import SimpleNamespace

from botocore.exceptions import ClientError
import pytest

from src.models.conversation_contracts import OptionContract
from src.services.agent_request_processor import (
    AgentRequestProcessor,
    PreparedAgentRequest,
    build_response_builder,
)
from src.agent.response_grounding import UNGROUNDED_TRANSACTION_FALLBACK


class ForbiddenCall:
    def __call__(self, *_args, **_kwargs):
        raise AssertionError("completed AgentRequest must not invoke dependencies")


class DurableRequests:
    def __init__(self):
        self.record = {
            "request_id": "request-safe",
            "status": "processing",
            "invocation_state": "not_started",
        }
        self.claim_calls = 0

    def claim_invocation(self, _request_id):
        self.claim_calls += 1
        if (
            self.record["status"] != "processing"
            or self.record["invocation_state"] != "not_started"
        ):
            return False
        self.record["invocation_state"] = "invoking"
        return True

    def fail(self, _request_id, *, error_code, message):
        self.record.update({
            "status": "failed",
            "error_code": error_code,
            "failure_message": message,
        })
        return self.record

    def fail_before_invocation(self, _request_id, *, error_code, message):
        assert self.record["status"] == "processing"
        assert self.record["invocation_state"] == "invoking"
        self.record.update({
            "status": "failed",
            "invocation_state": "failed",
            "error_code": error_code,
            "failure_message": message,
        })
        return self.record

    def get(self, _request_id):
        return self.record

    def mark_invocation_ambiguous(self, _request_id):
        if self.record["invocation_state"] != "invoking":
            return False
        self.record["invocation_state"] = "ambiguous"
        return True

    def complete(self, _request_id, response):
        self.record.update({
            "status": "completed",
            "invocation_state": "completed",
            "response": response,
        })
        return self.record


def prepared_request(record):
    return PreparedAgentRequest(
        payload=SimpleNamespace(message="safe", branch_id=None),
        record=record,
        context=SimpleNamespace(
            channel="whatsapp",
            user_id="customer-safe",
            customer_id="customer-safe",
            agent_session_id="session-safe",
            customer_name=None,
            customer_phone=None,
        ),
        identity_state={},
    )


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


def test_pre_invocation_session_state_failure_is_not_logged_as_agentcore_failure(
    caplog,
):
    class Sessions:
        calls = 0

        def get_whatsapp_order_state(self, *_args):
            self.calls += 1
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "private"}},
                "GetItem",
            )

    requests = DurableRequests()
    sessions = Sessions()
    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(
            agent_requests=requests,
            agent_sessions=sessions,
        ),
        agent_client_provider=ForbiddenCall(),
        identity_resolver=ForbiddenCall(),
        response_builder=ForbiddenCall(),
    )
    prepared = prepared_request(requests.record)

    with caplog.at_level("ERROR"):
        result = processor.invoke_prepared(prepared)

    assert result.outcome == "failed"
    assert requests.record["status"] == "failed"
    assert requests.record["invocation_state"] == "failed"
    assert requests.claim_calls == 1
    assert any(
        getattr(record, "event", None) == "agent_pre_invocation_state_failed"
        for record in caplog.records
    )
    assert not any(
        getattr(record, "event", None) == "agentcore_invocation_failed"
        for record in caplog.records
    )
    assert "private" not in caplog.text

    resumed = processor.invoke_prepared(prepared)

    assert resumed.outcome == "failed"
    assert requests.claim_calls == 1
    assert sessions.calls == 1


def test_actual_agentcore_failure_retains_ambiguous_replay_protection(caplog):
    requests = DurableRequests()
    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(
            agent_requests=requests,
            agent_sessions=SimpleNamespace(
                get_whatsapp_order_state=lambda *_args: {}
            ),
        ),
        agent_client_provider=lambda: SimpleNamespace(
            invoke=lambda _request: (_ for _ in ()).throw(
                RuntimeError("private runtime detail")
            )
        ),
        identity_resolver=ForbiddenCall(),
        response_builder=ForbiddenCall(),
    )

    with caplog.at_level("ERROR"):
        result = processor.invoke_prepared(
            prepared_request(requests.record),
            ambiguous_on_invocation_failure=True,
        )

    assert result.outcome == "ambiguous"
    assert requests.record["status"] == "processing"
    assert requests.record["invocation_state"] == "ambiguous"
    assert requests.claim_calls == 1
    assert any(
        getattr(record, "event", None) == "agentcore_invocation_failed"
        for record in caplog.records
    )
    assert "private runtime detail" not in caplog.text


def test_successful_invocation_still_claims_and_completes_request():
    requests = DurableRequests()
    invocation = SimpleNamespace(raw_result={})
    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(
            agent_requests=requests,
            agent_sessions=SimpleNamespace(
                get_whatsapp_order_state=lambda *_args: {}
            ),
        ),
        agent_client_provider=lambda: SimpleNamespace(
            invoke=lambda _request: invocation
        ),
        identity_resolver=ForbiddenCall(),
        response_builder=lambda *_args: SimpleNamespace(
            model_dump=lambda **_kwargs: {"text": "safe response"}
        ),
    )

    result = processor.invoke_prepared(prepared_request(requests.record))

    assert result.outcome == "completed"
    assert requests.claim_calls == 1
    assert requests.record["status"] == "completed"
    assert requests.record["invocation_state"] == "completed"


def test_search_results_persist_expected_item_selection_write_and_options(caplog):
    class Sessions:
        def __init__(self):
            self.state = {}

        def save_whatsapp_order_state(self, customer_id, session_id, **kwargs):
            self.state = {
                "offered_menu_items": kwargs["offered_menu_items"],
                "whatsapp_required_effect": kwargs["required_effect"],
            }

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
        "required_effect": "item_selected",
        "tool_calls": [{
            "tool_name": "any_menu_capability", "success": True,
            "result": {
                "success": True,
                "data": {"items": [
                    {"product_id": "item-1", "name": "First Item"},
                    {"product_id": "item-2", "name": "Second Item"},
                ]},
                "grounding": {
                    "authoritative_domains": ["menu"],
                    "required_next_effect": "item_selected",
                    "offered_options": [
                        {"id": "item-1", "label": "First Item"},
                        {"id": "item-2", "label": "Second Item"},
                    ],
                },
            },
        }],
    })

    processor._persist_whatsapp_grounding_state(context, invocation)
    state = processor._whatsapp_grounding_state(context)

    assert state == {
        "expected_write_tool": "start_cart_item_customization",
        "required_effect": "item_selected",
        "available_options": [
            {"id": "item-1", "label": "First Item"},
            {"id": "item-2", "label": "Second Item"},
        ],
    }
    loaded = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "whatsapp_grounding_state_loaded"
    )
    assert loaded.typed_contract_present is False
    assert loaded.required_effect_present is True
    assert loaded.required_effect == "item_selected"
    assert loaded.legacy_option_count == 2
    transition = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "whatsapp_grounding_state_transition"
    )
    assert transition.state_action == "contract_created"
    assert transition.contract_created is True
    assert transition.contract_replaced is False
    assert transition.produced_option_count == 2


def test_typed_non_item_selection_contract_logs_present_independent_of_legacy_items(caplog):
    fulfillment_contract = OptionContract(
        contract_id="fulfillment-contract",
        contract_version=1,
        required_effect="fulfillment_saved",
        consumer_capability="update_order_flow",
        source_capability="get_order_status",
        source_request_id="request-1",
        scope={"order_id": "ORD-1"},
        options=[
            {"id": "set_delivery", "label": "Delivery"},
            {"id": "set_takeaway", "label": "Takeaway"},
        ],
        created_at="2026-08-10T08:00:00+00:00",
        expires_at="2026-08-10T08:30:00+00:00",
    )

    class Sessions:
        def get_active_option_contract(self, customer_id, session_id):
            return fulfillment_contract

        def get_whatsapp_order_state(self, customer_id, session_id):
            # Stale legacy menu-selection state left over from an earlier,
            # unrelated turn; must not be conflated with the typed contract.
            return {
                "offered_menu_items": [
                    {"product_id": "item-1", "name": "First Item"},
                    {"product_id": "item-2", "name": "Second Item"},
                    {"product_id": "item-3", "name": "Third Item"},
                ],
                "whatsapp_required_effect": "item_selected",
            }

    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(agent_sessions=Sessions()),
        agent_client_provider=ForbiddenCall(),
        identity_resolver=ForbiddenCall(),
        response_builder=ForbiddenCall(),
    )
    context = SimpleNamespace(
        channel="whatsapp", customer_id="customer-1",
        user_id="user-1", agent_session_id="session-1",
    )

    with caplog.at_level("INFO"):
        state = processor._whatsapp_grounding_state(context)

    assert state["expected_write_tool"] == "update_order_flow"
    assert state["required_effect"] == "fulfillment_saved"

    loaded = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "whatsapp_grounding_state_loaded"
    )
    assert loaded.typed_contract_present is True
    assert loaded.legacy_option_count == 3
    assert not hasattr(loaded, "contract_present")
    assert not hasattr(loaded, "available_option_count")


def test_legacy_whatsapp_menu_state_is_inferred_only_at_orchestration_boundary():
    class Sessions:
        def get_whatsapp_order_state(self, customer_id, session_id):
            return {
                "offered_menu_items": [
                    {"product_id": "item-1", "name": "First Item"},
                ],
            }

    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(agent_sessions=Sessions()),
        agent_client_provider=ForbiddenCall(),
        identity_resolver=ForbiddenCall(),
        response_builder=ForbiddenCall(),
    )
    context = SimpleNamespace(
        channel="whatsapp", customer_id="customer-1",
        user_id="user-1", agent_session_id="session-1",
    )

    assert processor._whatsapp_grounding_state(context) == {
        "expected_write_tool": "start_cart_item_customization",
        "required_effect": "item_selected",
        "available_options": [{"id": "item-1", "label": "First Item"}],
    }


def test_successful_expected_write_clears_persisted_menu_selection_state(caplog):
    class Sessions:
        state = {
            "offered_menu_items": [{"product_id": "item-1", "name": "First"}],
            "whatsapp_required_effect": "item_selected",
        }

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
            "tool_name": "any_selection_capability",
            "success": True,
            "result": {
                "success": True,
                "user_message": "Choose a size.",
                "grounding": {
                    "transactional_effects": ["item_selected"],
                },
            },
        }]}),
        prior_required_effect="item_selected",
    )

    assert sessions.state == {}
    transition = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "whatsapp_grounding_state_transition"
    )
    assert transition.state_action == "contract_cleared_required_effect_satisfied"
    assert transition.state_cleared is True
    assert transition.state_clear_reason == "required_effect_satisfied"


def test_informational_search_does_not_replace_pending_item_choices(caplog):
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
            "required_effect": "item_selected",
            "tool_calls": [{
                "tool_name": "any_menu_capability", "success": True,
                "result": {
                    "success": True,
                    "data": {"items": [
                        {"product_id": "clarification-item", "name": "Clarification"},
                    ]},
                    "grounding": {
                        "authoritative_domains": ["menu"],
                        "required_next_effect": "item_selected",
                        "offered_options": [{
                            "id": "clarification-item",
                            "label": "Clarification",
                        }],
                    },
                },
            }],
        }),
        prior_expected_write_tool="start_cart_item_customization",
        prior_required_effect="item_selected",
        prior_available_option_count=1,
    )

    assert sessions.state["offered_menu_items"] == original
    transition = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "whatsapp_grounding_state_transition"
    )
    assert transition.state_action == "contract_retained_existing_requirement"
    assert transition.contract_retained is True
    assert transition.contract_replaced is False
    assert transition.existing_option_count == 1
    assert transition.produced_option_count == 1


def _grounding_response_builder():
    return build_response_builder(lambda: SimpleNamespace(
        carts=SimpleNamespace(get_active_cart=ForbiddenCall()),
        orders=SimpleNamespace(get_order_status=ForbiddenCall()),
    ))


def _grounding_context():
    return SimpleNamespace(
        channel="whatsapp",
        user_id="customer-1",
        customer_id="customer-1",
        agent_session_id="session-1",
    )


def test_backend_grounding_logs_runtime_agreement_without_public_diagnostics(caplog):
    response = _grounding_response_builder()(
        _grounding_context(),
        {"customer": {}},
        SimpleNamespace(
            text="How can I help?",
            raw_result={
                "tool_calls": [],
                "claim_assessment": {
                    "claims_transactional_progression": False,
                },
                "grounding_source": "conversation",
                "assessment_origin": "model",
                "semantic_classifier_status": "completed",
            },
        ),
    )

    completed = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "backend_grounding_completed"
    )
    assert response.text == "How can I help?"
    assert completed.runtime_backend_grounding_agree is True
    assert completed.backend_changed_runtime_text is False
    public = response.model_dump()
    assert "grounding_rejection_reason" not in public
    assert "assessment_origin" not in public
    assert "semantic_classifier_status" not in public


def test_backend_grounding_v2_accepts_no_tool_conversation_without_assessment(caplog):
    response = _grounding_response_builder()(
        _grounding_context(),
        {"customer": {}},
        SimpleNamespace(
            text="How can I help with your order?",
            raw_result={
                "grounding_protocol_version": 2,
                "tool_calls": [],
                "grounding_source": "conversation",
            },
        ),
    )

    completed = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "backend_grounding_completed"
    )
    assert response.text == "How can I help with your order?"
    assert completed.grounding_protocol_version == 2
    assert completed.assessment_transport_status == "not_required_v2"
    assert completed.runtime_backend_grounding_agree is True
    assert completed.runtime_backend_expected_action_agree is True
    assert completed.runtime_backend_required_effect_agree is True
    assert not any(
        getattr(record, "event", None) == "grounding_boundary_metadata_issue"
        for record in caplog.records
    )
    assert not any(
        getattr(record, "assessment_origin", None) == "boundary_missing_synthetic"
        for record in caplog.records
    )


def test_backend_grounding_v2_reapplies_authoritative_failed_write():
    response = _grounding_response_builder()(
        _grounding_context(),
        {"customer": {}},
        SimpleNamespace(
            text="I couldn't complete that change.",
            raw_result={
                "grounding_protocol_version": 2,
                "tool_calls": [{
                    "tool_name": "submit_order",
                    "success": False,
                    "is_write": True,
                    "result": {
                        "success": False,
                        "user_message": "I couldn't complete that change.",
                    },
                    "error_code": "INVALID_ORDER_STATE",
                }],
                "grounding_source": "failed_write",
                "grounding_rejection_reason": "authoritative_write_failed",
            },
        ),
    )

    assert response.text == "I couldn't complete that change."


def test_backend_grounding_logs_runtime_disagreement_without_changing_text(caplog):
    response = _grounding_response_builder()(
        _grounding_context(),
        {"customer": {}},
        SimpleNamespace(
            text="How can I help?",
            raw_result={
                "tool_calls": [],
                "claim_assessment": {
                    "claims_transactional_progression": False,
                },
                "grounding_source": "exact_artifact",
                "assessment_origin": "model",
                "semantic_classifier_status": "completed",
            },
        ),
    )

    mismatch = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "grounding_decision_mismatch"
    )
    assert response.text == "How can I help?"
    assert mismatch.runtime_grounding_source == "exact_artifact"
    assert mismatch.backend_grounding_source == "conversation"
    assert mismatch.backend_changed_runtime_text is False


def test_backend_grounding_labels_missing_assessment_synthetic_origin(caplog):
    response = _grounding_response_builder()(
        _grounding_context(),
        {"customer": {}},
        SimpleNamespace(
            text="Untrusted old-runtime response",
            raw_result={"tool_calls": [], "grounding_source": "conversation"},
        ),
    )

    issue = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "grounding_boundary_metadata_issue"
        and getattr(record, "boundary_metadata_issue", None)
        == "claim_assessment_missing"
    )
    completed = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "backend_grounding_completed"
    )
    assert response.text == UNGROUNDED_TRANSACTION_FALLBACK
    assert issue.assessment_origin == "boundary_missing_synthetic"
    assert completed.assessment_origin == "boundary_missing_synthetic"
    assert completed.backend_grounding_rejection_reason == (
        "unsupported_transactional_effect"
    )


def test_backend_grounding_logs_malformed_assessment_before_existing_failure(caplog):
    with pytest.raises(Exception):
        _grounding_response_builder()(
            _grounding_context(),
            {"customer": {}},
            SimpleNamespace(
                text="Untrusted malformed response",
                raw_result={
                    "tool_calls": [],
                    "claim_assessment": {
                        "claims_transactional_progression": False,
                        "claimed_actions": ["item_added"],
                    },
                    "grounding_source": "conversation",
                },
            ),
        )

    issue = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "grounding_boundary_metadata_issue"
        and getattr(record, "boundary_metadata_issue", None)
        == "claim_assessment_invalid"
    )
    assert issue.exception_type == "ValidationError"
    assert not hasattr(issue, "exception_message")


def test_grounding_state_requirement_mismatch_logs_no_persistence_change(caplog):
    class Sessions:
        def save_whatsapp_order_state(self, *_args, **_kwargs):
            raise AssertionError("mismatched contract must not be persisted")

        def clear_whatsapp_order_state(self, *_args, **_kwargs):
            raise AssertionError("mismatched contract must not clear state")

    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(agent_sessions=Sessions()),
        agent_client_provider=ForbiddenCall(),
        identity_resolver=ForbiddenCall(),
        response_builder=ForbiddenCall(),
    )
    processor._persist_whatsapp_grounding_state(
        SimpleNamespace(
            channel="whatsapp",
            customer_id="customer-1",
            user_id="user-1",
            agent_session_id="session-1",
        ),
        SimpleNamespace(raw_result={
            "required_effect": "checkout_started",
            "tool_calls": [{
                "tool_name": "menu_capability",
                "success": True,
                "result": {
                    "success": True,
                    "grounding": {
                        "required_next_effect": "item_selected",
                        "offered_options": [
                            {"id": "private-item", "label": "Private Item"},
                        ],
                    },
                },
            }],
        }),
    )

    transition = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "whatsapp_grounding_state_transition"
    )
    assert transition.state_action == "contract_not_persisted_requirement_mismatch"
    assert transition.contract_created is False
    assert transition.contract_replaced is False
    assert transition.state_cleared is False
    assert not hasattr(transition, "offered_options")
