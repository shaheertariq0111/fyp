from copy import deepcopy

from src.models.ticket import (
    validate_guard_for_ticket,
    validate_marker_for_ticket,
    validate_ticket_for_write,
)
from src.repositories.ticket_repository import (
    HumanSessionGuardConflictError,
    IdempotencyConflictError,
    ReusableTicketChangedError,
    TicketIdCollisionError,
    TicketVersionConflictError,
)


class MemoryMenuRepository:
    def __init__(self, items, groups, upsells=None):
        self.menu_pk = "MENU#restaurant"
        self.items = {item["product_id"]: deepcopy(item) for item in items}
        self.groups = {group["option_group_id"]: deepcopy(group) for group in groups}
        self.upsells = {group["upsell_group_id"]: deepcopy(group) for group in upsells or []}
        self.categories = {}

    def get_item(self, item_id):
        return deepcopy(self.items.get(item_id))

    def get_option_group(self, group_id):
        return deepcopy(self.groups.get(group_id))

    def get_upsell_group(self, group_id):
        return deepcopy(self.upsells.get(group_id))

    def search(self, available_only=True):
        return [deepcopy(item) for item in self.items.values()
                if not available_only or (item["available"] and not item.get("archived"))]

    def list_entities(self, entity_type):
        if entity_type == "menu_item":
            return [deepcopy(item) for item in self.items.values()]
        if entity_type == "option_group":
            return [deepcopy(group) for group in self.groups.values()]
        if entity_type == "upsell_group":
            return [deepcopy(group) for group in self.upsells.values()]
        if entity_type == "category":
            return [deepcopy(category) for category in self.categories.values()]
        return []

    def get_entity(self, entity_type, entity_id):
        if entity_type == "menu_item":
            return self.get_item(entity_id)
        if entity_type == "option_group":
            return self.get_option_group(entity_id)
        if entity_type == "upsell_group":
            return self.get_upsell_group(entity_id)
        if entity_type == "category":
            return deepcopy(self.categories.get(entity_id))
        return None

    def save_entity(self, entity):
        sk = entity["SK"]
        if sk.startswith("ITEM#"):
            self.items[entity["product_id"]] = deepcopy(entity)
        elif sk.startswith("OPTION_GROUP#"):
            self.groups[entity["option_group_id"]] = deepcopy(entity)
        elif sk.startswith("UPSELL_GROUP#"):
            self.upsells[entity["upsell_group_id"]] = deepcopy(entity)
        elif sk.startswith("CATEGORY#"):
            self.categories[entity["category_id"]] = deepcopy(entity)


class MemoryCartRepository:
    def __init__(self):
        self.data = {}

    def create(self, cart):
        self.data[cart["cart_id"]] = deepcopy(cart)

    def find_by_cart_id(self, user_id, cart_id):
        cart = self.data.get(cart_id)
        if not cart or cart.get("user_id") != user_id:
            return None
        return deepcopy(cart)

    def find_by_cart_item_id(self, user_id, item_id):
        return next((deepcopy(cart) for cart in self.data.values()
                     if cart.get("user_id") == user_id
                     and item_id in cart["cart_item_ids"]), None)

    def find_active_by_session(self, user_id, agent_session_id, terminal_statuses):
        matches = [
            deepcopy(cart) for cart in self.data.values()
            if cart["user_id"] == user_id
            and cart["agent_session_id"] == agent_session_id
            and cart["status"] not in terminal_statuses
        ]
        return max(matches, key=lambda cart: cart.get("updated_at", "")) if matches else None

    def save(self, cart, expected_version):
        assert self.data[cart["cart_id"]]["version"] == expected_version
        saved = deepcopy(cart)
        saved["version"] = expected_version + 1
        self.data[cart["cart_id"]] = saved


class MemoryOrderRepository:
    def __init__(self):
        self.data = {}

    def create(self, order):
        if order["order_id"] in self.data:
            return False
        self.data[order["order_id"]] = deepcopy(order)
        return True

    def get_by_order_id(self, order_id):
        return deepcopy(self.data.get(order_id))

    def get(self, user_id, order_id):
        order = self.data.get(order_id)
        if not order or order.get("user_id") != user_id:
            return None
        return deepcopy(order)

    def save(self, order, expected_version):
        assert self.data[order["order_id"]]["version"] == expected_version
        saved = deepcopy(order)
        saved["version"] = expected_version + 1
        self.data[order["order_id"]] = saved

    def list_active(self, user_id, terminal_statuses):
        return [deepcopy(order) for order in self.data.values()
                if order["user_id"] == user_id and order["status"] not in terminal_statuses]

    def list_all(self):
        return [deepcopy(order) for order in self.data.values()]


class MemoryAgentSessionRepository:
    SUPPORT_FIELDS = {
        "pending_support_intent",
        "pending_order_id",
        "pending_complaint_description",
        "pending_support_updated_at",
    }
    VERIFIED_ORDER_FIELDS = {
        "verified_order_id",
        "verified_order_status",
        "verified_order_at",
    }

    def __init__(self):
        self.data = {}
        self.conflicts_remaining = 0
        self.on_conflict = None
        self.clear_conflicts_remaining = 0
        self.on_clear_conflict = None
        self.clear_calls = 0

    def create(self, session):
        self.data[session["agent_session_id"]] = deepcopy(session)

    def get(self, agent_session_id):
        return deepcopy(self.data.get(agent_session_id))

    def get_owned(self, customer_id, agent_session_id):
        from src.repositories.agent_session_repository import SessionNotFoundError

        try:
            return deepcopy(self._get_owned_session(customer_id, agent_session_id))
        except SessionNotFoundError:
            return None

    def save(self, session):
        self.data[session["agent_session_id"]] = deepcopy(session)

    def _get_owned_session(self, customer_id, agent_session_id):
        from src.repositories.agent_session_repository import (
            SessionNotFoundError,
        )

        session = self.data.get(agent_session_id)
        if (
            session is None
            or session.get("PK") != f"CUSTOMER#{customer_id}"
            or session.get("SK") != f"SESSION#{agent_session_id}"
            or session.get("customer_id") != customer_id
            or session.get("agent_session_id") != agent_session_id
        ):
            raise SessionNotFoundError
        return session

    def get_support_state(self, customer_id, agent_session_id):
        session = self._get_owned_session(customer_id, agent_session_id)
        return {
            key: deepcopy(value)
            for key, value in session.items()
            if key in self.SUPPORT_FIELDS
        }

    def get_verified_order_context(self, customer_id, agent_session_id):
        session = self._get_owned_session(customer_id, agent_session_id)
        return {
            key: deepcopy(value)
            for key, value in session.items()
            if key in self.VERIFIED_ORDER_FIELDS
        }

    def update_verified_order_context(
        self,
        customer_id,
        agent_session_id,
        *,
        order_id,
        status,
        verified_at,
    ):
        session = self._get_owned_session(customer_id, agent_session_id)
        session["verified_order_id"] = order_id
        session["verified_order_status"] = status
        session["verified_order_at"] = verified_at

    def clear_verified_order_context(
        self,
        customer_id,
        agent_session_id,
        *,
        expected_verified_at,
    ):
        session = self._get_owned_session(customer_id, agent_session_id)
        if session.get("verified_order_at") != expected_verified_at:
            raise SupportStateConflictError
        for field in self.VERIFIED_ORDER_FIELDS:
            session.pop(field, None)

    def update_support_state(
        self,
        customer_id,
        agent_session_id,
        expected_updated_at,
        intent,
        order_id,
        description,
        updated_at,
    ):
        from src.repositories.agent_session_repository import (
            SupportStateConflictError,
        )

        session = self._get_owned_session(customer_id, agent_session_id)
        actual = session.get("pending_support_updated_at")
        if self.conflicts_remaining:
            self.conflicts_remaining -= 1
            if self.on_conflict:
                self.on_conflict()
            raise SupportStateConflictError
        if actual != expected_updated_at:
            raise SupportStateConflictError
        session["pending_support_intent"] = intent
        session["pending_support_updated_at"] = updated_at
        if order_id is None:
            session.pop("pending_order_id", None)
        else:
            session["pending_order_id"] = order_id
        if description is None:
            session.pop("pending_complaint_description", None)
        else:
            session["pending_complaint_description"] = description

    def clear_support_state(
        self,
        customer_id,
        agent_session_id,
        expected_updated_at=None,
    ):
        from src.repositories.agent_session_repository import (
            SupportStateConflictError,
        )

        session = self._get_owned_session(customer_id, agent_session_id)
        self.clear_calls += 1
        actual = session.get("pending_support_updated_at")
        if self.clear_conflicts_remaining:
            self.clear_conflicts_remaining -= 1
            if self.on_clear_conflict:
                self.on_clear_conflict()
            raise SupportStateConflictError
        if self.conflicts_remaining:
            self.conflicts_remaining -= 1
            if self.on_conflict:
                self.on_conflict()
            raise SupportStateConflictError
        if actual != expected_updated_at:
            raise SupportStateConflictError
        for field in self.SUPPORT_FIELDS:
            session.pop(field, None)


class MemoryTicketRepository:
    def __init__(self):
        self.data = {}
        self.markers = {}
        self.guards = {}
        self.ticket_collision_count = 0
        self.version_conflict_count = 0
        self.guard_conflict_count = 0
        self.reusable_change_count = 0
        self.bind_error = None
        self.save_error = None
        self.customer_pages = None
        self.save_calls = []
        self.query_status_calls = []
        self.query_status_error = None
        self.query_status_responses = None
        self.query_page_size_override = None

    @staticmethod
    def _marker_hash(marker):
        return marker["PK"].removeprefix("IDEMPOTENCY#")

    @staticmethod
    def _guard_identity(guard):
        return (guard["user_id"], guard["session_id"])

    @staticmethod
    def _guard_matches(existing, expected):
        return bool(
            existing
            and expected
            and existing["version"] == expected["version"]
            and existing["active_ticket_id"] == expected["active_ticket_id"]
        )

    def create_with_idempotency(
        self,
        ticket,
        marker,
        now_epoch,
        *,
        guard=None,
        expected_guard=None,
    ):
        ticket = validate_ticket_for_write(ticket)
        marker = validate_marker_for_ticket(marker, ticket)
        if guard is not None:
            guard = validate_guard_for_ticket(guard, ticket)
        if self.ticket_collision_count:
            self.ticket_collision_count -= 1
            raise TicketIdCollisionError
        if ticket["ticket_id"] in self.data:
            raise TicketIdCollisionError
        marker_hash = self._marker_hash(marker)
        existing_marker = self.markers.get(marker_hash)
        if existing_marker and existing_marker["expires_at"] > now_epoch:
            raise IdempotencyConflictError
        if guard is not None:
            if self.guard_conflict_count:
                self.guard_conflict_count -= 1
                raise HumanSessionGuardConflictError
            guard_identity = self._guard_identity(guard)
            existing_guard = self.guards.get(guard_identity)
            if expected_guard is None:
                if existing_guard:
                    raise HumanSessionGuardConflictError
            elif not self._guard_matches(existing_guard, expected_guard):
                raise HumanSessionGuardConflictError
        self.data[ticket["ticket_id"]] = deepcopy(ticket)
        self.markers[marker_hash] = deepcopy(marker)
        if guard is not None:
            self.guards[self._guard_identity(guard)] = deepcopy(guard)

    def bind_human_reuse(
        self,
        ticket,
        marker,
        guard,
        now_epoch,
        *,
        expected_guard=None,
    ):
        ticket = validate_ticket_for_write(ticket)
        marker = validate_marker_for_ticket(marker, ticket)
        guard = validate_guard_for_ticket(guard, ticket)
        if self.bind_error:
            error = self.bind_error
            self.bind_error = None
            raise error
        current = self.data.get(ticket["ticket_id"])
        if self.reusable_change_count:
            self.reusable_change_count -= 1
            if current:
                current["status"] = "closed"
            raise ReusableTicketChangedError
        if (
            not current
            or current.get("user_id") != ticket["user_id"]
            or current.get("session_id") != ticket["session_id"]
            or current.get("ticket_type") != "human_assistance"
            or current.get("status")
            not in {"open", "in_review", "waiting_for_customer"}
        ):
            raise ReusableTicketChangedError
        marker_hash = self._marker_hash(marker)
        existing_marker = self.markers.get(marker_hash)
        if existing_marker and existing_marker["expires_at"] > now_epoch:
            raise IdempotencyConflictError
        identity = self._guard_identity(guard)
        existing_guard = self.guards.get(identity)
        if expected_guard is None:
            if existing_guard:
                raise HumanSessionGuardConflictError
        elif not self._guard_matches(existing_guard, expected_guard):
            raise HumanSessionGuardConflictError
        self.markers[marker_hash] = deepcopy(marker)
        self.guards[identity] = deepcopy(guard)

    def get(self, ticket_id):
        return deepcopy(self.data.get(ticket_id))

    def get_idempotency_marker(self, idempotency_hash):
        return deepcopy(self.markers.get(idempotency_hash))

    def get_human_session_guard(self, user_id, session_id):
        guard = self.guards.get((user_id, session_id))
        return deepcopy(guard)

    def list_for_customer(self, user_id):
        if self.customer_pages is not None:
            return [
                deepcopy(ticket)
                for page in self.customer_pages
                for ticket in page
                if ticket["user_id"] == user_id
            ]
        tickets = [
            deepcopy(ticket)
            for ticket in self.data.values()
            if ticket["user_id"] == user_id
        ]
        return sorted(tickets, key=lambda ticket: ticket["GSI1SK"])

    @staticmethod
    def _ticket_cursor(ticket):
        return {
            "PK": ticket["PK"],
            "SK": ticket["SK"],
            "GSI2PK": ticket["GSI2PK"],
            "GSI2SK": ticket["GSI2SK"],
        }

    def query_status_page(self, status, *, limit, exclusive_start_key=None):
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("query limit must be between 1 and 100")
        self.query_status_calls.append({
            "status": status,
            "limit": limit,
            "exclusive_start_key": deepcopy(exclusive_start_key),
        })
        if self.query_status_error:
            raise self.query_status_error
        if self.query_status_responses is not None:
            if not self.query_status_responses:
                raise AssertionError("no fake status-query response remains")
            return deepcopy(self.query_status_responses.pop(0))
        tickets = sorted(
            (
                deepcopy(ticket)
                for ticket in self.data.values()
                if ticket["GSI2PK"] == f"STATUS#{status}"
            ),
            key=lambda ticket: ticket["GSI2SK"],
            reverse=True,
        )
        start = 0
        if exclusive_start_key is not None:
            if set(exclusive_start_key) != {
                "PK",
                "SK",
                "GSI2PK",
                "GSI2SK",
            } or any(
                not isinstance(value, str) or not value
                for value in exclusive_start_key.values()
            ):
                raise ValueError("invalid exclusive start key")
            if (
                exclusive_start_key["SK"] != "METADATA"
                or exclusive_start_key["GSI2PK"] != f"STATUS#{status}"
                or not exclusive_start_key["PK"].startswith("TICKET#")
            ):
                raise ValueError("invalid exclusive start key")
            start = next(
                (
                    index
                    for index, ticket in enumerate(tickets)
                    if ticket["GSI2SK"]
                    < exclusive_start_key["GSI2SK"]
                ),
                len(tickets),
            )
        effective_limit = self.query_page_size_override or limit
        page = tickets[start:start + effective_limit]
        has_more = start + len(page) < len(tickets)
        return {
            "items": deepcopy(page),
            "last_evaluated_key": (
                self._ticket_cursor(page[-1])
                if page and has_more
                else None
            ),
        }

    def save(self, ticket, expected_version):
        if self.save_error:
            raise self.save_error
        if self.version_conflict_count:
            self.version_conflict_count -= 1
            raise TicketVersionConflictError
        current = self.data.get(ticket["ticket_id"])
        if not current or current["version"] != expected_version:
            raise TicketVersionConflictError
        saved = validate_ticket_for_write(ticket)
        if saved["version"] != expected_version:
            raise ValueError("ticket version does not match expected_version")
        saved["version"] = expected_version + 1
        self.data[saved["ticket_id"]] = saved
        self.save_calls.append((deepcopy(saved), expected_version))
