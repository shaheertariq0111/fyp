from __future__ import annotations

import logging
import time

from src.agent.order_intent import (
    OrderIntentClassification,
    OrderIntentRequest,
    classify_order_intent,
)
from src.agent.response_grounding import (
    AssistantClaimAssessment,
    GroundedAssistantMemoryBuffer,
    SEMANTIC_CLASSIFIER_TIMEOUT_SECONDS,
    assess_assistant_claims,
    ground_authoritative_tool_response,
    ground_agent_response,
    run_semantic_classifier,
    tool_evidence_payload,
)
from src.agent.restaurant_agent import (
    agent_result_text,
    build_restaurant_agent,
    build_session_manager,
    invoke_restaurant_agent,
)
from src.agent.whatsapp_turn_intent import (
    WhatsAppTurnIntentRequest,
    WhatsAppTurnInterpretation,
    classify_whatsapp_turn,
)
from src.agent_client.schemas import (
    AgentInvocationRequest,
    AgentInvocationResult,
)
from src.models.tool_responses import (
    apply_legacy_item_selection_compatibility,
)


logger = logging.getLogger(__name__)


class LocalStrandsAgentRuntimeClient:
    """Local adapter used until the Strands agent moves behind AgentCore Runtime."""

    def invoke(self, request: AgentInvocationRequest) -> AgentInvocationResult:
        started = time.perf_counter()
        logger.info(
            "Agent runtime invocation started",
            extra={
                "event": "agentcore_invocation_started",
                "actor_id": request.user_id,
                "agent_session_id": request.agent_session_id,
                "channel": request.channel,
                "agentcore_invocation_status": "started",
            },
        )
        try:
            session_manager = None
            runtime_agent = None
            memory_buffer = None
            if request.channel == "whatsapp":
                session_manager = build_session_manager(request.agent_session_id)
                if session_manager is not None:
                    memory_buffer = GroundedAssistantMemoryBuffer(session_manager)
                runtime_agent = build_restaurant_agent(
                    session_manager=memory_buffer or session_manager
                )
            invocation_kwargs = {}
            if runtime_agent is not None:
                invocation_kwargs["agent"] = runtime_agent
            raw_result = invoke_restaurant_agent(
                request.message,
                user_id=request.user_id,
                agent_session_id=request.agent_session_id,
                request_id=request.request_id,
                branch_id=request.branch_id,
                customer_id=request.customer_id,
                customer_name=request.customer_name,
                customer_phone=request.customer_phone,
                channel=request.channel,
                **invocation_kwargs,
            )
        except Exception:
            if memory_buffer is not None:
                memory_buffer.pending_assistant = None
            logger.exception(
                "Agent runtime invocation failed",
                extra={
                    "event": "agentcore_invocation_failed",
                    "actor_id": request.user_id,
                    "agent_session_id": request.agent_session_id,
                    "channel": request.channel,
                    "agentcore_invocation_status": "failed",
                    "error_code": "AGENT_INVOCATION_FAILED",
                    "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        response_text = agent_result_text(raw_result)
        if request.channel == "whatsapp":
            tool_calls = list(getattr(raw_result, "tool_calls", []) or [])
            expected_write_tool = request.expected_write_tool
            required_effect, expected_write_tool = (
                apply_legacy_item_selection_compatibility(
                    request.required_effect,
                    expected_write_tool=expected_write_tool,
                )
            )
            grounded = ground_authoritative_tool_response(
                tool_calls=tool_calls,
                expected_write_tool=expected_write_tool,
                required_effect=required_effect,
                available_options=request.available_options,
            )
            assessment = None
            no_write_authorized = False
            informational_turn = False
            authoritative_fast_path = grounded is not None
            if grounded is None:
                try:
                    assessment = run_semantic_classifier(
                        classifier_name="grounding_assessment",
                        timeout_seconds=SEMANTIC_CLASSIFIER_TIMEOUT_SECONDS,
                        operation=lambda: assess_assistant_claims(
                            customer_message=request.message,
                            assistant_message=response_text,
                            tool_evidence=tool_evidence_payload(tool_calls),
                            required_effect=required_effect,
                            available_options=request.available_options,
                        ),
                    )
                except Exception:
                    assessment = AssistantClaimAssessment(
                        claims_transactional_progression=True,
                        claimed_actions=["other_transactional_progression"],
                        customer_requests_required_effect=bool(required_effect),
                    )
                no_write_authorized = not (
                    required_effect and assessment.customer_requests_required_effect
                )
                informational_turn = assessment.informational_turn
                grounded = ground_agent_response(
                    text=response_text,
                    tool_calls=tool_calls,
                    claim_assessment=assessment,
                    no_write_authorized=no_write_authorized,
                    informational_turn=informational_turn,
                    expected_write_tool=expected_write_tool,
                    required_effect=required_effect,
                    available_options=request.available_options,
                )
            logger.info(
                "Local WhatsApp response grounded",
                extra={
                    "event": "whatsapp_grounding_completed",
                    "authoritative_fast_path": authoritative_fast_path,
                    "grounding_source": grounded.source,
                },
            )
            response_text = grounded.text
            expected_write_tool = grounded.expected_transactional_action
            required_effect = grounded.required_next_effect
            required_effect, expected_write_tool = (
                apply_legacy_item_selection_compatibility(
                    required_effect,
                    expected_write_tool=expected_write_tool,
                )
            )
            if memory_buffer is not None:
                memory_buffer.commit(response_text, runtime_agent)
            try:
                setattr(
                    raw_result,
                    "claim_assessment",
                    assessment.model_dump() if assessment is not None else None,
                )
                setattr(raw_result, "no_write_authorized", no_write_authorized)
                setattr(raw_result, "informational_turn", informational_turn)
                setattr(raw_result, "expected_write_tool", expected_write_tool)
                setattr(raw_result, "required_effect", required_effect)
                setattr(raw_result, "available_options", request.available_options or [])
                setattr(raw_result, "grounding_source", grounded.source)
            except Exception:
                raise RuntimeError("Local runtime result cannot carry grounding metadata")
        logger.info(
            "Agent runtime invocation finished",
            extra={
                "event": "agentcore_invocation_completed",
                "actor_id": request.user_id,
                "agent_session_id": request.agent_session_id,
                "channel": request.channel,
                "agentcore_invocation_status": "completed",
                "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return AgentInvocationResult(text=response_text, raw_result=raw_result)

    def classify_order_intent(
        self,
        request: OrderIntentRequest,
    ) -> OrderIntentClassification:
        return run_semantic_classifier(
            classifier_name="order_intent",
            timeout_seconds=SEMANTIC_CLASSIFIER_TIMEOUT_SECONDS,
            operation=lambda: classify_order_intent(
                message=request.message,
                state=request.state,
                allowed_actions=request.allowed_actions,
                available_options=request.available_options,
            ),
        )

    def classify_whatsapp_turn(
        self,
        request: WhatsAppTurnIntentRequest,
    ) -> WhatsAppTurnInterpretation:
        return run_semantic_classifier(
            classifier_name="whatsapp_turn",
            timeout_seconds=SEMANTIC_CLASSIFIER_TIMEOUT_SECONDS,
            operation=lambda: classify_whatsapp_turn(
                message=request.message,
                state=request.state,
                allowed_actions=request.allowed_actions,
                available_options=request.available_options,
            ),
        )

    async def start_request(self, request: AgentInvocationRequest) -> dict:
        raise NotImplementedError("Durable AgentCore async requests are implemented in a later phase")

    async def get_request_status(self, request_id: str) -> dict:
        raise NotImplementedError("Durable AgentCore request status is implemented in a later phase")
