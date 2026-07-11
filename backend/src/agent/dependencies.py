from dataclasses import dataclass
from functools import lru_cache

from src.infrastructure.bedrock import get_bedrock_agent_runtime_client
from src.infrastructure.config import get_settings
from src.infrastructure.dynamodb import get_dynamodb_resource
from src.repositories.audit_repository import AuditRepository
from src.repositories.agent_session_repository import AgentSessionRepository
from src.repositories.cart_repository import CartRepository
from src.repositories.customer_repository import CustomerRepository
from src.repositories.menu_repository import MenuRepository
from src.repositories.order_repository import OrderRepository
from src.repositories.session_repository import MenuSessionRepository
from src.services.agent_session_service import AgentSessionService
from src.services.audit_service import AuditService
from src.services.cart_service import CartService
from src.services.customer_service import CustomerService
from src.services.knowledge_service import KnowledgeService
from src.services.menu_service import MenuService
from src.services.menu_session_service import MenuSessionService
from src.services.order_service import OrderService


@dataclass
class ServiceContainer:
    menu: MenuService
    menu_sessions: MenuSessionService
    carts: CartService
    orders: OrderService
    customers: CustomerService
    agent_sessions: AgentSessionService
    knowledge: KnowledgeService
    audit: AuditService


@lru_cache
def get_services() -> ServiceContainer:
    settings = get_settings()
    dynamodb = get_dynamodb_resource(settings)
    menu_repository = MenuRepository(dynamodb, settings.menu_table_name, settings.restaurant_id)
    cart_repository = CartRepository(dynamodb, settings.carts_table_name)
    order_repository = OrderRepository(dynamodb, settings.orders_table_name)
    customer_service = CustomerService(CustomerRepository(dynamodb, settings.customers_table_name))
    order_service = OrderService(order_repository, menu_repository)
    return ServiceContainer(
        menu=MenuService(menu_repository, settings.branch_id),
        menu_sessions=MenuSessionService(
            MenuSessionRepository(dynamodb, settings.menu_sessions_table_name), settings
        ),
        carts=CartService(cart_repository, menu_repository, order_service, settings),
        orders=order_service,
        customers=customer_service,
        agent_sessions=AgentSessionService(
            AgentSessionRepository(dynamodb, settings.agent_sessions_table_name),
            customer_service,
            settings,
        ),
        knowledge=KnowledgeService(
            get_bedrock_agent_runtime_client(settings), settings.knowledge_base_id,
            settings.knowledge_base_max_results,
        ),
        audit=AuditService(AuditRepository(dynamodb, settings.audit_table_name)),
    )
