from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone

from src.models.tool_responses import ToolResponse


ORDER_TRANSITIONS = {
    ("pending_confirmation", "confirm"): "awaiting_fulfillment_method",
    ("pending_confirmation", "cancel"): "rejected",
    ("awaiting_fulfillment_method", "set_delivery"): "awaiting_delivery_address",
    ("awaiting_fulfillment_method", "set_takeaway"): "ready_for_submission",
    ("awaiting_delivery_address", "save_address"): "ready_for_submission",
    ("ready_for_submission", "submit"): "submitted_to_restaurant",
    ("awaiting_fulfillment_method", "cancel"): "cancelled",
    ("awaiting_delivery_address", "cancel"): "cancelled",
    ("ready_for_submission", "cancel"): "cancelled",
}

TERMINAL_STATUSES = {"delivered", "rejected", "cancelled", "failed"}


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
            "agent_session_id": cart["agent_session_id"],
            "restaurant_id": cart["restaurant_id"], "branch_id": cart["branch_id"],
            "source_cart_id": cart["cart_id"], "status": "pending_confirmation",
            "items": items, "subtotal": cart["subtotal"], "delivery_fee": None,
            "total": cart["subtotal"], "currency": cart["currency"],
            "fulfillment_method": None, "delivery_address": None,
            "idempotency_keys": [], "version": 1, "created_at": now, "updated_at": now,
        }
        self.orders.create(order)
        return ToolResponse.ok(data=self._public(order),
                               user_message="The pending order is ready for confirmation.",
                               next_action="confirm_or_cancel")

    def update_order_flow(self, order_id: str, action: str, value: str | None = None,
                          idempotency_key: str | None = None) -> ToolResponse:
        order = self.orders.get_by_order_id(order_id)
        if not order:
            return ToolResponse.error(error_code="ORDER_NOT_FOUND", user_message="I couldn't find that order.")
        if idempotency_key and idempotency_key in order.get("idempotency_keys", []):
            return ToolResponse.ok(data=self._public(order),
                                   user_message="That order action was already processed.",
                                   next_action=self._next_action(order["status"]))
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
        elif action == "submit":
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
        return ToolResponse.ok(data=self._public(order), user_message="The order was updated successfully.",
                               next_action=self._next_action(next_status))

    def get_order_status(self, user_id: str, order_id: str | None = None) -> ToolResponse:
        if order_id:
            order = self.orders.get_by_order_id(order_id)
            if not order or order.get("user_id") != user_id:
                return ToolResponse.error(error_code="ORDER_NOT_FOUND",
                                          user_message="I couldn't find that order.")
            data = {"order": self._public(order)}
        else:
            data = {"orders": [self._public(order)
                                for order in self.orders.list_active(user_id, TERMINAL_STATUSES)]}
        return ToolResponse.ok(data=data, user_message="Here is the current order status.",
                               next_action="present_order_status")

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
            "ready_for_submission": "ask_submit_or_cancel",
            "submitted_to_restaurant": "await_restaurant_update",
        }.get(status, "none")

    @staticmethod
    def _public(order):
        return {key: deepcopy(order.get(key)) for key in
                ("order_id", "status", "items", "subtotal", "delivery_fee", "total", "currency",
                 "fulfillment_method", "version", "created_at", "updated_at")}
