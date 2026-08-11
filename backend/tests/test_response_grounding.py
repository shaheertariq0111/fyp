from types import SimpleNamespace
from typing import get_args
import time

import pytest
from pydantic import ValidationError

from src.agent.response_grounding import (
    ASSISTANT_CLAIM_SYSTEM_PROMPT,
    AssessmentOrigin,
    AssistantClaimAssessment,
    GroundedAssistantMemoryBuffer,
    GroundingRejectionReason,
    SemanticClassifierTimeout,
    SemanticClassifierStatus,
    UNGROUNDED_TRANSACTION_FALLBACK,
    assess_assistant_claims,
    ground_authoritative_tool_response,
    ground_agent_response,
    grounding_comparison_log_fields,
    run_semantic_classifier,
)
from src.models.tool_responses import TransactionalEffect


def tool_call(
    tool_name,
    *,
    success=True,
    is_write=False,
    user_message=None,
    data=None,
    grounding=None,
    next_action=None,
):
    result = {
        "success": success,
        "data": data or {},
    }
    if user_message is not None:
        result["user_message"] = user_message
    if grounding is not None:
        result["grounding"] = grounding
    if next_action is not None:
        result["next_action"] = next_action
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
        tool_evidence=[{
            "grounding": {
                "authoritative_domains": ["cart"],
                "transactional_effects": ["customization_saved"],
            },
            "data": {"cart": {"status": "customizing_item"}},
        }],
        required_effect="item_selected",
        available_options=[{"id": "item-1", "label": "First Item"}],
        agent=StructuredAgent(),
    )

    assert result.claims_transactional_progression is True
    assert calls[0][1]["structured_output_model"] is AssistantClaimAssessment
    assert "save that" in calls[0][0]
    assert "Your customization is saved." in calls[0][0]
    assert "customization_saved" in calls[0][0]
    assert "item_selected" in calls[0][0]
    assert "item-1" in calls[0][0]


def test_assistant_claim_prompt_defines_every_transactional_effect():
    for effect in get_args(TransactionalEffect):
        assert effect in ASSISTANT_CLAIM_SYSTEM_PROMPT


def test_grounding_rejection_taxonomy_is_stable_and_complete():
    assert set(get_args(GroundingRejectionReason)) == {
        "claim_assessment_missing",
        "unsupported_transactional_effect",
        "required_effect_not_satisfied",
        "selected_option_missing",
        "selected_option_invalid",
        "unsupported_authoritative_domain",
        "authoritative_claims_unsupported",
        "presentation_limit_exceeded",
        "immutable_fact_mismatch",
        "authoritative_write_failed",
        "successful_write_missing_safe_grounding",
    }
    assert set(get_args(AssessmentOrigin)) == {
        "model",
        "timeout_synthetic",
        "exception_synthetic",
        "boundary_missing_synthetic",
    }
    assert set(get_args(SemanticClassifierStatus)) == {
        "completed",
        "timed_out",
        "failed",
        "not_run_authoritative_fast_path",
    }


def test_search_selection_grounding_preserves_supported_bounded_model_response():
    text = "1. First Item - 10\n2. Second Item - 12\nWhich item would you like?"
    result = ground_agent_response(
        text=text,
        tool_calls=[tool_call(
            "search_menu",
            data={
                "items": [
                    {"item_id": "item-1", "name": "First Item", "price": 10},
                    {"item_id": "item-2", "name": "Second Item", "price": 12},
                ]
            },
            grounding={
                "authoritative_domains": ["menu"],
                "required_next_effect": "item_selected",
                "offered_options": [
                    {"id": "item-1", "label": "First Item"},
                    {"id": "item-2", "label": "Second Item"},
                ],
                "presentation": {"max_items": 5},
            },
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
            depends_on_authoritative_state=True,
            authoritative_state_domains=["menu"],
            authoritative_claims_supported=True,
            presented_authoritative_item_count=2,
        ),
    )

    assert result.text == text
    assert result.text.endswith("Which item would you like?")
    assert "size" not in result.text.lower()
    assert "crust" not in result.text.lower()
    assert result.required_next_effect == "item_selected"


def test_pending_effect_preserves_requested_prose_without_progression_claim():
    result = ground_agent_response(
        text="Great, which size would you like?",
        tool_calls=[],
        required_effect="item_selected",
        available_options=[{"id": "item-1", "label": "First Item"}],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            customer_requests_required_effect=True,
            selected_option="item-1",
        ),
    )

    assert result.text == "Great, which size would you like?"
    assert result.required_next_effect == "item_selected"


def test_successful_effect_satisfies_and_clears_pending_requirement():
    result = ground_agent_response(
        text="Great, which size would you like?",
        tool_calls=[tool_call(
            "any_selection_capability",
            success=True,
            is_write=True,
            grounding={
                "authoritative_domains": ["cart"],
                "transactional_effects": ["item_selected"],
            },
        )],
        required_effect="item_selected",
        available_options=[{"id": "item-1", "label": "First Item"}],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            customer_requests_required_effect=True,
            selected_option="item-1",
        ),
    )

    assert result.text == "Great, which size would you like?"
    assert result.required_next_effect is None


def test_informational_detour_preserves_pending_requirement():
    result = ground_agent_response(
        text="It contains vegetables.",
        tool_calls=[],
        required_effect="item_selected",
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            customer_requests_required_effect=False,
            informational_turn=True,
        ),
    )

    assert result.text == "It contains vegetables."
    assert result.required_next_effect == "item_selected"


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


def test_generic_authoritative_read_does_not_force_a_fast_path_response():
    result = ground_authoritative_tool_response(
        tool_calls=[tool_call(
            "get_active_cart",
            user_message="Your cart is currently empty.",
            data={"cart": None},
            grounding={"authoritative_domains": ["cart"]},
        )],
        expected_write_tool="start_cart_item_customization",
    )

    assert result is None


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
            "any_selection_capability",
            is_write=True,
            user_message="Which size would you like?",
            grounding={
                "transactional_effects": ["item_selected"],
                "exact_customer_text": "Which size would you like?",
            },
        )],
        expected_write_tool="start_cart_item_customization",
        required_effect="item_selected",
    )

    assert result.text == "Which size would you like?"
    assert result.source == "exact_artifact"
    assert result.required_next_effect is None


def test_successful_required_write_takes_priority_over_follow_up_read():
    result = ground_authoritative_tool_response(
        tool_calls=[
            tool_call(
                "any_selection_capability",
                is_write=True,
                user_message="Which size would you like?",
                grounding={
                    "transactional_effects": ["item_selected"],
                    "exact_customer_text": "Which size would you like?",
                },
            ),
            tool_call(
                "get_active_cart",
                user_message="The cart is customizing an item.",
                data={"cart": {"status": "customizing_item"}},
            ),
        ],
        expected_write_tool="start_cart_item_customization",
        required_effect="item_selected",
    )

    assert result is not None
    assert result.text == "Which size would you like?"
    assert result.source == "exact_artifact"
    assert result.required_next_effect is None


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
        authoritative_claims_supported=True,
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
            grounding={"authoritative_domains": ["cart"]},
        )],
        claim_assessment=assessment,
    )

    assert missing.text == UNGROUNDED_TRANSACTION_FALLBACK
    assert observed.text == "The model paraphrased the cart from memory."
    assert observed.source == "conversation"


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


def test_informational_menu_read_preserves_supported_model_presentation():
    text = "Cheese burst adds a cheese-filled layer to the crust."
    result = ground_agent_response(
        text=text,
        tool_calls=[tool_call(
            "search_menu",
            data={"items": [{"product_id": "item-1", "name": "Menu Item", "price": 10}]},
            grounding={"authoritative_domains": ["menu"]},
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
            depends_on_authoritative_state=True,
            authoritative_state_domains=["menu"],
            authoritative_claims_supported=True,
            presented_authoritative_item_count=1,
        ),
        no_write_authorized=True,
        informational_turn=True,
    )

    assert result.text == text
    assert result.source == "conversation"


def test_informational_read_does_not_erase_pending_transition():
    text = "The first option is the milder one."
    result = ground_agent_response(
        text=text,
        tool_calls=[tool_call(
            "search_menu",
            data={"items": [{"product_id": "item-1", "name": "Menu Item", "price": 10}]},
            grounding={"authoritative_domains": ["menu"]},
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
            depends_on_authoritative_state=True,
            authoritative_state_domains=["menu"],
            authoritative_claims_supported=True,
        ),
        no_write_authorized=True,
        informational_turn=True,
        expected_write_tool="start_cart_item_customization",
    )

    assert result.text == text
    assert result.source == "conversation"
    assert result.expected_transactional_action == "start_cart_item_customization"


def test_get_menu_item_evidence_does_not_force_a_fast_path_response():
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
            grounding={"authoritative_domains": ["menu"]},
        )],
        expected_write_tool="start_cart_item_customization",
    )

    assert result is None


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
    domain = (
        "restaurant_policy"
        if tool_name == "retrieve_restaurant_knowledge"
        else "menu"
    )
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
    assert result.text == answer
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
    search_text = "Menu Item is available for 10. Which item would you like?"
    search = ground_agent_response(
        text=search_text,
        tool_calls=[tool_call(
            "search_menu",
            data={"items": [{"item_id": "item-1", "name": "Menu Item", "price": 10}]},
            grounding={"authoritative_domains": ["menu"]},
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
            depends_on_authoritative_state=True,
            authoritative_state_domains=["menu"],
            authoritative_claims_supported=True,
        ),
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

    assert search.source == "conversation"
    assert search.text.endswith("Which item would you like?")
    assert [result.text for result in grounded] == [message for _, message in sequence]
    assert all(result.source == "successful_write" for result in grounded)


MAX_CUSTOMER_FACING_MENU_ITEMS = 5


def test_broad_menu_browse_has_a_bounded_customer_facing_result_set():
    items = [
        {
            "item_id": f"item-{index}",
            "name": f"Menu Choice {index}",
            "price": 10 + index,
        }
        for index in range(MAX_CUSTOMER_FACING_MENU_ITEMS + 3)
    ]

    bounded_text = "\n".join(
        f"{index + 1}. {item['name']} - {item['price']}"
        for index, item in enumerate(items[:MAX_CUSTOMER_FACING_MENU_ITEMS])
    )
    result = ground_agent_response(
        text=bounded_text,
        tool_calls=[tool_call(
            "search_menu",
            data={"items": items},
            grounding={
                "authoritative_domains": ["menu"],
                "presentation": {"max_items": MAX_CUSTOMER_FACING_MENU_ITEMS},
            },
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
            depends_on_authoritative_state=True,
            authoritative_state_domains=["menu"],
            authoritative_claims_supported=True,
            presented_authoritative_item_count=MAX_CUSTOMER_FACING_MENU_ITEMS,
        ),
    )

    presented = [line for line in result.text.splitlines() if line[:1].isdigit()]
    assert 0 < len(presented) <= MAX_CUSTOMER_FACING_MENU_ITEMS


def test_model_presentation_that_violates_declared_limit_fails_closed():
    result = ground_agent_response(
        text="The model presented too many authoritative entities.",
        tool_calls=[tool_call(
            "any_read_capability",
            data={"items": [{"item_id": str(index)} for index in range(6)]},
            grounding={
                "authoritative_domains": ["menu"],
                "presentation": {"max_items": 5},
            },
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
            depends_on_authoritative_state=True,
            authoritative_state_domains=["menu"],
            authoritative_claims_supported=True,
            presented_authoritative_item_count=6,
        ),
    )

    assert result.text == UNGROUNDED_TRANSACTION_FALLBACK


@pytest.mark.parametrize(
    ("customer_message", "model_reply"),
    [
        ("Thanks for checking that.", "You're welcome!"),
        ("Hello again.", "Hi! How can I help?"),
        ("Could you clarify what you meant?", "Of course—what should I clarify?"),
        ("How has your day been?", "It's going well, thank you!"),
    ],
)
def test_ordinary_conversation_is_not_replaced_by_an_existing_order_read(
    customer_message,
    model_reply,
):
    result = ground_agent_response(
        text=model_reply,
        tool_calls=[tool_call(
            "get_order_status",
            user_message="You have an order being prepared.",
            data={"orders": [{"order_id": "ORD-EXISTING", "status": "preparing"}]},
            grounding={"authoritative_domains": ["order"]},
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
        no_write_authorized=True,
    )

    assert customer_message
    assert result.text == model_reply
    assert result.source == "conversation"


@pytest.mark.parametrize(
    ("tool_name", "backend_message", "model_reply"),
    [
        (
            "handle_cart_upsell",
            "The add-on was added.",
            "Nice choice. Would you like anything else?",
        ),
        (
            "create_pending_order_from_cart",
            "The order is ready for fulfillment details.",
            "Great—would you prefer delivery or takeaway?",
        ),
    ],
)
def test_generic_success_evidence_does_not_require_canned_backend_presentation(
    tool_name,
    backend_message,
    model_reply,
):
    domain = "cart" if tool_name == "handle_cart_upsell" else "order"
    effect = "item_added" if tool_name == "handle_cart_upsell" else "checkout_started"
    result = ground_agent_response(
        text=model_reply,
        tool_calls=[tool_call(
            tool_name,
            is_write=True,
            user_message=backend_message,
            data={"cart": {"status": "ready"}},
            grounding={
                "authoritative_domains": [domain],
                "transactional_effects": [effect],
            },
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
    )

    assert result.text == model_reply


def test_menu_item_evidence_allows_a_natural_grounded_continuation():
    model_reply = (
        "The Garden Flatbread is 14 and includes roasted vegetables. "
        "Would you like to customize it?"
    )
    result = ground_agent_response(
        text=model_reply,
        tool_calls=[tool_call(
            "get_menu_item",
            data={
                "item": {
                    "item_id": "item-1",
                    "name": "Garden Flatbread",
                    "description": "Includes roasted vegetables.",
                    "price": 14,
                }
            },
            grounding={"authoritative_domains": ["menu"]},
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
            depends_on_authoritative_state=True,
            authoritative_state_domains=["menu"],
            authoritative_claims_supported=True,
        ),
        no_write_authorized=True,
    )

    assert result.text == model_reply
    assert "14" in result.text


@pytest.mark.parametrize(
    ("model_claim", "claimed_action"),
    [
        ("I added that item to your cart.", "item_added"),
        ("Checkout is now started.", "checkout_started"),
        ("Your order was submitted successfully.", "order_submitted"),
    ],
)
def test_unsupported_transactional_progression_fails_closed(
    model_claim,
    claimed_action,
):
    result = ground_agent_response(
        text=model_claim,
        tool_calls=[],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=True,
            claimed_actions=[claimed_action],
        ),
        no_write_authorized=True,
    )

    assert result.text == UNGROUNDED_TRANSACTION_FALLBACK
    assert result.source == "ungrounded_transaction_fallback"


def test_supported_transactional_effect_allows_natural_model_presentation():
    text = "Perfect, that choice is saved. Would you like anything else?"
    result = ground_agent_response(
        text=text,
        tool_calls=[tool_call(
            "any_write_capability",
            is_write=True,
            user_message="The customization was saved.",
            data={"cart": {"status": "item_ready"}},
            grounding={
                "authoritative_domains": ["cart"],
                "transactional_effects": ["customization_saved"],
            },
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=True,
            claimed_actions=["customization_saved"],
        ),
    )

    assert result.text == text
    assert result.source == "conversation"


def test_successful_unrelated_effect_does_not_authorize_model_progression():
    result = ground_agent_response(
        text="Your order was submitted.",
        tool_calls=[tool_call(
            "any_write_capability",
            is_write=True,
            user_message="The fulfillment method was saved.",
            data={"order": {"status": "awaiting_delivery_address"}},
            grounding={
                "authoritative_domains": ["order"],
                "transactional_effects": ["fulfillment_saved"],
            },
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=True,
            claimed_actions=["order_submitted"],
        ),
    )

    assert result.text == UNGROUNDED_TRANSACTION_FALLBACK


def test_supported_and_fabricated_effects_in_one_response_fail_closed():
    result = ground_agent_response(
        text="The item was added and the order was submitted.",
        tool_calls=[tool_call(
            "any_write_capability",
            is_write=True,
            grounding={"transactional_effects": ["item_added"]},
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=True,
            claimed_actions=["item_added", "order_submitted"],
        ),
    )

    assert result.text == UNGROUNDED_TRANSACTION_FALLBACK


def test_supported_and_fabricated_authoritative_facts_fail_closed():
    result = ground_agent_response(
        text="Order ORD-REAL is preparing and its total is 1.",
        tool_calls=[tool_call(
            "any_order_read",
            grounding={
                "authoritative_domains": ["order"],
                "immutable_facts": [
                    {"path": "order.order_id", "value": "ORD-REAL"},
                    {"path": "order.status", "value": "preparing"},
                    {"path": "order.total", "value": 25},
                ],
            },
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            depends_on_authoritative_state=True,
            authoritative_state_domains=["order"],
            authoritative_claims_supported=True,
            claimed_immutable_facts=[
                {"path": "order.order_id", "value": "ORD-REAL"},
                {"path": "order.total", "value": 1},
            ],
        ),
    )

    assert result.text == UNGROUNDED_TRANSACTION_FALLBACK


def test_natural_order_status_requires_immutable_facts_to_be_preserved():
    call = tool_call(
        "any_order_read",
        grounding={
            "authoritative_domains": ["order"],
            "immutable_facts": [
                {"path": "order.order_id", "value": "ORD-REAL"},
                {"path": "order.status", "value": "preparing"},
                {"path": "order.total", "value": 25},
            ],
        },
    )
    assessment = AssistantClaimAssessment(
        claims_transactional_progression=False,
        depends_on_authoritative_state=True,
        authoritative_state_domains=["order"],
        authoritative_claims_supported=True,
        claimed_immutable_facts=[
            {"path": "order.order_id", "value": "ORD-REAL"},
            {"path": "order.status", "value": "preparing"},
            {"path": "order.total", "value": 25},
        ],
    )

    result = ground_agent_response(
        text="Order ORD-REAL is currently preparing with a total of 25.",
        tool_calls=[call],
        claim_assessment=assessment,
    )

    assert result.text == "Order ORD-REAL is currently preparing with a total of 25."


@pytest.mark.parametrize(
    "claimed_fact",
    [
        {"path": "order.order_id", "value": "ORD-FABRICATED"},
        {"path": "order.total", "value": 1},
        {"path": "order.unknown", "value": "invented"},
    ],
)
def test_fabricated_or_unknown_immutable_fact_fails_closed(claimed_fact):
    result = ground_agent_response(
        text="The assistant asserted one critical order fact.",
        tool_calls=[tool_call(
            "any_order_read",
            grounding={
                "authoritative_domains": ["order"],
                "immutable_facts": [
                    {"path": "order.order_id", "value": "ORD-REAL"},
                    {"path": "order.total", "value": 25},
                ],
            },
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            depends_on_authoritative_state=True,
            authoritative_state_domains=["order"],
            authoritative_claims_supported=True,
            claimed_immutable_facts=[claimed_fact],
        ),
    )

    assert result.text == UNGROUNDED_TRANSACTION_FALLBACK


def test_unclaimed_immutable_fact_may_be_omitted_from_natural_response():
    result = ground_agent_response(
        text="Your order is still being handled.",
        tool_calls=[tool_call(
            "any_order_read",
            grounding={
                "authoritative_domains": ["order"],
                "immutable_facts": [
                    {"path": "order.order_id", "value": "ORD-REAL"},
                    {"path": "order.total", "value": 25},
                ],
            },
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            depends_on_authoritative_state=True,
            authoritative_state_domains=["order"],
            authoritative_claims_supported=True,
            claimed_immutable_facts=[],
        ),
    )

    assert result.text == "Your order is still being handled."


def test_multiple_order_indexed_immutable_paths_are_verified():
    result = ground_agent_response(
        text="The first order is preparing and the second is completed.",
        tool_calls=[tool_call(
            "any_order_read",
            grounding={
                "authoritative_domains": ["order"],
                "immutable_facts": [
                    {"path": "orders[0].order_id", "value": "ORD-ONE"},
                    {"path": "orders[0].status", "value": "preparing"},
                    {"path": "orders[1].order_id", "value": "ORD-TWO"},
                    {"path": "orders[1].status", "value": "completed"},
                ],
            },
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            depends_on_authoritative_state=True,
            authoritative_state_domains=["order"],
            authoritative_claims_supported=True,
            claimed_immutable_facts=[
                {"path": "orders[0].status", "value": "preparing"},
                {"path": "orders[1].status", "value": "completed"},
            ],
        ),
    )

    assert result.text == "The first order is preparing and the second is completed."


@pytest.mark.parametrize(
    ("tool_name", "backend_message", "data"),
    [
        (
            "choose_takeaway",
            "Total: PKR 2,450. Please confirm.",
            {"order": {"order_id": "ORD-AUTH", "total": 2450, "status": "pending_confirmation"}},
        ),
        (
            "confirm_order",
            "Order ORD-AUTH was submitted. Total: PKR 2,450.",
            {"order": {"order_id": "ORD-AUTH", "total": 2450, "status": "submitted_to_restaurant"}},
        ),
    ],
)
def test_critical_transaction_artifacts_remain_authoritative(
    tool_name,
    backend_message,
    data,
):
    result = ground_agent_response(
        text="Order ORD-FABRICATED is complete for PKR 1.",
        tool_calls=[tool_call(
            tool_name,
            is_write=True,
            user_message=backend_message,
            data=data,
            grounding={
                "authoritative_domains": ["order"],
                "transactional_effects": [
                    "order_submitted"
                    if tool_name == "confirm_order"
                    else "fulfillment_saved"
                ],
                "exact_customer_text": backend_message,
            },
        )],
    )

    assert result.text == backend_message
    assert "ORD-FABRICATED" not in result.text
    assert data["order"]["status"] in {
        "pending_confirmation",
        "submitted_to_restaurant",
    }


def test_exact_artifact_remains_complete_during_harmless_side_conversation():
    artifact = "Order ORD-EXACT total: PKR 2,450. Please confirm."
    result = ground_agent_response(
        text="Happy to explain later. The total looks different to me.",
        tool_calls=[tool_call(
            "any_confirmation_capability",
            is_write=True,
            user_message=artifact,
            grounding={
                "authoritative_domains": ["order"],
                "transactional_effects": ["fulfillment_saved"],
                "exact_customer_text": artifact,
            },
        )],
    )

    assert result.text == artifact


def test_conversation_continuity_preserves_natural_and_authoritative_boundaries():
    prior_status = ground_agent_response(
        text="The model invented a status.",
        tool_calls=[tool_call(
            "get_order_status",
            user_message="Order ORD-PRIOR has been submitted.",
            data={"order": {"order_id": "ORD-PRIOR", "status": "submitted_to_restaurant"}},
            grounding={
                "authoritative_domains": ["order"],
                "exact_customer_text": "Order ORD-PRIOR has been submitted.",
            },
        )],
    )
    acknowledgement = ground_agent_response(
        text="You're welcome!",
        tool_calls=[],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
        no_write_authorized=True,
    )
    menu_browse = ground_agent_response(
        text="Here are a few current choices.",
        tool_calls=[tool_call(
            "search_menu",
            data={"items": [
                {"item_id": f"choice-{index}", "name": f"Choice {index}", "price": index + 10}
                for index in range(MAX_CUSTOMER_FACING_MENU_ITEMS + 2)
            ]},
            grounding={
                "authoritative_domains": ["menu"],
                "presentation": {"max_items": MAX_CUSTOMER_FACING_MENU_ITEMS},
            },
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
    )
    item_selection = ground_agent_response(
        text="Choice 0 is 10. Shall we customize it?",
        tool_calls=[tool_call(
            "get_menu_item",
            data={"item": {"item_id": "choice-0", "name": "Choice 0", "price": 10}},
            grounding={"authoritative_domains": ["menu"]},
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
            depends_on_authoritative_state=True,
            authoritative_state_domains=["menu"],
            authoritative_claims_supported=True,
        ),
        no_write_authorized=True,
    )
    customization = ground_agent_response(
        text="The model must not rewrite exact options.",
        tool_calls=[tool_call(
            "start_cart_item_customization",
            is_write=True,
            user_message="Choose one size:\n1. Small — PKR 10\n2. Large — PKR 15",
            data={"cart": {"status": "customizing_item"}},
        )],
    )
    upsell = ground_agent_response(
        text="The model must not rewrite this priced upsell.",
        tool_calls=[tool_call(
            "handle_cart_upsell",
            is_write=True,
            user_message="Would you like a side for PKR 4?",
            data={"cart": {"status": "awaiting_upsell_decision"}},
        )],
    )
    checkout = ground_agent_response(
        text="All set—delivery or takeaway?",
        tool_calls=[tool_call(
            "create_pending_order_from_cart",
            is_write=True,
            user_message="The order is ready for fulfillment details.",
            data={"order": {"order_id": "ORD-NEW", "status": "awaiting_fulfillment_method"}},
            grounding={
                "authoritative_domains": ["order"],
                "transactional_effects": ["checkout_started"],
            },
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_actions=[],
        ),
    )
    fulfillment = ground_agent_response(
        text="The model invented a different total.",
        tool_calls=[tool_call(
            "choose_takeaway",
            is_write=True,
            user_message="Order ORD-NEW total: PKR 14. Please confirm.",
            data={"order": {"order_id": "ORD-NEW", "total": 14, "status": "pending_confirmation"}},
        )],
    )
    submission = ground_agent_response(
        text="The model invented a different order ID.",
        tool_calls=[tool_call(
            "confirm_order",
            is_write=True,
            user_message="Order ORD-NEW was submitted. Total: PKR 14.",
            data={"order": {"order_id": "ORD-NEW", "total": 14, "status": "submitted_to_restaurant"}},
        )],
    )

    presented_menu_lines = [
        line for line in menu_browse.text.splitlines() if line[:1].isdigit()
    ]
    assert prior_status.text == "Order ORD-PRIOR has been submitted."
    assert acknowledgement.text == "You're welcome!"
    assert len(presented_menu_lines) <= MAX_CUSTOMER_FACING_MENU_ITEMS
    assert item_selection.text == "Choice 0 is 10. Shall we customize it?"
    assert customization.text == "Choose one size:\n1. Small — PKR 10\n2. Large — PKR 15"
    assert upsell.text == "Would you like a side for PKR 4?"
    assert checkout.text == "All set—delivery or takeaway?"
    assert fulfillment.text == "Order ORD-NEW total: PKR 14. Please confirm."
    assert submission.text == "Order ORD-NEW was submitted. Total: PKR 14."


def _assert_observed_rejection(
    result,
    reason,
    *,
    expected_action="start_cart_item_customization",
    required_effect="item_selected",
):
    assert result.text == UNGROUNDED_TRANSACTION_FALLBACK
    assert result.source == "ungrounded_transaction_fallback"
    assert result.expected_transactional_action == expected_action
    assert result.required_next_effect == required_effect
    assert result.rejection_reason == reason
    assert result.diagnostics is not None


def test_grounding_observes_missing_assessment_without_behavior_change():
    result = ground_agent_response(
        text="Untrusted response",
        tool_calls=[],
        expected_write_tool="start_cart_item_customization",
        required_effect="item_selected",
    )
    _assert_observed_rejection(result, "claim_assessment_missing")
    assert result.required_next_effect == "item_selected"


def test_grounding_observes_unsupported_effect_without_behavior_change():
    result = ground_agent_response(
        text="The item was added.",
        tool_calls=[],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=True,
            claimed_actions=["item_added"],
        ),
        expected_write_tool="start_cart_item_customization",
        required_effect="item_selected",
    )
    _assert_observed_rejection(result, "unsupported_transactional_effect")
    assert result.diagnostics.unsupported_effects == ("item_added",)


def test_grounding_observes_required_effect_without_rejecting_conversation():
    result = ground_agent_response(
        text="Which size would you like?",
        tool_calls=[],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            customer_requests_required_effect=True,
            selected_option="item-1",
        ),
        expected_write_tool="start_cart_item_customization",
        required_effect="item_selected",
        available_options=[{"id": "item-1", "label": "First"}],
    )
    assert result.text == "Which size would you like?"
    assert result.source == "conversation"
    assert result.rejection_reason is None
    assert result.required_next_effect == "item_selected"


@pytest.mark.parametrize(
    ("selected_option", "reason"),
    [(None, "selected_option_missing"), ("item-2", "selected_option_invalid")],
)
def test_grounding_observes_selected_option_contract_without_rejection(selected_option, reason):
    result = ground_agent_response(
        text="Which size would you like?",
        tool_calls=[tool_call(
            "selection_capability",
            is_write=True,
            grounding={"transactional_effects": ["item_selected"]},
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            customer_requests_required_effect=True,
            selected_option=selected_option,
        ),
        expected_write_tool="start_cart_item_customization",
        required_effect="item_selected",
        available_options=[{"id": "item-1", "label": "First"}],
    )
    assert result.text == "Which size would you like?"
    assert result.source == "conversation"
    assert result.rejection_reason is None
    assert result.diagnostics.selected_option_present is (selected_option is not None)
    assert result.diagnostics.selected_option_contract_evaluated is True


def test_grounding_observes_unsupported_authoritative_domain():
    result = ground_agent_response(
        text="The menu says so.",
        tool_calls=[],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            depends_on_authoritative_state=True,
            authoritative_state_domains=["menu"],
            authoritative_claims_supported=True,
        ),
        expected_write_tool="start_cart_item_customization",
        required_effect="item_selected",
    )
    _assert_observed_rejection(result, "unsupported_authoritative_domain")


def test_grounding_observes_unsupported_authoritative_claims():
    result = ground_agent_response(
        text="The menu says so.",
        tool_calls=[tool_call(
            "search_menu",
            grounding={"authoritative_domains": ["menu"]},
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            depends_on_authoritative_state=True,
            authoritative_state_domains=["menu"],
            authoritative_claims_supported=False,
        ),
        expected_write_tool="start_cart_item_customization",
        required_effect="item_selected",
    )
    _assert_observed_rejection(result, "authoritative_claims_unsupported")


def test_grounding_observes_presentation_limit_rejection():
    result = ground_agent_response(
        text="Too many items.",
        tool_calls=[tool_call(
            "search_menu",
            grounding={"presentation": {"max_items": 1}},
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            presented_authoritative_item_count=2,
        ),
        expected_write_tool="start_cart_item_customization",
        required_effect="item_selected",
    )
    _assert_observed_rejection(result, "presentation_limit_exceeded")
    assert result.diagnostics.presentation_limit == 1


def test_grounding_observes_immutable_fact_mismatch():
    result = ground_agent_response(
        text="The order is complete.",
        tool_calls=[tool_call(
            "get_order_status",
            grounding={
                "immutable_facts": [{"path": "order.status", "value": "pending"}],
            },
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            claimed_immutable_facts=[
                {"path": "order.status", "value": "complete"},
            ],
        ),
        expected_write_tool="start_cart_item_customization",
        required_effect="item_selected",
    )
    _assert_observed_rejection(result, "immutable_fact_mismatch")
    assert result.diagnostics.immutable_fact_mismatch_count == 1


def test_successful_conversation_and_authoritative_read_have_no_rejection():
    conversation = ground_agent_response(
        text="How can I help?",
        tool_calls=[],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
        ),
    )
    authoritative_read = ground_agent_response(
        text="One current menu option.",
        tool_calls=[tool_call(
            "search_menu",
            grounding={"authoritative_domains": ["menu"]},
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
            depends_on_authoritative_state=True,
            authoritative_state_domains=["menu"],
            authoritative_claims_supported=True,
        ),
    )
    assert conversation.text == "How can I help?"
    assert conversation.source == "conversation"
    assert conversation.rejection_reason is None
    assert authoritative_read.text == "One current menu option."
    assert authoritative_read.source == "conversation"
    assert authoritative_read.rejection_reason is None


def test_authoritative_write_outcomes_have_observability_without_behavior_change():
    failed = ground_authoritative_tool_response(tool_calls=[tool_call(
        "confirm_order",
        success=False,
        is_write=True,
        user_message="The order could not be confirmed.",
    )])
    exact = ground_authoritative_tool_response(tool_calls=[tool_call(
        "confirm_order",
        is_write=True,
        grounding={"exact_customer_text": "Order confirmed."},
    )])
    missing_safe_grounding = ground_authoritative_tool_response(tool_calls=[tool_call(
        "confirm_order",
        is_write=True,
    )])
    assert failed.text == "The order could not be confirmed."
    assert failed.source == "failed_write"
    assert failed.expected_transactional_action is None
    assert failed.required_next_effect is None
    assert failed.rejection_reason == "authoritative_write_failed"
    assert exact.text == "Order confirmed."
    assert exact.source == "exact_artifact"
    assert exact.rejection_reason is None
    assert missing_safe_grounding.text == (
        "I couldn't complete that change. Your authoritative order state was not advanced."
    )
    assert missing_safe_grounding.source == "write_without_grounding"
    assert missing_safe_grounding.rejection_reason == (
        "successful_write_missing_safe_grounding"
    )


def test_semantic_classifier_completed_logs_queue_model_and_total_timing(caplog):
    assert run_semantic_classifier(
        classifier_name="test_classifier",
        operation=lambda: "complete",
    ) == "complete"

    started = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "semantic_classifier_started"
    )
    completed = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "semantic_classifier_completed"
    )
    assert started.classifier_run_id == completed.classifier_run_id
    assert completed.semantic_classifier_execution_started is True
    assert completed.semantic_classifier_queue_wait_ms >= 0
    assert completed.semantic_classifier_model_duration_ms >= 0
    assert completed.semantic_classifier_total_duration_ms >= 0


def test_semantic_classifier_timeout_does_not_log_final_model_duration(caplog):
    with pytest.raises(SemanticClassifierTimeout):
        run_semantic_classifier(
            classifier_name="test_timeout",
            operation=lambda: time.sleep(0.05),
            timeout_seconds=0.001,
        )

    timed_out = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "semantic_classifier_timed_out"
    )
    assert timed_out.semantic_classifier_execution_started is True
    assert timed_out.semantic_classifier_total_duration_ms >= 0
    assert timed_out.semantic_classifier_model_elapsed_at_timeout_ms >= 0
    assert not hasattr(timed_out, "semantic_classifier_model_duration_ms")


def test_semantic_classifier_failure_logs_only_safe_exception_type(caplog):
    def fail():
        raise ValueError("private model output")

    with pytest.raises(ValueError, match="private model output"):
        run_semantic_classifier(
            classifier_name="test_failure",
            operation=fail,
        )

    failed = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "semantic_classifier_failed"
    )
    assert failed.exception_type == "ValueError"
    assert "private model output" not in failed.getMessage()
    assert failed.exc_info is None
    assert not hasattr(failed, "exception_message")


def test_diagnostic_enum_values_fail_closed_at_logging_boundaries():
    grounded = ground_agent_response(
        text="Hello.",
        tool_calls=[tool_call(
            "malformed_capability",
            grounding={
                "transactional_effects": ["private effect payload"],
                "authoritative_domains": ["private domain payload"],
            },
        )],
        claim_assessment=AssistantClaimAssessment(
            claims_transactional_progression=False,
        ),
    )

    assert grounded.diagnostics.supported_effects == ()
    assert grounded.diagnostics.supported_domains == ()
    comparison = grounding_comparison_log_fields(
        runtime_grounding_source="private source payload",
        runtime_grounding_rejection_reason="private reason payload",
        runtime_text=grounded.text,
        backend_response=grounded,
        runtime_claim_assessment_present=True,
        runtime_grounding_metadata_present=True,
        assessment_transport_status="present_valid",
    )
    assert comparison["runtime_grounding_source"] is None
    assert comparison["runtime_grounding_rejection_reason"] is None


@pytest.mark.parametrize(
    "malformed_grounding",
    [
        {"authoritative_domains": None},
        {"transactional_effects": None},
    ],
)
def test_malformed_diagnostic_shapes_preserve_missing_assessment_fallback(
    malformed_grounding,
):
    result = ground_agent_response(
        text="Untrusted response",
        tool_calls=[tool_call(
            "malformed_read",
            grounding=malformed_grounding,
        )],
        expected_write_tool="start_cart_item_customization",
        required_effect="item_selected",
    )

    assert result.text == UNGROUNDED_TRANSACTION_FALLBACK
    assert result.source == "ungrounded_transaction_fallback"
    assert result.expected_transactional_action == (
        "start_cart_item_customization"
    )
    assert result.required_next_effect == "item_selected"
    assert result.rejection_reason == "claim_assessment_missing"


def test_malformed_unrelated_diagnostics_cannot_prevent_authoritative_exits():
    malformed = tool_call(
        "malformed_read",
        grounding={
            "authoritative_domains": None,
            "transactional_effects": None,
        },
    )
    failed = ground_authoritative_tool_response(tool_calls=[
        malformed,
        tool_call(
            "failed_write",
            success=False,
            is_write=True,
            user_message="The change failed safely.",
        ),
    ])
    exact = ground_authoritative_tool_response(tool_calls=[
        tool_call(
            "malformed_read",
            grounding={"authoritative_domains": None},
        ),
        tool_call(
            "authoritative_read",
            grounding={"exact_customer_text": "Authoritative result."},
        ),
    ])

    assert failed.text == "The change failed safely."
    assert failed.source == "failed_write"
    assert failed.expected_transactional_action is None
    assert failed.required_next_effect is None
    assert exact.text == "Authoritative result."
    assert exact.source == "exact_artifact"
    assert exact.expected_transactional_action is None
    assert exact.required_next_effect is None
