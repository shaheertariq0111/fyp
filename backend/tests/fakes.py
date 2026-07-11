from copy import deepcopy


class MemoryMenuRepository:
    def __init__(self, items, groups, upsells=None):
        self.items = {item["product_id"]: deepcopy(item) for item in items}
        self.groups = {group["option_group_id"]: deepcopy(group) for group in groups}
        self.upsells = {group["upsell_group_id"]: deepcopy(group) for group in upsells or []}

    def get_item(self, item_id):
        return deepcopy(self.items.get(item_id))

    def get_option_group(self, group_id):
        return deepcopy(self.groups.get(group_id))

    def get_upsell_group(self, group_id):
        return deepcopy(self.upsells.get(group_id))

    def search(self, available_only=True):
        return [deepcopy(item) for item in self.items.values()
                if not available_only or item["available"]]


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
