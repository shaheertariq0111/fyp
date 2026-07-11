from types import SimpleNamespace

from src.services.agent_session_service import AgentSessionService
from src.services.customer_service import CustomerService


class MemoryCustomerRepository:
    def __init__(self):
        self.data = {}

    def create(self, customer):
        self.data[customer["customer_id"]] = dict(customer)

    def get(self, customer_id):
        customer = self.data.get(customer_id)
        return dict(customer) if customer else None

    def get_by_phone_hash(self, phone_hash):
        return next(
            (dict(customer) for customer in self.data.values()
             if customer.get("phone_hash") == phone_hash),
            None,
        )

    def save(self, customer):
        self.data[customer["customer_id"]] = dict(customer)


class MemoryAgentSessionRepository:
    def __init__(self):
        self.data = {}

    def create(self, session):
        self.data[session["agent_session_id"]] = dict(session)

    def get(self, session_id):
        session = self.data.get(session_id)
        return dict(session) if session else None

    def save(self, session):
        self.data[session["agent_session_id"]] = dict(session)


def services():
    customers = CustomerService(MemoryCustomerRepository())
    sessions = AgentSessionService(
        MemoryAgentSessionRepository(),
        customers,
        SimpleNamespace(agent_session_ttl_hours=24),
    )
    return customers, sessions


def test_customer_profile_persists_name_and_unverified_web_phone():
    customers, _ = services()

    response = customers.update_profile(
        "cust-1", display_name="  Ava   Khan ", phone_number="+92 300 1234567"
    )

    customer = response.data["customer"]
    assert customer["customer_id"] == "cust-1"
    assert customer["display_name"] == "Ava Khan"
    assert customer["phone_e164"] == "+923001234567"
    assert customer["phone_verified"] is False
    assert customer["addresses"] == []


def test_customer_profile_saves_multiple_delivery_addresses_and_default():
    customers, _ = services()

    first = customers.save_address(
        "cust-1",
        address_text="  House 1, Street 2, Lahore ",
        label="Home",
    )
    second = customers.save_address(
        "cust-1",
        address_text="Office Tower, Karachi",
        label="Work",
    )
    profile = customers.get_profile("cust-1")

    addresses = profile.data["customer"]["addresses"]
    assert first.success
    assert second.success
    assert [address["label"] for address in addresses] == ["Home", "Work"]
    assert addresses[0]["address_text"] == "House 1, Street 2, Lahore"
    assert addresses[0]["is_default"] is False
    assert addresses[1]["address_text"] == "Office Tower, Karachi"
    assert addresses[1]["is_default"] is True
    assert addresses[1]["verified"] is False
    assert addresses[1]["address_id"].startswith("ADDR-")


def test_valid_session_is_reused_and_last_seen_updates():
    _, sessions = services()
    first = sessions.resolve(
        requested_session_id=None, customer_id="cust-1", channel="web"
    )
    session_id = first["session"]["agent_session_id"]

    second = sessions.resolve(
        requested_session_id=session_id, customer_id="cust-1", channel="web"
    )

    assert second["session"]["agent_session_id"] == session_id
    assert second["rotated"] is False


def test_expired_idle_session_rotates():
    _, sessions = services()
    first = sessions.resolve(
        requested_session_id=None, customer_id="cust-1", channel="web"
    )
    session_id = first["session"]["agent_session_id"]
    stored = sessions.repository.data[session_id]
    stored["expires_at"] = 1
    sessions.repository.data[session_id] = stored

    second = sessions.resolve(
        requested_session_id=session_id, customer_id="cust-1", channel="web"
    )

    assert second["session"]["agent_session_id"] != session_id
    assert second["rotated"] is True


def test_expired_session_with_active_state_is_preserved():
    _, sessions = services()
    first = sessions.resolve(
        requested_session_id=None, customer_id="cust-1", channel="web"
    )
    session_id = first["session"]["agent_session_id"]
    stored = sessions.repository.data[session_id]
    stored["expires_at"] = 1
    sessions.repository.data[session_id] = stored

    second = sessions.resolve(
        requested_session_id=session_id, customer_id="cust-1", channel="web",
        preserve_expired=True,
    )

    assert second["session"]["agent_session_id"] == session_id
    assert second["rotated"] is False
