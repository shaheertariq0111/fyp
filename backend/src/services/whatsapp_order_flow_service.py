from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from src.agent.order_intent import OrderIntentClassification, OrderIntentRequest
from src.models.tool_responses import ToolResponse


MENU_REQUEST_PATTERN = re.compile(
    r"\b(?:order|want|would\s+like|can\s+i|could\s+i|show\s+me|menu|"
    r"options?|get|need|one|small|medium|large)\b",
    re.IGNORECASE,
)
MENU_ITEM_PATTERN = re.compile(r"\b(?:pizza|pepperoni)\b", re.IGNORECASE)
ORDER_START_PATTERN = re.compile(
    r"\b(?:order|ordering|place\s+an?\s+order|buy)\b",
    re.IGNORECASE,
)
RECOMMENDATION_PATTERN = re.compile(
    r"\b(?:recommend|recommendation|suggest|suggestion|right\s+thing|"
    r"something\s+(?:spicy|cheap|affordable|budget|chicken|vegetarian|veggie))\b",
    re.IGNORECASE,
)
MENU_PREFERENCE_PATTERN = re.compile(
    r"\b(spicy|cheap|affordable|budget|chicken|vegetarian|veggie)\b",
    re.IGNORECASE,
)
PRIVACY_BYPASS_PATTERN = re.compile(
    r"\b(?:all|every|other)\s+(?:customer|user|people|person)(?:'s|\s+)orders?\b|"
    r"\bcustomer\s+orders?\b",
    re.IGNORECASE,
)
PRICE_BYPASS_PATTERN = re.compile(
    r"\b(?:change|set|override|make)\b.*\bprice\b|"
    r"\b(?:confirm|place|order)\b.*\bfor\s+free\b",
    re.IGNORECASE,
)
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
ORDER_FOLLOW_UP_PATTERN = re.compile(
    r"\b(?:when\s+will\s+it\s+(?:come|arrive)|how\s+long\s+will\s+it\s+take|"
    r"is\s+it\s+coming|has\s+it\s+been\s+prepared|"
    r"(?:now\s+)?what(?:\s+s|\s+is)\s+the\s+status|the\s+last\s+one|"
    r"(?:my\s+)?last\s+order|latest\s+order)\b",
    re.IGNORECASE,
)
ETA_FOLLOW_UP_PATTERN = re.compile(
    r"\b(?:when|how\s+long|coming|arrive|eta)\b",
    re.IGNORECASE,
)
OFF_TOPIC_PATTERN = re.compile(
    r"\b(?:jokes?|weather|general\s+trivia|homework|coding|programming|"
    r"write\s+(?:me\s+)?code|personal\s+advice)\b",
    re.IGNORECASE,
)
DOMAIN_SCOPE_RESPONSE = (
    "I can help with menu items, orders, delivery, payments, allergies, "
    "complaints, and restaurant support."
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
TAKEAWAY_PATTERN = re.compile(
    r"\b(?:takeaway|take\s*away|pickup|pick\s*up|collect|collection)\b",
    re.IGNORECASE,
)
ORDER_INTENT_CONFIDENCE_THRESHOLD = 0.85


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WhatsAppOrderFlowResult:
    text: str
    tool_calls: list[dict[str, Any]]


class WhatsAppOrderFlowService:
    """Translate WhatsApp choices into the existing menu, cart, and order services."""

    def __init__(
        self,
        menu,
        carts,
        orders,
        agent_sessions,
        intent_client=None,
        intent_client_factory=None,
    ):
        self.menu = menu
        self.carts = carts
        self.orders = orders
        self.agent_sessions = agent_sessions
        self.intent_client = intent_client
        self.intent_client_factory = intent_client_factory

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

        if PRIVACY_BYPASS_PATTERN.search(message):
            return WhatsAppOrderFlowResult(
                text="I can only show orders linked to your verified customer account.",
                tool_calls=[],
            )
        if PRICE_BYPASS_PATTERN.search(message):
            return WhatsAppOrderFlowResult(
                text=(
                    "I can't override backend prices. Continue using the displayed "
                    "menu price and backend-calculated total."
                ),
                tool_calls=[],
            )

        order = self.orders.get_active_order_for_session(user_id, session_id)
        if (
            ORDER_STATUS_PATTERN.search(normalized)
            or ORDER_FOLLOW_UP_PATTERN.search(normalized)
        ):
            response = self.orders.get_order_status(
                user_id,
                order.get("order_id") if order is not None else None,
            )
            text = response.user_message
            if order is not None and ETA_FOLLOW_UP_PATTERN.search(normalized):
                text = (
                    f"Exact ETA is unavailable. Here is the current order status:\n"
                    f"{response.user_message}"
                )
            return self._result(
                "get_order_status",
                response,
                is_write=False,
                text=text,
            )
        if NON_ORDER_PATTERN.search(normalized) and not CANCEL_PATTERN.search(normalized):
            return None

        if order is not None and order.get("status") in {
            "awaiting_fulfillment_method",
            "awaiting_delivery_address",
            "pending_confirmation",
        }:
            return self._handle_order(
                user_id,
                session_id,
                order,
                normalized,
                message,
                request_id,
            )

        if OFF_TOPIC_PATTERN.search(normalized):
            return WhatsAppOrderFlowResult(
                text=DOMAIN_SCOPE_RESPONSE,
                tool_calls=[],
            )

        cart_response = self.carts.get_active_cart(user_id, session_id)
        cart = (cart_response.data or {}).get("cart") if cart_response.success else None
        if isinstance(cart, dict):
            return self._handle_cart(
                cart_response,
                cart,
                normalized,
                message,
                user_id,
                session_id,
                request_id,
            )

        menu_state = self.agent_sessions.get_whatsapp_order_state(user_id, session_id)
        offered_items = menu_state.get("offered_menu_items", [])
        if offered_items:
            selected = self._select(offered_items, normalized, ("name", "product_id"))
            if selected is None:
                intent = self._interpret(
                    state="menu_selection",
                    allowed_actions=["select_menu_item"],
                    message=message,
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                    options=self._intent_options(
                        offered_items,
                        id_field="product_id",
                        label_field="name",
                    ),
                )
                selected = self._selected_by_intent(
                    intent,
                    action="select_menu_item",
                    choices=offered_items,
                    id_field="product_id",
                )
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
        response = self.menu.search_menu(
            query=query or None,
            available_only=True,
            limit=5,
        )
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
        message: str,
        user_id: str,
        session_id: str,
        request_id: str,
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
                intent = self._interpret(
                    state="customization_mode",
                    allowed_actions=["select_customization_mode"],
                    message=message,
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                    options=self._intent_options(
                        choices,
                        id_field="value",
                        label_field="label",
                    ),
                )
                selected = self._selected_by_intent(
                    intent,
                    action="select_customization_mode",
                    choices=choices,
                    id_field="value",
                )
            if selected is None:
                return WhatsAppOrderFlowResult(
                    text=(
                        "Please choose how to customize these items:\n"
                        "1. Same customization\n"
                        "2. Customize separately"
                    ),
                    tool_calls=[],
                )
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
                intent = self._interpret(
                    state="customization_choice",
                    allowed_actions=["select_option"],
                    message=message,
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                    options=self._intent_options(
                        options,
                        id_field="option_id",
                        label_field="display_label",
                    ),
                )
                selected = self._selected_by_intent(
                    intent,
                    action="select_option",
                    choices=options,
                    id_field="option_id",
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
                intent = self._interpret(
                    state="upsell",
                    allowed_actions=["skip_upsell", "select_upsell"],
                    message=message,
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                    options=self._intent_options(
                        upsell_items,
                        id_field="product_id",
                        label_field="name",
                    ),
                )
                if intent is not None and intent.action == "skip_upsell":
                    response = self.carts.handle_upsell(cart_id, "skip")
                    return self._cart_result("handle_cart_upsell", response)
                selected = self._selected_by_intent(
                    intent,
                    action="select_upsell",
                    choices=upsell_items,
                    id_field="product_id",
                )
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
            checkout = bool(CHECKOUT_PATTERN.search(normalized))
            if not checkout:
                intent = self._interpret(
                    state="cart_ready",
                    allowed_actions=["checkout"],
                    message=message,
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                )
                checkout = intent is not None and intent.action == "checkout"
            if not checkout:
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
        session_id: str,
        order: dict[str, Any],
        normalized: str,
        original_message: str,
        request_id: str,
    ) -> WhatsAppOrderFlowResult:
        order_id = order["order_id"]
        status = order.get("status")
        if status == "awaiting_fulfillment_method":
            action = None
            if DELIVERY_PATTERN.search(normalized) or normalized == "1":
                action = "delivery"
            elif TAKEAWAY_PATTERN.search(normalized) or normalized == "2":
                action = "takeaway"
            elif CANCEL_PATTERN.search(normalized):
                action = "cancel"
            else:
                intent = self._interpret(
                    state="awaiting_fulfillment_method",
                    allowed_actions=["delivery", "takeaway", "cancel"],
                    message=original_message,
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                )
                action = intent.action if intent is not None else None
            if action == "delivery":
                response = self.orders.update_order_flow(order_id, "set_delivery")
                tool_name = "update_order_flow"
                is_write = True
            elif action == "takeaway":
                response = self.orders.update_order_flow(order_id, "set_takeaway")
                tool_name = "update_order_flow"
                is_write = True
            elif action == "cancel":
                response = self.orders.update_order_flow(order_id, "cancel")
                tool_name = "update_order_flow"
                is_write = True
            else:
                response = self.orders.get_order_status(user_id, order_id)
                tool_name = "get_order_status"
                is_write = False
                clarification = (
                    "Please choose fulfilment:\n"
                    "1. Delivery\n"
                    "2. Takeaway\n"
                    "Or reply cancel."
                )
            return self._result(
                tool_name,
                response,
                is_write=is_write,
                text=(
                    clarification
                    if action is None
                    else self._order_step_text(response)
                ),
            )

        if status == "awaiting_delivery_address":
            if TAKEAWAY_PATTERN.search(normalized):
                response = self.orders.update_order_flow(order_id, "set_takeaway")
            elif CANCEL_PATTERN.search(normalized) and normalized != "cancel address":
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
            action = None
            if CONFIRM_PATTERN.search(normalized):
                action = "confirm"
            elif CANCEL_PATTERN.search(normalized):
                action = "cancel"
            else:
                intent = self._interpret(
                    state="pending_confirmation",
                    allowed_actions=["confirm", "cancel"],
                    message=original_message,
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                )
                action = intent.action if intent is not None else None
            if action == "confirm":
                response = self.orders.update_order_flow(
                    order_id,
                    "confirm",
                    idempotency_key=request_id,
                )
                tool_name = "update_order_flow"
                is_write = True
            elif action == "cancel":
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

    def _interpret(
        self,
        *,
        state: str,
        allowed_actions: list[str],
        message: str,
        user_id: str,
        session_id: str,
        request_id: str,
        options: list[dict[str, str]] | None = None,
    ) -> OrderIntentClassification | None:
        if state != "upsell" and SKIP_PATTERN.search(self._normalize(message)):
            return None
        try:
            intent_client = self.intent_client
            if intent_client is None and self.intent_client_factory is not None:
                intent_client = self.intent_client_factory()
            if intent_client is None:
                return None
            intent = intent_client.classify_order_intent(
                OrderIntentRequest(
                    message=message,
                    state=state,
                    allowed_actions=allowed_actions,
                    available_options=options or [],
                    user_id=user_id,
                    agent_session_id=session_id,
                    request_id=request_id,
                )
            )
        except Exception as exc:
            logger.warning(
                "WhatsApp order intent classification failed",
                extra={
                    "event": "order_intent_classification_failed",
                    "request_id": request_id,
                    "actor_id": user_id,
                    "agent_session_id": session_id,
                    "channel": "whatsapp",
                    "order_state": state,
                    "error_type": type(exc).__name__,
                },
            )
            return None
        if (
            intent.action not in allowed_actions
            or intent.confidence < ORDER_INTENT_CONFIDENCE_THRESHOLD
        ):
            return None
        return intent

    @staticmethod
    def _intent_options(
        choices: list[dict[str, Any]],
        *,
        id_field: str,
        label_field: str,
    ) -> list[dict[str, str]]:
        return [
            {
                "id": str(choice[id_field]),
                "label": str(choice.get(label_field) or choice[id_field]),
            }
            for choice in choices
            if choice.get(id_field)
        ]

    @staticmethod
    def _selected_by_intent(
        intent: OrderIntentClassification | None,
        *,
        action: str,
        choices: list[dict[str, Any]],
        id_field: str,
    ) -> dict[str, Any] | None:
        if intent is None or intent.action != action or not intent.selected_option:
            return None
        return next(
            (
                choice
                for choice in choices
                if str(choice.get(id_field)) == intent.selected_option
            ),
            None,
        )

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
            return (
                f"{response.user_message} Please choose another preference, "
                "ask for available categories, or view the menu."
            )
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
        has_item_request = bool(
            MENU_REQUEST_PATTERN.search(normalized)
            and MENU_ITEM_PATTERN.search(normalized)
        )
        has_order_request = bool(ORDER_START_PATTERN.search(normalized))
        has_recommendation = bool(RECOMMENDATION_PATTERN.search(normalized))
        if not (has_item_request or has_order_request or has_recommendation):
            return None
        if "pepperoni" in normalized:
            return "pepperoni"
        preference = MENU_PREFERENCE_PATTERN.search(normalized)
        if preference:
            aliases = {
                "affordable": "cheap",
                "budget": "cheap",
                "veggie": "vegetarian",
            }
            return aliases.get(preference.group(1), preference.group(1))
        if "pizza" in normalized:
            return "pizza"
        return ""
