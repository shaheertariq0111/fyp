from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from src.agent.order_intent import OrderIntentClassification, OrderIntentRequest
from src.models.tool_responses import ToolResponse
from src.services.menu_query_service import MenuQueryResolver, MenuSearchPlan


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
DIRECT_MENU_BROWSE_PATTERN = re.compile(
    r"^(?:menu|show\s+(?:me\s+)?(?:(?:the|your)\s+)?"
    r"(?:(?:whole|full)\s+)?menu)$",
    re.IGNORECASE,
)
MENU_MORE_PATTERN = re.compile(
    r"\b(?:show\s+(?:me\s+)?more(?:\s+(?:items?|options?))?|"
    r"more\s+(?:items?|options?)|not\s+(?:the\s+)?full\s+menu|"
    r"not\s+(?:the\s+)?whole\s+menu)\b",
    re.IGNORECASE,
)
SPECIFIC_MENU_QUERY_PATTERN = re.compile(
    r"^(?:do\s+you\s+have|have\s+you\s+got|show\s+(?:me\s+)?)\s+"
    r"(?P<query>.+)$",
    re.IGNORECASE,
)
RESET_ORDER_FLOW_PATTERN = re.compile(
    r"^(?:start\s+(?:a\s+)?new\s+order|new\s+order|restart\s+order|"
    r"reset\s+order|clear\s+(?:my\s+)?cart|discard\s+this\s+order)$",
    re.IGNORECASE,
)
EXPLICIT_ORDER_CANCEL_PATTERN = re.compile(
    r"\b(?:cancel\s+(?:my|this|the)\s+order|stop\s+(?:my|this|the)\s+order|"
    r"i\s+don\s*t\s+want\s+to\s+order|i\s+do\s+not\s+want\s+to\s+order)\b",
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
NEGATED_CONFIRM_PATTERN = re.compile(
    r"\b(?:don\s*t|do\s+not|not|never)\s+(?:confirm|submit|place)\b",
    re.IGNORECASE,
)
NEGATED_CANCEL_PATTERN = re.compile(
    r"\b(?:don\s*t|do\s+not|not|never)\s+(?:want\s+to\s+)?"
    r"(?:cancel|stop)\b",
    re.IGNORECASE,
)
NEGATED_SELECTION_PATTERN = re.compile(
    r"\b(?:don\s*t\s+want|do\s+not\s+want|not)\b|"
    r"\bno\s+(?:option\s+|item\s+)?\d+\b",
    re.IGNORECASE,
)
NEGATED_FULFILLMENT_PATTERN = re.compile(
    r"\b(?:don\s*t\s+want|do\s+not\s+want|not|no)\s+"
    r"(?:delivery|takeaway|take\s+away|pickup)\b",
    re.IGNORECASE,
)
SKIP_PATTERN = re.compile(
    r"\b(?:no|none|skip|without|no\s+thanks|nothing\s+else)\b",
    re.IGNORECASE,
)
PROCEED_WITHOUT_ADDON_PATTERN = re.compile(
    r"^(?:checkout|check\s*out|continue|proceed|skip|no|no\s+thanks|"
    r"nothing\s+else|that\s+s\s+all|thats\s+all)$",
    re.IGNORECASE,
)
DELIVERY_PATTERN = re.compile(r"\bdelivery\b", re.IGNORECASE)
TAKEAWAY_PATTERN = re.compile(
    r"\b(?:takeaway|take\s*away|pickup|pick\s*up|collect|collection)\b",
    re.IGNORECASE,
)
ORDER_INTENT_CONFIDENCE_THRESHOLD = 0.85
CONVERSATION_INTENT_ACTIONS = [
    "latest_order_eta",
    "latest_order_status",
    "menu_browse",
    "menu_browse_more",
]
CANCELLABLE_ORDER_FLOW_STATUSES = {
    "awaiting_fulfillment_method",
    "awaiting_delivery_address",
    "awaiting_customer_name",
    "pending_confirmation",
}
CUSTOMER_NAME_PATTERN = re.compile(
    r"\b(?:my\s+name\s+is|name\s+is|it(?:'s|\s+is)(?:\s+actually)?|"
    r"change\s+my\s+name\s+to|put\s+it\s+under)\s+"
    r"(?P<name>[A-Za-z][A-Za-z .'-]{1,79})[.!]?\s*$",
    re.IGNORECASE,
)
UNSAFE_CUSTOMER_NAME_PATTERN = re.compile(
    r"\b(?:yes|no|ok|confirm|cancel|delivery|takeaway|pickup|menu|pizza|order|"
    r"pepperoni|mushroom|chicken|cheese|cola|burger|fries|wings|drink|"
    r"address|street|road|house|apartment|complaint|support|please|thanks)\b",
    re.IGNORECASE,
)
INVALID_CUSTOMER_NAME_CORRECTION_MESSAGE = (
    "Please provide the customer name only, for example: "
    "my name is Shaheer Tariq."
)


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
        menu_query_resolver=None,
    ):
        self.menu = menu
        self.carts = carts
        self.orders = orders
        self.agent_sessions = agent_sessions
        self.intent_client = intent_client
        self.intent_client_factory = intent_client_factory
        self.menu_query_resolver = menu_query_resolver or MenuQueryResolver()

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
        resolved_menu_plan = self.menu_query_resolver.resolve(message)
        deterministic_menu_query = self._menu_query(normalized)
        reset_requested = bool(RESET_ORDER_FLOW_PATTERN.fullmatch(normalized))
        cancel_requested = bool(
            EXPLICIT_ORDER_CANCEL_PATTERN.search(normalized)
            and not NEGATED_CANCEL_PATTERN.search(normalized)
        )
        if reset_requested or cancel_requested:
            return self._reset_ordering_flow(
                user_id=user_id,
                session_id=session_id,
                order=order,
                reset_requested=reset_requested,
            )
        if NON_ORDER_PATTERN.search(normalized) and not CANCEL_PATTERN.search(normalized):
            return None

        if OFF_TOPIC_PATTERN.search(normalized):
            return WhatsAppOrderFlowResult(
                text=DOMAIN_SCOPE_RESPONSE,
                tool_calls=[],
            )

        conversation_intent = None
        if self._should_classify_conversation_intent(order, normalized):
            conversation_intent = self._interpret(
                state="conversation",
                allowed_actions=CONVERSATION_INTENT_ACTIONS,
                message=message,
                user_id=user_id,
                session_id=session_id,
                request_id=request_id,
            )
        is_eta_intent = (
            conversation_intent is not None
            and conversation_intent.action == "latest_order_eta"
        )
        is_status_intent = (
            conversation_intent is not None
            and conversation_intent.action == "latest_order_status"
        )
        is_menu_browse_intent = (
            conversation_intent is not None
            and conversation_intent.action == "menu_browse"
        )
        is_menu_more_intent = (
            conversation_intent is not None
            and conversation_intent.action == "menu_browse_more"
        )
        if resolved_menu_plan is not None:
            is_eta_intent = False
            is_status_intent = False
            is_menu_browse_intent = False
            is_menu_more_intent = False
        if (
            is_eta_intent
            or is_status_intent
            or ORDER_STATUS_PATTERN.search(normalized)
            or ORDER_FOLLOW_UP_PATTERN.search(normalized)
        ):
            response = self.orders.get_order_status(
                user_id,
                order.get("order_id") if order is not None else None,
            )
            text = response.user_message
            if order is not None and (
                is_eta_intent or ETA_FOLLOW_UP_PATTERN.search(normalized)
            ):
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
        if order is not None and order.get("status") in {
            "awaiting_fulfillment_method",
            "awaiting_delivery_address",
            "awaiting_customer_name",
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
        deterministic_menu_more = bool(
            offered_items and MENU_MORE_PATTERN.search(normalized)
        )
        is_menu_more_intent = is_menu_more_intent or deterministic_menu_more
        if offered_items and not (
            is_menu_browse_intent
            or is_menu_more_intent
            or resolved_menu_plan is not None
            or deterministic_menu_query is not None
        ):
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
                    channel="whatsapp",
                )
                return self._cart_result("start_cart_item_customization", response)
            if self._is_choice_attempt(normalized):
                return WhatsAppOrderFlowResult(
                    text=self._menu_choice_prompt(offered_items),
                    tool_calls=[],
                )

        direct_menu_browse = bool(DIRECT_MENU_BROWSE_PATTERN.fullmatch(normalized))
        search_plan = resolved_menu_plan
        if search_plan is None:
            if deterministic_menu_query is not None:
                search_plan = (
                    MenuSearchPlan(mode="browse")
                    if not deterministic_menu_query
                    else MenuSearchPlan(
                        mode="product_search",
                        query=deterministic_menu_query,
                    )
                )
            elif is_menu_more_intent:
                search_plan = self.menu_query_resolver.from_state_value(
                    menu_state.get("whatsapp_menu_query")
                )
            elif is_menu_browse_intent or direct_menu_browse:
                search_plan = MenuSearchPlan(mode="browse")
        if search_plan is None:
            return None
        shown_ids = (
            list(menu_state.get("shown_menu_item_ids") or [])
            if is_menu_more_intent
            else []
        )
        if is_menu_more_intent and not offered_items:
            return WhatsAppOrderFlowResult(
                text="Please ask to see the menu first, then I can show more options.",
                tool_calls=[],
            )
        search_kwargs = {
            "query": search_plan.query,
            "available_only": True,
            "limit": 5,
            "exclude_product_ids": shown_ids,
        }
        if search_plan.category:
            search_kwargs["category"] = search_plan.category
        if search_plan.tags:
            search_kwargs["tags"] = list(search_plan.tags)
        response = self.menu.search_menu(**search_kwargs)
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
                menu_query=search_plan.state_value,
                shown_menu_item_ids=[
                    *shown_ids,
                    *[
                        str(item["product_id"])
                        for item in items
                        if item.get("product_id")
                    ],
                ],
                menu_has_more=bool((response.data or {}).get("has_more")),
            )
        elif is_menu_more_intent:
            return WhatsAppOrderFlowResult(
                text="You've reached the end of the available menu options.",
                tool_calls=[],
            )
        return self._result(
            "search_menu",
            response,
            is_write=False,
            text=self._menu_results_text(
                response,
                is_continuation=is_menu_more_intent,
            ),
        )

    def _reset_ordering_flow(
        self,
        *,
        user_id: str,
        session_id: str,
        order: dict[str, Any] | None,
        reset_requested: bool,
    ) -> WhatsAppOrderFlowResult:
        self.agent_sessions.clear_whatsapp_order_state(user_id, session_id)
        if order is not None and order.get("status") in CANCELLABLE_ORDER_FLOW_STATUSES:
            response = self.orders.update_order_flow(
                user_id,
                order["order_id"],
                "cancel",
            )
            text = (
                "I've cleared the current ordering flow. You're ready to start "
                "a new order. What would you like?"
                if reset_requested
                else "I've cancelled the pending order."
            )
            return self._result(
                "update_order_flow",
                response,
                is_write=True,
                text=text,
            )

        response = self.carts.discard_active_cart(user_id, session_id)
        discarded = bool((response.data or {}).get("discarded"))
        if reset_requested:
            text = (
                "I've cleared the current ordering flow. You're ready to start "
                "a new order. What would you like?"
            )
        elif discarded:
            text = "I've discarded the current cart."
        elif order is not None:
            text = (
                "That order has already been submitted, so I haven't cancelled "
                "the restaurant order."
            )
        else:
            text = "There isn't an active cart or pending order to cancel."
        return self._result(
            "discard_active_cart",
            response,
            is_write=discarded,
            text=text,
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
            response = self.carts.set_customization_mode(
                user_id, cart_id, selected["value"]
            )
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
                user_id,
                active_choice["cart_item_id"],
                active_choice["field_name"],
                selected["option_id"],
            )
            if response.success and response.next_action == "offer_upsell":
                response = self.carts.handle_upsell(
                    user_id, response.data["cart_id"], "get_options"
                )
            return self._cart_result("save_customization_choice", response)

        if status in {"item_ready", "awaiting_upsell_decision"}:
            options_response = self.carts.handle_upsell(
                user_id, cart_id, "get_options"
            )
            proceed_without_addon = bool(
                PROCEED_WITHOUT_ADDON_PATTERN.fullmatch(normalized)
            )
            if status == "item_ready" and not proceed_without_addon:
                return self._cart_result("handle_cart_upsell", options_response)
            if proceed_without_addon:
                return self._proceed_without_addon(user_id, cart_id)

            upsell_items = (options_response.data or {}).get("upsell_items", [])
            selected = self._select(upsell_items, normalized, ("name", "product_id"))
            if selected is None:
                intent = self._interpret(
                    state="upsell",
                    allowed_actions=[
                        "proceed_without_addon",
                        "skip_upsell",
                        "select_upsell",
                    ],
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
                if intent is not None and intent.action in {
                    "proceed_without_addon",
                    "skip_upsell",
                }:
                    return self._proceed_without_addon(user_id, cart_id)
                selected = self._selected_by_intent(
                    intent,
                    action="select_upsell",
                    choices=upsell_items,
                    id_field="product_id",
                )
            if selected is None:
                return self._cart_result("handle_cart_upsell", options_response)
            response = self.carts.handle_upsell(
                user_id,
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
            response = self.carts.create_pending_order(user_id, cart_id)
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
            if (
                DELIVERY_PATTERN.search(normalized) or normalized == "1"
            ) and not NEGATED_FULFILLMENT_PATTERN.search(normalized):
                action = "delivery"
            elif (
                TAKEAWAY_PATTERN.search(normalized) or normalized == "2"
            ) and not NEGATED_FULFILLMENT_PATTERN.search(normalized):
                action = "takeaway"
            elif CANCEL_PATTERN.fullmatch(normalized) and not NEGATED_CANCEL_PATTERN.search(
                normalized
            ):
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
                response = self.orders.update_order_flow(user_id, order_id, "set_delivery")
                tool_name = "update_order_flow"
                is_write = True
            elif action == "takeaway":
                response = self.orders.update_order_flow(user_id, order_id, "set_takeaway")
                tool_name = "update_order_flow"
                is_write = True
            elif action == "cancel":
                response = self.orders.update_order_flow(user_id, order_id, "cancel")
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
            if TAKEAWAY_PATTERN.fullmatch(normalized):
                response = self.orders.update_order_flow(user_id, order_id, "set_takeaway")
            elif (
                CANCEL_PATTERN.fullmatch(normalized)
                and normalized != "cancel address"
                and not NEGATED_CANCEL_PATTERN.search(normalized)
            ):
                response = self.orders.update_order_flow(user_id, order_id, "cancel")
            else:
                response = self.orders.update_order_flow(
                    user_id,
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

        if status == "awaiting_customer_name":
            suggested_name = order.get("suggested_customer_name")
            suggestion_rejected = order.get("customer_name_suggestion_rejected")
            name_match = CUSTOMER_NAME_PATTERN.search(original_message)
            if name_match is not None:
                cleaned_name = self._clean_corrected_customer_name(
                    name_match.group("name"),
                    original_message,
                )
                if cleaned_name is None:
                    return WhatsAppOrderFlowResult(
                        text=INVALID_CUSTOMER_NAME_CORRECTION_MESSAGE,
                        tool_calls=[],
                    )
                response = self.orders.update_order_flow(
                    user_id,
                    order_id,
                    "save_customer_name",
                    cleaned_name,
                )
            elif NEGATED_CONFIRM_PATTERN.search(
                normalized
            ) or NEGATED_CANCEL_PATTERN.search(normalized):
                return WhatsAppOrderFlowResult(
                    text=(
                        "I haven't changed the name. Reply yes to use the "
                        "suggested name, or send the correct name for the order."
                    ),
                    tool_calls=[],
                )
            elif (
                CANCEL_PATTERN.fullmatch(normalized)
                and not NEGATED_CANCEL_PATTERN.search(normalized)
            ):
                response = self.orders.update_order_flow(user_id, order_id, "cancel")
            elif suggested_name and not suggestion_rejected and CONFIRM_PATTERN.search(
                normalized
            ):
                response = self.orders.update_order_flow(
                    user_id,
                    order_id,
                    "confirm_customer_name",
                )
            elif suggested_name and not suggestion_rejected and (
                SKIP_PATTERN.search(normalized) or normalized == "reject"
            ):
                response = self.orders.update_order_flow(
                    user_id,
                    order_id,
                    "reject_customer_name",
                )
            else:
                response = self.orders.update_order_flow(
                    user_id,
                    order_id,
                    "save_customer_name",
                    original_message.strip(),
                )
            return self._result(
                "update_order_flow",
                response,
                is_write=True,
                text=self._order_step_text(response),
            )

        if status == "pending_confirmation":
            if NEGATED_CONFIRM_PATTERN.search(normalized) or NEGATED_CANCEL_PATTERN.search(
                normalized
            ):
                return WhatsAppOrderFlowResult(
                    text=(
                        "I haven't changed the order. Reply confirm to submit it, "
                        "or cancel to discard it."
                    ),
                    tool_calls=[],
                )
            correction_match = CUSTOMER_NAME_PATTERN.search(original_message)
            intent = None
            if correction_match is None and not (
                CONFIRM_PATTERN.fullmatch(normalized)
                or CANCEL_PATTERN.fullmatch(normalized)
            ):
                intent = self._interpret(
                    state="pending_confirmation",
                    allowed_actions=[
                        "customer_name_correction",
                        "confirm",
                        "cancel",
                    ],
                    message=original_message,
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                )
            corrected_name = (
                correction_match.group("name")
                if correction_match is not None
                else intent.extracted_name
                if intent is not None
                and intent.action == "customer_name_correction"
                else None
            )
            correction_attempted = correction_match is not None or (
                intent is not None
                and intent.action == "customer_name_correction"
            )
            if correction_attempted:
                cleaned_name = self._clean_corrected_customer_name(
                    corrected_name,
                    original_message,
                )
                if cleaned_name is None:
                    return WhatsAppOrderFlowResult(
                        text=INVALID_CUSTOMER_NAME_CORRECTION_MESSAGE,
                        tool_calls=[],
                    )
                response = self.orders.update_order_flow(
                    user_id,
                    order_id,
                    "save_customer_name",
                    cleaned_name,
                )
                return self._result(
                    "update_order_flow",
                    response,
                    is_write=True,
                    text=self._order_step_text(response),
                )

            action = None
            if CONFIRM_PATTERN.fullmatch(normalized):
                action = "confirm"
            elif CANCEL_PATTERN.fullmatch(normalized):
                action = "cancel"
            else:
                action = intent.action if intent is not None else None
            if action == "confirm":
                response = self.orders.update_order_flow(
                    user_id,
                    order_id,
                    "confirm",
                    idempotency_key=request_id,
                )
                tool_name = "update_order_flow"
                is_write = True
            elif action == "cancel":
                response = self.orders.update_order_flow(user_id, order_id, "cancel")
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
        normalized = self._normalize(message)
        if state != "upsell" and SKIP_PATTERN.search(normalized):
            return None
        if "confirm" in allowed_actions and NEGATED_CONFIRM_PATTERN.search(normalized):
            return None
        if "cancel" in allowed_actions and NEGATED_CANCEL_PATTERN.search(normalized):
            return None
        if {"delivery", "takeaway"}.intersection(allowed_actions) and (
            NEGATED_FULFILLMENT_PATTERN.search(normalized)
        ):
            return None
        if state != "upsell" and any(
            action.startswith("select_") for action in allowed_actions
        ) and (
            NEGATED_SELECTION_PATTERN.search(normalized)
        ):
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
    def _clean_corrected_customer_name(
        value: object,
        original_message: str,
    ) -> str | None:
        from src.services.customer_service import CustomerService

        candidate = value.rstrip(".! ") if isinstance(value, str) else value
        cleaned_name = CustomerService.clean_customer_name(candidate)
        if (
            cleaned_name is None
            or UNSAFE_CUSTOMER_NAME_PATTERN.search(cleaned_name)
            or " ".join(cleaned_name.casefold().split())
            not in " ".join(original_message.casefold().split())
        ):
            return None
        return cleaned_name

    @staticmethod
    def _should_classify_conversation_intent(
        order: dict[str, Any] | None,
        normalized: str,
    ) -> bool:
        if (
            normalized.isdigit()
            or SKIP_PATTERN.search(normalized)
            or PROCEED_WITHOUT_ADDON_PATTERN.fullmatch(normalized)
            or DIRECT_MENU_BROWSE_PATTERN.fullmatch(normalized)
        ):
            return False
        if order is None:
            return True
        status = order.get("status")
        if status == "awaiting_fulfillment_method" and (
            DELIVERY_PATTERN.search(normalized)
            or TAKEAWAY_PATTERN.search(normalized)
            or CANCEL_PATTERN.search(normalized)
        ):
            return False
        if status == "pending_confirmation" and (
            CONFIRM_PATTERN.search(normalized) or CANCEL_PATTERN.search(normalized)
        ):
            return False
        return True

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

    def _proceed_without_addon(
        self, user_id: str, cart_id: str
    ) -> WhatsAppOrderFlowResult:
        skip_response = self.carts.handle_upsell(user_id, cart_id, "skip")
        skip_result = self._cart_result("handle_cart_upsell", skip_response)
        if not skip_result.tool_calls[0]["success"]:
            return skip_result
        order_response = self.carts.create_pending_order(user_id, cart_id)
        order_result = self._result(
            "create_pending_order_from_cart",
            order_response,
            is_write=True,
            text=self._order_step_text(order_response),
        )
        return WhatsAppOrderFlowResult(
            text=order_result.text,
            tool_calls=[*skip_result.tool_calls, *order_result.tool_calls],
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
        if response.next_action == "ask_customer_name":
            return response.user_message
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
    def _menu_results_text(
        cls,
        response: ToolResponse,
        *,
        is_continuation: bool = False,
    ) -> str:
        items = (response.data or {}).get("items", [])
        if not items:
            return (
                f"{response.user_message} Please choose another preference, "
                "ask for available categories, or view the menu."
            )
        has_more = bool((response.data or {}).get("has_more"))
        heading = (
            "Here are more options you can choose from:"
            if is_continuation
            else (
                f"Here are the first {len(items)} options you can choose from:"
                if has_more
                else "Sure, here are some options you can choose from:"
            )
        )
        lines = [
            heading,
            *[
                f"{index}. {item.get('name', 'Menu item')} - {cls._menu_price(item)}"
                for index, item in enumerate(items, start=1)
            ],
        ]
        if has_more:
            lines.append("Reply show more to see more menu options.")
        lines.append("Which item would you like? Reply with its number or name.")
        return "\n".join(lines)

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
        if NEGATED_SELECTION_PATTERN.search(normalized):
            return None
        number_matches = re.findall(r"\b(\d+)\b", normalized)
        if len(set(number_matches)) > 1:
            return None
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
        return bool(
            re.fullmatch(r"(?:option\s+|item\s+)?\d+", normalized)
            or (
                NEGATED_SELECTION_PATTERN.search(normalized)
                and re.search(r"\b\d+\b", normalized)
            )
            or len(set(re.findall(r"\b\d+\b", normalized))) > 1
        )

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", value.casefold())).strip()

    @staticmethod
    def _menu_query(normalized: str) -> str | None:
        if NON_ORDER_PATTERN.search(normalized):
            return None
        specific_match = SPECIFIC_MENU_QUERY_PATTERN.fullmatch(normalized)
        if specific_match is not None:
            query = re.sub(
                r"^(?:(?:any|some|the)\s+)+",
                "",
                specific_match.group("query"),
            ).strip()
            query = re.sub(
                r"\s+(?:on|from)\s+(?:(?:the|your)\s+)?menu$",
                "",
                query,
            ).strip()
            broad_queries = {
                "menu",
                "the menu",
                "your menu",
                "full menu",
                "the full menu",
                "whole menu",
                "the whole menu",
                "more",
                "more items",
                "more options",
            }
            blocked_queries = {"my order", "order status", "the status", "status"}
            if query and query not in broad_queries and query not in blocked_queries:
                return query
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
