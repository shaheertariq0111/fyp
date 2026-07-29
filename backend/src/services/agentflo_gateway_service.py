from __future__ import annotations

import json
import logging
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)
OUTBOUND_ERROR_CODE = "AGENTFLO_OUTBOUND_FAILED"
DEFAULT_TIMEOUT_SECONDS = 10


class AgentfloGatewayService:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        tenant_id: str,
        agent_id: str,
        actor_id: str,
        open_request: Callable[..., Any] = urlopen,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self.actor_id = actor_id or agent_id
        self.open_request = open_request
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def send_text(
        self,
        *,
        customer_number: str,
        conversation_id: str,
        sender_id: str,
        text: str,
        request_id: str,
    ) -> dict[str, Any]:
        if not self.configured:
            return {
                "sent": False,
                "skipped": True,
                "reason": "gateway_not_configured",
            }
        if not all((
            customer_number,
            conversation_id,
            sender_id,
            text,
        )):
            return self._failure(
                stage="validation",
                request_id=request_id,
            )

        auth = self._post_json(
            path="/auth/token",
            payload={"apiKey": self.api_key},
            stage="auth",
            request_id=request_id,
        )
        if auth is None:
            return self._failure_result()
        token = auth.get("token")
        if auth.get("success") is not True or not isinstance(token, str):
            self._failure(stage="auth_response", request_id=request_id)
            return self._failure_result()
        token = token.strip()
        if not token:
            self._failure(stage="auth_response", request_id=request_id)
            return self._failure_result()

        gateway_number = (
            customer_number[1:]
            if customer_number.startswith("+")
            else customer_number
        )
        outbound = self._post_json(
            path="/whatsapp/outbound",
            payload={
                "tenantId": self.tenant_id,
                "agentId": self.agent_id,
                "userId": gateway_number,
                "conversationId": conversation_id,
                "actorId": self.actor_id,
                "actorType": "agent",
                "recipient": {
                    "type": "phone",
                    "value": gateway_number,
                },
                "sender": {
                    "phoneNumberId": sender_id,
                },
                "message": {
                    "type": "text",
                    "text": text,
                },
                "firestore": False,
                "kinesis": False,
            },
            stage="outbound",
            request_id=request_id,
            token=token,
        )
        if outbound is None:
            return self._failure_result()

        downstream = outbound.get("downstream")
        downstream = downstream if isinstance(downstream, dict) else {}
        raw_status = outbound.get("status")
        status = raw_status.strip() if isinstance(raw_status, str) else ""
        normalized_status = status.lower()
        downstream_accepted = downstream.get("accepted")
        rejected = (
            outbound.get("success") is False
            or normalized_status in {"failed", "rejected"}
            or downstream_accepted is False
        )
        accepted = (
            normalized_status == "accepted"
            or downstream_accepted is True
        )
        if rejected or not accepted:
            self._failure(stage="outbound_response", request_id=request_id)
            return self._failure_result()

        result: dict[str, Any] = {
            "sent": True,
            "status": status or "accepted",
        }
        provider_message_id = downstream.get("providerMessageId")
        if isinstance(provider_message_id, str) and provider_message_id:
            result["providerMessageId"] = provider_message_id
        return result

    def _post_json(
        self,
        *,
        path: str,
        payload: dict[str, Any],
        stage: str,
        request_id: str,
        token: str | None = None,
    ) -> dict[str, Any] | None:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self.open_request(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                status_code = getattr(response, "status", 200)
                body = response.read()
        except HTTPError as exc:
            self._failure(
                stage=stage,
                request_id=request_id,
                status_code=exc.code,
                exception_type=type(exc).__name__,
            )
            return None
        except (URLError, TimeoutError, OSError) as exc:
            self._failure(
                stage=stage,
                request_id=request_id,
                exception_type=type(exc).__name__,
            )
            return None

        if not 200 <= status_code < 300:
            self._failure(
                stage=stage,
                request_id=request_id,
                status_code=status_code,
            )
            return None
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._failure(
                stage=f"{stage}_response",
                request_id=request_id,
                status_code=status_code,
                exception_type=type(exc).__name__,
            )
            return None
        if not isinstance(decoded, dict):
            self._failure(
                stage=f"{stage}_response",
                request_id=request_id,
                status_code=status_code,
            )
            return None
        return decoded

    def _failure(
        self,
        *,
        stage: str,
        request_id: str,
        status_code: int | None = None,
        exception_type: str | None = None,
    ) -> dict[str, Any]:
        logger.warning(
            "Agentflo outbound gateway request failed",
            extra={
                "event": "agentflo_outbound_failed",
                "request_id": request_id,
                "gateway_stage": stage,
                "status_code": status_code,
                "exception_type": exception_type,
                "error_code": OUTBOUND_ERROR_CODE,
            },
        )
        return self._failure_result()

    @staticmethod
    def _failure_result() -> dict[str, Any]:
        return {
            "sent": False,
            "error_code": OUTBOUND_ERROR_CODE,
        }
