from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.services.agent_request_service import AgentRequestService
from src.services.whatsapp_conversation_service import WhatsAppConversationReply


class MemoryAgentRequestRepository:
    def __init__(self):
        self.data = {}
        self.idempotency_claims = []
        self.idempotency_result = True

    def create(self, request):
        assert request["request_id"] not in self.data
        self.data[request["request_id"]] = deepcopy(request)

    def get(self, request_id):
        request = self.data.get(request_id)
        return deepcopy(request) if request else None

    def save(self, request):
        self.data[request["request_id"]] = deepcopy(request)

    def transition_invocation_state(self, request_id, *, expected_state, next_state, updated_at):
        request = self.data.get(request_id)
        if not request or request.get("status") != "processing" or request.get("invocation_state") != expected_state:
            return False
        request["invocation_state"] = next_state
        request["updated_at"] = updated_at
        return True

    def claim_idempotency_key(self, marker, *, now_epoch):
        self.idempotency_claims.append((deepcopy(marker), now_epoch))
        if self.idempotency_result:
            self.data[marker["PK"]] = deepcopy(marker)
        return self.idempotency_result

    def get_idempotency_key(self, message_id):
        marker = self.data.get(f"agentflo-whatsapp-message:{message_id}")
        return deepcopy(marker) if marker else None

    def save_idempotency_key(self, marker):
        self.data[marker["PK"]] = deepcopy(marker)

    def transition_idempotency_delivery_state(
        self,
        message_id,
        *,
        expected_state,
        next_state,
        updated_at,
    ):
        marker = self.data.get(f"agentflo-whatsapp-message:{message_id}")
        if marker is None or marker.get("delivery_state") != expected_state:
            return False
        marker["delivery_state"] = next_state
        marker["updated_at"] = updated_at
        return True

    def complete_delivery_with_receipt_pending(self, message_id, *, updated_at):
        marker = self.data.get(f"agentflo-whatsapp-message:{message_id}")
        if (
            marker is None
            or marker.get("delivery_state") != "outbound_sending"
            or "submitted_order_id" not in marker
        ):
            return False
        marker["delivery_state"] = "completed"
        marker["receipt_activation_state"] = "pending"
        marker["updated_at"] = updated_at
        return True

    def transition_receipt_activation_state(
        self,
        message_id,
        *,
        expected_state,
        next_state,
        updated_at,
    ):
        if (expected_state, next_state) not in {
            ("pending", "completed"),
            ("pending", "manual_review"),
        }:
            raise ValueError("RECEIPT_ACTIVATION_TRANSITION_INVALID")
        marker = self.data.get(f"agentflo-whatsapp-message:{message_id}")
        if (
            marker is None
            or marker.get("delivery_state") != "completed"
            or marker.get("receipt_activation_state") != expected_state
        ):
            return False
        marker["receipt_activation_state"] = next_state
        marker["updated_at"] = updated_at
        return True

    def delete_idempotency_key(self, message_id):
        self.data.pop(f"agentflo-whatsapp-message:{message_id}", None)


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


def test_deterministic_voice_request_is_created_once_and_completed_request_resumes():
    requests = service()
    arguments = {
        "actor_id": "opaque-customer", "session_id": "opaque-session",
        "message": "private transcript", "channel": "whatsapp",
        "request_payload": {"message": "private transcript"},
        "request_id": "req-voice-" + "a" * 64,
    }

    first, created = requests.start_or_resume_processing(**arguments)
    assert created is True
    requests.complete(first["request_id"], {"text": "completed response"})
    resumed, created = requests.start_or_resume_processing(**arguments)

    assert created is False
    assert resumed["status"] == "completed"
    assert len([key for key in requests.repository.data if key.startswith("req-voice-")]) == 1


def test_agentflo_whatsapp_message_claim_uses_exact_key_and_24_hour_ttl(
    monkeypatch,
):
    requests = service()
    now = requests._now()
    monkeypatch.setattr(requests, "_now", lambda: now)

    assert requests.claim_agentflo_whatsapp_message("wamid.synthetic-1")
    marker, now_epoch = requests.repository.idempotency_claims[0]
    assert marker == {
        "PK": "agentflo-whatsapp-message:wamid.synthetic-1",
        "SK": "IDEMPOTENCY",
        "record_type": "agentflo_whatsapp_message_idempotency",
        "delivery_state": "processing",
        "created_at": now.isoformat(),
        "expires_at": int(now.timestamp()) + 24 * 60 * 60,
    }
    assert now_epoch == int(now.timestamp())


def test_agentflo_whatsapp_duplicate_claim_is_reported():
    requests = service()
    requests.repository.idempotency_result = False

    assert not requests.claim_agentflo_whatsapp_message("wamid.synthetic-1")


def test_agentflo_whatsapp_response_marker_tracks_delivery_and_release():
    requests = service()
    assert requests.claim_agentflo_whatsapp_message("wamid.synthetic-1")

    requests.cache_agentflo_whatsapp_response(
        "wamid.synthetic-1",
        request_id="req-1",
        session_id="session-1",
        customer_id="customer-1",
        reply="Authoritative reply.",
    )
    ready = requests.get_agentflo_whatsapp_message("wamid.synthetic-1")
    assert ready["delivery_state"] == "response_ready"
    assert ready["reply"] == "Authoritative reply."

    assert requests.claim_agentflo_whatsapp_outbound("wamid.synthetic-1")
    sending = requests.get_agentflo_whatsapp_message("wamid.synthetic-1")
    assert sending["delivery_state"] == "outbound_sending"

    assert requests.complete_agentflo_whatsapp_message("wamid.synthetic-1")
    completed = requests.get_agentflo_whatsapp_message("wamid.synthetic-1")
    assert completed["delivery_state"] == "completed"

    requests.release_agentflo_whatsapp_message("wamid.synthetic-1")
    assert requests.get_agentflo_whatsapp_message("wamid.synthetic-1") is None


def test_agentflo_whatsapp_outbound_lock_can_be_released_for_retry():
    requests = service()
    assert requests.claim_agentflo_whatsapp_message("wamid.synthetic-1")
    requests.cache_agentflo_whatsapp_response(
        "wamid.synthetic-1",
        request_id="req-1",
        session_id="session-1",
        customer_id="customer-1",
        reply="Authoritative reply.",
    )

    assert requests.claim_agentflo_whatsapp_outbound("wamid.synthetic-1")
    assert not requests.claim_agentflo_whatsapp_outbound("wamid.synthetic-1")
    assert requests.retry_agentflo_whatsapp_outbound("wamid.synthetic-1")
    assert (
        requests.get_agentflo_whatsapp_message("wamid.synthetic-1")[
            "delivery_state"
        ]
        == "response_ready"
    )


def test_response_cache_optionally_persists_structured_submission_signal():
    requests = service()
    assert requests.claim_agentflo_whatsapp_message("wamid.receipt")
    requests.cache_agentflo_whatsapp_response(
        "wamid.receipt",
        request_id="req-1",
        session_id="session-1",
        customer_id="customer-1",
        reply="Authoritative reply.",
        submitted_order_id="ORD-123",
    )

    marker = requests.get_agentflo_whatsapp_message("wamid.receipt")
    assert marker["submitted_order_id"] == "ORD-123"
    assert "receipt_activation_state" not in marker
    for forbidden in (
        "receipt_snapshot",
        "items",
        "totals",
        "address",
        "customer_number",
        "sender_id",
    ):
        assert forbidden not in marker


def test_phase_4a_reply_signal_can_be_cached_without_inference():
    requests = service()
    assert requests.claim_agentflo_whatsapp_message("wamid.reply-signal")
    reply = WhatsAppConversationReply(
        "Authoritative reply.",
        "req-1",
        "session-1",
        "customer-1",
        submitted_order_id="ORD-123",
    )

    requests.cache_agentflo_whatsapp_response(
        "wamid.reply-signal",
        request_id=reply.request_id,
        session_id=reply.session_id,
        customer_id=reply.customer_id,
        reply=reply.reply,
        submitted_order_id=reply.submitted_order_id,
    )

    marker = requests.get_agentflo_whatsapp_message("wamid.reply-signal")
    assert marker["submitted_order_id"] == "ORD-123"


def test_response_cache_without_submission_signal_remains_unchanged():
    requests = service()
    assert requests.claim_agentflo_whatsapp_message("wamid.normal")
    requests.cache_agentflo_whatsapp_response(
        "wamid.normal",
        request_id="req-1",
        session_id="session-1",
        customer_id="customer-1",
        reply="Normal reply.",
    )
    marker = requests.get_agentflo_whatsapp_message("wamid.normal")
    assert "submitted_order_id" not in marker
    assert "receipt_activation_state" not in marker


@pytest.mark.parametrize("order_id", ["", "   ", " ORD-123 ", 123])
def test_response_cache_rejects_invalid_submitted_order_id(order_id):
    requests = service()
    assert requests.claim_agentflo_whatsapp_message("wamid.invalid")
    with pytest.raises(ValueError, match="AGENTFLO_SUBMITTED_ORDER_ID_INVALID"):
        requests.cache_agentflo_whatsapp_response(
            "wamid.invalid",
            request_id="req-1",
            session_id="session-1",
            customer_id="customer-1",
            reply="Reply.",
            submitted_order_id=order_id,
        )
    assert (
        requests.get_agentflo_whatsapp_message("wamid.invalid")["delivery_state"]
        == "processing"
    )


def test_atomic_confirmation_checkpoint_and_activation_wrappers():
    requests = service()
    assert requests.claim_agentflo_whatsapp_message("wamid.receipt")
    requests.cache_agentflo_whatsapp_response(
        "wamid.receipt",
        request_id="req-1",
        session_id="session-1",
        customer_id="customer-1",
        reply="Reply.",
        submitted_order_id="ORD-123",
    )
    assert requests.claim_agentflo_whatsapp_outbound("wamid.receipt")

    assert requests.complete_agentflo_whatsapp_with_receipt_pending(
        "wamid.receipt"
    )
    pending = requests.get_agentflo_whatsapp_message("wamid.receipt")
    assert pending["delivery_state"] == "completed"
    assert pending["receipt_activation_state"] == "pending"
    assert requests.complete_agentflo_whatsapp_receipt_activation(
        "wamid.receipt"
    )
    assert (
        requests.get_agentflo_whatsapp_message("wamid.receipt")[
            "receipt_activation_state"
        ]
        == "completed"
    )
    assert not requests.complete_agentflo_whatsapp_receipt_activation(
        "wamid.receipt"
    )


def test_receipt_pending_checkpoint_requires_outbound_and_submission_signal():
    requests = service()
    assert requests.claim_agentflo_whatsapp_message("wamid.normal")
    assert not requests.complete_agentflo_whatsapp_with_receipt_pending(
        "wamid.normal"
    )
    requests.cache_agentflo_whatsapp_response(
        "wamid.normal",
        request_id="req-1",
        session_id="session-1",
        customer_id="customer-1",
        reply="Reply.",
    )
    assert requests.claim_agentflo_whatsapp_outbound("wamid.normal")
    assert not requests.complete_agentflo_whatsapp_with_receipt_pending(
        "wamid.normal"
    )
    assert requests.complete_agentflo_whatsapp_message("wamid.normal")
    marker = requests.get_agentflo_whatsapp_message("wamid.normal")
    assert marker["delivery_state"] == "completed"
    assert "receipt_activation_state" not in marker


@pytest.mark.parametrize("delivery_state", ["processing", "response_ready", "completed"])
def test_non_outbound_delivery_state_cannot_become_receipt_pending(
    delivery_state,
):
    requests = service()
    key = f"agentflo-whatsapp-message:wamid.{delivery_state}"
    requests.repository.data[key] = {
        "PK": key,
        "SK": "IDEMPOTENCY",
        "delivery_state": delivery_state,
        "submitted_order_id": "ORD-123",
    }

    assert not requests.complete_agentflo_whatsapp_with_receipt_pending(
        f"wamid.{delivery_state}"
    )
    marker = requests.repository.data[key]
    assert marker["delivery_state"] == delivery_state
    assert "receipt_activation_state" not in marker


def test_pending_activation_can_move_to_manual_review_only_once():
    requests = service()
    key = "agentflo-whatsapp-message:wamid.manual"
    requests.repository.data[key] = {
        "PK": key,
        "SK": "IDEMPOTENCY",
        "delivery_state": "completed",
        "receipt_activation_state": "pending",
    }
    assert requests.mark_agentflo_whatsapp_receipt_manual_review("wamid.manual")
    assert not requests.mark_agentflo_whatsapp_receipt_manual_review(
        "wamid.manual"
    )
