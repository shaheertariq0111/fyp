from __future__ import annotations

from copy import deepcopy

import pytest
from botocore.exceptions import ClientError

from src.models.receipt_job import TERMINAL_RECEIPT_JOB_STATES, receipt_job_id
from src.repositories.receipt_job_repository import ReceiptJobConditionFailed
from src.services.agentflo_gateway_service import (
    ClassifiedDocumentSendResult,
    DocumentSendDisposition,
)
from src.services.receipt_pdf_renderer import (
    ReceiptPdfRenderError,
    RenderedReceiptPdf,
)
from src.services.receipt_storage_service import (
    ReceiptStorageError,
    ReceiptStorageService,
    StoredReceiptArtifact,
)
from src.workers.receipt_worker import ReceiptWorker, ReceiptWorkerResult


ORDER_ID = "private-order-123"
JOB_ID = receipt_job_id(ORDER_ID, 1)
PDF = b"%PDF-generated-private-content"
STORED_PDF = b"%PDF-exact-stored-content"
EXPECTED_KEY = ReceiptStorageService.object_key(JOB_ID, 1)


def job(state="queued", *, s3_key=None):
    record = {
        "job_id": JOB_ID,
        "order_id": ORDER_ID,
        "receipt_version": 1,
        "state": state,
        "version": 3,
        "customer_number": "+923001234567",
        "sender_id": "private-sender",
        "conversation_id": "private-conversation",
        "request_id": "private-request",
        "attempt_count": 0,
    }
    if state in {"pending_enqueue", "retryable_failure"}:
        record.update(
            GSI1PK="RECEIPT_OUTBOX",
            GSI1SK=123,
            next_retry_at=123,
            generic_failure_code="OLD_FAILURE",
        )
    if s3_key is not None:
        record["s3_key"] = s3_key
    return record


def snapshot(**overrides):
    value = {
        "schema_version": 1,
        "order_id": ORDER_ID,
        "customer_name": "Private Customer",
        "delivery_address": "Private Address",
        "items": [{"name": "Private Item"}],
    }
    value.update(overrides)
    return value


class FakeJobs:
    def __init__(self, record=None):
        self.record = deepcopy(record)
        self.calls = []
        self.acquire_held = False
        self.extend_result = True
        self.fail_transition_to = None

    def get(self, job_id):
        self.calls.append(("get", job_id))
        return deepcopy(self.record) if self.record is not None else None

    @staticmethod
    def is_terminal(record):
        return record["state"] in TERMINAL_RECEIPT_JOB_STATES

    def acquire_lease(self, job_id, owner, *, lease_seconds):
        self.calls.append(("acquire", job_id, owner, lease_seconds))
        if self.acquire_held:
            raise ReceiptJobConditionFailed("RECEIPT_JOB_LEASE_HELD")
        self.record["lease_owner"] = owner
        self.record["attempt_count"] += 1
        return deepcopy(self.record)

    def extend_lease(self, job_id, owner, *, lease_seconds):
        self.calls.append(("extend", job_id, owner, lease_seconds))
        return self.extend_result

    def release_lease(self, job_id, owner):
        self.calls.append(("release", job_id, owner))
        if self.record is not None and self.record.get("lease_owner") == owner:
            self.record.pop("lease_owner", None)
        return True

    def transition(
        self,
        record,
        next_state,
        *,
        values=None,
        remove=(),
        lease_owner=None,
    ):
        self.calls.append((
            "transition",
            record["state"],
            next_state,
            deepcopy(values),
            remove,
            lease_owner,
        ))
        if self.fail_transition_to == next_state:
            self.fail_transition_to = None
            raise ReceiptJobConditionFailed("RECEIPT_JOB_CONDITION_FAILED")
        if self.record.get("lease_owner") != lease_owner:
            raise ReceiptJobConditionFailed("RECEIPT_JOB_CONDITION_FAILED")
        updated = deepcopy(self.record)
        updated.update(values or {})
        for field in remove:
            updated.pop(field, None)
        updated["state"] = next_state
        updated["version"] += 1
        self.record = updated
        return deepcopy(updated)


class FakeOrders:
    def __init__(self, order=None):
        self.order = order if order is not None else {
            "status": "completed",
            "receipt_snapshot": snapshot(),
            "items": "must-not-be-used",
        }
        self.calls = []
        self.error = None

    def get_by_order_id(self, order_id):
        self.calls.append(order_id)
        if self.error is not None:
            raise self.error
        return deepcopy(self.order)

    def create(self, *_args, **_kwargs):
        raise AssertionError("worker must never create orders")

    def save(self, *_args, **_kwargs):
        raise AssertionError("worker must never save orders")


class FakeRenderer:
    def __init__(self):
        self.render_calls = []
        self.filename_calls = []
        self.error = None

    def render(self, value):
        self.render_calls.append(value)
        if self.error is not None:
            raise self.error
        return RenderedReceiptPdf(PDF, "ignored-render-name.pdf")

    def filename_for_order_id(self, order_id):
        self.filename_calls.append(order_id)
        return "Order-Receipt-private-order-123.pdf"


class FakeStorage:
    def __init__(self):
        self.store_calls = []
        self.load_calls = []
        self.store_error = None
        self.load_error = None

    def store_pdf(self, job_id, receipt_version, content):
        self.store_calls.append((job_id, receipt_version, content))
        if self.store_error is not None:
            raise self.store_error
        return StoredReceiptArtifact(EXPECTED_KEY, None, len(content))

    def load_pdf(self, job_id, receipt_version):
        self.load_calls.append((job_id, receipt_version))
        if self.load_error is not None:
            raise self.load_error
        return StoredReceiptArtifact(
            EXPECTED_KEY,
            STORED_PDF,
            len(STORED_PDF),
        )


class FakeGateway:
    def __init__(self, disposition=DocumentSendDisposition.SENT):
        self.calls = []
        self.result = ClassifiedDocumentSendResult(
            disposition,
            provider_message_id=" provider-123 " if disposition is DocumentSendDisposition.SENT else None,
        )
        self.error = None

    def send_document_classified(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def make_worker(record=None, *, state="queued"):
    jobs = FakeJobs(job(state) if record is None else record)
    orders = FakeOrders()
    renderer = FakeRenderer()
    storage = FakeStorage()
    gateway = FakeGateway()
    worker = ReceiptWorker(
        jobs=jobs,
        orders=orders,
        renderer=renderer,
        storage=storage,
        gateway=gateway,
        lease_seconds=60,
    )
    return worker, jobs, orders, renderer, storage, gateway


def test_missing_job_is_nonretryable_noop():
    worker, jobs, orders, renderer, storage, gateway = make_worker()
    jobs.record = None

    result = worker.process(JOB_ID, owner="worker-a")

    assert result == ReceiptWorkerResult(JOB_ID, "missing", False, False)
    assert [call[0] for call in jobs.calls] == ["get"]
    assert orders.calls == renderer.render_calls == storage.load_calls == []
    assert gateway.calls == []


@pytest.mark.parametrize("state", ["sent", "permanent_failure", "manual_review"])
def test_terminal_duplicate_has_no_side_effects(state):
    worker, jobs, orders, renderer, storage, gateway = make_worker(state=state)

    result = worker.process(JOB_ID, owner="worker-a")

    assert result.terminal is True
    assert result.retryable is False
    assert [call[0] for call in jobs.calls] == ["get"]
    assert orders.calls == renderer.render_calls == storage.load_calls == []
    assert gateway.calls == []


def test_lease_contention_is_retryable_without_downstream_work():
    worker, jobs, orders, renderer, storage, gateway = make_worker()
    jobs.acquire_held = True

    result = worker.process(JOB_ID, owner="worker-a")

    assert result.retryable is True
    assert orders.calls == renderer.render_calls == storage.store_calls == []
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("state", "s3_key", "first_target"),
    [
        ("pending_enqueue", None, "processing"),
        ("queued", None, "processing"),
        ("processing", None, "generated"),
        ("retryable_failure", None, "processing"),
        ("retryable_failure", EXPECTED_KEY, "generated"),
    ],
)
def test_entry_states_resume_through_required_checkpoint(state, s3_key, first_target):
    worker, jobs, _, renderer, _, _ = make_worker(job(state, s3_key=s3_key))

    result = worker.process(JOB_ID, owner="worker-a")

    transitions = [call for call in jobs.calls if call[0] == "transition"]
    assert transitions[0][2] == first_target
    assert all(call[5] == "worker-a" for call in transitions)
    assert result.state == "sent"
    if s3_key:
        assert renderer.render_calls == []
    if state in {"pending_enqueue", "retryable_failure"}:
        for field in ("GSI1PK", "GSI1SK", "next_retry_at"):
            assert field not in jobs.record


def test_ambiguous_entry_moves_to_manual_review_without_send():
    worker, jobs, orders, renderer, storage, gateway = make_worker(
        state="outbound_sending"
    )

    result = worker.process(JOB_ID, owner="worker-a")

    assert result == ReceiptWorkerResult(JOB_ID, "manual_review", True, False)
    assert jobs.record["generic_failure_code"] == (
        "RECEIPT_OUTBOUND_OUTCOME_AMBIGUOUS"
    )
    assert orders.calls == renderer.render_calls == storage.load_calls == []
    assert gateway.calls == []


def test_success_uses_only_snapshot_stores_then_loads_and_sends_exact_bytes():
    worker, jobs, orders, renderer, storage, gateway = make_worker()

    result = worker.process(JOB_ID, owner="worker-a")

    assert result == ReceiptWorkerResult(JOB_ID, "sent", True, False)
    assert orders.calls == [ORDER_ID]
    assert renderer.render_calls == [orders.order["receipt_snapshot"]]
    assert storage.store_calls == [(JOB_ID, 1, PDF)]
    assert storage.load_calls == [(JOB_ID, 1)]
    assert gateway.calls == [{
        "customer_number": "+923001234567",
        "conversation_id": "private-conversation",
        "sender_id": "private-sender",
        "document": STORED_PDF,
        "filename": "Order-Receipt-private-order-123.pdf",
        "caption": None,
        "request_id": "private-request",
    }]
    assert renderer.filename_calls == [ORDER_ID]
    assert jobs.record["s3_key"] == EXPECTED_KEY
    assert jobs.record["provider_message_id"] == "provider-123"


def test_order_missing_is_retryable_and_order_is_never_mutated():
    worker, jobs, orders, renderer, storage, gateway = make_worker()
    orders.order = None

    result = worker.process(JOB_ID, owner="worker-a")

    assert result.state == "retryable_failure"
    assert result.retryable is True
    assert jobs.record["generic_failure_code"] == "RECEIPT_ORDER_LOOKUP_PENDING"
    assert renderer.render_calls == storage.store_calls == gateway.calls == []


def test_order_aws_failure_is_retryable_and_sanitized(caplog):
    worker, jobs, orders, _, _, _ = make_worker()
    orders.error = ClientError(
        {"Error": {"Code": "InternalError", "Message": "private AWS body"}},
        "Query",
    )

    with caplog.at_level("WARNING"):
        result = worker.process(JOB_ID, owner="worker-a")

    assert result.retryable is True
    assert jobs.record["generic_failure_code"] == "RECEIPT_ORDER_LOOKUP_FAILED"
    assert "private AWS body" not in caplog.text


@pytest.mark.parametrize(
    ("receipt_snapshot", "error_code"),
    [
        (None, "RECEIPT_SNAPSHOT_UNAVAILABLE"),
        (snapshot(order_id="other"), "RECEIPT_SNAPSHOT_IDENTITY_INVALID"),
        (snapshot(schema_version=2), "RECEIPT_SNAPSHOT_IDENTITY_INVALID"),
        (snapshot(schema_version=True), "RECEIPT_SNAPSHOT_IDENTITY_INVALID"),
    ],
)
def test_invalid_snapshot_identity_is_permanent(receipt_snapshot, error_code):
    worker, jobs, orders, renderer, storage, gateway = make_worker()
    orders.order["receipt_snapshot"] = receipt_snapshot

    result = worker.process(JOB_ID, owner="worker-a")

    assert result == ReceiptWorkerResult(
        JOB_ID,
        "permanent_failure",
        True,
        False,
    )
    assert jobs.record["generic_failure_code"] == error_code
    assert renderer.render_calls == storage.store_calls == gateway.calls == []


def test_render_error_is_permanent_but_runtime_error_propagates():
    worker, jobs, _, renderer, storage, gateway = make_worker()
    renderer.error = ReceiptPdfRenderError("RECEIPT_PDF_TOO_LARGE")
    result = worker.process(JOB_ID, owner="worker-a")
    assert result.state == "permanent_failure"
    assert jobs.record["generic_failure_code"] == "RECEIPT_PDF_TOO_LARGE"
    assert storage.store_calls == gateway.calls == []

    worker, _, _, renderer, _, _ = make_worker()
    renderer.error = RuntimeError("programming bug")
    with pytest.raises(RuntimeError, match="programming bug"):
        worker.process(JOB_ID, owner="worker-a")


@pytest.mark.parametrize("retryable", [True, False])
def test_store_failure_maps_to_retry_or_permanent_without_outbound(retryable):
    worker, jobs, _, _, storage, gateway = make_worker()
    storage.store_error = ReceiptStorageError(
        "RECEIPT_STORAGE_OPERATION_FAILED",
        retryable=retryable,
    )

    result = worker.process(JOB_ID, owner="worker-a")

    assert result.retryable is retryable
    assert result.terminal is not retryable
    assert gateway.calls == []
    assert jobs.record["state"] == (
        "retryable_failure" if retryable else "permanent_failure"
    )


def test_unexpected_storage_error_propagates():
    worker, _, _, _, storage, _ = make_worker()
    storage.store_error = TypeError("storage programming bug")
    with pytest.raises(TypeError, match="storage programming bug"):
        worker.process(JOB_ID, owner="worker-a")


def test_generated_resume_never_rerenders_and_rejects_mismatched_key():
    worker, jobs, orders, renderer, storage, gateway = make_worker(
        job("generated", s3_key="receipts/untrusted.pdf")
    )

    result = worker.process(JOB_ID, owner="worker-a")

    assert result.state == "permanent_failure"
    assert jobs.record["generic_failure_code"] == "RECEIPT_STORAGE_KEY_MISMATCH"
    assert orders.calls == renderer.render_calls == storage.load_calls == []
    assert gateway.calls == []


@pytest.mark.parametrize("retryable", [True, False])
def test_stored_artifact_failure_never_regenerates(retryable):
    worker, jobs, orders, renderer, storage, gateway = make_worker(
        job("generated", s3_key=EXPECTED_KEY)
    )
    storage.load_error = ReceiptStorageError(
        "RECEIPT_STORAGE_ARTIFACT_MISSING",
        retryable=retryable,
    )

    result = worker.process(JOB_ID, owner="worker-a")

    assert result.retryable is retryable
    assert jobs.record["s3_key"] == EXPECTED_KEY
    assert orders.calls == renderer.render_calls == []
    assert gateway.calls == []


def test_lost_lease_before_outbound_and_transition_race_do_not_send():
    worker, jobs, _, _, _, gateway = make_worker(
        job("generated", s3_key=EXPECTED_KEY)
    )
    jobs.extend_result = False
    result = worker.process(JOB_ID, owner="worker-a")
    assert result.retryable is True
    assert gateway.calls == []

    worker, jobs, _, _, _, gateway = make_worker(
        job("generated", s3_key=EXPECTED_KEY)
    )
    jobs.fail_transition_to = "outbound_sending"
    with pytest.raises(ReceiptJobConditionFailed):
        worker.process(JOB_ID, owner="worker-a")
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("disposition", "state", "terminal", "retryable", "failure_code"),
    [
        (DocumentSendDisposition.RETRYABLE_FAILURE, "generated", False, True, "RECEIPT_OUTBOUND_RETRYABLE"),
        (DocumentSendDisposition.PERMANENT_FAILURE, "permanent_failure", True, False, "RECEIPT_OUTBOUND_PERMANENT"),
        (DocumentSendDisposition.MANUAL_REVIEW, "manual_review", True, False, "RECEIPT_OUTBOUND_OUTCOME_AMBIGUOUS"),
    ],
)
def test_classified_outbound_outcomes(disposition, state, terminal, retryable, failure_code):
    worker, jobs, _, renderer, _, gateway = make_worker(
        job("generated", s3_key=EXPECTED_KEY)
    )
    gateway.result = ClassifiedDocumentSendResult(disposition)

    result = worker.process(JOB_ID, owner="worker-a")

    assert (result.state, result.terminal, result.retryable) == (
        state,
        terminal,
        retryable,
    )
    assert jobs.record["generic_failure_code"] == failure_code
    assert renderer.render_calls == []


def test_sent_without_provider_id_does_not_persist_empty_checkpoint():
    worker, jobs, _, _, _, gateway = make_worker(
        job("generated", s3_key=EXPECTED_KEY)
    )
    gateway.result = ClassifiedDocumentSendResult(DocumentSendDisposition.SENT)

    result = worker.process(JOB_ID, owner="worker-a")

    assert result.state == "sent"
    assert "provider_message_id" not in jobs.record


def test_unexpected_outbound_error_and_sent_checkpoint_crash_remain_ambiguous():
    worker, jobs, _, _, _, gateway = make_worker(
        job("generated", s3_key=EXPECTED_KEY)
    )
    gateway.error = RuntimeError("outbound programming failure")
    with pytest.raises(RuntimeError, match="outbound programming failure"):
        worker.process(JOB_ID, owner="worker-a")
    assert jobs.record["state"] == "outbound_sending"

    gateway.error = None
    worker.process(JOB_ID, owner="worker-b")
    assert jobs.record["state"] == "manual_review"
    assert len(gateway.calls) == 1

    worker, jobs, _, _, _, gateway = make_worker(
        job("generated", s3_key=EXPECTED_KEY)
    )
    jobs.fail_transition_to = "sent"
    with pytest.raises(ReceiptJobConditionFailed):
        worker.process(JOB_ID, owner="worker-a")
    assert jobs.record["state"] == "outbound_sending"
    assert len(gateway.calls) == 1


def test_crash_after_store_can_overwrite_same_deterministic_key():
    worker, jobs, _, _, storage, _ = make_worker()
    jobs.fail_transition_to = "generated"
    with pytest.raises(ReceiptJobConditionFailed):
        worker.process(JOB_ID, owner="worker-a")
    assert jobs.record["state"] == "processing"

    worker.process(JOB_ID, owner="worker-b")
    assert [call[:2] for call in storage.store_calls] == [
        (JOB_ID, 1),
        (JOB_ID, 1),
    ]


def test_worker_logs_and_result_exclude_private_data(caplog):
    worker, _, _, _, _, _ = make_worker()
    with caplog.at_level("INFO"):
        result = worker.process(JOB_ID, owner="worker-a")

    assert result.job_id == JOB_ID
    assert set(result.__dataclass_fields__) == {
        "job_id",
        "state",
        "terminal",
        "retryable",
    }
    assert any(
        getattr(record, "receipt_job_id", None) == JOB_ID
        for record in caplog.records
    )
    structured_log = " ".join(
        str(value)
        for record in caplog.records
        for key, value in vars(record).items()
        if key not in {"args", "exc_info", "exc_text", "stack_info"}
    )
    for private in (
        ORDER_ID,
        "+923001234567",
        "private-sender",
        "private-conversation",
        "private-request",
        "Private Customer",
        "Private Address",
        "Private Item",
        STORED_PDF.decode(),
        EXPECTED_KEY,
    ):
        assert private not in structured_log
