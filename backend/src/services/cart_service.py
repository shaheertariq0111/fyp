from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone

from src.models.tool_responses import ToolResponse


class CartService:
    def __init__(self, cart_repository, menu_repository, order_service, settings):
        self.carts = cart_repository
        self.menu = menu_repository
        self.order_service = order_service
        self.settings = settings

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4()}"

    def start_item_customization(self, user_id: str, session_id: str, item_id: str,
                                 quantity: int = 1) -> ToolResponse:
        if quantity < 1:
            return ToolResponse.error(error_code="INVALID_QUANTITY",
                                      user_message="Quantity must be at least one.")
        menu_item = self.menu.get_item(item_id)
        if not menu_item:
            return ToolResponse.error(error_code="ITEM_NOT_FOUND",
                                      user_message="I couldn't find that menu item.")
        if not menu_item.get("available"):
            return ToolResponse.error(error_code="ITEM_UNAVAILABLE",
                                      user_message="That item is currently unavailable.")
        groups = self._groups(menu_item)
        now = self._now()
        cart_id = self._id("CART")
        cart = {
            "PK": user_id, "SK": f"CART#{cart_id}", "cart_id": cart_id,
            "user_id": user_id, "agent_session_id": session_id,
            "restaurant_id": self.settings.restaurant_id, "branch_id": self.settings.branch_id,
            "status": "cart_created" if quantity > 1 and groups else "customizing_item",
            "customization_mode": None if quantity > 1 and groups else "single",
            "requested_quantity": quantity, "source_item_id": item_id,
            "active_cart_item_id": None, "items": [], "cart_item_ids": [],
            "subtotal": 0, "currency": menu_item["currency"], "version": 1,
            "created_at": now, "updated_at": now,
        }
        if not (quantity > 1 and groups):
            cart["items"] = [self._new_item(menu_item, quantity, "Item 1")]
            cart["cart_item_ids"] = [cart["items"][0]["cart_item_id"]]
            cart["active_cart_item_id"] = cart["items"][0]["cart_item_id"]
            self._recalculate(cart)
            if not cart["items"][0]["missing_required_fields"]:
                cart["active_cart_item_id"] = None
                cart["status"] = "item_ready"
        self.carts.create(cart)
        if quantity > 1 and groups:
            return ToolResponse.ok(
                data=self._cart_data(cart),
                user_message="Should these items use the same customization or be customized separately?",
                next_action="set_customization_mode",
                buttons=[
                    {"label": "Same", "action": "set_customization_mode",
                     "metadata": {"cart_id": cart_id, "mode": "same"}},
                    {"label": "Customize separately", "action": "set_customization_mode",
                     "metadata": {"cart_id": cart_id, "mode": "separate"}},
                ],
            )
        if cart["status"] == "item_ready":
            return ToolResponse.ok(data=self._cart_data(cart),
                                   user_message="The item is ready.", next_action="offer_upsell")
        return self._next_choice_response(cart)

    def set_customization_mode(self, cart_id: str, mode: str) -> ToolResponse:
        if mode not in {"same", "separate"}:
            return ToolResponse.error(error_code="INVALID_CUSTOMIZATION_MODE",
                                      user_message="Choose same or separate customization.")
        cart = self.carts.find_by_cart_id(cart_id)
        if not cart:
            return ToolResponse.error(error_code="CART_NOT_FOUND", user_message="I couldn't find that cart.")
        if cart["status"] != "cart_created" or cart.get("items"):
            return ToolResponse.error(error_code="INVALID_CART_STATE",
                                      user_message="That customization mode can't be changed now.")
        item = self.menu.get_item(cart["source_item_id"])
        quantity = cart["requested_quantity"]
        if mode == "same":
            cart["items"] = [self._new_item(item, quantity, f"Items 1–{quantity}")]
        else:
            cart["items"] = [self._new_item(item, 1, f"Item {index} of {quantity}")
                             for index in range(1, quantity + 1)]
        cart["cart_item_ids"] = [entry["cart_item_id"] for entry in cart["items"]]
        cart["customization_mode"] = mode
        cart["status"] = "customizing_item"
        cart["active_cart_item_id"] = cart["items"][0]["cart_item_id"]
        self._recalculate(cart)
        self._save(cart)
        return self._next_choice_response(cart)

    def save_choice(self, cart_item_id: str, field_name: str,
                    selected_option_id: str) -> ToolResponse:
        cart = self.carts.find_by_cart_item_id(cart_item_id)
        if not cart:
            return ToolResponse.error(error_code="CART_NOT_FOUND", user_message="I couldn't find that cart.")
        if cart["status"] != "customizing_item" or cart.get("active_cart_item_id") != cart_item_id:
            return ToolResponse.error(error_code="INVALID_CART_STATE",
                                      user_message="That item isn't awaiting this choice.")
        item = next(entry for entry in cart["items"] if entry["cart_item_id"] == cart_item_id)
        menu_item = self.menu.get_item(item["item_id"])
        groups = {group["option_group_id"]: group for group in self._groups(menu_item)}
        group = groups.get(field_name)
        if not group:
            return ToolResponse.error(error_code="INVALID_CUSTOMIZATION",
                                      user_message="That customization isn't valid for this item.")
        option = next((entry for entry in group.get("options", [])
                       if entry.get("option_id") == selected_option_id), None)
        if not option:
            return ToolResponse.error(error_code="INVALID_OPTION",
                                      user_message="Please choose one of the available options.")
        item["selected_options"][field_name] = selected_option_id
        self._refresh_item(item, menu_item)
        if not item["missing_required_fields"]:
            self._advance_active_item(cart)
        self._recalculate(cart)
        self._save(cart)
        if cart["status"] == "item_ready":
            return ToolResponse.ok(data=self._cart_data(cart),
                                   user_message="All required item choices are complete.",
                                   next_action="offer_upsell")
        return self._next_choice_response(cart)

    def handle_upsell(self, cart_id: str, action: str, item_id: str | None = None,
                      quantity: int = 1) -> ToolResponse:
        if action not in {"get_options", "add_item", "skip"}:
            return ToolResponse.error(error_code="INVALID_UPSELL_ACTION",
                                      user_message="That upsell action isn't supported.")
        cart = self.carts.find_by_cart_id(cart_id)
        if not cart:
            return ToolResponse.error(error_code="CART_NOT_FOUND", user_message="I couldn't find that cart.")
        if cart["status"] not in {"item_ready", "awaiting_upsell_decision"}:
            return ToolResponse.error(error_code="INVALID_CART_STATE",
                                      user_message="This cart isn't ready for add-ons.")
        allowed = self._upsell_items(cart)
        cart["status"] = "awaiting_upsell_decision"
        if action == "get_options":
            self._save(cart)
            return ToolResponse.ok(data={**self._cart_data(cart), "items": list(allowed.values())},
                                   user_message="Here are the currently available add-ons.",
                                   next_action="choose_upsell")
        if action == "add_item":
            if quantity < 1 or not item_id or item_id not in allowed:
                return ToolResponse.error(error_code="INVALID_UPSELL_ITEM",
                                          user_message="Please choose an available add-on.")
            menu_item = self.menu.get_item(item_id)
            entry = self._new_item(menu_item, quantity, menu_item["name"], is_upsell=True)
            self._refresh_item(entry, menu_item)
            cart["items"].append(entry)
            cart["cart_item_ids"].append(entry["cart_item_id"])
            self._recalculate(cart)
            self._save(cart)
            return ToolResponse.ok(data=self._cart_data(cart), user_message="The add-on was added.",
                                   next_action="choose_upsell")
        cart["status"] = "cart_ready"
        self._save(cart)
        return ToolResponse.ok(data=self._cart_data(cart), user_message="Your cart is ready.",
                               next_action="create_pending_order")

    def create_pending_order(self, cart_id: str) -> ToolResponse:
        cart = self.carts.find_by_cart_id(cart_id)
        if not cart:
            return ToolResponse.error(error_code="CART_NOT_FOUND", user_message="I couldn't find that cart.")
        if cart["status"] != "cart_ready":
            return ToolResponse.error(error_code="CART_NOT_READY",
                                      user_message="Please complete the cart before creating an order.")
        validation = self._validate_and_reprice(cart)
        if validation:
            return validation
        response = self.order_service.create_pending_from_cart(cart)
        if response.success:
            cart["status"] = "pending_confirmation"
            self._save(cart)
        return response

    def _new_item(self, menu_item, quantity, label, is_upsell=False):
        item = {
            "cart_item_id": self._id("CARTITEM"), "label": label,
            "item_id": menu_item["product_id"], "name": menu_item["name"],
            "quantity": quantity, "selected_options": {},
            "missing_required_fields": [], "current_step": None,
            "current_price": 0, "is_upsell": is_upsell,
        }
        self._refresh_item(item, menu_item)
        return item

    def _groups(self, menu_item):
        return [group for group_id in menu_item.get("customization_group_ids", [])
                if (group := self.menu.get_option_group(group_id))]

    def _refresh_item(self, item, menu_item):
        groups = self._groups(menu_item)
        required = [group["option_group_id"] for group in groups if group.get("required")]
        item["missing_required_fields"] = [field for field in required
                                           if field not in item["selected_options"]]
        item["current_step"] = (item["missing_required_fields"][0]
                                if item["missing_required_fields"] else None)
        unit_price = menu_item.get("starting_price", 0) or 0
        for group in groups:
            selected = item["selected_options"].get(group["option_group_id"])
            option = next((entry for entry in group.get("options", [])
                           if entry.get("option_id") == selected), None)
            if not option:
                continue
            if "price_key" in option:
                unit_price = menu_item.get("base_prices", {}).get(option["price_key"], unit_price)
            unit_price += option.get("price_delta", 0)
        item["current_price"] = unit_price * item["quantity"]

    def _advance_active_item(self, cart):
        current = next(index for index, item in enumerate(cart["items"])
                       if item["cart_item_id"] == cart["active_cart_item_id"])
        remaining = [item for item in cart["items"][current + 1:]
                     if item["missing_required_fields"]]
        if remaining:
            cart["active_cart_item_id"] = remaining[0]["cart_item_id"]
        else:
            cart["active_cart_item_id"] = None
            cart["status"] = "item_ready"

    def _recalculate(self, cart):
        cart["subtotal"] = sum(item["current_price"] for item in cart["items"])
        cart["updated_at"] = self._now()

    def _save(self, cart):
        version = cart["version"]
        self.carts.save(cart, version)
        cart["version"] = version + 1

    def _next_choice_response(self, cart):
        item = next(entry for entry in cart["items"]
                    if entry["cart_item_id"] == cart["active_cart_item_id"])
        menu_item = self.menu.get_item(item["item_id"])
        group = next(group for group in self._groups(menu_item)
                     if group["option_group_id"] == item["current_step"])
        return ToolResponse.ok(
            data={**self._cart_data(cart), "cart_item_id": item["cart_item_id"],
                  "label": item["label"], "field_name": group["option_group_id"],
                  "question": group["question"], "options": group["options"]},
            user_message=f"{item['label']}: {group['question']}",
            next_action="ask_customization_choice",
        )

    def _upsell_items(self, cart):
        result = {}
        for cart_item in cart["items"]:
            if cart_item.get("is_upsell"):
                continue
            source = self.menu.get_item(cart_item["item_id"])
            for group_id in source.get("upsell_group_ids", []):
                group = self.menu.get_upsell_group(group_id) or {}
                for item_id in group.get("items", []):
                    item = self.menu.get_item(item_id)
                    if item and item.get("available"):
                        result[item_id] = {key: item.get(key) for key in
                                           ("product_id", "name", "starting_price", "currency")}
        return result

    def _validate_and_reprice(self, cart):
        for item in cart["items"]:
            source = self.menu.get_item(item["item_id"])
            if not source or not source.get("available"):
                return ToolResponse.error(error_code="ITEM_UNAVAILABLE",
                                          user_message="An item in this cart is no longer available.")
            self._refresh_item(item, source)
            if item["missing_required_fields"]:
                return ToolResponse.error(error_code="CART_NOT_READY",
                                          user_message="A required customization is incomplete.")
        self._recalculate(cart)
        return None

    @staticmethod
    def _cart_data(cart):
        return {key: deepcopy(cart.get(key)) for key in
                ("cart_id", "status", "customization_mode", "active_cart_item_id",
                 "items", "subtotal", "currency", "version")}
