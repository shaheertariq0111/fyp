from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal


UNGROUNDED_ORDER_SUBMISSION_FALLBACK = (
    "I couldn't verify that this order was submitted, so I won't confirm it as "
    "placed. Please ask me to check your order status before trying again."
)
UNGROUNDED_ORDER_SUBMISSION_ERROR_CODE = (
    "UNGROUNDED_ORDER_SUBMISSION_CLAIM"
)
AUTHORITATIVE_SUBMISSION_TOOLS = {"confirm_order", "update_order_flow"}

# This is intentionally limited to successful final-order-submission claims.
# It is a deterministic safety backstop, not conversational intent routing.
_ORDER_SUBMISSION_CLAIM_PATTERNS = (
    re.compile(
        r"\b(?:your|the|this)\s+order\s+"
        r"(?:has\s+been|was|is)\s+(?:now\s+)?(?:successfully\s+)?"
        r"(?:submitted|placed|confirmed)(?:\s+successfully)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:your|the|this)\s+order\s+\S+\s+"
        r"(?:has\s+been|was|is)\s+(?:now\s+)?(?:successfully\s+)?"
        r"(?:submitted|placed|confirmed)(?:\s+successfully)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\border\s+(?:now\s+)?successfully\s+"
        r"(?:submitted|placed|confirmed)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:i|we)(?:'ve|\s+have)?\s+(?:successfully\s+)?"
        r"(?:submitted|placed|confirmed)\s+(?:your|the)\s+order\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\border\s+id\s*:\s*\S+.{0,500}"
        r"\bstatus\s*:\s*submitted\s+to\s+(?:the\s+)?restaurant\b",
        re.IGNORECASE | re.DOTALL,
    ),
)
_ORDER_SUPPORT_CONTEXT_PATTERN = re.compile(
    r"\border\s+(?:complaint|support|ticket|request|issue|problem|case)\b"
    r"|\b(?:complaint|support|ticket|request|issue|problem|case)\s+"
    r"(?:about|for|regarding)\s+(?:your|the|this)?\s*order\b",
    re.IGNORECASE,
)

SubmissionSafetySource = Literal[
    "unchanged",
    "authoritative_submission",
    "authoritative_existing_order_status",
    "unsupported_submission_claim",
]


@dataclass(frozen=True, slots=True)
class AuthoritativeOrderSubmission:
    order_id: str
    confirmation_text: str


@dataclass(frozen=True, slots=True)
class SubmissionSafetyDecision:
    text: str
    source: SubmissionSafetySource
    submission: AuthoritativeOrderSubmission | None = None

    @property
    def blocked(self) -> bool:
        return self.source == "unsupported_submission_claim"

    @property
    def submitted_order_id(self) -> str | None:
        return self.submission.order_id if self.submission is not None else None


def select_submission_safe_response(
    *,
    text: str,
    tool_calls: Any,
) -> SubmissionSafetyDecision:
    """Enforce the narrow final-order-submission proof contract."""

    calls = list(tool_calls) if isinstance(tool_calls, list) else []
    submission = authoritative_order_submission_from_tool_calls(calls)
    if submission is not None:
        return SubmissionSafetyDecision(
            submission.confirmation_text,
            "authoritative_submission",
            submission,
        )
    if text == UNGROUNDED_ORDER_SUBMISSION_FALLBACK:
        return SubmissionSafetyDecision(text, "unchanged")
    if not _claims_successful_order_submission(text):
        return SubmissionSafetyDecision(text, "unchanged")
    if _is_authoritative_existing_order_status(text, calls):
        return SubmissionSafetyDecision(
            text,
            "authoritative_existing_order_status",
        )
    return SubmissionSafetyDecision(
        UNGROUNDED_ORDER_SUBMISSION_FALLBACK,
        "unsupported_submission_claim",
    )


def submitted_order_id_from_response(response: Any) -> str | None:
    submission = authoritative_order_submission_from_response(response)
    return submission.order_id if submission is not None else None


def authoritative_order_submission_from_response(
    response: Any,
) -> AuthoritativeOrderSubmission | None:
    if not isinstance(response, dict):
        return None
    return authoritative_order_submission_from_tool_calls(
        response.get("tool_calls")
    )


def authoritative_order_submission_from_tool_calls(
    tool_calls: Any,
) -> AuthoritativeOrderSubmission | None:
    if not isinstance(tool_calls, list):
        return None

    proofs: set[tuple[str, str]] = set()
    for call in tool_calls:
        if (
            _value(call, "success") is not True
            or _value(call, "tool_name") not in AUTHORITATIVE_SUBMISSION_TOOLS
        ):
            continue
        result = _mapping(_value(call, "result"))
        if result.get("success") is not True:
            continue
        data = _mapping(result.get("data"))
        if data.get("status") != "submitted_to_restaurant":
            continue
        order_id = data.get("order_id")
        if not _is_canonical_string(order_id):
            continue
        agent = _mapping(result.get("agent"))
        submitted_order_id = agent.get("submitted_order_id")
        confirmation_text = agent.get("submission_confirmation")
        if (
            not _is_canonical_string(submitted_order_id)
            or submitted_order_id != order_id
            or not _is_canonical_string(confirmation_text)
        ):
            continue
        proofs.add((order_id, confirmation_text))

    if len(proofs) != 1:
        return None
    order_id, confirmation_text = next(iter(proofs))
    return AuthoritativeOrderSubmission(order_id, confirmation_text)


def _claims_successful_order_submission(text: str) -> bool:
    if _ORDER_SUPPORT_CONTEXT_PATTERN.search(text):
        return False
    return any(pattern.search(text) for pattern in _ORDER_SUBMISSION_CLAIM_PATTERNS)


def _claims_agent_submission_action(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:i|we)(?:'ve|\s+have)?\s+(?:successfully\s+)?"
            r"(?:submitted|placed|confirmed)\s+(?:your|the)\s+order\b",
            text,
            re.IGNORECASE,
        )
    )


def _is_authoritative_existing_order_status(
    text: str,
    tool_calls: list[Any],
) -> bool:
    if _claims_agent_submission_action(text):
        return False
    for call in tool_calls:
        if (
            _value(call, "tool_name") != "get_order_status"
            or _value(call, "success") is not True
        ):
            continue
        result = _mapping(_value(call, "result"))
        if result.get("success") is not True:
            continue
        data = _mapping(result.get("data"))
        agent = _mapping(result.get("agent"))
        status_message = agent.get("status_message")
        selected_order_id = agent.get("selected_order_id")
        if (
            not _is_canonical_string(status_message)
            or result.get("user_message") != status_message
            or not _is_canonical_string(selected_order_id)
        ):
            continue
        orders = []
        if isinstance(data.get("order"), dict):
            orders.append(data["order"])
        if isinstance(data.get("orders"), list):
            orders.extend(
                order for order in data["orders"] if isinstance(order, dict)
            )
        if any(
            order.get("order_id") == selected_order_id
            and order.get("status") == "submitted_to_restaurant"
            for order in orders
        ):
            mentioned_order_ids = _mentioned_order_ids(text)
            if not mentioned_order_ids or mentioned_order_ids == {
                selected_order_id
            }:
                return True
    return False


def _mentioned_order_ids(text: str) -> set[str]:
    return {
        match.rstrip(".,;:!?)]}")
        for match in re.findall(r"\bORD-[A-Za-z0-9][A-Za-z0-9_-]*", text)
    }


def _is_canonical_string(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
    )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)
