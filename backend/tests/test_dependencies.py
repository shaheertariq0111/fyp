from types import SimpleNamespace

from src.agent import dependencies
from src.repositories.ticket_repository import TicketRepository
from src.services.support_flow_service import SupportFlowService
from src.services.ticket_service import TicketService
from src.services.whatsapp_receipt_activation_service import (
    WhatsAppReceiptActivationService,
)
from test_config import make_test_settings


class FakeTable:
    def __init__(self, name):
        self.name = name

    def __getattr__(self, operation):
        raise AssertionError(
            f"DynamoDB operation {operation} must not run during construction"
        )


class FakeDynamoResource:
    def __init__(self):
        self.table_names = []
        self.meta = SimpleNamespace(client=object())

    def Table(self, table_name):
        self.table_names.append(table_name)
        return FakeTable(table_name)


def test_service_container_wires_ticket_service_without_table_access(
    monkeypatch,
):
    settings = make_test_settings(
        tickets_table_name="tickets-phase-2a",
        support_phone_number="+1 555 0199",
    )
    dynamodb = FakeDynamoResource()
    dependencies.get_services.cache_clear()
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(
        dependencies,
        "get_dynamodb_resource",
        lambda configured: dynamodb,
    )
    monkeypatch.setattr(
        dependencies,
        "get_bedrock_agent_runtime_client",
        lambda configured: object(),
    )

    services = dependencies.get_services()

    assert isinstance(services.tickets, TicketService)
    assert isinstance(services.tickets.repository, TicketRepository)
    assert services.tickets.repository.table_name == "tickets-phase-2a"
    assert services.tickets.repository.table.name == "tickets-phase-2a"
    assert services.tickets.orders is services.orders.orders
    assert services.tickets.support_phone_number == "+1 555 0199"
    assert isinstance(services.support_flow, SupportFlowService)
    assert services.support_flow.agent_sessions is services.agent_sessions
    assert services.support_flow.tickets is services.tickets
    assert services.support_flow.orders is services.orders.orders
    assert services.whatsapp_receipt_activation is None
    assert dynamodb.table_names.count("tickets-phase-2a") == 1
    dependencies.get_services.cache_clear()


def test_receipt_activation_reuses_application_dynamodb_when_enabled(
    monkeypatch,
):
    settings = make_test_settings(
        receipt_activation_enabled=True,
        receipt_jobs_table_name="receipt-jobs-test",
        receipt_job_queue_url="https://sqs.example.test/receipt",
    )
    dynamodb = FakeDynamoResource()
    receipt_jobs = object()
    captured = {}

    def build_receipt_runtime(configured, *, dynamodb):
        captured["settings"] = configured
        captured["dynamodb"] = dynamodb
        return SimpleNamespace(jobs=receipt_jobs)

    dependencies.get_services.cache_clear()
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(
        dependencies,
        "get_dynamodb_resource",
        lambda configured: dynamodb,
    )
    monkeypatch.setattr(
        dependencies,
        "get_bedrock_agent_runtime_client",
        lambda configured: object(),
    )
    monkeypatch.setattr(
        dependencies,
        "build_receipt_submission_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled receipt dependencies must not be built")
        ),
    )
    monkeypatch.setattr(
        dependencies,
        "build_receipt_submission_runtime",
        build_receipt_runtime,
    )

    services = dependencies.get_services()

    assert isinstance(
        services.whatsapp_receipt_activation,
        WhatsAppReceiptActivationService,
    )
    assert services.whatsapp_receipt_activation.agent_requests is services.agent_requests
    assert services.whatsapp_receipt_activation.receipt_jobs is receipt_jobs
    assert captured == {"settings": settings, "dynamodb": dynamodb}
    dependencies.get_services.cache_clear()
