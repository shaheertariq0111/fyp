import json
import logging

from agent_runtime.logging import JsonFormatter


def _record(**extra):
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="completed",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_serializes_existing_and_phase_one_fields():
    fields = {
        "event": "whatsapp_grounding_completed",
        "request_id": "req-1",
        "classifier_name": "assistant_claims",
        "classifier_run_id": "random-run-1",
        "classifier_timeout_seconds": 3.0,
        "semantic_classifier_total_duration_ms": 3001.25,
        "semantic_classifier_queue_wait_ms": 0.25,
        "semantic_classifier_model_duration_ms": 250.5,
        "semantic_classifier_model_elapsed_at_timeout_ms": 3000.75,
        "semantic_classifier_execution_started": True,
        "authoritative_fast_path": False,
        "grounding_protocol_version": 2,
        "grounding_source": "conversation",
        "grounding_rejection_reason": "required_effect_not_supported",
        "assessment_origin": "model",
        "semantic_classifier_status": "completed",
        "tool_call_count": 2,
        "successful_tool_call_count": 2,
        "write_tool_call_count": 1,
        "successful_write_count": 1,
        "tool_evidence_count": 1,
        "claimed_effect_count": 1,
        "supported_effect_count": 1,
        "unsupported_effect_count": 0,
        "claimed_effects": ("item_selected",),
        "supported_effects": ("item_selected",),
        "unsupported_effects": (),
        "claimed_domain_count": 1,
        "supported_domain_count": 1,
        "unsupported_domain_count": 0,
        "claimed_domains": ("cart",),
        "supported_domains": ("cart",),
        "unsupported_domains": (),
        "required_effect_present": True,
        "required_effect": "item_selected",
        "required_effect_supported": True,
        "customer_requests_required_effect": True,
        "available_option_count": 2,
        "selected_option_present": True,
        "selected_option_contract_evaluated": True,
        "selected_option_in_contract": True,
        "authoritative_claims_supported": True,
        "immutable_fact_claim_count": 1,
        "immutable_fact_mismatch_count": 0,
        "presentation_item_count": 2,
        "presentation_limit": 5,
        "exception_type": "TimeoutError",
    }

    payload = json.loads(JsonFormatter().format(_record(**fields)))

    for key, value in fields.items():
        expected = list(value) if isinstance(value, tuple) else value
        assert payload[key] == expected


def test_json_formatter_rejects_unknown_sensitive_extras():
    excluded = {
        "unknown_extra": "not-approved",
        "customer_message": "private text",
        "assistant_message": "private reply",
        "prompt": "private prompt",
        "classifier_payload": {"text": "private"},
        "claim_assessment_payload": {"claim": "private"},
        "tool_result_payload": {"result": "private"},
        "customer_name": "Private Name",
        "phone": "+10000000000",
        "email": "private@example.com",
        "address": "private address",
        "order_id": "order-private",
        "ticket_id": "ticket-private",
        "cart_id": "cart-private",
        "product_id": "product-private",
        "option_id": "option-private",
        "entity_id": "entity-private",
        "immutable_fact_values": ["private"],
        "offered_options": [{"id": "product-private"}],
        "exception_message": "private exception detail",
    }

    payload = json.loads(JsonFormatter().format(_record(**excluded)))

    assert not excluded.keys() & payload.keys()
