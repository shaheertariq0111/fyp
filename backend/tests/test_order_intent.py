import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.agent.order_intent import (
    ORDER_INTENT_SYSTEM_PROMPT,
    OrderIntentClassification,
    classify_order_intent,
)
from src.agent.whatsapp_turn_intent import (
    WHATSAPP_TURN_INTENT_SYSTEM_PROMPT,
    WhatsAppTurnInterpretation,
    classify_whatsapp_turn,
)
from src.services.whatsapp_turn_policy_service import WhatsAppTurnPolicyService


class StructuredAgent:
    def __init__(self, classification):
        self.classification = classification
        self.calls = []

    def __call__(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return SimpleNamespace(structured_output=self.classification)


def test_order_intent_classifier_uses_only_structured_state_and_allowed_actions():
    agent = StructuredAgent(
        OrderIntentClassification(
            action="checkout",
            confidence=0.95,
        )
    )

    result = classify_order_intent(
        message="checkouttt",
        state="cart_ready",
        allowed_actions=["checkout"],
        available_options=[],
        agent=agent,
    )

    payload = json.loads(agent.calls[0][0])
    assert result.action == "checkout"
    assert result.confidence == 0.95
    assert payload == {
        "current_state": "cart_ready",
        "allowed_actions": ["checkout"],
        "available_options": [],
        "customer_message": "checkouttt",
    }
    assert agent.calls[0][1]["structured_output_model"] is OrderIntentClassification


def test_order_intent_schema_rejects_transactional_fields():
    with pytest.raises(ValidationError):
        OrderIntentClassification.model_validate({
            "action": "confirm",
            "confidence": 0.99,
            "order_id": "ORD-FAKE",
            "total": 1,
            "customer_response": "Your order is confirmed.",
        })


def test_order_intent_prompt_defines_flexible_latest_order_intents():
    assert "latest_order_eta" in ORDER_INTENT_SYSTEM_PROMPT
    assert "latest_order_status" in ORDER_INTENT_SYSTEM_PROMPT
    assert "when will I receive my order" in ORDER_INTENT_SYSTEM_PROMPT
    assert "Never infer an ETA or order status" in ORDER_INTENT_SYSTEM_PROMPT


def test_order_intent_prompt_defines_addon_progression_without_transaction_control():
    assert "proceed_without_addon" in ORDER_INTENT_SYSTEM_PROMPT
    assert "skip add-ons" in ORDER_INTENT_SYSTEM_PROMPT
    assert "Never calculate prices or totals" in ORDER_INTENT_SYSTEM_PROMPT


def test_order_intent_prompt_routes_menu_browsing_to_authoritative_data():
    assert "menu_browse" in ORDER_INTENT_SYSTEM_PROMPT
    assert "authoritative backend menu" in ORDER_INTENT_SYSTEM_PROMPT
    assert "never provide or infer item names" in ORDER_INTENT_SYSTEM_PROMPT
    assert "menu_browse_more" in ORDER_INTENT_SYSTEM_PROMPT


def test_whatsapp_turn_classifier_uses_strict_structured_context():
    agent = StructuredAgent(WhatsAppTurnInterpretation(
        action="menu_item_detail",
        confidence=0.95,
        informational_only=True,
        wants_to_order=False,
        question_type="contents",
        target_items=["choco bread"],
    ))

    result = classify_whatsapp_turn(
        message="what is choco bread?",
        state="menu_selection",
        allowed_actions=["menu_item_detail", "select_menu_item"],
        available_options=[{"id": "choco-bread", "label": "Choco Bread"}],
        agent=agent,
    )

    assert result.informational_only is True
    assert result.target_items == ["choco bread"]
    assert agent.calls[0][1]["structured_output_model"] is WhatsAppTurnInterpretation


@pytest.mark.parametrize(
    "payload",
    [
        {
            "action": "unknown_action",
            "confidence": 0.95,
            "informational_only": False,
            "wants_to_order": False,
        },
        {
            "action": "menu_item_detail",
            "confidence": 0.95,
            "informational_only": True,
            "wants_to_order": True,
            "target_items": ["lava cake"],
        },
    ],
)
def test_whatsapp_turn_schema_rejects_unknown_actions_and_conflicting_flags(payload):
    with pytest.raises(ValidationError):
        WhatsAppTurnInterpretation.model_validate(payload)


@pytest.mark.parametrize(
    ("interpretation", "options", "reason"),
    [
        (
            WhatsAppTurnInterpretation(
                action="menu_item_detail",
                confidence=0.4,
                informational_only=True,
                wants_to_order=False,
                target_items=["lava cake"],
            ),
            [],
            "low_confidence",
        ),
        (
            WhatsAppTurnInterpretation(
                action="select_menu_item",
                confidence=0.95,
                informational_only=False,
                wants_to_order=True,
                selected_option="invented-item",
            ),
            [{"id": "real-item", "label": "Real Item"}],
            "invalid_selected_option",
        ),
        (
            WhatsAppTurnInterpretation(
                action="checkout",
                confidence=0.95,
                informational_only=True,
                wants_to_order=False,
            ),
            [],
            "invalid_transactional_flags",
        ),
    ],
)
def test_whatsapp_turn_policy_rejects_unsafe_output(interpretation, options, reason):
    decision = WhatsAppTurnPolicyService().validate(
        interpretation,
        allowed_actions=[interpretation.action],
        available_options=options,
    )

    assert decision.accepted is False
    assert decision.reason == reason


def test_whatsapp_turn_prompt_keeps_menu_facts_and_transactions_backend_owned():
    assert "informational_only=true" in WHATSAPP_TURN_INTENT_SYSTEM_PROMPT
    assert "backend validates every extracted value" in WHATSAPP_TURN_INTENT_SYSTEM_PROMPT
    assert "Never answer the customer" in WHATSAPP_TURN_INTENT_SYSTEM_PROMPT
