from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.models.tool_responses import ToolResponse


MENU_REQUEST_PATTERN = re.compile(
    r"\b(?:order|want|would\s+like|can\s+i|could\s+i|show\s+me|menu|"
    r"options?|get|need|one|small|medium|large)\b",
    re.IGNORECASE,
)
MENU_ITEM_PATTERN = re.compile(r"\b(?:pizza|pepperoni)\b", re.IGNORECASE)
NON_ORDER_PATTERN = re.compile(
    r"\b(?:complain|complaint|refund|support|human|agent|manager|cancel|"
    r"wrong|missing|late|cold|damaged|issue|problem)\b",
    re.IGNORECASE,
)
ORDER_STATUS_PATTERN = re.compile(
    r"\b(?:status|track|tracking|where\s+is)\b.*\border\b|"
    r"\border\b.*\b(?:status|track|tracking)\b",
    re.IGNORECASE,
)
CHECKOUT_PATTERN = re.compile(
    r"\b(?:checkout|check\s*out|proceed|continue|confirm|done|ready)\b",
    re.IGNORECASE,
)
CONFIRM_PATTERN = re.compile(r"\b(?:confirm|yes|submit|place\s+it)\b", re.IGNORECASE)
CANCEL_PATTERN = re.compile(r"\b(?:cancel|stop|never\s*mind)\b", re.IGNORECASE)
SKIP_PATTERN = re.compile(
    r"\b(?:no|none|skip|without|no\s+thanks|nothing\s+else)\b",
    re.IGNORECASE,
)
DELIVERY_PATTERN = re.compile(r"\bdelivery\b", re.IGNORECASE)
TAKEAWAY_PATTERN = re.compile(r"\b(?:takeaway|take\s*away|pickup|pick\s*up)\b", re.IGNORECASE)


@dataclass(frozen=True)
class WhatsAppOrderFlowResult:
    text: str
    tool_calls: list[dict[str, Any]]


class WhatsAppOrderFlowService:
    """Translate WhatsApp choices into the existing menu, cart, and order services."""

    def __init__(self, menu, carts, orders, agent_sessions):
        self.menu = menu
        self.carts = carts
        self.orders = orders
        self.agent_sessions = agent_sessions

    def handle(
        self,
        *,
        user_id: str,
        session_id: str,
        message: str,
        request_id: str,
        customer_id: str | None = None,
        customer_name: str | None = None,
        customer_phone: str | None = None,
    ) -> WhatsAppOrderFlowResult | None:
        normalized = self._normalize(message)
        if not normalized:
            return None

        if ORDER_STATUS_PATTERN.search(normalized):
            response = self.orders.get_order_status(user_id)
            return self._result("get_order_status", response, is_write=False)
        if NON_ORDER_PATTERN.search(normalized) and not CANCEL_PATTERN.search(normalized):
            return None

        order = self.orders.get_active_order_for_session(user_id, session_id)
        if order is not None and order.get("status") in {
            "awaiting_fulfillment_method",
            "awaiting_delivery_address",
            "pending_confirmation",
        }:
            return self._handle_order(user_id, order, normalized, message, request_id)

        cart_response = self.carts.get_active_cart(user_id, session_id)
        cart = (cart_response.data or {}).get("cart") if cart_response.success else None
        if isinstance(cart, dict):
            return self._handle_cart(cart_response, cart, normalized)

        menu_state = self.agent_sessions.get_whatsapp_order_state(user_id, session_id)
        offered_items = menu_state.get("offered_menu_items", [])
        if offered_items:
            selected = self._select(offered_items, normalized, ("name", "product_id"))
            if selected is not None:
                self.agent_sessions.clear_whatsapp_order_state(user_id, session_id)
                response = self.carts.start_item_customization(
                    user_id,
                    session_id,
                    selected["product_id"],
                    self._quantity(normalized),
                    customer_id=customer_id,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                )
                return self._cart_result("start_cart_item_customization", response)
            if self._is_choice_attempt(normalized):
                return WhatsAppOrderFlowResult(
                    text=self._menu_choice_prompt(offered_items),
                    tool_calls=[],
                )

        query = self._menu_query(normalized)
        if query is None:
            return None
        response = self.menu.search_menu(query=query, available_only=True, limit=5)
        items = (response.data or {}).get("items", []) if response.success else []
        if items:
            self.agent_sessions.save_whatsapp_order_state(
                user_id,
                session_id,
                offered_menu_items=[
                    {
                        "product_id": item.get("product_id"),
                        "name": item.get("name"),
                        "base_prices": item.get("base_prices"),
                        "starting_price": item.get("starting_price"),
                        "price": item.get("price"),
                        "currency": item.get("currency"),
                    }
                    for item in items
                    if item.get("product_id")
                ],
            )
        return self._result(
            "search_menu",
            response,
            is_write=False,
            text=self._menu_results_text(response),
        )

    def _handle_cart(
        self,
        cart_response: ToolResponse,
        cart: dict[str, Any],
        normalized: str,
    ) -> WhatsAppOrderFlowResult:
        status = cart.get("status")
        cart_id = cart.get("cart_id")
        agent = cart_response.agent or {}

        if status == "cart_created":
            choices = agent.get("choices") or [
                {"label": "Same", "value": "same"},
                {"label": "Customize separately", "value": "separate"},
            ]
            selected = self._select(choices, normalized, ("label", "value"))
            if selected is None:
                return self._result("get_active_cart", cart_response, is_write=False)
            response = self.carts.set_customization_mode(cart_id, selected["value"])
            return self._cart_result("set_customization_mode", response)

        if status == "customizing_item":
            active_choice = agent.get("active_choice") or cart
            options = active_choice.get("options", [])
            selected = self._select(
                options,
                normalized,
                ("display_label", "label", "option_id"),
            )
            if selected is None:
                return WhatsAppOrderFlowResult(
                    text=(
                        active_choice.get("choice_prompt")
                        or "Please choose one of the listed options."
                    ),
                    tool_calls=[],
                )
            response = self.carts.save_choice(
                active_choice["cart_item_id"],
                active_choice["field_name"],
                selected["option_id"],
            )
            if response.success and response.next_action == "offer_upsell":
                response = self.carts.handle_upsell(response.data["cart_id"], "get_options")
            return self._cart_result("save_customization_choice", response)

        if status in {"item_ready", "awaiting_upsell_decision"}:
            options_response = self.carts.handle_upsell(cart_id, "get_options")
            if status == "item_ready" and not SKIP_PATTERN.search(normalized):
                return self._cart_result("handle_cart_upsell", options_response)
            if SKIP_PATTERN.search(normalized):
                response = self.carts.handle_upsell(cart_id, "skip")
                return self._cart_result("handle_cart_upsell", response)

            upsell_items = (options_response.data or {}).get("upsell_items", [])
            selected = self._select(upsell_items, normalized, ("name", "product_id"))
            if selected is None:
                return self._cart_result("handle_cart_upsell", options_response)
            response = self.carts.handle_upsell(
                cart_id,
                "add_item",
                selected["product_id"],
                self._quantity(normalized),
            )
            return self._cart_result("handle_cart_upsell", response)

        if status == "cart_ready":
            if not CHECKOUT_PATTERN.search(normalized):
                return WhatsAppOrderFlowResult(
                    text="Your cart is ready. Reply checkout to continue.",
                    tool_calls=[],
                )
            response = self.carts.create_pending_order(cart_id)
            return self._result(
                "create_pending_order_from_cart",
                response,
                is_write=True,
                text=self._order_step_text(response),
            )

        return self._result("get_active_cart", cart_response, is_write=False)

    def _handle_order(
        self,
        user_id: str,
        order: dict[str, Any],
        normalized: str,
        original_message: str,
        request_id: str,
    ) -> WhatsAppOrderFlowResult:
        order_id = order["order_id"]
        status = order.get("status")
        if status == "awaiting_fulfillment_method":
            if DELIVERY_PATTERN.search(normalized) or normalized == "1":
                response = self.orders.update_order_flow(order_id, "set_delivery")
                tool_name = "update_order_flow"
                is_write = True
            elif TAKEAWAY_PATTERN.search(normalized) or normalized == "2":
                response = self.orders.update_order_flow(order_id, "set_takeaway")
                tool_name = "update_order_flow"
                is_write = True
            else:
                response = self.orders.get_order_status(user_id, order_id)
                tool_name = "get_order_status"
                is_write = False
            return self._result(
                tool_name,
                response,
                is_write=is_write,
                text=self._order_step_text(response),
            )

        if status == "awaiting_delivery_address":
            if CANCEL_PATTERN.search(normalized):
                response = self.orders.update_order_flow(order_id, "cancel")
            else:
                response = self.orders.update_order_flow(
                    order_id,
                    "save_address",
                    original_message.strip(),
                )
            return self._result(
                "update_order_flow",
                response,
                is_write=True,
                text=self._order_step_text(response),
            )

        if status == "pending_confirmation":
            if CONFIRM_PATTERN.search(normalized):
                response = self.orders.update_order_flow(
                    order_id,
                    "confirm",
                    idempotency_key=request_id,
                )
                tool_name = "update_order_flow"
                is_write = True
            elif CANCEL_PATTERN.search(normalized):
                response = self.orders.update_order_flow(order_id, "cancel")
                tool_name = "update_order_flow"
                is_write = True
            else:
                response = self.orders.get_order_status(user_id, order_id)
                tool_name = "get_order_status"
                is_write = False
            return self._result(
                tool_name,
                response,
                is_write=is_write,
                text=self._order_step_text(response),
            )

        response = self.orders.get_order_status(user_id, order_id)
        return self._result("get_order_status", response, is_write=False)

    def _cart_result(self, tool_name: str, response: ToolResponse) -> WhatsAppOrderFlowResult:
        return self._result(
            tool_name,
            response,
            is_write=True,
            text=self._cart_step_text(response),
        )

    @staticmethod
    def _result(
        tool_name: str,
        response: ToolResponse,
        *,
        is_write: bool,
        text: str | None = None,
    ) -> WhatsAppOrderFlowResult:
        dumped = response.model_dump(exclude_none=True)
        return WhatsAppOrderFlowResult(
            text=text or response.user_message,
            tool_calls=[{
                "tool_name": tool_name,
                "success": response.success,
                "is_write": is_write,
                "result": dumped,
                "error_code": response.error_code,
            }],
        )

    @classmethod
    def _cart_step_text(cls, response: ToolResponse) -> str:
        agent = response.agent or {}
        for key in ("choice_prompt", "upsell_prompt"):
            value = agent.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if response.next_action == "create_pending_order":
            return "Your cart is ready. Reply checkout to continue."
        return response.user_message

    @classmethod
    def _order_step_text(cls, response: ToolResponse) -> str:
        agent = response.agent or {}
        if response.next_action == "ask_fulfillment_method":
            return cls._order_summary(agent) + "\n\nChoose fulfilment:\n1. Delivery\n2. Takeaway"
        if response.next_action == "ask_delivery_address":
            return "Please send the delivery address for this order."
        for key in ("confirmation_summary", "submission_confirmation", "status_message"):
            value = agent.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return response.user_message

    @classmethod
    def _order_summary(cls, agent: dict[str, Any]) -> str:
        summary = agent.get("order_summary") or {}
        lines = ["Order summary:"]
        for index, item in enumerate(summary.get("items") or [], start=1):
            lines.append(f"{index}. {item.get('name', 'Item')} x {item.get('quantity', 1)}")
        lines.append(
            f"Total: {cls._money(summary.get('total'), summary.get('currency'))}"
        )
        return "\n".join(lines)

    @classmethod
    def _menu_results_text(cls, response: ToolResponse) -> str:
        items = (response.data or {}).get("items", [])
        if not items:
            return response.user_message
        return "\n".join([
            "Here are the matching options I found:",
            *[
                f"{index}. {item.get('name', 'Menu item')} - {cls._menu_price(item)}"
                for index, item in enumerate(items, start=1)
            ],
            "Which item would you like? Reply with its number or name.",
        ])

    @classmethod
    def _menu_choice_prompt(cls, items: list[dict[str, Any]]) -> str:
        return "\n".join([
            "Please choose one of these menu items:",
            *[
                f"{index}. {item.get('name', 'Menu item')} - {cls._menu_price(item)}"
                for index, item in enumerate(items, start=1)
            ],
        ])

    @classmethod
    def _menu_price(cls, item: dict[str, Any]) -> str:
        base_prices = item.get("base_prices")
        if isinstance(base_prices, dict) and base_prices:
            return ", ".join(
                f"{str(size).replace('_', ' ').title()} {cls._money(price, item.get('currency'))}"
                for size, price in base_prices.items()
            )
        amount = item.get("starting_price")
        if amount is None:
            amount = item.get("price")
        return cls._money(amount, item.get("currency"))

    @staticmethod
    def _money(amount: Any, currency: Any) -> str:
        label = "PKR" if str(currency or "").upper() == "PKR" else str(currency or "").strip()
        if amount is None:
            return "price unavailable"
        try:
            value = float(amount)
        except (TypeError, ValueError):
            return f"{label} {amount}".strip()
        number = f"{value:,.2f}".rstrip("0").rstrip(".")
        return f"{label} {number}".strip()

    @staticmethod
    def _select(
        choices: list[dict[str, Any]],
        normalized: str,
        text_fields: tuple[str, ...],
    ) -> dict[str, Any] | None:
        number_match = re.search(r"\b(\d+)\b", normalized)
        if number_match:
            index = int(number_match.group(1)) - 1
            if 0 <= index < len(choices):
                return choices[index]
        normalized_tokens = set(normalized.split())
        matches = []
        for choice in choices:
            values = [
                WhatsAppOrderFlowService._normalize(str(choice.get(field) or ""))
                for field in text_fields
            ]
            if any(value and (value == normalized or value in normalized) for value in values):
                matches.append(choice)
                continue
            if any(value and set(value.split()).issubset(normalized_tokens) for value in values):
                matches.append(choice)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _quantity(normalized: str) -> int:
        match = re.search(r"\b(\d+)\b", normalized)
        if match and not normalized.strip().isdigit():
            return max(1, int(match.group(1)))
        return 1

    @staticmethod
    def _is_choice_attempt(normalized: str) -> bool:
        return bool(re.fullmatch(r"(?:option\s+|item\s+)?\d+", normalized))

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", value.casefold())).strip()

    @staticmethod
    def _menu_query(normalized: str) -> str | None:
        if NON_ORDER_PATTERN.search(normalized):
            return None
        if not MENU_REQUEST_PATTERN.search(normalized) or not MENU_ITEM_PATTERN.search(normalized):
            return None
        if "pepperoni" in normalized:
            return "pepperoni"
        return "pizza"
