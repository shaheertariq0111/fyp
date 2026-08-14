from __future__ import annotations

from dataclasses import dataclass
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
    "authoritative_read",
    "authoritative_continuation",
    "continuation_unavailable",
    "conversation",
    "exact_artifact",
    "failed_read",
    "failed_write",
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
    "unsupported_order_submission_claim",
]

FAILED_TRANSACTION_FALLBACK = (
    "I couldn't complete that change. Your authoritative order state was not advanced."
)
FAILED_READ_FALLBACK = (
    "I couldn't retrieve that information right now. Please try again."
)
AUTHORITATIVE_MENU_READ_TOOLS = frozenset(
    {"search_menu", "get_menu_item", "search_menu_options"}
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
    return GroundedAgentResponse(
        submission_decision.text,
        selected.source,
        selected.rejection_reason,
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


def grounding_decision_log_fields(response: GroundedAgentResponse) -> dict[str, Any]:
    return {
        "grounding_source": response.source,
        "grounding_rejection_reason": response.rejection_reason,
    }


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
