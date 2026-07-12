from __future__ import annotations

import json
import logging
import time
from typing import Any

import boto3
from botocore.config import Config

from src.agent_client.schemas import AgentInvocationRequest, AgentInvocationResult


logger = logging.getLogger(__name__)


class AgentCoreRuntimeClient:
    """Adapter for invoking the deployed Amazon Bedrock AgentCore Runtime."""

    def __init__(
        self,
        *,
        runtime_arn: str,
        aws_region: str,
        client: Any | None = None,
    ) -> None:
        self.runtime_arn = runtime_arn
        self.client = client or boto3.client(
            "bedrock-agentcore",
            region_name=aws_region,
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )

    def invoke(self, request: AgentInvocationRequest) -> AgentInvocationResult:
        started = time.perf_counter()
        logger.info(
            "AgentCore runtime invocation started",
            extra={
                "event": "agentcore_invocation_started",
                "actor_id": request.user_id,
                "agent_session_id": request.agent_session_id,
                "channel": request.channel,
                "agentcore_invocation_status": "started",
            },
        )
        try:
            response = self.client.invoke_agent_runtime(
                agentRuntimeArn=self.runtime_arn,
                runtimeSessionId=request.agent_session_id,
                runtimeUserId=request.user_id,
                contentType="application/json",
                accept="application/json",
                payload=json.dumps(self._payload(request)).encode("utf-8"),
            )
            status_code = int(response.get("statusCode") or 200)
            result = self._read_response(response)
            if status_code >= 400:
                raise RuntimeError(f"AgentCore Runtime returned HTTP {status_code}")
        except Exception as exc:
            logger.exception(
                "AgentCore runtime invocation failed",
                extra={
                    "event": "agentcore_invocation_failed",
                    "actor_id": request.user_id,
                    "agent_session_id": request.agent_session_id,
                    "channel": request.channel,
                    "agentcore_invocation_status": "failed",
                    "error_code": "AGENT_INVOCATION_FAILED",
                    "exception_message": str(exc),
                    "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        logger.info(
            "AgentCore runtime invocation finished",
            extra={
                "event": "agentcore_invocation_completed",
                "actor_id": request.user_id,
                "agent_session_id": request.agent_session_id,
                "channel": request.channel,
                "agentcore_invocation_status": "completed",
                "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return AgentInvocationResult(
            text=str(result.get("text") or ""),
            raw_result=result,
        )

    async def start_request(self, request: AgentInvocationRequest) -> dict:
        raise NotImplementedError("Durable AgentCore async requests are handled by request service")

    async def get_request_status(self, request_id: str) -> dict:
        raise NotImplementedError("Durable AgentCore request status is handled by request service")

    @staticmethod
    def _payload(request: AgentInvocationRequest) -> dict[str, Any]:
        return {
            "message": request.message,
            "user_id": request.user_id,
            "agent_session_id": request.agent_session_id,
            "branch_id": request.branch_id,
            "customer_id": request.customer_id,
            "customer_name": request.customer_name,
            "customer_phone": request.customer_phone,
            "channel": request.channel,
        }

    @staticmethod
    def _read_response(response: dict[str, Any]) -> dict[str, Any]:
        stream = response.get("response")
        raw_body = stream.read() if hasattr(stream, "read") else stream
        if isinstance(raw_body, bytes):
            raw_body = raw_body.decode("utf-8")
        if not raw_body:
            return {}
        result = json.loads(raw_body)
        if not isinstance(result, dict):
            raise ValueError("AgentCore Runtime returned a non-object JSON response")
        return result
