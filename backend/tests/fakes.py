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

    def find_by_cart_id(self, cart_id):
        return deepcopy(self.data.get(cart_id))

    def find_by_cart_item_id(self, item_id):
        return next((deepcopy(cart) for cart in self.data.values()
                     if item_id in cart["cart_item_ids"]), None)

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
        self.data[order["order_id"]] = deepcopy(order)

    def get_by_order_id(self, order_id):
        return deepcopy(self.data.get(order_id))

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
        self.customer_pages = None
        self.save_calls = []

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

    def list_by_status(self, status, limit=50, exclusive_start_key=None):
        tickets = sorted(
            (
                deepcopy(ticket)
                for ticket in self.data.values()
                if ticket["status"] == status
            ),
            key=lambda ticket: ticket["GSI2SK"],
        )
        return {"items": tickets[:limit], "next_cursor": None}

    def save(self, ticket, expected_version):
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
