from types import SimpleNamespace

import pytest

from src.agent.continuation import TransactionalContinuation
from src.agent.response_grounding import (
    FAILED_TRANSACTION_FALLBACK,
    GroundedAssistantMemoryBuffer,
    ground_agent_response,
    ground_authoritative_tool_response,
    grounding_decision_log_fields,
)


def tool_call(
    *,
    tool_name="update_order_flow",
    is_write=True,
    outer_success=True,
    inner_success=True,
    user_message="Done.",
    grounding=None,
):
    result = {"success": inner_success, "user_message": user_message}
    if grounding is not None:
        result["grounding"] = grounding
    return {
        "tool_name": tool_name,
        "is_write": is_write,
        "success": outer_success,
        "result": result,
    }


@pytest.mark.parametrize(
    ("outer_success", "inner_success"),
    [(False, True), (True, False), (False, False)],
)
def test_failed_write_uses_authoritative_backend_message(
    outer_success,
    inner_success,
):
    result = ground_agent_response(
        text="Your order was updated successfully.",
        tool_calls=[
            tool_call(
                outer_success=outer_success,
                inner_success=inner_success,
                user_message="That change could not be completed.",
            )
        ],
    )

    assert result.text == "That change could not be completed."
    assert result.source == "failed_write"
    assert result.rejection_reason == "authoritative_write_failed"


def test_failed_write_without_backend_message_uses_safe_fallback():
    result = ground_agent_response(
        text="Your order was submitted.",
        tool_calls=[
            tool_call(
                outer_success=False,
                inner_success=False,
                user_message="",
            )
        ],
    )

    assert result.text == FAILED_TRANSACTION_FALLBACK
    assert result.source == "failed_write"


def test_successful_write_uses_exact_authoritative_customer_artifact():
    result = ground_agent_response(
        text="A shorter model-written summary.",
        tool_calls=[
            tool_call(
                user_message="The customization was saved.",
                grounding={
                    "transactional_effects": ["customization_saved"],
                    "exact_customer_text": (
                        "The customization was saved. Choose a size to continue."
                    ),
                },
            )
        ],
    )

    assert result.text == (
        "The customization was saved. Choose a size to continue."
    )
    assert result.source == "exact_artifact"
    assert result.rejection_reason is None


@pytest.mark.parametrize(
    ("tool_name", "effect", "model_text", "user_message"),
    [
        (
            "update_cart_item_customization",
            "customization_saved",
            "Saved the cheese and switched you to delivery.",
            "The customization was saved.",
        ),
        (
            "update_order_flow",
            "fulfillment_saved",
            "Delivery is selected and I also changed your address.",
            "The fulfillment method was saved.",
        ),
        (
            "save_customer_address",
            "address_saved",
            "The address and cart quantity were updated.",
            "The delivery address was saved.",
        ),
    ],
)
def test_successful_grounded_write_uses_authoritative_backend_presentation(
    tool_name,
    effect,
    model_text,
    user_message,
):
    result = ground_agent_response(
        text=model_text,
        tool_calls=[
            tool_call(
                tool_name=tool_name,
                user_message=user_message,
                grounding={"transactional_effects": [effect]},
            )
        ],
    )

    assert result.text == user_message
    assert model_text not in result.text
    assert result.source == "successful_write"
    assert result.rejection_reason is None


def test_successful_write_without_grounding_uses_backend_message():
    result = ground_agent_response(
        text="An unverified model-written success.",
        tool_calls=[tool_call(user_message="The cart was updated.")],
    )

    assert result.text == "The cart was updated."
    assert result.source == "successful_write"
    assert result.rejection_reason is None


def test_successful_write_without_safe_text_fails_closed():
    result = ground_agent_response(
        text="An unverified model-written success.",
        tool_calls=[
            tool_call(
                user_message="",
                grounding={"transactional_effects": ["customization_saved"]},
            )
        ],
    )

    assert result.text == FAILED_TRANSACTION_FALLBACK
    assert result.source == "write_without_grounding"
    assert result.rejection_reason == "successful_write_missing_safe_grounding"


def test_latest_successful_write_backend_message_wins_over_earlier_exact_artifact():
    result = ground_agent_response(
        text="Untrusted combined write summary.",
        tool_calls=[
            tool_call(
                user_message="Earlier write completed.",
                grounding={"exact_customer_text": "Earlier exact artifact."},
            ),
            tool_call(
                tool_name="save_customer_address",
                user_message="The delivery address was saved.",
                grounding={"transactional_effects": ["address_saved"]},
            ),
        ],
    )

    assert result.text == "The delivery address was saved."
    assert result.source == "successful_write"


def test_exact_read_artifact_is_authoritative():
    result = ground_authoritative_tool_response(
        tool_calls=[
            tool_call(
                tool_name="get_order_status",
                is_write=False,
                grounding={"exact_customer_text": "Order ORD-123 is being prepared."},
            )
        ]
    )

    assert result is not None
    assert result.text == "Order ORD-123 is being prepared."
    assert result.source == "exact_artifact"


def test_search_menu_exact_artifact_replaces_fabricated_model_listing():
    authoritative = (
        "Here are the current menu options I found:\n"
        "1. Real Pizza Alpha — MYR 20\n"
        "2. Real Pizza Beta — MYR 24"
    )
    result = ground_agent_response(
        text="Imaginary Supreme and Imaginary Deluxe are available.",
        tool_calls=[
            tool_call(
                tool_name="search_menu",
                is_write=False,
                user_message="I found current menu options.",
                grounding={"exact_customer_text": authoritative},
            )
        ],
    )

    assert result.text == authoritative
    assert "Real Pizza Alpha" in result.text
    assert "Real Pizza Beta" in result.text
    assert "Imaginary" not in result.text
    assert result.source == "exact_artifact"


def test_search_menu_without_exact_artifact_never_uses_model_listing():
    result = ground_agent_response(
        text="Imaginary Supreme is available.",
        tool_calls=[
            tool_call(
                tool_name="search_menu",
                is_write=False,
                user_message="I found current menu options.",
                grounding={"authoritative_domains": ["menu"]},
            )
        ],
    )

    assert result.text == "I found current menu options."
    assert "Imaginary Supreme" not in result.text
    assert result.source == "authoritative_read"
    assert result.rejection_reason == (
        "authoritative_menu_read_missing_exact_artifact"
    )


@pytest.mark.parametrize(
    ("tool_name", "user_message", "outer_success", "inner_success"),
    [
        (
            "search_menu",
            "I couldn't retrieve the menu right now.",
            False,
            False,
        ),
        (
            "get_order_status",
            "I couldn't retrieve your order status right now.",
            True,
            False,
        ),
    ],
)
def test_failed_read_uses_authoritative_backend_failure_text(
    tool_name,
    user_message,
    outer_success,
    inner_success,
):
    result = ground_agent_response(
        text="Here is the information you requested.",
        tool_calls=[
            tool_call(
                tool_name=tool_name,
                is_write=False,
                outer_success=outer_success,
                inner_success=inner_success,
                user_message=user_message,
            )
        ],
    )

    assert result.text == user_message
    assert result.source == "failed_read"
    assert result.rejection_reason == "authoritative_read_failed"


def test_failed_read_without_backend_text_uses_safe_read_fallback():
    result = ground_agent_response(
        text="Here are fabricated results.",
        tool_calls=[
            tool_call(
                tool_name="search_menu",
                is_write=False,
                outer_success=False,
                inner_success=False,
                user_message="",
            )
        ],
    )

    assert result.text == (
        "I couldn't retrieve that information right now. Please try again."
    )
    assert result.source == "failed_read"
    assert result.rejection_reason == "authoritative_read_failed"


def test_successful_read_recovery_ignores_earlier_failed_read():
    raw = "Your support ticket is currently being reviewed."
    result = ground_agent_response(
        text=raw,
        tool_calls=[
            tool_call(
                tool_name="get_support_ticket_status",
                is_write=False,
                outer_success=False,
                inner_success=False,
                user_message="The first support lookup failed.",
            ),
            tool_call(
                tool_name="get_support_ticket_status",
                is_write=False,
                user_message="The support ticket was retrieved.",
                grounding={"authoritative_domains": ["support"]},
            ),
        ],
    )

    assert result.text == raw
    assert result.source == "conversation"


def test_successful_non_menu_read_preserves_main_agent_response_without_classifier():
    result = ground_agent_response(
        text="Your support ticket is currently being reviewed.",
        tool_calls=[
            tool_call(
                tool_name="get_support_ticket_status",
                is_write=False,
                user_message="The support ticket was retrieved.",
                grounding={"authoritative_domains": ["support"]},
            )
        ],
    )

    assert result.text == "Your support ticket is currently being reviewed."
    assert result.source == "conversation"


def test_general_conversation_preserves_main_agent_response_without_classifier():
    result = ground_agent_response(
        text="Hello! How can I help with your order today?",
        tool_calls=[],
    )

    assert result.text == "Hello! How can I help with your order today?"
    assert result.source == "conversation"


class MemorySession:
    def __init__(self):
        self.messages = []

    def append_message(self, message, agent, **kwargs):
        self.messages.append(message)

    def initialize(self, agent):
        return None

    def sync_agent(self, agent):
        return None


def test_final_message_buffer_stores_only_selected_replacement():
    session = MemorySession()
    buffer = GroundedAssistantMemoryBuffer(session)
    agent = SimpleNamespace()

    buffer.append_message(
        {"role": "user", "content": [{"text": "Submit my order"}]},
        agent,
    )
    buffer.append_message(
        {"role": "assistant", "content": [{"text": "Raw draft"}]},
        agent,
    )
    buffer.commit("Authoritative confirmation", agent)

    assert session.messages == [
        {"role": "user", "content": [{"text": "Submit my order"}]},
        {
            "role": "assistant",
            "content": [{"text": "Authoritative confirmation"}],
        },
    ]


def test_final_message_buffer_does_not_persist_raw_draft_after_failed_commit():
    class FailingSession(MemorySession):
        def append_message(self, message, agent, **kwargs):
            if message.get("role") == "assistant":
                raise RuntimeError("memory unavailable")
            super().append_message(message, agent, **kwargs)

    session = FailingSession()
    buffer = GroundedAssistantMemoryBuffer(session)
    agent = SimpleNamespace()
    buffer.append_message(
        {"role": "assistant", "content": [{"text": "Raw draft"}]},
        agent,
    )

    with pytest.raises(RuntimeError, match="memory unavailable"):
        buffer.commit("Selected response", agent)

    buffer.pending_assistant = None
    assert session.messages == []


def test_grounding_log_fields_contain_only_deterministic_diagnostics():
    response = ground_agent_response(text="Hello", tool_calls=[])

    assert grounding_decision_log_fields(response) == {
        "grounding_source": "conversation",
        "grounding_rejection_reason": None,
    }


def continuation(
    *,
    scope="order",
    resource_id="ORD-1",
    state="awaiting_fulfillment_method",
    required_effect="fulfillment_saved",
    required_input="fulfillment_method",
    valid_next_actions=("update_order_flow:set_takeaway",),
    offered_options=(),
    pending_prompt="Would you like delivery or takeaway?",
):
    return TransactionalContinuation(
        scope=scope,
        resource_id=resource_id,
        state=state,
        required_effect=required_effect,
        required_input=required_input,
        valid_next_actions=tuple(valid_next_actions),
        offered_options=tuple(offered_options),
        pending_prompt=pending_prompt,
    )


def effect_call(effect, *, tool_name="update_order_flow", success=True):
    return {
        "tool_name": tool_name,
        "is_write": True,
        "success": success,
        "result": {
            "success": success,
            "user_message": "Backend confirmed the change.",
            "grounding": {"transactional_effects": [effect]},
        },
    }


def test_no_tool_turn_cannot_claim_fulfillment_progress_without_effect():
    """Regression: production emitted 'takeaway selected' with no tool call."""
    result = ground_agent_response(
        text=(
            "Fulfillment Method: Takeaway\n"
            "Your order is now ready for pickup."
        ),
        tool_calls=[],
        continuation=continuation(),
    )

    assert "ready for pickup" not in result.text
    assert result.text == "Would you like delivery or takeaway?"
    assert result.source == "authoritative_continuation"
    assert result.rejection_reason == "required_effect_not_satisfied"


def test_no_tool_turn_cannot_claim_customization_progress_without_effect():
    """Regression: production claimed crust selection with no tool call."""
    result = ground_agent_response(
        text="Great, Classic Crust it is! Your pizza is fully customized.",
        tool_calls=[],
        continuation=continuation(
            scope="cart",
            resource_id="cart-1",
            state="customizing_item",
            required_effect="customization_saved",
            required_input="customization_choice",
            valid_next_actions=("save_customization_choice",),
            pending_prompt="Choose a crust.\n\n1. Regular Crust\n2. Crunchy Thin Crust",
        ),
    )

    assert "Classic Crust" not in result.text
    assert result.text.startswith("Choose a crust.")
    assert result.source == "authoritative_continuation"


def test_successful_required_effect_restores_normal_grounding():
    result = ground_agent_response(
        text="Takeaway is set.",
        tool_calls=[effect_call("fulfillment_saved")],
        continuation=continuation(),
    )

    assert result.text == "Backend confirmed the change."
    assert result.source == "successful_write"
    assert result.rejection_reason is None


def test_unrelated_successful_effect_does_not_satisfy_continuation():
    result = ground_agent_response(
        text="All set!",
        tool_calls=[effect_call("customization_saved")],
        continuation=continuation(),
    )

    assert "Would you like delivery or takeaway?" in result.text


def test_failed_write_keeps_backend_failure_text_and_restates_pending_state():
    result = ground_agent_response(
        text="Takeaway selected!",
        tool_calls=[effect_call("fulfillment_saved", success=False)],
        continuation=continuation(),
    )

    assert "Takeaway selected!" not in result.text
    assert "Backend confirmed the change." in result.text
    assert "Would you like delivery or takeaway?" in result.text


def test_informational_tool_turn_keeps_answer_and_restates_pending_state():
    """FAQ answers must survive; the pending question is restated, not replaced."""
    result = ground_agent_response(
        text="We close at 11pm.",
        tool_calls=[
            {
                "tool_name": "retrieve_restaurant_knowledge",
                "is_write": False,
                "success": True,
                "result": {"success": True, "user_message": "knowledge"},
            }
        ],
        continuation=continuation(),
    )

    assert "We close at 11pm." in result.text
    assert "Would you like delivery or takeaway?" in result.text


def test_pending_prompt_is_not_duplicated_when_already_present():
    result = ground_agent_response(
        text="Would you like delivery or takeaway?",
        tool_calls=[
            {
                "tool_name": "retrieve_restaurant_knowledge",
                "is_write": False,
                "success": True,
                "result": {"success": True, "user_message": "knowledge"},
            }
        ],
        continuation=continuation(),
    )

    assert result.text.count("Would you like delivery or takeaway?") == 1


def test_non_blocking_continuation_leaves_general_conversation_untouched():
    result = ground_agent_response(
        text="Hello! How can I help?",
        tool_calls=[],
        continuation=continuation(
            state="pending_confirmation",
            required_effect=None,
            required_input="confirm_or_cancel",
            pending_prompt=None,
        ),
    )

    assert result.text == "Hello! How can I help?"
    assert result.source == "conversation"


def test_general_conversation_without_continuation_is_unchanged():
    result = ground_agent_response(
        text="Hello! How can I help with your order today?",
        tool_calls=[],
        continuation=None,
    )

    assert result.text == "Hello! How can I help with your order today?"
    assert result.source == "conversation"
