from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.agent.response_grounding import (
    AssistantClaimAssessment,
    GroundedAssistantMemoryBuffer,
    UNGROUNDED_TRANSACTION_FALLBACK,
    assess_assistant_claims,
    ground_authoritative_tool_response,
    ground_agent_response,
)


def tool_call(
    tool_name,
    *,
    success=True,
    is_write=False,
    user_message=None,
    data=None,
):
    result = {
        "success": success,
        "data": data or {},
    }
    if user_message is not None:
        result["user_message"] = user_message
    return SimpleNamespace(
        tool_name=tool_name,
        success=success,
        is_write=is_write,
        result=result,
        error_code=None if success else "WRITE_FAILED",
    )


def test_assistant_claim_classifier_uses_only_untrusted_messages_and_schema():
    calls = []

    class StructuredAgent:
        def __call__(self, prompt, **kwargs):
            calls.append((prompt, kwargs))
            return SimpleNamespace(structured_output=AssistantClaimAssessment(
                claims_transactional_progression=True,
                claimed_actions=["customization_saved"],
            ))

    result = assess_assistant_claims(
        customer_message="save that",
        assistant_message="Your customization is saved.",
        agent=StructuredAgent(),
    )

    assert result.claims_transactional_progression is True
    assert calls[0][1]["structured_output_model"] is AssistantClaimAssessment
    assert "save that" in calls[0][0]
    assert "Your customization is saved." in calls[0][0]


def test_search_selection_grounding_asks_only_for_item_and_sets_expected_action():
    result = ground_agent_response(
        text="Choose item 1 or 2 and tell me the size and crust.",
        tool_calls=[tool_call(
            "search_menu",
            data={
                "items": [
                    {"item_id": "item-1", "name": "First Item", "price": 10},
                    {"item_id": "item-2", "name": "Second Item", "price": 12},
                ]
            },
        )],
    )

    assert "1. First Item" in result.text
    assert "2. Second Item" in result.text
    assert result.text.endswith("Which item would you like?")
    assert "size" not in result.text.lower()
    assert "crust" not in result.text.lower()
    assert result.expected_transactional_action == "start_cart_item_customization"


def test_claimed_progression_without_write_evidence_fails_closed():
    result = ground_agent_response(
        text="The item is selected, customization saved, and it is added to your cart.",
        tool_calls=[],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=True,
            claimed_actions=["item_selected", "customization_saved", "item_added"],
        ),
        no_write_authorized=True,
    )

    assert result.text == UNGROUNDED_TRANSACTION_FALLBACK
    assert result.source == "ungrounded_transaction_fallback"


def test_successful_write_uses_authoritative_progression_message():
    result = ground_agent_response(
        text="I selected a size and invented the next crust question.",
        tool_calls=[tool_call(
            "start_cart_item_customization",
            is_write=True,
            user_message="Which size would you like? Available options: Small, Large.",
            data={"cart": {"status": "customizing_item"}},
        )],
    )

    assert result.text == "Which size would you like? Available options: Small, Large."
    assert result.source == "successful_write"


def test_authoritative_tool_fast_path_supports_current_state_reads():
    result = ground_authoritative_tool_response(
        tool_calls=[tool_call(
            "get_active_cart",
            user_message="Your cart is currently empty.",
            data={"cart": None},
        )],
        expected_write_tool="start_cart_item_customization",
    )

    assert result is not None
    assert result.text == "Your cart is currently empty."
    assert result.source == "authoritative_read"
    assert result.expected_transactional_action == "start_cart_item_customization"


def test_authoritative_tool_fast_path_rejects_insufficient_read_evidence():
    result = ground_authoritative_tool_response(
        tool_calls=[tool_call(
            "retrieve_restaurant_knowledge",
            data={"documents": [{"title": "Policy"}]},
        )],
    )

    assert result is None


def test_failed_write_never_uses_model_success_prose():
    result = ground_agent_response(
        text="Your customization was saved successfully.",
        tool_calls=[tool_call(
            "save_customization_choice",
            success=False,
            is_write=True,
            user_message="Please choose one of the available options.",
        )],
    )

    assert result.text == "Please choose one of the available options."
    assert result.source == "failed_write"


def test_successful_required_write_consumes_pending_transition():
    result = ground_agent_response(
        text="The model invented progression.",
        tool_calls=[tool_call(
            "start_cart_item_customization",
            is_write=True,
            user_message="Which size would you like?",
        )],
        expected_write_tool="start_cart_item_customization",
    )

    assert result.text == "Which size would you like?"
    assert result.source == "successful_write"
    assert result.expected_transactional_action is None


def test_successful_required_write_takes_priority_over_follow_up_read():
    result = ground_authoritative_tool_response(
        tool_calls=[
            tool_call(
                "start_cart_item_customization",
                is_write=True,
                user_message="Which size would you like?",
            ),
            tool_call(
                "get_active_cart",
                user_message="The cart is customizing an item.",
                data={"cart": {"status": "customizing_item"}},
            ),
        ],
        expected_write_tool="start_cart_item_customization",
    )

    assert result is not None
    assert result.text == "Which size would you like?"
    assert result.source == "successful_write"
    assert result.expected_transactional_action is None


def test_failed_required_write_preserves_pending_transition():
    result = ground_agent_response(
        text="The model claimed the item was selected.",
        tool_calls=[tool_call(
            "start_cart_item_customization",
            success=False,
            is_write=True,
            user_message="Please choose an available item.",
        )],
        expected_write_tool="start_cart_item_customization",
    )

    assert result.text == "Please choose an available item."
    assert result.source == "failed_write"
    assert result.expected_transactional_action == "start_cart_item_customization"


def test_informational_clarification_during_ordering_requires_no_write():
    text = "Thin crust is the crispier option; pan crust is thicker."
    result = ground_agent_response(
        text=text,
        tool_calls=[],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
        no_write_authorized=True,
    )

    assert result.text == text
    assert result.source == "conversation"


def test_transactional_customer_intent_does_not_block_conversational_response():
    text = "Absolutely. What kind of food are you in the mood for?"

    result = ground_agent_response(
        text=text,
        tool_calls=[],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
        no_write_authorized=False,
    )

    assert result.text == text
    assert result.source == "conversation"


def test_clarification_preserves_pending_authoritative_transition():
    text = "Would you like the first option or the second one?"

    result = ground_agent_response(
        text=text,
        tool_calls=[],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
        no_write_authorized=True,
        expected_write_tool="start_cart_item_customization",
    )

    assert result.text == text
    assert result.source == "conversation"
    assert result.expected_transactional_action == "start_cart_item_customization"


def test_transaction_can_resume_after_informational_clarification():
    clarification = ground_agent_response(
        text="Thin crust is crispier.",
        tool_calls=[],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
        no_write_authorized=True,
    )
    resumed = ground_agent_response(
        text="Great, I saved thin crust.",
        tool_calls=[tool_call(
            "save_customization_choice",
            is_write=True,
            user_message="Would you like extra cheese? Available options: Yes, No.",
        )],
    )

    assert clarification.source == "conversation"
    assert resumed.text == "Would you like extra cheese? Available options: Yes, No."
    assert resumed.source == "successful_write"


def test_pending_required_transition_cannot_be_claimed_without_write_evidence():
    result = ground_agent_response(
        text="Everything you picked is locked in; we're ready for fulfillment.",
        tool_calls=[],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=True,
            claimed_actions=["cart_progressed"],
        ),
        expected_write_tool="start_cart_item_customization",
    )

    assert result.text == UNGROUNDED_TRANSACTION_FALLBACK
    assert result.expected_transactional_action == "start_cart_item_customization"


def test_authoritative_state_claim_requires_authoritative_read_evidence():
    assessment = AssistantClaimAssessment(
        claims_transactional_progression=False,
        claimed_actions=[],
        depends_on_authoritative_state=True,
        authoritative_state_domains=["cart"],
    )

    missing = ground_agent_response(
        text="Your cart is empty.",
        tool_calls=[],
        claim_assessment=assessment,
    )
    observed = ground_agent_response(
        text="The model paraphrased the cart from memory.",
        tool_calls=[tool_call(
            "get_active_cart",
            user_message="Your cart is currently empty.",
            data={"cart": None},
        )],
        claim_assessment=assessment,
    )

    assert missing.text == UNGROUNDED_TRANSACTION_FALLBACK
    assert observed.text == "Your cart is currently empty."
    assert observed.source == "authoritative_read"


def test_contradictory_claim_classification_is_rejected():
    with pytest.raises(ValidationError, match="claim flag and claimed actions conflict"):
        AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=["item_added"],
        )


def test_malformed_classifier_output_is_rejected():
    class MalformedAgent:
        def __call__(self, prompt, **kwargs):
            return SimpleNamespace(structured_output={"unexpected": True})

    with pytest.raises(ValidationError):
        assess_assistant_claims(
            customer_message="do it",
            assistant_message="It is done.",
            agent=MalformedAgent(),
        )


def test_informational_menu_read_uses_authoritative_fast_path():
    text = "Cheese burst adds a cheese-filled layer to the crust."
    result = ground_agent_response(
        text=text,
        tool_calls=[tool_call(
            "search_menu",
            data={"items": [{"product_id": "item-1", "name": "Menu Item", "price": 10}]},
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
        no_write_authorized=True,
        informational_turn=True,
    )

    assert result.text.endswith("Which item would you like?")
    assert "Menu Item" in result.text
    assert result.source == "menu_search"


def test_informational_read_does_not_erase_pending_transition():
    text = "The first option is the milder one."
    result = ground_agent_response(
        text=text,
        tool_calls=[tool_call(
            "search_menu",
            data={"items": [{"product_id": "item-1", "name": "Menu Item", "price": 10}]},
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
        no_write_authorized=True,
        informational_turn=True,
        expected_write_tool="start_cart_item_customization",
    )

    assert result.text.endswith("Which item would you like?")
    assert result.source == "menu_search"
    assert result.expected_transactional_action == "start_cart_item_customization"


def test_get_menu_item_fast_path_preserves_pending_transition():
    result = ground_authoritative_tool_response(
        tool_calls=[tool_call(
            "get_menu_item",
            data={
                "item": {
                    "product_id": "item-1",
                    "name": "Menu Item",
                    "description": "Authoritative description.",
                    "price": 10,
                }
            },
        )],
        expected_write_tool="start_cart_item_customization",
    )

    assert result is not None
    assert "Authoritative description." in result.text
    assert result.source == "menu_item"
    assert result.expected_transactional_action == "start_cart_item_customization"


@pytest.mark.parametrize(
    ("question", "answer", "tool_name"),
    [
        ("What is cheese burst?", "Cheese burst has a cheese-filled crust.", "search_menu"),
        ("Which crust is less spicy?", "Neither listed crust adds spice.", "search_menu"),
        ("What comes with this pizza?", "It includes the listed toppings.", "get_menu_item"),
        ("Which pizza do you recommend?", "I recommend the first available option.", "search_menu"),
        ("How much is delivery?", "The current delivery fee policy is shown here.", "retrieve_restaurant_knowledge"),
    ],
)
def test_active_order_authoritative_reads_do_not_advance_state(question, answer, tool_name):
    call = tool_call(
        tool_name,
        data=(
            {"item": {"product_id": "item-1", "name": "Menu Item", "price": 10}}
            if tool_name == "get_menu_item"
            else {"items": [{"product_id": "item-1", "name": "Menu Item", "price": 10}]}
        ),
    )
    result = ground_agent_response(
        text=answer,
        tool_calls=[call],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
        no_write_authorized=True,
        informational_turn=True,
    )

    assert question
    if tool_name == "retrieve_restaurant_knowledge":
        assert result.text == answer
    elif tool_name == "get_menu_item":
        assert result.text == "Menu Item - 10"
    else:
        assert result.text.endswith("Which item would you like?")
    assert not any(getattr(current, "is_write", False) for current in [call])


def test_memory_commit_failure_never_persists_raw_assistant_and_next_turn_is_safe():
    class Manager:
        def __init__(self):
            self.messages = []
            self.fail_assistant = True

        def append_message(self, message, agent, **kwargs):
            if message["role"] == "assistant" and self.fail_assistant:
                raise RuntimeError("memory unavailable")
            self.messages.append(message)

        def redact_latest_message(self, message, agent):
            raise AssertionError("raw assistant redaction must not be used")

    manager = Manager()
    agent = SimpleNamespace()
    first = GroundedAssistantMemoryBuffer(manager)
    first.append_message({"role": "user", "content": [{"text": "select it"}]}, agent)
    first.append_message(
        {"role": "assistant", "content": [{"text": "Raw fabricated progression"}]},
        agent,
    )
    with pytest.raises(RuntimeError, match="memory unavailable"):
        first.commit(UNGROUNDED_TRANSACTION_FALLBACK, agent)

    assert "Raw fabricated progression" not in str(manager.messages)

    manager.fail_assistant = False
    second = GroundedAssistantMemoryBuffer(manager)
    second.append_message({"role": "user", "content": [{"text": "try again"}]}, agent)
    second.append_message(
        {"role": "assistant", "content": [{"text": "Another raw claim"}]},
        agent,
    )
    second.commit(UNGROUNDED_TRANSACTION_FALLBACK, agent)

    assert "Raw fabricated progression" not in str(manager.messages)
    assert "Another raw claim" not in str(manager.messages)
    assert manager.messages[-1]["content"][0]["text"] == UNGROUNDED_TRANSACTION_FALLBACK


def test_full_normal_order_write_sequence_remains_authoritatively_grounded():
    search = ground_agent_response(
        text="Choose an item and an invented customization.",
        tool_calls=[tool_call(
            "search_menu",
            data={"items": [{"item_id": "item-1", "name": "Menu Item", "price": 10}]},
        )],
    )
    sequence = [
        ("start_cart_item_customization", "Choose a size."),
        ("save_customization_choice", "Choose a crust."),
        ("save_customization_choice", "Your cart is ready."),
        ("create_pending_order_from_cart", "Choose delivery or takeaway."),
        ("choose_takeaway", "Please confirm the order summary."),
        ("choose_delivery", "Please provide the delivery address."),
        ("save_order_address", "Please confirm the order summary."),
        ("update_order_flow", "The order was updated successfully."),
        ("cancel_order", "The order was cancelled."),
        ("confirm_order", "Your order was submitted to the restaurant."),
    ]

    grounded = [
        ground_agent_response(
            text="Model-generated transactional prose.",
            tool_calls=[tool_call(name, is_write=True, user_message=message)],
        )
        for name, message in sequence
    ]

    assert search.source == "menu_search"
    assert search.text.endswith("Which item would you like?")
    assert [result.text for result in grounded] == [message for _, message in sequence]
    assert all(result.source == "successful_write" for result in grounded)
