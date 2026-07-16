from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
import re

from src.models.tool_responses import ToolResponse


ORDER_HANDOFF_CART_STATUS = "converted_to_order"
TERMINAL_CART_STATUSES = {
    ORDER_HANDOFF_CART_STATUS,
    "pending_confirmation",  # Legacy cart handoff status from older checkout flow.
    "cancelled",
    "expired",
}


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
                                 quantity: int = 1, customer_id: str | None = None,
                                 customer_name: str | None = None,
                                 customer_phone: str | None = None) -> ToolResponse:
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
            "customer_id": customer_id or user_id, "customer_name": customer_name,
            "customer_phone": customer_phone,
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
                agent=self._cart_agent(
                    cart,
                    "set_customization_mode",
                    required_input="customization_mode",
                    choices=[
                        {"label": "Same", "value": "same"},
                        {"label": "Customize separately", "value": "separate"},
                    ],
                    instruction="Ask whether these items should use the same customization or be customized separately.",
                ),
                buttons=[
                    {"label": "Same", "action": "set_customization_mode",
                     "metadata": {"cart_id": cart_id, "mode": "same"}},
                    {"label": "Customize separately", "action": "set_customization_mode",
                     "metadata": {"cart_id": cart_id, "mode": "separate"}},
                ],
            )
        if cart["status"] == "item_ready":
            return ToolResponse.ok(data=self._cart_data(cart),
                                   user_message="The item is ready.", next_action="offer_upsell",
                                   agent=self._cart_agent(
                                       cart,
                                       "offer_upsell",
                                       instruction="Offer backend upsells or skip add-ons before creating a pending order.",
                                   ))
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
        completed_was_upsell = item.get("is_upsell", False)
        item["selected_options"][field_name] = selected_option_id
        self._refresh_item(item, menu_item)
        if not item["missing_required_fields"]:
            self._advance_active_item(cart)
            if completed_was_upsell and cart["status"] == "item_ready":
                cart["status"] = "cart_ready"
        self._recalculate(cart)
        self._save(cart)
        if cart["status"] == "cart_ready":
            return ToolResponse.ok(data=self._cart_data(cart),
                                   user_message="Your cart is ready.",
                                   next_action="create_pending_order",
                                   agent=self._cart_agent(
                                       cart,
                                       "create_pending_order",
                                       instruction="Cart is ready. If the customer wants to proceed, call create_pending_order_from_cart with this cart_id.",
                                   ))
        if cart["status"] == "item_ready":
            return ToolResponse.ok(data=self._cart_data(cart),
                                   user_message="All required item choices are complete.",
                                   next_action="offer_upsell",
                                   agent=self._cart_agent(
                                       cart,
                                       "offer_upsell",
                                       instruction="Offer backend upsells or skip add-ons before creating a pending order.",
                                   ))
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
            upsell_items = list(allowed.values())
            return ToolResponse.ok(data={**self._cart_data(cart), "upsell_items": upsell_items},
                                   user_message="Here are the currently available add-ons.",
                                   next_action="choose_upsell",
                                   agent=self._cart_agent(
                                       cart,
                                       "choose_upsell",
                                       required_input="upsell_decision",
                                       upsell_items=upsell_items,
                                       instruction="Offer only these upsell_items. If the customer declines, call handle_cart_upsell with action skip.",
                                   ))
        if action == "add_item":
            if quantity < 1 or not item_id or item_id not in allowed:
                return ToolResponse.error(error_code="INVALID_UPSELL_ITEM",
                                          user_message="Please choose an available add-on.")
            menu_item = self.menu.get_item(item_id)
            entry = self._new_item(menu_item, quantity, menu_item["name"], is_upsell=True)
            self._refresh_item(entry, menu_item)
            cart["items"].append(entry)
            cart["cart_item_ids"].append(entry["cart_item_id"])
            if entry["missing_required_fields"]:
                cart["active_cart_item_id"] = entry["cart_item_id"]
                cart["status"] = "customizing_item"
            self._recalculate(cart)
            self._save(cart)
            if entry["missing_required_fields"]:
                return self._next_choice_response(cart)
            cart["status"] = "cart_ready"
            self._save(cart)
            return ToolResponse.ok(data=self._cart_data(cart), user_message="The add-on was added.",
                                   next_action="create_pending_order",
                                   agent=self._cart_agent(
                                       cart,
                                       "create_pending_order",
                                       instruction="One add-on was added. Do not offer more add-ons; proceed to checkout when the customer is ready.",
                                   ))
        cart["status"] = "cart_ready"
        self._save(cart)
        return ToolResponse.ok(data=self._cart_data(cart), user_message="Your cart is ready.",
                               next_action="create_pending_order",
                               agent=self._cart_agent(
                                   cart,
                                   "create_pending_order",
                                   instruction="Cart is ready. If the customer wants to proceed, call create_pending_order_from_cart with this cart_id.",
                               ))

    def create_pending_order(self, cart_id: str) -> ToolResponse:
        cart = self.carts.find_by_cart_id(cart_id)
        if not cart:
            return ToolResponse.error(error_code="CART_NOT_FOUND", user_message="I couldn't find that cart.")
        if cart["status"] in {"item_ready", "awaiting_upsell_decision"}:
            cart["status"] = "cart_ready"
            self._save(cart)
        if cart["status"] != "cart_ready":
            return ToolResponse.error(error_code="CART_NOT_READY",
                                      user_message="Please complete the cart before creating an order.")
        validation = self._validate_and_reprice(cart)
        if validation:
            return validation
        response = self.order_service.create_pending_from_cart(cart)
        if response.success:
            cart["status"] = ORDER_HANDOFF_CART_STATUS
            self._save(cart)
        return response

    def create_pending_from_menu_order(
        self,
        user_id: str,
        session_id: str,
        items: list[dict],
        customer_id: str | None = None,
        customer_name: str | None = None,
        customer_phone: str | None = None,
    ) -> ToolResponse:
        if not items:
            return ToolResponse.error(error_code="CART_EMPTY", user_message="Please add an item first.")
        now = self._now()
        cart_id = self._id("CART")
        cart = {
            "PK": user_id, "SK": f"CART#{cart_id}", "cart_id": cart_id,
            "user_id": user_id, "agent_session_id": session_id,
            "customer_id": customer_id or user_id, "customer_name": customer_name,
            "customer_phone": customer_phone,
            "restaurant_id": self.settings.restaurant_id, "branch_id": self.settings.branch_id,
            "status": "cart_ready", "customization_mode": "website",
            "requested_quantity": 1, "source_item_id": None,
            "active_cart_item_id": None, "items": [], "cart_item_ids": [],
            "subtotal": 0, "currency": None, "version": 1,
            "created_at": now, "updated_at": now,
        }
        for index, source_item in enumerate(items, 1):
            quantity = source_item.get("quantity", 1)
            if quantity < 1:
                return ToolResponse.error(error_code="INVALID_QUANTITY",
                                          user_message="Quantity must be at least one.")
            menu_item = self.menu.get_item(source_item.get("item_id"))
            if not menu_item:
                return ToolResponse.error(error_code="ITEM_NOT_FOUND",
                                          user_message="I couldn't find that menu item.")
            if not menu_item.get("available"):
                return ToolResponse.error(error_code="ITEM_UNAVAILABLE",
                                          user_message="That item is currently unavailable.")
            if cart["currency"] is None:
                cart["currency"] = menu_item["currency"]
            elif cart["currency"] != menu_item["currency"]:
                return ToolResponse.error(error_code="INVALID_CART",
                                          user_message="Cart items must use the same currency.")
            entry = self._new_item(menu_item, quantity, source_item.get("label") or f"Item {index}",
                                   is_upsell=source_item.get("is_upsell", False))
            entry["selected_options"] = dict(source_item.get("selected_options") or {})
            invalid = self._validate_selected_options(entry, menu_item)
            if invalid:
                return invalid
            self._refresh_item(entry, menu_item)
            if entry["missing_required_fields"]:
                return ToolResponse.error(error_code="CART_NOT_READY",
                                          user_message="A required customization is incomplete.")
            cart["items"].append(entry)
            cart["cart_item_ids"].append(entry["cart_item_id"])
        self._recalculate(cart)
        validation = self._validate_and_reprice(cart)
        if validation:
            return validation
        self.carts.create(cart)
        response = self.order_service.create_pending_from_cart(cart)
        if response.success:
            cart["status"] = ORDER_HANDOFF_CART_STATUS
            self._save(cart)
        return response

    def get_active_cart(self, user_id: str, session_id: str) -> ToolResponse:
        cart = self.carts.find_active_by_session(user_id, session_id, TERMINAL_CART_STATUSES)
        if not cart:
            order_result = self.order_service.get_order_status(user_id)
            active_orders = []
            active_order_guidance = []
            if order_result.success:
                active_orders = order_result.data.get("orders", [])
                active_order_guidance = (order_result.agent or {}).get("orders", [])
            return ToolResponse.ok(
                data={"cart": None, "orders": active_orders},
                user_message=("There isn't an active cart for this chat session."
                              if not active_orders else
                              "There isn't an active cart, but there is an active order."),
                next_action="present_cart_status",
                agent={
                    "entity": "cart",
                    "cart": None,
                    "orders": active_order_guidance,
                    "state": "no_active_cart",
                    "valid_next_actions": ["get_order_status", "update_order_flow",
                                           "search_menu", "create_menu_session_link"],
                    "instruction": (
                        "Tell the customer no active chat cart was found. If orders are present, "
                        "summarize the active order status using those order IDs and continue the "
                        "order flow from the order state. Do not use a cart_id as an order_id. If "
                        "there are no orders, offer to search the menu or open the website."
                    ),
                },
            )
        data = self._cart_data(cart)
        if cart.get("status") == "customizing_item" and cart.get("active_cart_item_id"):
            data.update(self._active_choice_data(cart))
        return ToolResponse.ok(
            data={"cart": data},
            user_message="Here is the current cart.",
            next_action="present_cart_status",
            agent=self._cart_agent(
                cart,
                "present_cart_status",
                active_choice=data if cart.get("status") == "customizing_item" else None,
                instruction="Present the current cart from data.cart. If a question is present, ask that question next.",
            ),
        )

    def add_item_to_active_cart(
        self,
        user_id: str,
        session_id: str,
        item_id: str,
        quantity: int = 1,
    ) -> ToolResponse:
        if quantity < 1:
            return ToolResponse.error(error_code="INVALID_QUANTITY",
                                      user_message="Quantity must be at least one.")
        cart = self.carts.find_active_by_session(user_id, session_id, TERMINAL_CART_STATUSES)
        if not cart:
            return self.start_item_customization(user_id, session_id, item_id, quantity)
        if cart.get("status") == "customizing_item":
            return ToolResponse.error(
                error_code="INVALID_CART_STATE",
                user_message="Please finish the current item choices before adding another item.",
            )
        if cart.get("status") not in {"item_ready", "awaiting_upsell_decision", "cart_ready"}:
            return ToolResponse.error(
                error_code="INVALID_CART_STATE",
                user_message="This cart can't accept another item right now.",
            )
        menu_item = self.menu.get_item(item_id)
        if not menu_item:
            return ToolResponse.error(error_code="ITEM_NOT_FOUND",
                                      user_message="I couldn't find that menu item.")
        if not menu_item.get("available"):
            return ToolResponse.error(error_code="ITEM_UNAVAILABLE",
                                      user_message="That item is currently unavailable.")
        if cart.get("currency") and cart["currency"] != menu_item["currency"]:
            return ToolResponse.error(error_code="INVALID_CART",
                                      user_message="Cart items must use the same currency.")
        entry = self._new_item(menu_item, quantity, menu_item["name"])
        cart["items"].append(entry)
        cart["cart_item_ids"].append(entry["cart_item_id"])
        if entry["missing_required_fields"]:
            cart["active_cart_item_id"] = entry["cart_item_id"]
            cart["status"] = "customizing_item"
        else:
            cart["active_cart_item_id"] = None
            cart["status"] = "item_ready"
        self._recalculate(cart)
        self._save(cart)
        if entry["missing_required_fields"]:
            return self._next_choice_response(cart)
        return ToolResponse.ok(
            data=self._cart_data(cart),
            user_message="The item was added to your cart.",
            next_action="offer_upsell",
            agent=self._cart_agent(
                cart,
                "offer_upsell",
                instruction="Item was added. Offer backend upsells or skip add-ons before creating a pending order.",
            ),
        )

    def save_active_choice(self, user_id: str, session_id: str, choice_text: str) -> ToolResponse:
        cart = self.carts.find_active_by_session(user_id, session_id, TERMINAL_CART_STATUSES)
        if not cart:
            return ToolResponse.error(
                error_code="CART_NOT_FOUND",
                user_message="There isn't an active cart for this chat session.",
            )
        if cart.get("status") != "customizing_item" or not cart.get("active_cart_item_id"):
            return ToolResponse.error(
                error_code="INVALID_CART_STATE",
                user_message="The active cart is not waiting for an item choice.",
            )
        item = next(
            entry for entry in cart["items"]
            if entry["cart_item_id"] == cart["active_cart_item_id"]
        )
        menu_item = self.menu.get_item(item["item_id"])
        group = next(
            group for group in self._groups(menu_item)
            if group["option_group_id"] == item["current_step"]
        )
        option = self._match_option(group, choice_text)
        if not option:
            options = ", ".join(
                option.get("name") or option.get("label") or option["option_id"]
                for option in group.get("options", [])
                if option.get("available", True)
            )
            return ToolResponse.error(
                error_code="INVALID_OPTION",
                user_message=f"{group['question']} Available options: {options}.",
            )
        return self.save_choice(
            item["cart_item_id"],
            group["option_group_id"],
            option["option_id"],
        )

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

    def _validate_selected_options(self, item, menu_item):
        groups = {group["option_group_id"]: group for group in self._groups(menu_item)}
        for field, selected in item["selected_options"].items():
            group = groups.get(field)
            if not group:
                return ToolResponse.error(error_code="INVALID_CUSTOMIZATION",
                                          user_message="That customization isn't valid for this item.")
            option_ids = [option.get("option_id") for option in group.get("options", [])]
            selected_ids = selected if isinstance(selected, list) else [selected]
            if any(option_id not in option_ids for option_id in selected_ids):
                return ToolResponse.error(error_code="INVALID_OPTION",
                                          user_message="Please choose one of the available options.")
        return None

    @classmethod
    def _match_option(cls, group, choice_text: str):
        normalized_choice = cls._normalize(choice_text)
        choice_tokens = set(cls._tokens(choice_text))
        if not normalized_choice:
            return None
        matches = []
        for option in group.get("options", []):
            if not option.get("available", True):
                continue
            values = [
                option.get("option_id", ""),
                option.get("name", ""),
                option.get("label", ""),
            ]
            normalized_values = {cls._normalize(value) for value in values if value}
            value_tokens = set().union(*(set(cls._tokens(value)) for value in values if value))
            if (
                normalized_choice in normalized_values
                or any(normalized_choice and normalized_choice in value for value in normalized_values)
                or (choice_tokens and choice_tokens.issubset(value_tokens))
            ):
                matches.append(option)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _tokens(value: str) -> list[str]:
        return re.findall(r"[\w-]+", value.casefold())

    @classmethod
    def _normalize(cls, value: str) -> str:
        return " ".join(cls._tokens(value))

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
            agent=self._cart_agent(
                cart,
                "ask_customization_choice",
                active_choice={
                    "cart_item_id": item["cart_item_id"],
                    "label": item["label"],
                    "field_name": group["option_group_id"],
                    "question": group["question"],
                    "options": group["options"],
                },
                required_input="customization_choice",
                instruction="Ask this exact question and save one of these option IDs with save_customization_choice.",
            ),
        )

    def _active_choice_data(self, cart):
        item = next(entry for entry in cart["items"]
                    if entry["cart_item_id"] == cart["active_cart_item_id"])
        menu_item = self.menu.get_item(item["item_id"])
        group = next(group for group in self._groups(menu_item)
                     if group["option_group_id"] == item["current_step"])
        return {
            "cart_item_id": item["cart_item_id"],
            "label": item["label"],
            "field_name": group["option_group_id"],
            "question": group["question"],
            "options": group["options"],
        }

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
                                           ("product_id", "name", "starting_price", "currency",
                                            "requires_customization", "customization_group_ids")}
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

    def _cart_agent(
        self,
        cart,
        next_action,
        *,
        active_choice=None,
        choices=None,
        upsell_items=None,
        required_input=None,
        instruction=None,
    ):
        payload = {
            "entity": "cart",
            "cart_id": cart.get("cart_id"),
            "cart_status": cart.get("status"),
            "next_action": next_action,
            "required_input": required_input,
            "valid_next_actions": self._cart_valid_next_actions(cart, next_action),
            "cart_summary": {
                "items": [
                    {
                        "cart_item_id": item.get("cart_item_id"),
                        "label": item.get("label"),
                        "item_id": item.get("item_id"),
                        "name": item.get("name"),
                        "quantity": item.get("quantity"),
                        "selected_options": deepcopy(item.get("selected_options", {})),
                        "missing_required_fields": list(item.get("missing_required_fields", [])),
                        "current_step": item.get("current_step"),
                        "line_total": item.get("current_price"),
                        "is_upsell": item.get("is_upsell", False),
                    }
                    for item in cart.get("items", [])
                ],
                "subtotal": cart.get("subtotal"),
                "currency": cart.get("currency"),
            },
        }
        if active_choice:
            payload["active_choice"] = active_choice
        if choices:
            payload["choices"] = choices
        if upsell_items is not None:
            payload["upsell_items"] = upsell_items
        if instruction:
            payload["instruction"] = instruction
        return payload

    @staticmethod
    def _cart_valid_next_actions(cart, next_action):
        if next_action == "set_customization_mode":
            return ["set_customization_mode"]
        if next_action == "ask_customization_choice":
            return ["save_customization_choice"]
        if next_action == "offer_upsell":
            return ["handle_cart_upsell:get_options", "handle_cart_upsell:skip"]
        if next_action == "choose_upsell":
            return ["handle_cart_upsell:add_item", "handle_cart_upsell:skip"]
        if next_action == "create_pending_order":
            return ["create_pending_order_from_cart"]
        if next_action == "present_cart_status":
            status = cart.get("status")
            if status == "customizing_item":
                return ["save_customization_choice"]
            if status in {"item_ready", "awaiting_upsell_decision"}:
                return ["handle_cart_upsell:get_options", "handle_cart_upsell:skip"]
            if status == "cart_ready":
                return ["create_pending_order_from_cart"]
        return []
