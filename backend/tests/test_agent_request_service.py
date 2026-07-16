from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.services.agent_request_service import AgentRequestService


class MemoryAgentRequestRepository:
    def __init__(self):
        self.data = {}

    def create(self, request):
        assert request["request_id"] not in self.data
        self.data[request["request_id"]] = deepcopy(request)

    def get(self, request_id):
        request = self.data.get(request_id)
        return deepcopy(request) if request else None

    def save(self, request):
        self.data[request["request_id"]] = deepcopy(request)


def service():
    return AgentRequestService(
        MemoryAgentRequestRepository(),
        SimpleNamespace(agent_request_ttl_hours=2),
    )


def test_agent_request_service_tracks_processing_completed_and_ttl():
    requests = service()

    processing = requests.start_processing(
        actor_id="cust-1",
        session_id="web-1",
        message="hello",
        channel="web",
        request_payload={"message": "hello"},
    )
    completed = requests.complete(processing["request_id"], {"text": "hi"})

    assert processing["request_id"].startswith("req-")
    assert processing["status"] == "processing"
    assert processing["actor_id"] == "cust-1"
    assert processing["session_id"] == "web-1"
    assert processing["expires_at"] > 0
    assert completed["status"] == "completed"
    assert completed["response"] == {"text": "hi"}
    assert requests.get(processing["request_id"])["status"] == "completed"


def test_agent_request_service_fails_with_safe_error_payload():
    requests = service()
    processing = requests.start_processing(
        actor_id="cust-1",
        session_id="web-1",
        message="hello",
        channel="web",
        request_payload={"message": "hello"},
    )

    failed = requests.fail(
        processing["request_id"],
        error_code="AGENT_INVOCATION_FAILED",
        message="The request could not be completed.",
    )

    assert failed["status"] == "failed"
    assert failed["error_code"] == "AGENT_INVOCATION_FAILED"
    assert failed["failure_message"] == "The request could not be completed."


def test_agent_request_service_missing_request_raises_structured_code():
    with pytest.raises(ValueError, match="AGENT_REQUEST_NOT_FOUND"):
        service().complete("missing", {"text": "hi"})
