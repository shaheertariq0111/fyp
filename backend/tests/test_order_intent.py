import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.agent.order_intent import (
    ORDER_INTENT_SYSTEM_PROMPT,
    OrderIntentClassification,
    classify_order_intent,
)


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
