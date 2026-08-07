from __future__ import annotations

import inspect

from src.composition import receipt_dependencies as composition
from src.infrastructure.receipt_config import (
    ReceiptProcessingSettings,
    ReceiptRecoverySettings,
)
from src.repositories.order_repository import OrderRepository
from src.repositories.receipt_job_repository import ReceiptJobRepository
from src.services.agentflo_gateway_service import AgentfloGatewayService
from src.services.receipt_job_service import ReceiptJobService
from src.services.receipt_pdf_renderer import ReceiptPdfRenderer
from src.services.receipt_queue_service import ReceiptQueueService
from src.services.receipt_storage_service import ReceiptStorageService
from src.workers.receipt_sqs_handler import ReceiptSqsHandler
from src.workers.receipt_worker import ReceiptWorker
from test_config import make_test_settings


SECRET_ARN = "arn:aws:secretsmanager:us-west-2:123456789012:secret:receipt"
SECRET_VALUE = "synthetic-agentflo-key"


class FakeTable:
    def __init__(self, name):
        self.name = name

    def __getattr__(self, operation):
        raise AssertionError(f"DynamoDB {operation} must not run during composition")


class FakeDynamo:
    def __init__(self):
        self.table_names = []

    def Table(self, name):
        self.table_names.append(name)
        return FakeTable(name)


class FakeSecretsManager:
    def __init__(self):
        self.calls = []

    def get_secret_value(self, **kwargs):
        self.calls.append(kwargs)
        return {"SecretString": SECRET_VALUE}


def processing_settings(**overrides):
    values = {
        "receipt_jobs_table_name": "receipt-jobs-test",
        "receipt_job_queue_url": "https://sqs.example.test/receipt",
        "orders_table_name": "orders-test",
        "receipt_bucket_name": "receipt-bucket-test",
        "receipt_merchant_name": "Test Merchant",
        "receipt_job_ttl_hours": 336,
        "receipt_enqueue_retry_seconds": 45,
        "receipt_worker_lease_seconds": 120,
        "agentflo_gateway_base_url": "https://gateway.example.test",
        "agentflo_gateway_tenant_id": "tenant-test",
        "agentflo_gateway_agent_id": "agent-test",
        "agentflo_gateway_actor_id": "actor-test",
        "agentflo_gateway_api_key_secret_arn": SECRET_ARN,
    }
    values.update(overrides)
    return ReceiptProcessingSettings(_env_file=None, aws_region="us-west-2", **values)


def recovery_settings(**overrides):
    values = {
        "aws_region": "us-west-2",
        "receipt_jobs_table_name": "receipt-jobs-test",
        "receipt_job_queue_url": "https://sqs.example.test/receipt",
        "receipt_job_ttl_hours": 336,
        "receipt_enqueue_retry_seconds": 45,
    }
    values.update(overrides)
    return ReceiptRecoverySettings(_env_file=None, **values)


def forbid(*_args, **_kwargs):
    raise AssertionError("forbidden dependency was constructed")


def test_backend_submission_factory_builds_only_job_dependencies(monkeypatch):
    settings = make_test_settings(
        receipt_activation_enabled=True,
        receipt_jobs_table_name="receipt-jobs-test",
        receipt_job_queue_url="https://sqs.example.test/receipt",
        receipt_job_ttl_hours=336,
        receipt_enqueue_retry_seconds=45,
    )
    dynamodb = FakeDynamo()
    sqs = object()
    for name in (
        "get_s3_client",
        "get_secretsmanager_client",
        "OrderRepository",
        "ReceiptPdfRenderer",
        "ReceiptStorageService",
        "AgentfloGatewayService",
        "ReceiptWorker",
    ):
        monkeypatch.setattr(composition, name, forbid)

    runtime = composition.build_receipt_submission_runtime(
        settings,
        dynamodb=dynamodb,
        sqs_client=sqs,
    )

    assert isinstance(runtime.repository, ReceiptJobRepository)
    assert isinstance(runtime.queue, ReceiptQueueService)
    assert isinstance(runtime.jobs, ReceiptJobService)
    assert runtime.repository.table.name == "receipt-jobs-test"
    assert runtime.queue.queue_url == "https://sqs.example.test/receipt"
    assert runtime.queue.client is sqs
    assert runtime.jobs.job_ttl_hours == 336
    assert runtime.jobs.enqueue_retry_seconds == 45
    assert dynamodb.table_names == ["receipt-jobs-test"]


def test_processing_factory_builds_exact_worker_graph():
    settings = processing_settings()
    dynamodb = FakeDynamo()
    sqs = object()
    s3 = object()
    secrets = FakeSecretsManager()

    runtime = composition.build_receipt_processing_runtime(
        settings,
        dynamodb=dynamodb,
        sqs_client=sqs,
        s3_client=s3,
        secretsmanager_client=secrets,
    )

    assert isinstance(runtime.repository, ReceiptJobRepository)
    assert isinstance(runtime.queue, ReceiptQueueService)
    assert isinstance(runtime.jobs, ReceiptJobService)
    assert isinstance(runtime.orders, OrderRepository)
    assert not hasattr(runtime.orders, "scan")
    assert ".query(" in inspect.getsource(OrderRepository.get_by_order_id)
    assert 'IndexName="GSI1"' in inspect.getsource(OrderRepository.get_by_order_id)
    assert isinstance(runtime.renderer, ReceiptPdfRenderer)
    assert runtime.renderer.merchant_name == "Test Merchant"
    assert isinstance(runtime.storage, ReceiptStorageService)
    assert runtime.storage.bucket_name == "receipt-bucket-test"
    assert runtime.storage.client is s3
    assert isinstance(runtime.gateway, AgentfloGatewayService)
    assert runtime.gateway.api_key == SECRET_VALUE
    assert isinstance(runtime.worker, ReceiptWorker)
    assert runtime.worker.orders is runtime.orders
    assert runtime.worker.lease_seconds == 120
    assert isinstance(runtime.handler, ReceiptSqsHandler)
    assert runtime.handler.worker is runtime.worker
    assert secrets.calls == [{"SecretId": SECRET_ARN}]
    assert dynamodb.table_names == ["receipt-jobs-test", "orders-test"]


def test_recovery_factory_constructs_only_job_dependencies(monkeypatch):
    settings = recovery_settings()
    dynamodb = FakeDynamo()
    sqs = object()
    for name in (
        "get_s3_client",
        "get_secretsmanager_client",
        "OrderRepository",
        "ReceiptPdfRenderer",
        "ReceiptStorageService",
        "AgentfloGatewayService",
        "ReceiptWorker",
        "ReceiptSqsHandler",
    ):
        monkeypatch.setattr(composition, name, forbid)

    runtime = composition.build_receipt_recovery_runtime(
        settings,
        dynamodb=dynamodb,
        sqs_client=sqs,
    )

    assert isinstance(runtime.repository, ReceiptJobRepository)
    assert isinstance(runtime.queue, ReceiptQueueService)
    assert isinstance(runtime.jobs, ReceiptJobService)
    assert runtime.repository.table.name == "receipt-jobs-test"
    assert runtime.queue.client is sqs
    assert dynamodb.table_names == ["receipt-jobs-test"]


def test_processing_factory_fallback_never_calls_global_settings(monkeypatch):
    settings = processing_settings()
    monkeypatch.setattr(composition, "get_settings", forbid)
    monkeypatch.setattr(
        composition,
        "get_receipt_processing_settings",
        lambda: settings,
    )

    runtime = composition.build_receipt_processing_runtime(
        dynamodb=FakeDynamo(),
        sqs_client=object(),
        s3_client=object(),
        secretsmanager_client=FakeSecretsManager(),
    )

    assert runtime.worker.lease_seconds == 120


def test_recovery_factory_fallback_never_calls_global_settings(monkeypatch):
    settings = recovery_settings()
    monkeypatch.setattr(composition, "get_settings", forbid)
    monkeypatch.setattr(
        composition,
        "get_receipt_recovery_settings",
        lambda: settings,
    )

    runtime = composition.build_receipt_recovery_runtime(
        dynamodb=FakeDynamo(),
        sqs_client=object(),
    )

    assert runtime.jobs.job_ttl_hours == 336
