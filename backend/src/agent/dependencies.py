from dataclasses import dataclass
from functools import lru_cache

from src.infrastructure.bedrock import get_bedrock_agent_runtime_client
from src.infrastructure.config import get_settings
from src.infrastructure.dynamodb import get_dynamodb_resource
from src.repositories.audit_repository import AuditRepository
from src.repositories.agent_session_repository import AgentSessionRepository
from src.repositories.agent_request_repository import AgentRequestRepository
from src.repositories.cart_repository import CartRepository
from src.repositories.customer_repository import CustomerRepository
from src.repositories.menu_repository import MenuRepository
from src.repositories.order_repository import OrderRepository
from src.repositories.session_repository import MenuSessionRepository
from src.repositories.ticket_repository import TicketRepository
from src.services.agent_session_service import AgentSessionService
from src.services.agent_request_service import AgentRequestService
from src.services.audit_service import AuditService
from src.services.cart_service import CartService
from src.services.customer_service import CustomerService
from src.services.knowledge_service import KnowledgeService
from src.services.menu_service import MenuService
from src.services.menu_session_service import MenuSessionService
from src.services.order_service import OrderService
from src.services.support_flow_service import SupportFlowService
from src.services.ticket_service import TicketService


@dataclass
class ServiceContainer:
    menu: MenuService
    menu_sessions: MenuSessionService
    carts: CartService
    orders: OrderService
    customers: CustomerService
    agent_sessions: AgentSessionService
    agent_requests: AgentRequestService
    tickets: TicketService
    support_flow: SupportFlowService
    knowledge: KnowledgeService
    audit: AuditService


@lru_cache
def get_services() -> ServiceContainer:
    settings = get_settings()
    dynamodb = get_dynamodb_resource(settings)
    menu_repository = MenuRepository(dynamodb, settings.menu_table_name, settings.restaurant_id)
    cart_repository = CartRepository(dynamodb, settings.carts_table_name)
    order_repository = OrderRepository(dynamodb, settings.orders_table_name)
    ticket_repository = TicketRepository(dynamodb, settings.tickets_table_name)
    customer_service = CustomerService(CustomerRepository(dynamodb, settings.customers_table_name))
    order_service = OrderService(order_repository, menu_repository)
    agent_session_service = AgentSessionService(
        AgentSessionRepository(dynamodb, settings.agent_sessions_table_name),
        customer_service,
        settings,
    )
    ticket_service = TicketService(
        ticket_repository,
        order_repository,
        support_phone_number=settings.support_phone_number,
    )
    return ServiceContainer(
        menu=MenuService(menu_repository, settings.branch_id),
        menu_sessions=MenuSessionService(
            MenuSessionRepository(dynamodb, settings.menu_sessions_table_name), settings
        ),
        carts=CartService(cart_repository, menu_repository, order_service, settings),
        orders=order_service,
        customers=customer_service,
        agent_sessions=agent_session_service,
        agent_requests=AgentRequestService(
            AgentRequestRepository(dynamodb, settings.agent_requests_table_name),
            settings,
        ),
        tickets=ticket_service,
        support_flow=SupportFlowService(
            agent_session_service,
            ticket_service,
            order_repository,
        ),
        knowledge=KnowledgeService(
            get_bedrock_agent_runtime_client(settings), settings.knowledge_base_id,
            settings.knowledge_base_max_results,
        ),
        audit=AuditService(AuditRepository(dynamodb, settings.audit_table_name)),
    )
