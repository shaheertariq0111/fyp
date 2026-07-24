from types import SimpleNamespace

from src.agent import dependencies
from src.repositories.ticket_repository import TicketRepository
from src.services.ticket_service import TicketService
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
    assert dynamodb.table_names.count("tickets-phase-2a") == 1
    dependencies.get_services.cache_clear()
