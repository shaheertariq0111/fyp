from __future__ import annotations

from functools import lru_cache
import logging
import re
from typing import Any

from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.session import FileSessionManager
from strands.types.agent import Limits

from src.agent.context import AgentRequestContext, request_context
from src.agent.continuation import (
    CONTINUATION_UNAVAILABLE,
    continuation_context_block,
    resolve_transactional_continuation,
)
from src.agent.system_prompt import RESTAURANT_AGENT_SYSTEM_PROMPT
from src.agent.tools import MVP_TOOLS
from src.infrastructure.config import get_bedrock_model_settings, get_settings


logger = logging.getLogger(__name__)

# One customer message can legitimately need a short chain of tool calls, for
# example: search_menu, start_cart_item_customization, two customization saves,
# an upsell decision, checkout, then a status read. That realistic worst case is
# roughly eight turns, so this leaves headroom while still bounding the runaway
# read loop seen in production, which reached 36 identical calls before the
# caller timed out. This is per incoming customer message, not per order.
MAX_AGENT_TURNS_PER_INVOCATION = 12


def build_bedrock_model() -> BedrockModel:
    settings = get_bedrock_model_settings()
    model_config: dict[str, Any] = {
        "model_id": settings.bedrock_model_id,
        "temperature": 0.2,
        "max_tokens": 1200,
    }
    if settings.bedrock_guardrail_id:
        model_config["guardrail_id"] = settings.bedrock_guardrail_id
    if settings.bedrock_guardrail_version:
        model_config["guardrail_version"] = settings.bedrock_guardrail_version
    return BedrockModel(region_name=settings.aws_region, **model_config)


def build_session_manager(agent_session_id: str) -> FileSessionManager | None:
    settings = get_settings()
    if not settings.strands_session_storage_dir:
        return None
    return FileSessionManager(
        session_id=agent_session_id,
        storage_dir=settings.strands_session_storage_dir,
    )


def build_restaurant_agent(
    model: BedrockModel | str | None = None,
    session_manager: Any | None = None,
) -> Agent:
    return Agent(
        model=model or build_bedrock_model(),
        tools=MVP_TOOLS,
        system_prompt=RESTAURANT_AGENT_SYSTEM_PROMPT,
        name="restaurant-ordering-agent",
        description="Dom, Domino's ordering assistant.",
        session_manager=session_manager,
        callback_handler=None,
        record_direct_tool_call=True,
    )


@lru_cache
def get_restaurant_agent() -> Agent:
    return build_restaurant_agent()


def invoke_restaurant_agent(
    message: str,
    *,
    user_id: str,
    agent_session_id: str,
    request_id: str | None = None,
    branch_id: str | None = None,
    customer_id: str | None = None,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    channel: str = "web",
    agent: Agent | None = None,
    **kwargs: Any,
):
    context = AgentRequestContext(
        user_id=user_id,
        agent_session_id=agent_session_id,
        request_id=request_id,
        branch_id=branch_id,
        customer_id=customer_id or user_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        channel=channel,
        current_message=message,
    )
    runtime_agent = agent or build_restaurant_agent(
        session_manager=build_session_manager(agent_session_id)
    )
    kwargs.setdefault("limits", Limits(turns=MAX_AGENT_TURNS_PER_INVOCATION))
    with request_context(context):
        continuation = resolve_request_continuation(
            user_id=user_id,
            agent_session_id=agent_session_id,
        )
        result = runtime_agent(
            _message_with_continuation(message, continuation),
            **kwargs,
        )
        # Attached independently so losing one never silently drops the other.
        # A missing continuation attribute would read as "nothing pending".
        try:
            setattr(result, "continuation", continuation)
        except Exception:
            logger.warning(
                "Continuation could not be attached to the agent result",
                exc_info=True,
                extra={"event": "continuation_attachment_failed"},
            )
        try:
            setattr(result, "tool_calls", list(context.tool_calls))
        except Exception:
            logger.warning(
                "Tool calls could not be attached to the agent result",
                exc_info=True,
                extra={"event": "tool_calls_attachment_failed"},
            )
        return result


def resolve_request_continuation(*, user_id: str, agent_session_id: str):
    """Resolve authoritative pending state for this turn.

    Returns ``CONTINUATION_UNAVAILABLE`` rather than ``None`` when the backend
    cannot be reached, so a failed read is never mistaken for "nothing pending".
    """
    try:
        from src.agent.dependencies import get_services

        services = get_services()
    except Exception:
        logger.warning(
            "Services unavailable while resolving transactional continuation",
            exc_info=True,
            extra={
                "event": "continuation_services_unavailable",
                "actor_id": user_id,
                "agent_session_id": agent_session_id,
            },
        )
        return CONTINUATION_UNAVAILABLE
    return resolve_transactional_continuation(
        services,
        user_id=user_id,
        agent_session_id=agent_session_id,
    )


def _message_with_continuation(message: str, continuation) -> str:
    """Prepend trusted backend state so state is read, never inferred."""
    block = continuation_context_block(continuation)
    return f"{block}\n\n{message}" if block else message


def agent_result_text(result: Any) -> str:
    message = getattr(result, "message", None)
    if isinstance(message, dict):
        parts = [
            block.get("text", "").strip()
            for block in message.get("content", [])
            if isinstance(block, dict) and "text" in block
        ]
        return sanitize_agent_text("\n".join(parts))
    return sanitize_agent_text(str(result))


def sanitize_agent_text(text: str) -> str:
    cleaned = re.sub(r"<thinking>.*?</thinking>\s*", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
