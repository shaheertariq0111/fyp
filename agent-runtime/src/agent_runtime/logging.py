import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "event",
            "http_request_id",
            "request_id",
            "actor_id",
            "agent_session_id",
            "session_id",
            "channel",
            "tool_name",
            "tool_success",
            "is_write",
            "error_code",
            "agentcore_invocation_status",
            "agent_request_status",
            "bedrock_response_time_ms",
            "response_time_ms",
            "classifier_name",
            "classifier_run_id",
            "classifier_timeout_seconds",
            "semantic_classifier_total_duration_ms",
            "semantic_classifier_queue_wait_ms",
            "semantic_classifier_model_duration_ms",
            "semantic_classifier_model_elapsed_at_timeout_ms",
            "semantic_classifier_execution_started",
            "authoritative_fast_path",
            "grounding_protocol_version",
            "grounding_source",
            "grounding_rejection_reason",
            "assessment_origin",
            "semantic_classifier_status",
            "tool_call_count",
            "successful_tool_call_count",
            "write_tool_call_count",
            "successful_write_count",
            "tool_evidence_count",
            "claimed_effect_count",
            "supported_effect_count",
            "unsupported_effect_count",
            "claimed_effects",
            "supported_effects",
            "unsupported_effects",
            "claimed_domain_count",
            "supported_domain_count",
            "unsupported_domain_count",
            "claimed_domains",
            "supported_domains",
            "unsupported_domains",
            "required_effect_present",
            "required_effect",
            "required_effect_supported",
            "customer_requests_required_effect",
            "available_option_count",
            "selected_option_present",
            "selected_option_contract_evaluated",
            "selected_option_in_contract",
            "authoritative_claims_supported",
            "immutable_fact_claim_count",
            "immutable_fact_mismatch_count",
            "presentation_item_count",
            "presentation_limit",
            "exception_type",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
