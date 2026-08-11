import json
import logging
import sys
from datetime import datetime, timezone

from src.infrastructure.logging import SAFE_LOG_FIELDS as _BACKEND_SAFE_LOG_FIELDS

# Single source of truth for allowed field names, imported from the backend so the
# two processes can never drift apart silently (see backend/src/infrastructure/logging.py).
#
# One deliberate, documented exception: exception_message carries a raw str(exc)
# (set in backend/src/agent/tools.py and backend/src/agent_client/agentcore.py) which
# is unbounded free text, and this runtime sits closer to raw customer/model
# interaction than backend's own infrastructure code. Excluded on purpose.
SAFE_LOG_FIELDS = tuple(
    field for field in _BACKEND_SAFE_LOG_FIELDS if field != "exception_message"
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in SAFE_LOG_FIELDS:
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
