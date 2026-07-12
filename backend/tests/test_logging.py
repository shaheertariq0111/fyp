import json
import logging
import sys

from src.infrastructure.logging import JsonFormatter


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
