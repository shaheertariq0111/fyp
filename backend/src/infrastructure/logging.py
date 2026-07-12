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
    "channel",
    "bedrock_response_time_ms",
    "dynamodb_operation",
    "dynamodb_table",
    "exception_type",
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
