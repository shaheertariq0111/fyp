from __future__ import annotations

from dataclasses import dataclass

from src.infrastructure.config import Settings, get_settings
from src.infrastructure.dynamodb import get_dynamodb_resource
from src.infrastructure.receipt_config import (
    ReceiptProcessingSettings,
    ReceiptRecoverySettings,
    get_receipt_processing_settings,
    get_receipt_recovery_settings,
)
from src.infrastructure.s3 import get_s3_client
from src.infrastructure.secrets import (
    SecretsManagerSecretLoader,
    get_secretsmanager_client,
)
from src.infrastructure.sqs import create_sqs_client
from src.repositories.order_repository import OrderRepository
from src.repositories.receipt_job_repository import ReceiptJobRepository
from src.services.agentflo_gateway_service import AgentfloGatewayService
from src.services.receipt_job_service import ReceiptJobService
from src.services.receipt_pdf_renderer import ReceiptPdfRenderer
from src.services.receipt_queue_service import ReceiptQueueService
from src.services.receipt_storage_service import ReceiptStorageService
from src.workers.receipt_sqs_handler import ReceiptSqsHandler
from src.workers.receipt_worker import ReceiptWorker


@dataclass(frozen=True, slots=True)
class ReceiptSubmissionRuntime:
    repository: ReceiptJobRepository
    queue: ReceiptQueueService
    jobs: ReceiptJobService


@dataclass(frozen=True, slots=True)
class ReceiptProcessingRuntime:
    repository: ReceiptJobRepository
    queue: ReceiptQueueService
    jobs: ReceiptJobService
    orders: OrderRepository
    renderer: ReceiptPdfRenderer
    storage: ReceiptStorageService
    gateway: AgentfloGatewayService
    worker: ReceiptWorker
    handler: ReceiptSqsHandler


@dataclass(frozen=True, slots=True)
class ReceiptRecoveryRuntime:
    repository: ReceiptJobRepository
    queue: ReceiptQueueService
    jobs: ReceiptJobService


def _job_dependencies(
    settings: Settings | ReceiptRecoverySettings,
    *,
    dynamodb,
    sqs_client,
) -> tuple[ReceiptJobRepository, ReceiptQueueService, ReceiptJobService]:
    repository = ReceiptJobRepository(dynamodb, settings.receipt_jobs_table_name)
    queue = ReceiptQueueService(sqs_client, settings.receipt_job_queue_url)
    jobs = ReceiptJobService(
        repository,
        queue,
        job_ttl_hours=settings.receipt_job_ttl_hours,
        enqueue_retry_seconds=settings.receipt_enqueue_retry_seconds,
    )
    return repository, queue, jobs


def build_receipt_submission_runtime(
    settings: Settings | None = None,
    *,
    dynamodb=None,
    sqs_client=None,
) -> ReceiptSubmissionRuntime:
    settings = settings or get_settings()
    if not settings.receipt_activation_enabled:
        raise ValueError("RECEIPT_ACTIVATION_DISABLED")
    settings.validate_receipt_activation_settings()
    if dynamodb is None:
        dynamodb = get_dynamodb_resource(settings)
    sqs_client = create_sqs_client(
        region_name=settings.aws_region,
        client=sqs_client,
    )
    repository, queue, jobs = _job_dependencies(
        settings,
        dynamodb=dynamodb,
        sqs_client=sqs_client,
    )
    return ReceiptSubmissionRuntime(repository, queue, jobs)


def build_receipt_processing_runtime(
    settings: ReceiptProcessingSettings | None = None,
    *,
    dynamodb=None,
    sqs_client=None,
    s3_client=None,
    secretsmanager_client=None,
) -> ReceiptProcessingRuntime:
    if settings is None:
        settings = get_receipt_processing_settings()
    if dynamodb is None:
        dynamodb = get_dynamodb_resource(settings)
    sqs_client = create_sqs_client(
        region_name=settings.aws_region,
        client=sqs_client,
    )
    if s3_client is None:
        s3_client = get_s3_client(settings)
    secretsmanager_client = get_secretsmanager_client(
        settings,
        client=secretsmanager_client,
    )
    api_key = SecretsManagerSecretLoader(
        secretsmanager_client,
        settings.agentflo_gateway_api_key_secret_arn,
    ).load()
    repository, queue, jobs = _job_dependencies(
        settings,
        dynamodb=dynamodb,
        sqs_client=sqs_client,
    )
    orders = OrderRepository(dynamodb, settings.orders_table_name)
    renderer = ReceiptPdfRenderer(merchant_name=settings.receipt_merchant_name)
    storage = ReceiptStorageService(
        client=s3_client,
        bucket_name=settings.receipt_bucket_name,
    )
    gateway = AgentfloGatewayService(
        base_url=settings.agentflo_gateway_base_url,
        api_key=api_key,
        tenant_id=settings.agentflo_gateway_tenant_id,
        agent_id=settings.agentflo_gateway_agent_id,
        actor_id=settings.agentflo_gateway_actor_id,
    )
    worker = ReceiptWorker(
        jobs=jobs,
        orders=orders,
        renderer=renderer,
        storage=storage,
        gateway=gateway,
        lease_seconds=settings.receipt_worker_lease_seconds,
    )
    handler = ReceiptSqsHandler(worker)
    return ReceiptProcessingRuntime(
        repository,
        queue,
        jobs,
        orders,
        renderer,
        storage,
        gateway,
        worker,
        handler,
    )


def build_receipt_recovery_runtime(
    settings: ReceiptRecoverySettings | None = None,
    *,
    dynamodb=None,
    sqs_client=None,
) -> ReceiptRecoveryRuntime:
    if settings is None:
        settings = get_receipt_recovery_settings()
    if dynamodb is None:
        dynamodb = get_dynamodb_resource(settings)
    sqs_client = create_sqs_client(
        region_name=settings.aws_region,
        client=sqs_client,
    )
    repository, queue, jobs = _job_dependencies(
        settings,
        dynamodb=dynamodb,
        sqs_client=sqs_client,
    )
    return ReceiptRecoveryRuntime(repository, queue, jobs)
