from __future__ import annotations

import base64
import json
import logging
import tempfile
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from src.models.whatsapp_voice_job import validate_voice_audio_id
from src.services.agentflo_media_service import (
    DownloadedVoiceMedia,
    VoiceMediaError,
    detect_voice_media_format,
)


logger = logging.getLogger(__name__)
OUTBOUND_ERROR_CODE = "AGENTFLO_OUTBOUND_FAILED"
AUDIO_OUTBOUND_ERROR_CODE = "AGENTFLO_AUDIO_OUTBOUND_FAILED"
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_MEDIA_BYTES = 10 * 1024 * 1024

MEDIA_ERROR_MESSAGES = {
    "AGENTFLO_MEDIA_AUDIO_ID_REQUIRED": "The Agentflo media identifier is invalid.",
    "AGENTFLO_MEDIA_NOT_CONFIGURED": "Agentflo media access is not configured.",
    "AGENTFLO_MEDIA_AUTH_FAILED": "Agentflo media authentication failed.",
    "AGENTFLO_MEDIA_NOT_FOUND": "The Agentflo media object was not found.",
    "AGENTFLO_MEDIA_REQUEST_REJECTED": "The Agentflo media request was rejected.",
    "AGENTFLO_MEDIA_DOWNLOAD_FAILED": "The Agentflo media download failed.",
    "AGENTFLO_MEDIA_REDIRECT_REJECTED": "The Agentflo media response redirected unexpectedly.",
    "AGENTFLO_MEDIA_TOO_LARGE": "The Agentflo media object exceeds the size limit.",
    "AGENTFLO_MEDIA_EMPTY": "The Agentflo media object is empty.",
    "AGENTFLO_MEDIA_FORMAT_UNSUPPORTED": "The Agentflo media format is unsupported.",
}


class AgentfloGatewayRequestError(Exception):
    def __init__(
        self,
        *,
        stage: str,
        status_code: int | None = None,
        exception_type: str | None = None,
    ) -> None:
        self.stage = stage
        self.status_code = status_code
        self.exception_type = exception_type
        super().__init__("The Agentflo gateway request failed.")


class AgentfloMediaDownloadError(Exception):
    def __init__(
        self,
        error_code: str,
        *,
        retryable: bool,
        stage: str,
        status_code: int | None = None,
    ) -> None:
        self.error_code = error_code
        self.retryable = retryable
        self.stage = stage
        self.status_code = status_code
        super().__init__(MEDIA_ERROR_MESSAGES[error_code])


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_without_redirects(request: Request, *, timeout: float):
    return build_opener(_RejectRedirects).open(request, timeout=timeout)


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
        open_media_request: Callable[..., Any] = _open_without_redirects,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_media_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
        max_audio_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
        audio_firestore: bool = True,
        audio_kinesis: bool = True,
    ):
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self.actor_id = actor_id or agent_id
        self.open_request = open_request
        self.open_media_request = open_media_request
        self.timeout_seconds = timeout_seconds
        self.max_media_bytes = max_media_bytes
        self.max_audio_bytes = max_audio_bytes
        self.audio_firestore = audio_firestore
        self.audio_kinesis = audio_kinesis
        if (
            self.timeout_seconds <= 0
            or self.max_media_bytes <= 0
            or self.max_audio_bytes <= 0
        ):
            raise ValueError("Agentflo gateway limits must be positive")

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

        try:
            token = self._authenticate()
        except AgentfloGatewayRequestError as exc:
            self._failure(
                stage=exc.stage,
                request_id=request_id,
                status_code=exc.status_code,
                exception_type=exc.exception_type,
            )
            return self._failure_result()

        gateway_number = (
            customer_number[1:]
            if customer_number.startswith("+")
            else customer_number
        )
        return self._send_outbound(
            token=token,
            request_id=request_id,
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
            error_code=OUTBOUND_ERROR_CODE,
        )

    def send_audio(
        self,
        *,
        customer_number: str,
        conversation_id: str,
        sender_id: str,
        audio: bytes,
        request_id: str,
    ) -> dict[str, Any]:
        if not self.configured:
            return {
                "sent": False,
                "skipped": True,
                "reason": "gateway_not_configured",
            }
        if (
            not customer_number
            or not conversation_id
            or not sender_id
            or not isinstance(audio, bytes)
            or not audio
            or len(audio) > self.max_audio_bytes
        ):
            self._failure(
                stage="audio_validation",
                request_id=request_id,
                error_code=AUDIO_OUTBOUND_ERROR_CODE,
            )
            return self._failure_result(AUDIO_OUTBOUND_ERROR_CODE)
        encoded = base64.b64encode(audio).decode("ascii")
        max_encoded_bytes = 4 * ((self.max_audio_bytes + 2) // 3)
        if len(encoded.encode("ascii")) > max_encoded_bytes:
            self._failure(
                stage="audio_encoding",
                request_id=request_id,
                error_code=AUDIO_OUTBOUND_ERROR_CODE,
            )
            return self._failure_result(AUDIO_OUTBOUND_ERROR_CODE)
        try:
            token = self._authenticate()
        except AgentfloGatewayRequestError as exc:
            self._failure(
                stage=exc.stage,
                request_id=request_id,
                status_code=exc.status_code,
                exception_type=exc.exception_type,
                error_code=AUDIO_OUTBOUND_ERROR_CODE,
            )
            return self._failure_result(AUDIO_OUTBOUND_ERROR_CODE)

        gateway_number = (
            customer_number[1:]
            if customer_number.startswith("+")
            else customer_number
        )
        return self._send_outbound(
            token=token,
            request_id=request_id,
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
                "source": "agent",
                "firestore": self.audio_firestore,
                "kinesis": self.audio_kinesis,
                "message": {
                    "type": "audio",
                    "base64": encoded,
                },
                "log": {},
            },
            error_code=AUDIO_OUTBOUND_ERROR_CODE,
        )

    def _send_outbound(
        self,
        *,
        token: str,
        request_id: str,
        payload: dict[str, Any],
        error_code: str,
    ) -> dict[str, Any]:
        outbound = self._post_json(
            path="/whatsapp/outbound",
            payload=payload,
            stage="outbound",
            request_id=request_id,
            token=token,
            error_code=error_code,
        )
        if outbound is None:
            return self._failure_result(error_code)

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
            self._failure(
                stage="outbound_response",
                request_id=request_id,
                error_code=error_code,
            )
            return self._failure_result(error_code)

        result: dict[str, Any] = {
            "sent": True,
            "status": status or "accepted",
        }
        provider_message_id = downstream.get("providerMessageId")
        if isinstance(provider_message_id, str) and provider_message_id:
            result["providerMessageId"] = provider_message_id
        return result

    def download_media(
        self,
        audio_id: str,
        *,
        request_id: str,
    ) -> DownloadedVoiceMedia:
        try:
            return self._download_media(audio_id)
        except AgentfloMediaDownloadError as exc:
            logger.warning(
                "Agentflo media request failed",
                extra={
                    "event": "agentflo_media_download_failed",
                    "request_id": request_id,
                    "gateway_stage": exc.stage,
                    "status_code": exc.status_code,
                    "error_code": exc.error_code,
                    "retryable": exc.retryable,
                },
            )
            raise

    def _download_media(self, audio_id: str) -> DownloadedVoiceMedia:
        if not self.configured:
            raise AgentfloMediaDownloadError(
                "AGENTFLO_MEDIA_NOT_CONFIGURED",
                retryable=False,
                stage="configuration",
            )
        try:
            normalized_audio_id = validate_voice_audio_id(audio_id)
        except ValueError:
            raise AgentfloMediaDownloadError(
                "AGENTFLO_MEDIA_AUDIO_ID_REQUIRED",
                retryable=False,
                stage="validation",
            ) from None

        try:
            token = self._authenticate()
        except AgentfloGatewayRequestError as exc:
            raise self._media_request_error(exc, auth=True) from None

        request = Request(
            f"{self.base_url}/whatsapp/media/{quote(normalized_audio_id, safe='')}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "audio/*,application/octet-stream",
            },
            method="GET",
        )
        try:
            with self.open_media_request(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                status_code = getattr(response, "status", 200)
                if 300 <= status_code < 400:
                    raise AgentfloMediaDownloadError(
                        "AGENTFLO_MEDIA_REDIRECT_REJECTED",
                        retryable=False,
                        stage="media",
                        status_code=status_code,
                    )
                if not 200 <= status_code < 300:
                    raise self._media_status_error(status_code)

                raw_content_type = response.headers.get("Content-Type", "")
                content_type = raw_content_type.partition(";")[0].strip().lower()
                if not (
                    content_type.startswith("audio/")
                    or content_type == "application/octet-stream"
                ):
                    raise AgentfloMediaDownloadError(
                        "AGENTFLO_MEDIA_FORMAT_UNSUPPORTED",
                        retryable=False,
                        stage="media_response",
                        status_code=status_code,
                    )

                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError:
                        raise AgentfloMediaDownloadError(
                            "AGENTFLO_MEDIA_DOWNLOAD_FAILED",
                            retryable=True,
                            stage="media_response",
                            status_code=status_code,
                        ) from None
                    if declared_size < 0:
                        raise AgentfloMediaDownloadError(
                            "AGENTFLO_MEDIA_DOWNLOAD_FAILED",
                            retryable=True,
                            stage="media_response",
                            status_code=status_code,
                        )
                    if declared_size > self.max_media_bytes:
                        raise AgentfloMediaDownloadError(
                            "AGENTFLO_MEDIA_TOO_LARGE",
                            retryable=False,
                            stage="media_response",
                            status_code=status_code,
                        )

                body = response.read(self.max_media_bytes + 1)
        except AgentfloMediaDownloadError:
            raise
        except HTTPError as exc:
            if 300 <= exc.code < 400:
                raise AgentfloMediaDownloadError(
                    "AGENTFLO_MEDIA_REDIRECT_REJECTED",
                    retryable=False,
                    stage="media",
                    status_code=exc.code,
                ) from None
            raise self._media_status_error(exc.code) from None
        except (URLError, TimeoutError, OSError):
            raise AgentfloMediaDownloadError(
                "AGENTFLO_MEDIA_DOWNLOAD_FAILED",
                retryable=True,
                stage="media",
            ) from None

        if not body:
            raise AgentfloMediaDownloadError(
                "AGENTFLO_MEDIA_EMPTY",
                retryable=False,
                stage="media_response",
                status_code=status_code,
            )
        if len(body) > self.max_media_bytes:
            raise AgentfloMediaDownloadError(
                "AGENTFLO_MEDIA_TOO_LARGE",
                retryable=False,
                stage="media_response",
                status_code=status_code,
            )

        media_file = tempfile.SpooledTemporaryFile(
            max_size=min(self.max_media_bytes, 1024 * 1024),
            mode="w+b",
        )
        try:
            media_file.write(body)
            media_file.seek(0)
            try:
                media_format, suffix, normalized_content_type = (
                    detect_voice_media_format(media_file)
                )
            except VoiceMediaError:
                raise AgentfloMediaDownloadError(
                    "AGENTFLO_MEDIA_FORMAT_UNSUPPORTED",
                    retryable=False,
                    stage="media_response",
                    status_code=status_code,
                ) from None
            media_file.seek(0)
            return DownloadedVoiceMedia(
                file=media_file,
                media_format=media_format,
                suffix=suffix,
                content_type=normalized_content_type,
                size_bytes=len(body),
            )
        except Exception:
            media_file.close()
            raise

    def _authenticate(self) -> str:
        auth = self._request_json(
            path="/auth/token",
            payload={"apiKey": self.api_key},
            stage="auth",
        )
        token = auth.get("token")
        if auth.get("success") is not True or not isinstance(token, str):
            raise AgentfloGatewayRequestError(stage="auth_response")
        token = token.strip()
        if not token:
            raise AgentfloGatewayRequestError(stage="auth_response")
        return token

    @staticmethod
    def _media_request_error(
        exc: AgentfloGatewayRequestError,
        *,
        auth: bool,
    ) -> AgentfloMediaDownloadError:
        status_code = exc.status_code
        if status_code == 429 or (status_code is not None and status_code >= 500):
            return AgentfloMediaDownloadError(
                "AGENTFLO_MEDIA_DOWNLOAD_FAILED",
                retryable=True,
                stage=exc.stage,
                status_code=status_code,
            )
        if status_code is None and exc.stage == "auth":
            return AgentfloMediaDownloadError(
                "AGENTFLO_MEDIA_DOWNLOAD_FAILED",
                retryable=True,
                stage=exc.stage,
            )
        return AgentfloMediaDownloadError(
            "AGENTFLO_MEDIA_AUTH_FAILED" if auth else "AGENTFLO_MEDIA_REQUEST_REJECTED",
            retryable=False,
            stage=exc.stage,
            status_code=status_code,
        )

    @staticmethod
    def _media_status_error(status_code: int) -> AgentfloMediaDownloadError:
        if status_code in {401, 403}:
            code, retryable = "AGENTFLO_MEDIA_AUTH_FAILED", False
        elif status_code == 404:
            code, retryable = "AGENTFLO_MEDIA_NOT_FOUND", False
        elif status_code == 429 or status_code >= 500:
            code, retryable = "AGENTFLO_MEDIA_DOWNLOAD_FAILED", True
        else:
            code, retryable = "AGENTFLO_MEDIA_REQUEST_REJECTED", False
        return AgentfloMediaDownloadError(
            code,
            retryable=retryable,
            stage="media",
            status_code=status_code,
        )

    def _post_json(
        self,
        *,
        path: str,
        payload: dict[str, Any],
        stage: str,
        request_id: str,
        token: str | None = None,
        error_code: str = OUTBOUND_ERROR_CODE,
    ) -> dict[str, Any] | None:
        try:
            return self._request_json(
                path=path,
                payload=payload,
                stage=stage,
                token=token,
            )
        except AgentfloGatewayRequestError as exc:
            self._failure(
                stage=exc.stage,
                request_id=request_id,
                status_code=exc.status_code,
                exception_type=exc.exception_type,
                error_code=error_code,
            )
            return None

    def _request_json(
        self,
        *,
        path: str,
        payload: dict[str, Any],
        stage: str,
        token: str | None = None,
    ) -> dict[str, Any]:
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
            raise AgentfloGatewayRequestError(
                stage=stage,
                status_code=exc.code,
                exception_type=type(exc).__name__,
            ) from None
        except (URLError, TimeoutError, OSError) as exc:
            raise AgentfloGatewayRequestError(
                stage=stage,
                exception_type=type(exc).__name__,
            ) from None

        if not 200 <= status_code < 300:
            raise AgentfloGatewayRequestError(
                stage=stage,
                status_code=status_code,
            )
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentfloGatewayRequestError(
                stage=f"{stage}_response",
                status_code=status_code,
                exception_type=type(exc).__name__,
            ) from None
        if not isinstance(decoded, dict):
            raise AgentfloGatewayRequestError(
                stage=f"{stage}_response",
                status_code=status_code,
            )
        return decoded

    def _failure(
        self,
        *,
        stage: str,
        request_id: str,
        status_code: int | None = None,
        exception_type: str | None = None,
        error_code: str = OUTBOUND_ERROR_CODE,
    ) -> dict[str, Any]:
        logger.warning(
            "Agentflo outbound gateway request failed",
            extra={
                "event": "agentflo_outbound_failed",
                "request_id": request_id,
                "gateway_stage": stage,
                "status_code": status_code,
                "exception_type": exception_type,
                "error_code": error_code,
            },
        )
        return self._failure_result(error_code)

    @staticmethod
    def _failure_result(error_code: str = OUTBOUND_ERROR_CODE) -> dict[str, Any]:
        return {
            "sent": False,
            "error_code": error_code,
        }
