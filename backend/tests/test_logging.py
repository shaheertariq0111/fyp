import json
import logging
import sys

from src.infrastructure.logging import JsonFormatter, configure_logging


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


def test_json_formatter_includes_safe_fields_and_omits_unapproved_fields():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-1"
    record.http_request_id = "http-1"
    record.user_id = "user-1"
    record.agent_session_id = "session-1"
    record.route = "/api/chat"
    record.status_code = 200
    record.payload_shape = {
        "detected_message_type": "audio",
        "messages_array_exists": True,
    }
    record.password = "do-not-log"
    record.session_token = "do-not-log"
    record.conversation_text = "do-not-log"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["request_id"] == "req-1"
    assert payload["http_request_id"] == "http-1"
    assert payload["actor_id"] == "user-1"
    assert payload["agent_session_id"] == "session-1"
    assert payload["route"] == "/api/chat"
    assert payload["status_code"] == 200
    assert payload["payload_shape"] == {
        "detected_message_type": "audio",
        "messages_array_exists": True,
    }
    assert "password" not in payload
    assert "session_token" not in payload
    assert "conversation_text" not in payload


def test_json_formatter_omits_exception_traceback_and_exception_message():
    try:
        raise RuntimeError("secret nested request body")
    except RuntimeError:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["exception_type"] == "RuntimeError"
    assert "exception" not in payload
    assert "secret nested request body" not in json.dumps(payload)


def test_json_formatter_includes_phase_one_grounding_and_comparison_fields():
    fields = {
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
        "boundary_metadata_issue": "grounding_source_missing",
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
        "runtime_grounding_source": "conversation",
        "runtime_grounding_rejection_reason": "none",
        "backend_grounding_source": "conversation",
        "backend_grounding_rejection_reason": "none",
        "runtime_backend_grounding_agree": True,
        "backend_changed_runtime_text": False,
        "runtime_claim_assessment_present": True,
        "runtime_grounding_metadata_present": True,
        "assessment_transport_status": "present_valid",
        "runtime_backend_expected_action_agree": True,
        "runtime_backend_required_effect_agree": True,
    }

    payload = json.loads(JsonFormatter().format(_record(**fields)))

    for key, value in fields.items():
        expected = list(value) if isinstance(value, tuple) else value
        assert payload[key] == expected


def test_json_formatter_includes_phase_one_state_fields():
    fields = {
        "contract_present": True,
        "existing_contract_present": True,
        "new_contract_produced": True,
        "existing_option_count": 2,
        "produced_option_count": 3,
        "available_option_count": 2,
        "required_effect_present": True,
        "required_effect": "item_selected",
        "prior_required_effect_satisfied": False,
        "contract_created": False,
        "contract_retained": True,
        "contract_replaced": False,
        "state_cleared": False,
        "state_clear_reason": "none",
        "state_action": "contract_retained_existing_requirement",
        "state_age_ms": 1250.5,
    }

    payload = json.loads(JsonFormatter().format(_record(**fields)))

    for key, value in fields.items():
        assert payload[key] == value


def test_json_formatter_still_excludes_unapproved_sensitive_extras():
    excluded = {
        "unknown_extra": "not-approved",
        "customer_message": "private text",
        "assistant_message": "private reply",
        "prompt": "private prompt",
        "classifier_payload": {"text": "private"},
        "claim_assessment_payload": {"claim": "private"},
        "tool_result_payload": {"result": "private"},
        "order_id": "order-private",
        "ticket_id": "ticket-private",
        "cart_id": "cart-private",
        "product_id": "product-private",
        "option_id": "option-private",
        "entity_id": "entity-private",
        "immutable_fact_values": ["private"],
        "offered_options": [{"id": "product-private"}],
    }

    payload = json.loads(JsonFormatter().format(_record(**excluded)))

    assert not excluded.keys() & payload.keys()


def test_configure_logging_suppresses_url_bearing_access_loggers():
    root = logging.getLogger()
    logger_names = ("httpx", "httpcore", "uvicorn.access")

    original_handlers = list(root.handlers)
    original_root_level = root.level
    original_logger_levels = {
        name: logging.getLogger(name).level
        for name in logger_names
    }

    try:
        configure_logging("INFO")

        for name in logger_names:
            assert logging.getLogger(name).level >= logging.WARNING
    finally:
        root.handlers.clear()
        for handler in original_handlers:
            root.addHandler(handler)
        root.setLevel(original_root_level)

        for name, level in original_logger_levels.items():
            logging.getLogger(name).setLevel(level)
