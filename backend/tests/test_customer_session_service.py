from types import SimpleNamespace
from datetime import datetime, timezone

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

    def get_owned(self, customer_id, session_id):
        session = self.data.get(session_id)
        if not session or session.get("customer_id") != customer_id:
            return None
        return dict(session)

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
    assert customer["name_confirmed"] is True
    assert customer["name_source"] == "customer_provided"


def test_whatsapp_profile_name_is_stored_without_confirming_customer_name():
    customers, _ = services()

    response = customers.update_profile(
        "cust-1",
        whatsapp_profile_name="  WhatsApp Alias ",
        phone_number="+92 300 1234567",
        channel="whatsapp",
        phone_verified=True,
    )

    customer = response.data["customer"]
    assert customer["display_name"] is None
    assert customer["name_confirmed"] is False
    assert customer["name_source"] is None
    assert customer["whatsapp_profile_name"] == "WhatsApp Alias"


def test_customer_provided_name_replaces_unconfirmed_whatsapp_name():
    customers, _ = services()
    customers.update_profile(
        "cust-1",
        whatsapp_profile_name="WhatsApp Alias",
        channel="whatsapp",
    )

    response = customers.confirm_customer_name(
        "cust-1",
        "Ava Khan",
        source="customer_provided",
    )

    customer = response.data["customer"]
    assert customer["display_name"] == "Ava Khan"
    assert customer["name_confirmed"] is True
    assert customer["name_source"] == "customer_provided"
    assert customer["name_confirmed_at"]
    assert customer["whatsapp_profile_name"] == "WhatsApp Alias"


def test_legacy_display_name_remains_confirmed_for_backward_compatibility():
    customers, _ = services()
    customers.repository.data["cust-legacy"] = {
        "customer_id": "cust-legacy",
        "display_name": "Legacy Customer",
        "channel_profiles": {"web": {"created_at": "legacy"}},
        "addresses": [],
    }

    profile = customers.get_profile("cust-legacy").data["customer"]

    assert profile["name_confirmed"] is True
    assert profile["name_source"] == "legacy"


def test_legacy_whatsapp_display_name_is_not_silently_confirmed():
    customers, _ = services()
    customers.repository.data["whatsapp-legacy"] = {
        "customer_id": "whatsapp-legacy",
        "display_name": "Legacy WhatsApp Alias",
        "channel_profiles": {"whatsapp": {"created_at": "legacy"}},
        "addresses": [],
    }

    profile = customers.get_profile("whatsapp-legacy").data["customer"]

    assert profile["display_name"] == "Legacy WhatsApp Alias"
    assert profile["name_confirmed"] is False
    assert profile["name_source"] is None


def test_explicit_customer_name_confirmation_metadata_takes_priority():
    confirmed = {
        "customer_id": "whatsapp-confirmed",
        "display_name": "Confirmed Name",
        "name_confirmed": True,
        "channel_profiles": {"whatsapp": {}},
    }
    unconfirmed = {
        "customer_id": "cust-unconfirmed",
        "display_name": "Unconfirmed Name",
        "name_confirmed": False,
        "channel_profiles": {"web": {}},
    }

    assert CustomerService.confirmed_name(confirmed) == "Confirmed Name"
    assert CustomerService.confirmed_name(unconfirmed) is None


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


def test_trusted_channel_can_create_and_reuse_stable_requested_session():
    _, sessions = services()
    stable_session_id = "whatsapp-0123456789abcdef"

    first = sessions.resolve(
        requested_session_id=stable_session_id,
        customer_id="cust-whatsapp",
        channel="whatsapp",
        allow_requested_session_creation=True,
    )
    second = sessions.resolve(
        requested_session_id=stable_session_id,
        customer_id="cust-whatsapp",
        channel="whatsapp",
        preserve_expired=True,
        allow_requested_session_creation=True,
    )

    assert first["session"]["agent_session_id"] == stable_session_id
    assert first["rotated"] is True
    assert second["session"]["agent_session_id"] == stable_session_id
    assert second["rotated"] is False


def test_known_customer_uses_owned_session_when_duplicate_id_exists():
    customers = CustomerService(MemoryCustomerRepository())

    class DuplicateRepository:
        def __init__(self):
            self.canonical = {
                "PK": "CUSTOMER#cust-canonical",
                "SK": "SESSION#whatsapp-stable",
                "agent_session_id": "whatsapp-stable",
                "customer_id": "cust-canonical",
                "channel": "whatsapp",
                "status": "active",
                "expires_at": 9999999999,
            }
            self.synthetic = {
                **self.canonical,
                "PK": "CUSTOMER#whatsapp-stable",
                "customer_id": "whatsapp-stable",
            }
            self.create_calls = []

        def get(self, _session_id):
            return dict(self.synthetic)

        def get_owned(self, customer_id, _session_id):
            return dict(self.canonical) if customer_id == "cust-canonical" else None

        def save(self, session):
            self.canonical = dict(session)

        def create(self, session):
            self.create_calls.append(session)
            raise AssertionError("owned session already exists")

    repository = DuplicateRepository()
    sessions = AgentSessionService(
        repository,
        customers,
        SimpleNamespace(agent_session_ttl_hours=24),
    )

    result = sessions.resolve(
        requested_session_id="whatsapp-stable",
        customer_id="cust-canonical",
        channel="whatsapp",
        preserve_expired=True,
        allow_requested_session_creation=True,
    )

    assert result["session"]["customer_id"] == "cust-canonical"
    assert result["session"]["agent_session_id"] == "whatsapp-stable"
    assert result["rotated"] is False
    assert repository.create_calls == []


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
