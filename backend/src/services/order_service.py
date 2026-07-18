from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone

from src.models.tool_responses import ToolResponse


ORDER_TRANSITIONS = {
    ("pending_confirmation", "confirm"): "submitted_to_restaurant",
    ("pending_confirmation", "cancel"): "rejected",
    ("awaiting_fulfillment_method", "set_delivery"): "awaiting_delivery_address",
    ("awaiting_fulfillment_method", "set_takeaway"): "pending_confirmation",
    ("awaiting_delivery_address", "save_address"): "pending_confirmation",
    ("awaiting_fulfillment_method", "cancel"): "cancelled",
    ("awaiting_delivery_address", "cancel"): "cancelled",
}

TERMINAL_STATUSES = {"delivered", "completed", "rejected", "cancelled", "failed"}
UNFINISHED_ORDER_STATUSES = {
    "awaiting_fulfillment_method",
    "awaiting_delivery_address",
    "pending_confirmation",
}
PLACED_ACTIVE_ORDER_STATUSES = {
    "submitted_to_restaurant",
    "accepted",
    "preparing",
    "ready_for_pickup",
    "out_for_delivery",
}
ACTIVE_ORDER_STATUSES = UNFINISHED_ORDER_STATUSES | PLACED_ACTIVE_ORDER_STATUSES
ADMIN_ORDER_TRANSITIONS = {
    ("submitted_to_restaurant", "accept"): "accepted",
    ("submitted_to_restaurant", "reject"): "rejected",
    ("submitted_to_restaurant", "fail"): "failed",
    ("accepted", "start_preparing"): "preparing",
    ("accepted", "fail"): "failed",
    ("preparing", "mark_ready"): "ready_for_pickup",
    ("preparing", "dispatch"): "out_for_delivery",
    ("preparing", "fail"): "failed",
    ("ready_for_pickup", "complete"): "completed",
    ("out_for_delivery", "deliver"): "delivered",
    ("out_for_delivery", "fail"): "failed",
}


class OrderService:
    def __init__(self, repository, menu_repository):
        self.orders = repository
        self.menu = menu_repository

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def create_pending_from_cart(self, cart: dict) -> ToolResponse:
        order_id = f"ORD-{uuid.uuid4()}"
        now = self._now()
        items = [{
            "item_id": item["item_id"], "name": item["name"],
            "quantity": item["quantity"], "customizations": deepcopy(item["selected_options"]),
            "line_total": item["current_price"],
            "unit_price": item["current_price"] // item["quantity"],
        } for item in cart["items"]]
        order = {
            "PK": cart["user_id"], "SK": f"ORDER#{order_id}",
            "GSI1PK": f"ORDER#{order_id}", "GSI1SK": "METADATA",
            "order_id": order_id, "user_id": cart["user_id"],
            "customer_id": cart.get("customer_id") or cart["user_id"],
            "customer_name": cart.get("customer_name"),
            "customer_phone": cart.get("customer_phone"),
            "agent_session_id": cart["agent_session_id"],
            "restaurant_id": cart["restaurant_id"], "branch_id": cart["branch_id"],
            "source_cart_id": cart["cart_id"], "status": "awaiting_fulfillment_method",
            "items": items, "subtotal": cart["subtotal"], "delivery_fee": None,
            "total": cart["subtotal"], "currency": cart["currency"],
            "fulfillment_method": None, "delivery_address": None,
            "idempotency_keys": [], "version": 1, "created_at": now, "updated_at": now,
        }
        self.orders.create(order)
        return ToolResponse.ok(data=self._public(order),
                               user_message="The order is ready for fulfillment details.",
                               next_action="ask_fulfillment_method",
                               agent=self._order_agent(
                                   order,
                                   "ask_fulfillment_method",
                                   required_input="fulfillment_method",
                                   instruction="Summarize this order and ask the customer to choose delivery or takeaway before final confirmation.",
                               ))

    def update_order_flow(self, order_id: str, action: str, value: str | None = None,
                          idempotency_key: str | None = None) -> ToolResponse:
        order = self.orders.get_by_order_id(order_id)
        if not order:
            return ToolResponse.error(error_code="ORDER_NOT_FOUND", user_message="I couldn't find that order.")
        if idempotency_key and idempotency_key in order.get("idempotency_keys", []):
            return ToolResponse.ok(data=self._public(order),
                                   user_message="That order action was already processed.",
                                   next_action=self._next_action(order["status"]),
                                   agent=self._order_agent(
                                       order,
                                       self._next_action(order["status"]),
                                       instruction="Tell the customer this order action was already processed and continue from the current order status.",
                                   ))
        next_status = ORDER_TRANSITIONS.get((order["status"], action))
        if not next_status:
            return ToolResponse.error(error_code="INVALID_ORDER_STATE",
                                      user_message="This order can't be updated that way right now.")
        if action == "save_address":
            if not value or not value.strip():
                return ToolResponse.error(error_code="ADDRESS_REQUIRED",
                                          user_message="Please provide a delivery address.")
            order["delivery_address"] = value.strip()
        elif action == "set_delivery":
            order["fulfillment_method"] = "delivery"
        elif action == "set_takeaway":
            order["fulfillment_method"] = "takeaway"
            order["delivery_address"] = None
        elif action == "confirm":
            invalid = self._validate_submission(order)
            if invalid:
                return invalid
        order["status"] = next_status
        order["updated_at"] = self._now()
        if idempotency_key:
            order.setdefault("idempotency_keys", []).append(idempotency_key)
        version = order["version"]
        self.orders.save(order, version)
        order["version"] = version + 1
        next_action = self._next_action(next_status)
        return ToolResponse.ok(data=self._public(order), user_message="The order was updated successfully.",
                               next_action=next_action,
                               agent=self._order_agent(
                                   order,
                                   next_action,
                                   required_input=self._required_input(next_status),
                                   instruction=self._instruction(next_status),
                               ))

    def get_order_status(self, user_id: str, order_id: str | None = None) -> ToolResponse:
        if order_id:
            order = self.orders.get_by_order_id(order_id)
            if not order or order.get("user_id") != user_id:
                return ToolResponse.error(error_code="ORDER_NOT_FOUND",
                                          user_message="I couldn't find that order.")
            data = {"order": self._public(order)}
            agent = self._order_agent(
                order,
                self._next_action(order["status"]),
                required_input=self._required_input(order["status"]),
                instruction="Present this order status and continue from the returned next_action if the customer wants to act.",
            )
        else:
            orders = [self._public(order)
                      for order in self.orders.list_active(user_id, TERMINAL_STATUSES)]
            data = {"orders": orders}
            agent = {
                "entity": "orders",
                "orders": [
                    {
                        "order_id": order.get("order_id"),
                        "status": order.get("status"),
                        "next_action": self._next_action(order.get("status")),
                        "required_input": self._required_input(order.get("status")),
                    }
                    for order in orders
                ],
                "valid_next_actions": ["update_order_flow", "search_menu", "create_menu_session_link"],
                "instruction": "Use these active backend orders to choose the next order step. If multiple orders match an action, ask which order_id the customer means.",
            }
        return ToolResponse.ok(data=data, user_message="Here is the current order status.",
                               next_action="present_order_status", agent=agent)

    def check_active_orders(self, user_id: str) -> ToolResponse:
        active_orders = [
            self._active_order_summary(order)
            for order in self.orders.list_active(user_id, TERMINAL_STATUSES)
            if order.get("status") in ACTIVE_ORDER_STATUSES
        ]
        unfinished_orders = [
            order for order in active_orders
            if order.get("status") in UNFINISHED_ORDER_STATUSES
        ]
        placed_orders = [
            order for order in active_orders
            if order.get("status") in PLACED_ACTIVE_ORDER_STATUSES
        ]
        routing_guidance = self._active_order_routing_guidance(
            unfinished_orders,
            placed_orders,
        )
        data = {
            "has_active_orders": bool(active_orders),
            "active_order_count": len(active_orders),
            "unfinished_orders": unfinished_orders,
            "placed_orders": placed_orders,
            "routing_guidance": routing_guidance,
        }
        agent = {
            "entity": "active_orders_check",
            **data,
            "valid_next_actions": [
                "get_order_status",
                "update_order_flow",
                "search_menu",
                "create_menu_session_link",
            ],
            "instruction": routing_guidance["instruction"],
        }
        return ToolResponse.ok(
            data=data,
            user_message="Active order check completed.",
            next_action=routing_guidance["next_action"],
            agent=agent,
        )

    def admin_list_orders(self, status: str | None = None, limit: int = 50) -> dict:
        orders = [self._public(order) for order in self.orders.list_all()]
        if status:
            orders = [order for order in orders if order.get("status") == status]
        orders.sort(key=lambda order: order.get("updated_at") or order.get("created_at") or "", reverse=True)
        return {"orders": orders[:limit], "next_cursor": None}

    def admin_get_order(self, order_id: str) -> dict:
        order = self.orders.get_by_order_id(order_id)
        if not order:
            raise ValueError("ORDER_NOT_FOUND")
        public = self._public(order)
        public["status_history"] = deepcopy(order.get("status_history", []))
        public["allowed_actions"] = self._admin_allowed_actions(order)
        return {"order": public}

    def admin_update_status(self, order_id: str, action: str, reason: str | None = None) -> dict:
        order = self.orders.get_by_order_id(order_id)
        if not order:
            raise ValueError("ORDER_NOT_FOUND")
        next_status = ADMIN_ORDER_TRANSITIONS.get((order.get("status"), action))
        if not next_status:
            raise ValueError("INVALID_ORDER_STATE")
        if action == "mark_ready" and order.get("fulfillment_method") != "takeaway":
            raise ValueError("INVALID_ORDER_STATE")
        if action == "dispatch" and order.get("fulfillment_method") != "delivery":
            raise ValueError("INVALID_ORDER_STATE")
        now = self._now()
        previous = order["status"]
        order["status"] = next_status
        order["updated_at"] = now
        order.setdefault("status_history", []).append({
            "from_status": previous,
            "to_status": next_status,
            "action": action,
            "actor": "admin",
            "reason": reason,
            "created_at": now,
        })
        version = order["version"]
        self.orders.save(order, version)
        order["version"] = version + 1
        public = self._public(order)
        public["status_history"] = deepcopy(order.get("status_history", []))
        public["allowed_actions"] = self._admin_allowed_actions(order)
        return {"order": public}

    def admin_analytics(self) -> dict:
        orders = [self._public(order) for order in self.orders.list_all()]
        by_status: dict[str, int] = {}
        revenue = 0
        today = self._now()[:10]
        today_orders = 0
        for order in orders:
            status = order.get("status") or "unknown"
            by_status[status] = by_status.get(status, 0) + 1
            if status not in {"rejected", "cancelled", "failed"}:
                revenue += order.get("total") or 0
            if str(order.get("created_at", "")).startswith(today):
                today_orders += 1
        active_orders = [
            order for order in orders
            if order.get("status") not in {"delivered", "completed", "rejected", "cancelled", "failed"}
        ]
        failed_orders = [order for order in orders if order.get("status") == "failed"]
        recent_orders = sorted(
            orders,
            key=lambda order: order.get("updated_at") or order.get("created_at") or "",
            reverse=True,
        )[:10]
        return {
            "today_orders": today_orders,
            "active_orders": len(active_orders),
            "revenue": revenue,
            "failed_orders": len(failed_orders),
            "by_status": by_status,
            "recent_orders": recent_orders,
        }

    def admin_failed_orders(self, limit: int = 50) -> dict:
        orders = [
            order for order in self.admin_list_orders(limit=1000)["orders"]
            if order.get("status") == "failed"
        ]
        return {"orders": orders[:limit], "next_cursor": None}

    def _validate_submission(self, order):
        method = order.get("fulfillment_method")
        if method not in {"delivery", "takeaway"}:
            return ToolResponse.error(error_code="FULFILLMENT_METHOD_REQUIRED",
                                      user_message="Choose delivery or takeaway first.")
        if method == "delivery" and not order.get("delivery_address"):
            return ToolResponse.error(error_code="ADDRESS_REQUIRED",
                                      user_message="A delivery address is required.")
        total = 0
        for item in order["items"]:
            source = self.menu.get_item(item["item_id"])
            if not source or not source.get("available"):
                return ToolResponse.error(error_code="ITEM_UNAVAILABLE",
                                          user_message="An order item is no longer available.")
            unit = source.get("starting_price", 0) or 0
            groups = [group for group_id in source.get("customization_group_ids", [])
                      if (group := self.menu.get_option_group(group_id))]
            for group in groups:
                selected = item.get("customizations", {}).get(group["option_group_id"])
                option = next((entry for entry in group.get("options", [])
                               if entry.get("option_id") == selected), None)
                if group.get("required") and not option:
                    return ToolResponse.error(error_code="INVALID_CUSTOMIZATION",
                                              user_message="An order customization is no longer valid.")
                if option:
                    if "price_key" in option:
                        unit = source.get("base_prices", {}).get(option["price_key"], unit)
                    unit += option.get("price_delta", 0)
            line_total = unit * item["quantity"]
            item["unit_price"], item["line_total"] = unit, line_total
            total += line_total
        order["subtotal"] = total
        order["total"] = total + (order.get("delivery_fee") or 0)
        return None

    @staticmethod
    def _next_action(status):
        return {
            "pending_confirmation": "confirm_or_cancel",
            "awaiting_fulfillment_method": "ask_fulfillment_method",
            "awaiting_delivery_address": "ask_delivery_address",
            "submitted_to_restaurant": "await_restaurant_update",
        }.get(status, "none")

    @staticmethod
    def _required_input(status):
        return {
            "pending_confirmation": "confirm_or_cancel",
            "awaiting_fulfillment_method": "fulfillment_method",
            "awaiting_delivery_address": "delivery_address",
        }.get(status)

    @classmethod
    def _instruction(cls, status):
        return {
            "pending_confirmation": "Summarize the complete order, fulfillment details, and total. Ask the customer to confirm or cancel. Confirm submits the order.",
            "awaiting_fulfillment_method": "Ask the customer to choose delivery or takeaway.",
            "awaiting_delivery_address": "Ask the customer for a delivery address.",
            "submitted_to_restaurant": "Tell the customer the order was submitted and await restaurant updates.",
        }.get(status, "Present the returned order status.")

    @classmethod
    def _order_agent(cls, order, next_action, *, required_input=None, instruction=None):
        return {
            "entity": "order",
            "order_id": order.get("order_id"),
            "order_status": order.get("status"),
            "next_action": next_action,
            "required_input": required_input,
            "valid_next_actions": cls._order_valid_next_actions(order.get("status")),
            "order_summary": {
                "items": deepcopy(order.get("items", [])),
                "subtotal": order.get("subtotal"),
                "delivery_fee": order.get("delivery_fee"),
                "total": order.get("total"),
                "currency": order.get("currency"),
                "fulfillment_method": order.get("fulfillment_method"),
                "delivery_address": order.get("delivery_address"),
            },
            "instruction": instruction or cls._instruction(order.get("status")),
        }

    @staticmethod
    def _order_valid_next_actions(status):
        return {
            "pending_confirmation": ["update_order_flow:confirm", "update_order_flow:cancel"],
            "awaiting_fulfillment_method": [
                "update_order_flow:set_delivery",
                "update_order_flow:set_takeaway",
                "update_order_flow:cancel",
            ],
            "awaiting_delivery_address": ["update_order_flow:save_address", "update_order_flow:cancel"],
            "submitted_to_restaurant": ["get_order_status"],
        }.get(status, [])

    @classmethod
    def _active_order_summary(cls, order):
        return {
            "order_id": order.get("order_id"),
            "status": order.get("status"),
            "next_action": cls._next_action(order.get("status")),
            "required_input": cls._required_input(order.get("status")),
            "item_count": len(order.get("items", [])),
            "subtotal": order.get("subtotal"),
            "total": order.get("total"),
            "currency": order.get("currency"),
            "fulfillment_method": order.get("fulfillment_method"),
            "updated_at": order.get("updated_at"),
        }

    @staticmethod
    def _active_order_routing_guidance(unfinished_orders, placed_orders):
        if unfinished_orders:
            return {
                "route": "resolve_unfinished_order_choice",
                "next_action": "ask_unfinished_order_choice",
                "customer_message": (
                    "You have an unfinished order. Would you like to continue it, "
                    "cancel it, or start a separate order?"
                ),
                "instruction": (
                    "Tell the customer there is an unfinished pre-submission order. "
                    "Offer to continue it, cancel it, or start a separate order. "
                    "Do not silently resume, overwrite, merge, or modify any placed order. "
                    "If the customer chooses continue or cancel, use the returned order_id "
                    "with the proper order tool. If they choose a separate order, begin "
                    "menu browsing without repeating this active-order check immediately."
                ),
            }
        if len(placed_orders) == 1:
            return {
                "route": "resolve_placed_active_order_choice",
                "next_action": "ask_active_order_choice",
                "customer_message": (
                    "You already have an active order. Changes cannot be made once an order "
                    "has been placed, but you can check its status or start a separate order. "
                    "Which would you like to do?"
                ),
                "instruction": (
                    "Tell the customer they already have an active placed order and use the "
                    "customer_message wording. Do not modify that placed order. If they choose "
                    "status, call get_order_status. If they choose a separate order, begin menu "
                    "browsing without repeating this active-order check immediately."
                ),
            }
        if len(placed_orders) > 1:
            return {
                "route": "resolve_placed_active_order_choice",
                "next_action": "ask_active_order_choice",
                "customer_message": (
                    "You already have active orders. Changes cannot be made once orders have "
                    "been placed, but you can check their status or start a separate order. "
                    "Which would you like to do?"
                ),
                "instruction": (
                    "Tell the customer they already have multiple active placed orders and use "
                    "plural wording. Do not modify, merge, or overwrite those orders. If they "
                    "choose status, call get_order_status. If they choose a separate order, "
                    "begin menu browsing without repeating this active-order check immediately."
                ),
            }
        return {
            "route": "begin_new_order",
            "next_action": "begin_menu_browsing",
            "customer_message": "No active orders were found. Begin menu or chat ordering normally.",
            "instruction": (
                "No active orders were found for this customer. Begin menu browsing or chat "
                "ordering normally. Offer the menu website or ask what item/category they want."
            ),
        }

    @staticmethod
    def _admin_allowed_actions(order):
        status = order.get("status")
        actions = [
            action for (current, action), _next in ADMIN_ORDER_TRANSITIONS.items()
            if current == status
        ]
        if order.get("fulfillment_method") != "takeaway":
            actions = [action for action in actions if action != "mark_ready"]
        if order.get("fulfillment_method") != "delivery":
            actions = [action for action in actions if action != "dispatch"]
        return actions

    @staticmethod
    def _public(order):
        return {key: deepcopy(order.get(key)) for key in
                ("order_id", "status", "items", "subtotal", "delivery_fee", "total", "currency",
                 "fulfillment_method", "delivery_address", "customer_id", "customer_name", "customer_phone",
                 "source_cart_id", "version", "created_at", "updated_at")}
