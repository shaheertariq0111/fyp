from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal

from strands.hooks.events import (
    AfterInvocationEvent,
    AgentInitializedEvent,
    MessageAddedEvent,
)

from src.agent.continuation import (
    TransactionalContinuation,
    continuation_satisfied_by,
    continuation_unavailable,
)
from src.agent.whatsapp_submission_safety import (
    select_submission_safe_response,
)

GroundingSource = Literal[
    "anchored_write",
    "authoritative_read",
    "authoritative_continuation",
    "continuation_unavailable",
    "conversation",
    "exact_artifact",
    "failed_read",
    "failed_write",
    "rephraseable_write",
    "successful_write",
    "write_without_grounding",
    "submission_safety_fallback",
]
GroundingRejectionReason = Literal[
    "authoritative_menu_read_missing_exact_artifact",
    "authoritative_read_failed",
    "authoritative_write_failed",
    "continuation_resolution_failed",
    "required_effect_not_satisfied",
    "successful_write_missing_safe_grounding",
    "ungrounded_menu_recommendation",
    "unsupported_order_submission_claim",
]

FAILED_TRANSACTION_FALLBACK = (
    "I couldn't complete that change. Your authoritative order state was not advanced."
)
FAILED_READ_FALLBACK = (
    "I couldn't retrieve that information right now. Please try again."
)
MENU_SELECTION_FALLBACK = (
    "Please choose one of the menu options by number or name so I can continue."
)
UNGROUNDED_MENU_RECOMMENDATION_FALLBACK = (
    "I should check the current menu before recommending items. Please ask me "
    "to show current recommendations or browse the menu."
)
UNGROUNDED_MENU_RECOMMENDATION_RETRY_INSTRUCTION = (
    "Internal retry instruction: your previous draft recommended or listed menu "
    "items without a successful menu tool result. Do not answer from memory. "
    "Silently call search_menu now. For broad recommendation requests, call "
    "search_menu without a query. Answer only from successful menu tool results."
)
AUTHORITATIVE_MENU_READ_TOOLS = frozenset(
    {"list_menu_categories", "search_menu", "get_menu_item", "search_menu_options"}
)
UNGROUNDED_MENU_RECOMMENDATION_PATTERN = re.compile(
    r"\b(?:"
    r"popular items from (?:our|the) menu|"
    r"recommend(?:ed|ation)?(?:\s+\w+){0,4}\s+(?:items?|picks?|options?)|"
    r"here are (?:some|a few|the) .{0,80}(?:items?|picks?|options?)|"
    r"\d+\.\s+\*?[\w][^\n]{0,60}\*?\s+-\s+"
    r")\b",
    re.IGNORECASE | re.DOTALL,
)
UNSUPPORTED_MENU_CUSTOMIZATION_PATTERN = re.compile(
    r"\b(?:"
    r"customi[sz]ation process|"
    r"start (?:the )?customi[sz]ation|"
    r"add (?:the )?.{0,80}\s+to (?:your|the) cart|"
    r"proceed to checkout|"
    r"delivery address|"
    r"name and phone|"
    r"phone number|"
    r"order details (?:are )?(?:now )?confirmed|"
    r"summary of your order|"
    r"ready for submission|"
    r"submit (?:your|the) order|"
    r"confirm (?:the )?(?:size|crust|sauce|drink|flavou?r|quantity)|"
    r"choose (?:the |a )?(?:size|crust|sauce|drink|flavou?r|quantity)|"
    r"which (?:size|crust|sauce|drink|flavou?r|quantity)"
    r")\b",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class GroundedAgentResponse:
    text: str
    source: GroundingSource
    rejection_reason: GroundingRejectionReason | None = None


class GroundedAssistantMemoryBuffer:
    """Delay the final assistant message until deterministic response selection."""

    def __init__(self, session_manager: Any) -> None:
        self.session_manager = session_manager
        self.original_append = session_manager.append_message
        self.pending_assistant: tuple[Any, Any] | None = None

    def register_hooks(self, registry, **kwargs) -> None:
        registry.add_callback(
            AgentInitializedEvent,
            lambda event: self.session_manager.initialize(event.agent),
        )
        registry.add_callback(
            MessageAddedEvent,
            lambda event: self.append_message(event.message, event.agent),
        )
        registry.add_callback(
            MessageAddedEvent,
            lambda event: self.session_manager.sync_agent(event.agent),
        )
        registry.add_callback(
            AfterInvocationEvent,
            lambda event: self.session_manager.sync_agent(event.agent),
        )

    def append_message(self, message, agent, **kwargs):
        role = (
            message.get("role")
            if isinstance(message, dict)
            else getattr(message, "role", None)
        )
        if role == "assistant":
            if self.pending_assistant is not None:
                pending, pending_agent = self.pending_assistant
                self.original_append(pending, pending_agent)
            self.pending_assistant = (message, agent)
            return None
        if self.pending_assistant is not None:
            pending, pending_agent = self.pending_assistant
            self.original_append(pending, pending_agent)
            self.pending_assistant = None
        return self.original_append(message, agent, **kwargs)

    def commit(self, text: str, agent: Any) -> None:
        self.original_append(
            {"role": "assistant", "content": [{"text": text}]},
            agent,
        )
        self.pending_assistant = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.session_manager, name)


def ground_authoritative_tool_response(
    *,
    tool_calls: list[Any],
) -> GroundedAgentResponse | None:
    """Select deterministic write outcomes and authoritative read results."""

    calls = list(tool_calls or [])
    for call in reversed(calls):
        if not bool(_value(call, "is_write")):
            continue
        result = _result(call)
        user_message = _clean_text(result.get("user_message"))
        if not _call_succeeded(call):
            return GroundedAgentResponse(
                user_message or FAILED_TRANSACTION_FALLBACK,
                "failed_write",
                "authoritative_write_failed",
            )
        evidence = _grounding_evidence(call)
        exact_text = _clean_text(evidence.get("exact_customer_text"))
        if exact_text:
            return GroundedAgentResponse(exact_text, "exact_artifact")
        if evidence.get("allows_semantic_rephrasing") is True and user_message:
            return GroundedAgentResponse(user_message, "rephraseable_write")
        if evidence.get("allows_natural_phrasing") is True and user_message:
            # The backend marked this outcome as fact-free. Its statement is
            # still guaranteed to reach the customer; the agent may only add the
            # next step around it. See _anchor_natural_phrasing.
            return GroundedAgentResponse(user_message, "anchored_write")
        return GroundedAgentResponse(
            user_message or FAILED_TRANSACTION_FALLBACK,
            "successful_write" if user_message else "write_without_grounding",
            (
                None
                if user_message
                else "successful_write_missing_safe_grounding"
            ),
        )

    read_calls = [call for call in calls if not bool(_value(call, "is_write"))]
    if read_calls and not _call_succeeded(read_calls[-1]):
        return GroundedAgentResponse(
            _clean_text(_result(read_calls[-1]).get("user_message"))
            or FAILED_READ_FALLBACK,
            "failed_read",
            "authoritative_read_failed",
        )

    for call in reversed(read_calls):
        if not _call_succeeded(call):
            continue
        exact_text = _clean_text(
            _grounding_evidence(call).get("exact_customer_text")
        )
        if exact_text:
            return GroundedAgentResponse(exact_text, "exact_artifact")
        if _value(call, "tool_name") in AUTHORITATIVE_MENU_READ_TOOLS:
            return GroundedAgentResponse(
                _clean_text(_result(call).get("user_message"))
                or FAILED_READ_FALLBACK,
                "authoritative_read",
                "authoritative_menu_read_missing_exact_artifact",
            )
    return None


def ground_agent_response(
    *,
    text: str,
    tool_calls: list[Any],
    continuation: TransactionalContinuation | None = None,
) -> GroundedAgentResponse:
    """Apply deterministic tool-evidence safeguards without semantic judgment."""

    authoritative = ground_authoritative_tool_response(tool_calls=tool_calls)
    if authoritative is not None and authoritative.source == "rephraseable_write":
        authoritative = _use_semantic_rephrasing(authoritative, text)
    if authoritative is not None and authoritative.source == "anchored_write":
        authoritative = _anchor_natural_phrasing(authoritative, text)
    selected = authoritative or GroundedAgentResponse(text, "conversation")
    selected = _enforce_transactional_continuation(
        selected=selected,
        authoritative=authoritative,
        tool_calls=tool_calls,
        continuation=continuation,
    )
    submission_decision = select_submission_safe_response(
        text=selected.text,
        tool_calls=tool_calls,
    )
    if submission_decision.source == "authoritative_submission":
        return GroundedAgentResponse(
            submission_decision.text,
            "exact_artifact",
        )
    if submission_decision.blocked:
        return GroundedAgentResponse(
            submission_decision.text,
            "submission_safety_fallback",
            "unsupported_order_submission_claim",
        )
    if (
        authoritative is None
        and _looks_like_ungrounded_menu_recommendation(
            submission_decision.text,
            tool_calls,
        )
    ):
        return GroundedAgentResponse(
            UNGROUNDED_MENU_RECOMMENDATION_FALLBACK,
            "conversation",
            "ungrounded_menu_recommendation",
        )
    return GroundedAgentResponse(
        submission_decision.text,
        selected.source,
        selected.rejection_reason,
    )


def _anchor_natural_phrasing(
    anchor: GroundedAgentResponse,
    text: str,
) -> GroundedAgentResponse:
    """Keep the backend statement while letting the agent add the next step.

    The authoritative sentence always reaches the customer. The agent's wording
    is kept around it so the reply can end with a real next step instead of a
    dead-end status line. This compares backend-authored text against the reply;
    it never inspects what the customer said.
    """
    model_text = _clean_text(text)
    if not model_text:
        return GroundedAgentResponse(anchor.text, "successful_write")
    if anchor.text in model_text:
        return GroundedAgentResponse(model_text, "successful_write")
    return GroundedAgentResponse(
        f"{anchor.text} {model_text}",
        "successful_write",
    )


def _use_semantic_rephrasing(
    fallback: GroundedAgentResponse,
    text: str,
) -> GroundedAgentResponse:
    """Let the model phrase simple required-next-step prompts naturally."""

    model_text = _clean_text(text)
    return GroundedAgentResponse(
        model_text or fallback.text,
        "successful_write",
    )


def _enforce_transactional_continuation(
    *,
    selected: GroundedAgentResponse,
    authoritative: GroundedAgentResponse | None,
    tool_calls: list[Any],
    continuation: TransactionalContinuation | None,
) -> GroundedAgentResponse:
    """Keep the response consistent with an unsatisfied backend requirement.

    This is state-based, not phrase-based: the only question asked is whether
    the effect the backend requires was actually produced this turn. When it was
    not, the authoritative pending state — never the model's prose — decides what
    the customer is told about that state.
    """

    if continuation_unavailable(continuation):
        # Authoritative state could not be read, so nothing can vouch for a
        # claim the model makes from memory alone. Backend-authored text from
        # this turn's own tool results is still trustworthy and is kept.
        if authoritative is not None:
            return selected
        return GroundedAgentResponse(
            FAILED_READ_FALLBACK,
            "continuation_unavailable",
            "continuation_resolution_failed",
        )
    if _unsupported_menu_offer_customization(
        selected=selected,
        tool_calls=tool_calls,
        continuation=continuation,
    ):
        return GroundedAgentResponse(
            MENU_SELECTION_FALLBACK,
            "authoritative_continuation",
            "required_effect_not_satisfied",
        )
    if continuation is None or not continuation.is_outstanding:
        return selected
    if continuation_satisfied_by(continuation, tool_calls):
        return selected
    pending_prompt = _clean_text(continuation.pending_prompt)
    if not pending_prompt:
        return selected
    if not tool_calls:
        # No tool ran, so the model learned nothing new this turn and cannot
        # speak for a state that did not move.
        return GroundedAgentResponse(
            pending_prompt,
            "authoritative_continuation",
            "required_effect_not_satisfied",
        )
    if pending_prompt in selected.text:
        return selected
    # Tools ran but none satisfied the requirement. Keep the grounded or
    # informational answer and restate the authoritative pending state.
    return GroundedAgentResponse(
        f"{selected.text}\n\n{pending_prompt}".strip(),
        selected.source if authoritative is not None else "conversation",
        selected.rejection_reason,
    )


def _unsupported_menu_offer_customization(
    *,
    selected: GroundedAgentResponse,
    tool_calls: list[Any],
    continuation: TransactionalContinuation | None,
) -> bool:
    if continuation is None or continuation.scope != "menu_offer":
        return False
    if tool_calls:
        return False
    return bool(UNSUPPORTED_MENU_CUSTOMIZATION_PATTERN.search(selected.text))


def _looks_like_ungrounded_menu_recommendation(
    text: str,
    tool_calls: list[Any],
) -> bool:
    for call in tool_calls:
        if (
            _value(call, "tool_name") in AUTHORITATIVE_MENU_READ_TOOLS
            and _call_succeeded(call)
        ):
            return False
    return bool(UNGROUNDED_MENU_RECOMMENDATION_PATTERN.search(text))


def grounding_decision_log_fields(response: GroundedAgentResponse) -> dict[str, Any]:
    return {
        "grounding_source": response.source,
        "grounding_rejection_reason": response.rejection_reason,
    }


def should_retry_grounding(response: GroundedAgentResponse) -> bool:
    return response.rejection_reason == "ungrounded_menu_recommendation"


def retry_message_for_grounding_failure(message: str) -> str:
    return f"{message}\n\n{UNGROUNDED_MENU_RECOMMENDATION_RETRY_INSTRUCTION}"


def _grounding_evidence(call: Any) -> dict[str, Any]:
    evidence = _result(call).get("grounding")
    return evidence if isinstance(evidence, dict) else {}


def _call_succeeded(call: Any) -> bool:
    return bool(_value(call, "success")) and _result(call).get("success") is True


def _result(call: Any) -> dict[str, Any]:
    result = _value(call, "result")
    return result if isinstance(result, dict) else {}


def _value(call: Any, key: str) -> Any:
    return call.get(key) if isinstance(call, dict) else getattr(call, key, None)


def _clean_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
