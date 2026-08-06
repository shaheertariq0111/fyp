from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Callable

from botocore.exceptions import BotoCoreError, ClientError


class PollySynthesisError(Exception):
    def __init__(self, error_code: str, *, retryable: bool, stage: str) -> None:
        self.error_code = error_code
        self.retryable = retryable
        self.stage = stage
        super().__init__("Voice reply synthesis failed.")


class PollySpeechSynthesisService:
    def __init__(
        self,
        *,
        client,
        voice_id: str,
        engine: str,
        language_code: str,
        max_text_chars: int,
        max_audio_bytes: int,
        timeout_seconds: float,
        executor_factory: Callable[..., ThreadPoolExecutor] = ThreadPoolExecutor,
    ) -> None:
        self.client = client
        self.voice_id = voice_id
        self.engine = engine
        self.language_code = language_code.strip()
        self.max_text_chars = max_text_chars
        self.max_audio_bytes = max_audio_bytes
        self.timeout_seconds = timeout_seconds
        self.executor_factory = executor_factory

    def synthesize(self, text: str) -> bytes:
        if not isinstance(text, str) or not text.strip():
            raise PollySynthesisError(
                "VOICE_REPLY_TEXT_EMPTY",
                retryable=False,
                stage="validation",
            )
        if len(text) > self.max_text_chars:
            raise PollySynthesisError(
                "VOICE_REPLY_TEXT_TOO_LONG",
                retryable=False,
                stage="validation",
            )

        executor = self.executor_factory(max_workers=1)
        future = executor.submit(self._synthesize_and_read, text)
        try:
            return future.result(timeout=self.timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            raise PollySynthesisError(
                "VOICE_REPLY_SYNTHESIS_TIMEOUT",
                retryable=True,
                stage="synthesis",
            ) from None
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _synthesize_and_read(self, text: str) -> bytes:
        request = {
            "Text": text,
            "TextType": "text",
            "OutputFormat": "mp3",
            "VoiceId": self.voice_id,
            "Engine": self.engine,
        }
        if self.language_code:
            request["LanguageCode"] = self.language_code
        try:
            response = self.client.synthesize_speech(**request)
            stream = response.get("AudioStream") if isinstance(response, dict) else None
            if stream is None or not hasattr(stream, "read") or not hasattr(stream, "close"):
                raise PollySynthesisError(
                    "VOICE_REPLY_SYNTHESIS_INVALID_RESPONSE",
                    retryable=True,
                    stage="synthesis_response",
                )
            try:
                audio = stream.read(self.max_audio_bytes + 1)
            finally:
                stream.close()
        except PollySynthesisError:
            raise
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = exc.response.get("Error", {}).get("Code")
            retryable = bool(
                status == 429
                or (isinstance(status, int) and status >= 500)
                or code in {"Throttling", "ThrottlingException", "ServiceFailureException"}
            )
            raise PollySynthesisError(
                "VOICE_REPLY_POLLY_REQUEST_FAILED",
                retryable=retryable,
                stage="synthesis",
            ) from None
        except (BotoCoreError, OSError):
            raise PollySynthesisError(
                "VOICE_REPLY_POLLY_REQUEST_FAILED",
                retryable=True,
                stage="synthesis",
            ) from None

        if not isinstance(audio, bytes) or not audio:
            raise PollySynthesisError(
                "VOICE_REPLY_SYNTHESIS_EMPTY",
                retryable=False,
                stage="synthesis_response",
            )
        if len(audio) > self.max_audio_bytes:
            raise PollySynthesisError(
                "VOICE_REPLY_SYNTHESIS_TOO_LARGE",
                retryable=False,
                stage="synthesis_response",
            )
        return audio
