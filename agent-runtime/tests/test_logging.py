import json
import logging

from agent_runtime.logging import JsonFormatter


def test_json_formatter_includes_safe_response_selection_fields_only():
    record = logging.LogRecord(
        name="agent_runtime.handler",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="WhatsApp response selected",
        args=(),
        exc_info=None,
    )
    record.event = "whatsapp_response_selected"
    record.grounding_source = "failed_write"
    record.grounding_rejection_reason = "authoritative_write_failed"
    record.tool_call_count = 2
    record.tool_names = ["search_menu", "confirm_order"]
    record.tool_arguments = {"address": "private customer address"}
    record.tool_results = {"user_message": "private backend result"}
    record.customer_text = "private customer message"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["grounding_source"] == "failed_write"
    assert payload["grounding_rejection_reason"] == "authoritative_write_failed"
    assert payload["tool_call_count"] == 2
    assert payload["tool_names"] == ["search_menu", "confirm_order"]
    assert "tool_arguments" not in payload
    assert "tool_results" not in payload
    assert "customer_text" not in payload
    assert "private" not in json.dumps(payload)


def test_json_formatter_preserves_disabled_memory_mode_without_customer_content():
    record = logging.LogRecord(
        name="agent_runtime.handler",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="AgentCore memory mode selected",
        args=(),
        exc_info=None,
    )
    record.event = "agentcore_memory_mode"
    record.channel = "whatsapp"
    record.memory_enabled = False
    record.customer_text = "private customer message"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "agentcore_memory_mode"
    assert payload["channel"] == "whatsapp"
    assert payload["memory_enabled"] is False
    assert "private" not in json.dumps(payload)
