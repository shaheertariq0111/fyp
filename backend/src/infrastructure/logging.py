from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


SAFE_LOG_FIELDS = (
    "event",
    "http_request_id",
    "request_id",
    "actor_id",
    "user_id",
    "customer_id",
    "agent_session_id",
    "session_id",
    "route",
    "method",
    "status_code",
    "response_time_ms",
    "agent_request_status",
    "agentcore_invocation_status",
    "tool_name",
    "tool_success",
    "is_write",
    "error_code",
    "aws_error_code",
    "channel",
    "bedrock_response_time_ms",
    "dynamodb_operation",
    "dynamodb_table",
    "exception_type",
    "exception_message",
    "payload_shape",
    "voice_job_id",
    "voice_job_state",
    "failure_stage",
    "retryable",
    "duplicate",
    "receive_count",
    "cleanup_after_valid_transcript",
    "audio_media_hostname",
    "source_audio_bytes",
    "generated_audio_bytes",
    "synthesis_duration_ms",
    "conversion_duration_ms",
    "outbound_status",
    "provider_message_id",
    "classifier_name",
    "classifier_run_id",
    "classifier_timeout_seconds",
    "semantic_classifier_total_duration_ms",
    "semantic_classifier_queue_wait_ms",
    "semantic_classifier_model_duration_ms",
    "semantic_classifier_model_elapsed_at_timeout_ms",
    "semantic_classifier_execution_started",
    "authoritative_fast_path",
    "grounding_source",
    "grounding_rejection_reason",
    "assessment_origin",
    "semantic_classifier_status",
    "boundary_metadata_issue",
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
    "runtime_grounding_source",
    "runtime_grounding_rejection_reason",
    "backend_grounding_source",
    "backend_grounding_rejection_reason",
    "runtime_backend_grounding_agree",
    "backend_changed_runtime_text",
    "runtime_claim_assessment_present",
    "runtime_grounding_metadata_present",
    "assessment_transport_status",
    "contract_present",
    "existing_contract_present",
    "new_contract_produced",
    "existing_option_count",
    "produced_option_count",
    "prior_required_effect_satisfied",
    "contract_created",
    "contract_retained",
    "contract_replaced",
    "state_cleared",
    "state_clear_reason",
    "state_action",
    "state_age_ms",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in SAFE_LOG_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if getattr(record, "actor_id", None) is None and getattr(record, "user_id", None) is not None:
            payload["actor_id"] = getattr(record, "user_id")
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())

    # These libraries can emit complete request URLs, including bearer
    # credentials stored in paths or signed media query parameters.
    for logger_name in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
