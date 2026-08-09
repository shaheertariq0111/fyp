from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from src.agent.order_intent import classify_order_intent
from src.agent.response_grounding import (
    AssistantClaimAssessment,
    GroundedAssistantMemoryBuffer,
    SEMANTIC_CLASSIFIER_TIMEOUT_SECONDS,
    assess_assistant_claims,
    ground_authoritative_tool_response,
    ground_agent_response,
    tool_evidence_payload,
    run_semantic_classifier,
)
from src.agent.whatsapp_turn_intent import classify_whatsapp_turn
from src.agent.restaurant_agent import agent_result_text, build_restaurant_agent, invoke_restaurant_agent
from src.agent.dependencies import get_services
from src.infrastructure.config import get_settings
from src.models.tool_responses import (
    apply_legacy_item_selection_compatibility,
)

from agent_runtime.config import get_agentcore_runtime_settings, get_secret_value
from agent_runtime.logging import configure_logging
from agent_runtime.memory import agentcore_actor_id, require_agentcore_memory_id
from agent_runtime.schemas import RuntimeRequest, RuntimeResponse, ToolCallResult


logger = logging.getLogger(__name__)


def load_agentcore_memory_integration() -> tuple[type[Any], type[Any]]:
    from bedrock_agentcore.memory.integrations.strands.config import (
        AgentCoreMemoryConfig,
    )
    from bedrock_agentcore.memory.integrations.strands.session_manager import (
        AgentCoreMemorySessionManager,
    )

    return AgentCoreMemoryConfig, AgentCoreMemorySessionManager


def ensure_session_token_secret(settings: Any) -> None:
    if os.getenv("SESSION_TOKEN_SECRET") or not settings.session_token_secret_arn:
        return
    os.environ["SESSION_TOKEN_SECRET"] = get_secret_value(
        settings.session_token_secret_arn,
        settings.aws_region,
    )
    get_settings.cache_clear()
    get_services.cache_clear()


def agentcore_memory_session_id(request: RuntimeRequest, settings: Any) -> str:
    if request.channel != "whatsapp":
        return request.agent_session_id
    namespace = str(
        getattr(settings, "whatsapp_agentcore_memory_namespace", "")
        or "whatsapp-agent-v2"
    ).strip()
    ttl_hours = int(getattr(settings, "whatsapp_agentcore_memory_ttl_hours", 6) or 6)
    ttl_seconds = max(ttl_hours, 1) * 60 * 60
    bucket = int(time.time() // ttl_seconds)
    base_session_id = (
        f"{namespace}-{request.agent_session_id}"
        if namespace
        else request.agent_session_id
    )
    return f"{base_session_id}-b{bucket}"


def invoke(event: dict[str, Any], context: Any | None = None) -> dict[str, Any]:
    request = RuntimeRequest.model_validate(event)
    settings = get_agentcore_runtime_settings()
    configure_logging(settings.log_level)
    if request.task == "classify_whatsapp_turn":
        if not request.state or not request.allowed_actions:
            raise ValueError(
                "WhatsApp turn classification requires state and allowed_actions"
            )
        turn_intent = run_semantic_classifier(
            classifier_name="whatsapp_turn",
            timeout_seconds=SEMANTIC_CLASSIFIER_TIMEOUT_SECONDS,
            operation=lambda: classify_whatsapp_turn(
                message=request.message,
                state=request.state,
                allowed_actions=request.allowed_actions,
                available_options=request.available_options,
            ),
        )
        return RuntimeResponse(
            text="",
            turn_intent=turn_intent,
        ).model_dump(exclude_none=True)
    if request.task == "classify_order_intent":
        if not request.state or not request.allowed_actions:
            raise ValueError(
                "Intent classification requires state and allowed_actions"
            )
        started = time.perf_counter()
        logger.info(
            "Classifying WhatsApp order intent",
            extra={
                "event": "order_intent_classification_started",
                "actor_id": request.user_id,
                "agent_session_id": request.agent_session_id,
                "channel": request.channel,
                "order_state": request.state,
            },
        )
        intent = run_semantic_classifier(
            classifier_name="order_intent",
            timeout_seconds=SEMANTIC_CLASSIFIER_TIMEOUT_SECONDS,
            operation=lambda: classify_order_intent(
                message=request.message,
                state=request.state,
                allowed_actions=request.allowed_actions,
                available_options=request.available_options,
            ),
        )
        logger.info(
            "WhatsApp order intent classified",
            extra={
                "event": "order_intent_classification_completed",
                "actor_id": request.user_id,
                "agent_session_id": request.agent_session_id,
                "channel": request.channel,
                "order_state": request.state,
                "interpreted_action": intent.action,
                "intent_confidence": intent.confidence,
                "response_time_ms": round(
                    (time.perf_counter() - started) * 1000,
                    2,
                ),
            },
        )
        return RuntimeResponse(
            text="",
            intent=intent,
        ).model_dump(exclude_none=True)
    ensure_session_token_secret(settings)
    memory_id = require_agentcore_memory_id(settings)
    actor_id = agentcore_actor_id(customer_id=request.customer_id, user_id=request.user_id)
    memory_session_id = agentcore_memory_session_id(request, settings)
    memory_config_cls, session_manager_cls = load_agentcore_memory_integration()
    memory_config = memory_config_cls(
        memory_id=memory_id,
        actor_id=actor_id,
        session_id=memory_session_id,
        batch_size=1,
    )
    logger.info(
        "Invoking restaurant agent",
        extra={
            "event": "agentcore_invocation_started",
            "actor_id": actor_id,
            "agent_session_id": request.agent_session_id,
            "memory_session_id": memory_session_id,
            "channel": request.channel,
            "agentcore_invocation_status": "started",
        },
    )
    started = time.perf_counter()
    try:
        with session_manager_cls(
            agentcore_memory_config=memory_config,
            region_name=settings.aws_region,
        ) as session_manager:
            memory_buffer = (
                GroundedAssistantMemoryBuffer(session_manager)
                if request.channel == "whatsapp"
                else None
            )
            try:
                runtime_agent = build_restaurant_agent(
                    session_manager=memory_buffer or session_manager
                )
                result = invoke_restaurant_agent(
                    request.message,
                    user_id=request.user_id,
                    agent_session_id=request.agent_session_id,
                    request_id=request.request_id,
                    branch_id=request.branch_id,
                    customer_id=request.customer_id,
                    customer_name=request.customer_name,
                    customer_phone=request.customer_phone,
                    channel=request.channel,
                    agent=runtime_agent,
                )
                tool_calls = [
                    ToolCallResult.model_validate(call)
                    for call in (getattr(result, "tool_calls", []) or [])
                ]
                raw_text = agent_result_text(result)
                claim_assessment = None
                grounded_text = raw_text
                no_write_authorized = False
                informational_turn = False
                expected_write_tool = request.expected_write_tool
                required_effect, expected_write_tool = (
                    apply_legacy_item_selection_compatibility(
                        request.required_effect,
                        expected_write_tool=expected_write_tool,
                    )
                )
                grounding_source = None
                if request.channel == "whatsapp":
                    grounded = ground_authoritative_tool_response(
                        tool_calls=tool_calls,
                        expected_write_tool=expected_write_tool,
                        required_effect=required_effect,
                        available_options=request.available_options,
                    )
                    authoritative_fast_path = grounded is not None
                    if grounded is None:
                        try:
                            claim_assessment = run_semantic_classifier(
                                classifier_name="grounding_assessment",
                                timeout_seconds=SEMANTIC_CLASSIFIER_TIMEOUT_SECONDS,
                                operation=lambda: assess_assistant_claims(
                                    customer_message=request.message,
                                    assistant_message=raw_text,
                                    tool_evidence=tool_evidence_payload(tool_calls),
                                    required_effect=required_effect,
                                    available_options=request.available_options,
                                ),
                            )
                        except Exception:
                            claim_assessment = AssistantClaimAssessment(
                                claims_transactional_progression=True,
                                claimed_actions=["other_transactional_progression"],
                                customer_requests_required_effect=bool(required_effect),
                            )
                        no_write_authorized = not (
                            required_effect
                            and claim_assessment.customer_requests_required_effect
                        )
                        informational_turn = claim_assessment.informational_turn
                        grounded = ground_agent_response(
                            text=raw_text,
                            tool_calls=tool_calls,
                            claim_assessment=claim_assessment,
                            no_write_authorized=no_write_authorized,
                            informational_turn=informational_turn,
                            expected_write_tool=expected_write_tool,
                            required_effect=required_effect,
                            available_options=request.available_options,
                        )
                    logger.info(
                        "WhatsApp response grounded",
                        extra={
                            "event": "whatsapp_grounding_completed",
                            "authoritative_fast_path": authoritative_fast_path,
                            "grounding_source": grounded.source,
                        },
                    )
                    grounded_text = grounded.text
                    grounding_source = grounded.source
                    expected_write_tool = grounded.expected_transactional_action
                    required_effect = grounded.required_next_effect
                    required_effect, expected_write_tool = (
                        apply_legacy_item_selection_compatibility(
                            required_effect,
                            expected_write_tool=expected_write_tool,
                        )
                    )
                if memory_buffer is not None:
                    memory_buffer.commit(grounded_text, runtime_agent)
            finally:
                if memory_buffer is not None:
                    memory_buffer.pending_assistant = None
    except Exception:
        logger.exception(
            "Restaurant agent invocation failed",
            extra={
                "event": "agentcore_invocation_failed",
                "actor_id": actor_id,
                "agent_session_id": request.agent_session_id,
                "memory_session_id": memory_session_id,
                "channel": request.channel,
                "agentcore_invocation_status": "failed",
                "error_code": "AGENT_INVOCATION_FAILED",
                "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        raise
    for call in tool_calls:
        logger.info(
            "AgentCore tool call completed",
            extra={
                "event": "agent_tool_completed",
                "actor_id": actor_id,
                "agent_session_id": request.agent_session_id,
                "memory_session_id": memory_session_id,
                "channel": request.channel,
                "tool_name": call.tool_name,
                "tool_success": call.success,
                "is_write": call.is_write,
                "error_code": call.error_code,
            },
        )
    logger.info(
        "Restaurant agent invocation completed",
        extra={
            "event": "agentcore_invocation_completed",
            "actor_id": actor_id,
            "agent_session_id": request.agent_session_id,
            "memory_session_id": memory_session_id,
            "channel": request.channel,
            "agentcore_invocation_status": "completed",
            "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
    response = RuntimeResponse(
        text=grounded_text,
        tool_calls=tool_calls,
        memory={
            "memory_id": memory_id,
            "actor_id": actor_id,
            "session_id": memory_session_id,
        },
        claim_assessment=claim_assessment,
        no_write_authorized=no_write_authorized,
        informational_turn=informational_turn,
        expected_write_tool=expected_write_tool,
        required_effect=required_effect if request.channel == "whatsapp" else None,
        available_options=(
            request.available_options
            if request.channel == "whatsapp" and request.available_options
            else None
        ),
        grounding_source=grounding_source,
    )
    return response.model_dump(exclude_none=True)


if __name__ == "__main__":
    import sys

    payload = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(invoke(payload), separators=(",", ":")))
