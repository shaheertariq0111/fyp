import json
import logging
import sys

from src.infrastructure.logging import JsonFormatter, configure_logging


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
