from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from strands.hooks.events import (
    AfterInvocationEvent,
    AgentInitializedEvent,
    MessageAddedEvent,
)

from src.agent.whatsapp_submission_safety import (
    select_submission_safe_response,
)

GroundingSource = Literal[
    "conversation",
    "exact_artifact",
    "failed_read",
    "failed_write",
    "successful_write",
    "write_without_grounding",
    "submission_safety_fallback",
]
GroundingRejectionReason = Literal[
    "authoritative_read_failed",
    "authoritative_write_failed",
    "successful_write_missing_safe_grounding",
    "unsupported_order_submission_claim",
]

FAILED_TRANSACTION_FALLBACK = (
    "I couldn't complete that change. Your authoritative order state was not advanced."
)
FAILED_READ_FALLBACK = (
    "I couldn't retrieve that information right now. Please try again."
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
    return None


def ground_agent_response(
    *,
    text: str,
    tool_calls: list[Any],
) -> GroundedAgentResponse:
    """Apply deterministic tool-evidence safeguards without semantic judgment."""

    authoritative = ground_authoritative_tool_response(tool_calls=tool_calls)
    selected = authoritative or GroundedAgentResponse(text, "conversation")
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
