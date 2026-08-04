from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Callable
from urllib.parse import urlsplit

import httpx
from botocore.exceptions import ClientError


logger = logging.getLogger(__name__)

TRANSCRIPT_MAX_BYTES = 1_048_576
TRANSCRIPTION_MAX_TIMEOUT_SECONDS = 180
TRANSCRIBE_JOB_NAME = re.compile(r"^[0-9A-Za-z._-]{1,200}$")
TRANSCRIPTION_ERROR_MESSAGES = {
    "VOICE_TRANSCRIPTION_CONFIGURATION_INVALID": (
        "Voice transcription configuration is invalid."
    ),
    "VOICE_TRANSCRIPTION_START_FAILED": "Voice transcription could not be started.",
    "VOICE_TRANSCRIPTION_CONFLICT": "The voice transcription job conflicts with existing work.",
    "VOICE_TRANSCRIPTION_FAILED": "Voice transcription failed.",
    "VOICE_TRANSCRIPTION_TIMEOUT": "Voice transcription timed out.",
    "VOICE_TRANSCRIPT_DOWNLOAD_FAILED": "The voice transcript could not be downloaded.",
    "VOICE_TRANSCRIPT_INVALID": "The voice transcript is invalid.",
    "VOICE_TRANSCRIPTION_CLEANUP_FAILED": "Voice transcription cleanup failed.",
}


class VoiceTranscriptionError(Exception):
    def __init__(self, error_code: str):
        self.error_code = error_code
        super().__init__(TRANSCRIPTION_ERROR_MESSAGES[error_code])


class TranscriptionService:
    def __init__(
        self,
        *,
        client,
        aws_region: str,
        transcript_client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval_seconds: float = 1.0,
        transcript_max_bytes: int = TRANSCRIPT_MAX_BYTES,
    ) -> None:
        self.client = client
        self.aws_region = aws_region.strip().lower()
        self.transcript_client = transcript_client
        self.clock = clock
        self.sleep = sleep
        self.poll_interval_seconds = poll_interval_seconds
        self.transcript_max_bytes = transcript_max_bytes

    @staticmethod
    def build_job_name(message_id: str, job_name_prefix: str) -> str:
        normalized_message_id = message_id.strip()
        normalized_prefix = job_name_prefix.strip()
        if not normalized_message_id or not TRANSCRIBE_JOB_NAME.fullmatch(
            normalized_prefix
        ):
            raise VoiceTranscriptionError(
                "VOICE_TRANSCRIPTION_CONFIGURATION_INVALID"
            )
        digest = hashlib.sha256(normalized_message_id.encode("utf-8")).hexdigest()
        job_name = f"{normalized_prefix}-whatsapp-voice-{digest}"
        if not TRANSCRIBE_JOB_NAME.fullmatch(job_name):
            raise VoiceTranscriptionError(
                "VOICE_TRANSCRIPTION_CONFIGURATION_INVALID"
            )
        return job_name

    def transcribe(
        self,
        *,
        media_s3_uri: str,
        media_format: str,
        job_name: str,
        language_code: str = "",
        identify_language: bool = False,
        timeout_seconds: float,
    ) -> str:
        normalized_language_code = language_code.strip()
        if (
            not self._valid_s3_uri(media_s3_uri)
            or media_format != "ogg"
            or not TRANSCRIBE_JOB_NAME.fullmatch(job_name)
            or not 0 < timeout_seconds <= TRANSCRIPTION_MAX_TIMEOUT_SECONDS
            or self.transcript_max_bytes <= 0
            or bool(normalized_language_code) == identify_language
        ):
            raise VoiceTranscriptionError(
                "VOICE_TRANSCRIPTION_CONFIGURATION_INVALID"
            )

        self._start_or_recover(
            media_s3_uri=media_s3_uri,
            media_format=media_format,
            job_name=job_name,
            language_code=normalized_language_code,
            identify_language=identify_language,
        )
        terminal = False
        primary_error: Exception | None = None
        try:
            job = self._wait_for_terminal_job(job_name, timeout_seconds)
            terminal = True
            if job.get("TranscriptionJobStatus") != "COMPLETED":
                raise VoiceTranscriptionError("VOICE_TRANSCRIPTION_FAILED")
            transcript = job.get("Transcript")
            transcript_uri = (
                transcript.get("TranscriptFileUri")
                if isinstance(transcript, dict)
                else None
            )
            if not isinstance(transcript_uri, str) or not transcript_uri.strip():
                raise VoiceTranscriptionError("VOICE_TRANSCRIPT_INVALID")
            return self._download_transcript(transcript_uri)
        except Exception as exc:
            primary_error = exc
            raise
        finally:
            if terminal:
                try:
                    self._delete_job(job_name)
                except VoiceTranscriptionError:
                    if primary_error is None:
                        raise
                    logger.warning(
                        "Voice transcription cleanup failed",
                        extra={
                            "event": "voice_transcription_cleanup_failed",
                            "error_code": "VOICE_TRANSCRIPTION_CLEANUP_FAILED",
                        },
                    )

    @staticmethod
    def _valid_s3_uri(media_s3_uri: str) -> bool:
        try:
            parsed = urlsplit(media_s3_uri)
        except (TypeError, ValueError):
            return False
        return bool(
            parsed.scheme == "s3"
            and parsed.netloc
            and parsed.path.strip("/")
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )

    def _start_or_recover(
        self,
        *,
        media_s3_uri: str,
        media_format: str,
        job_name: str,
        language_code: str,
        identify_language: bool,
    ) -> None:
        parameters = {
            "TranscriptionJobName": job_name,
            "Media": {"MediaFileUri": media_s3_uri},
            "MediaFormat": media_format,
        }
        if identify_language:
            parameters["IdentifyLanguage"] = True
        else:
            parameters["LanguageCode"] = language_code
        try:
            self.client.start_transcription_job(**parameters)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code != "ConflictException":
                raise VoiceTranscriptionError(
                    "VOICE_TRANSCRIPTION_START_FAILED"
                ) from None
            existing_job = self._get_job(job_name, conflict=True)
            existing_media = existing_job.get("Media")
            existing_uri = (
                existing_media.get("MediaFileUri")
                if isinstance(existing_media, dict)
                else None
            )
            if existing_uri != media_s3_uri:
                raise VoiceTranscriptionError("VOICE_TRANSCRIPTION_CONFLICT")
        except Exception:
            raise VoiceTranscriptionError("VOICE_TRANSCRIPTION_START_FAILED") from None

    def _wait_for_terminal_job(
        self,
        job_name: str,
        timeout_seconds: float,
    ) -> dict:
        started = self.clock()
        while True:
            job = self._get_job(job_name)
            status = job.get("TranscriptionJobStatus")
            if status in {"COMPLETED", "FAILED"}:
                return job
            if status not in {"QUEUED", "IN_PROGRESS"}:
                raise VoiceTranscriptionError("VOICE_TRANSCRIPTION_FAILED")
            if self.clock() - started >= timeout_seconds:
                raise VoiceTranscriptionError("VOICE_TRANSCRIPTION_TIMEOUT")
            self.sleep(self.poll_interval_seconds)

    def _get_job(self, job_name: str, *, conflict: bool = False) -> dict:
        try:
            response = self.client.get_transcription_job(
                TranscriptionJobName=job_name
            )
            job = response.get("TranscriptionJob")
            if not isinstance(job, dict):
                raise TypeError
            return job
        except VoiceTranscriptionError:
            raise
        except Exception:
            error_code = (
                "VOICE_TRANSCRIPTION_CONFLICT"
                if conflict
                else "VOICE_TRANSCRIPTION_FAILED"
            )
            raise VoiceTranscriptionError(error_code) from None

    def _download_transcript(self, transcript_uri: str) -> str:
        self._validate_transcript_uri(transcript_uri)
        try:
            if self.transcript_client is None:
                with httpx.Client() as client:
                    payload = self._read_transcript_response(client, transcript_uri)
            else:
                payload = self._read_transcript_response(
                    self.transcript_client,
                    transcript_uri,
                )
        except VoiceTranscriptionError:
            raise
        except (httpx.RequestError, httpx.TimeoutException, OSError, ValueError):
            raise VoiceTranscriptionError("VOICE_TRANSCRIPT_DOWNLOAD_FAILED") from None
        try:
            document = json.loads(payload)
            transcripts = document["results"]["transcripts"]
            transcript = transcripts[0]["transcript"].strip()
        except (
            KeyError,
            IndexError,
            TypeError,
            AttributeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            raise VoiceTranscriptionError("VOICE_TRANSCRIPT_INVALID") from None
        if not transcript:
            raise VoiceTranscriptionError("VOICE_TRANSCRIPT_INVALID")
        return transcript

    def _validate_transcript_uri(self, transcript_uri: str) -> None:
        try:
            parsed = urlsplit(transcript_uri)
            hostname = parsed.hostname
            port = parsed.port
        except (TypeError, ValueError):
            raise VoiceTranscriptionError("VOICE_TRANSCRIPT_DOWNLOAD_FAILED") from None
        expected_hosts = {
            f"s3.{self.aws_region}.amazonaws.com",
            f"s3-{self.aws_region}.amazonaws.com",
        }
        if (
            parsed.scheme.lower() != "https"
            or hostname not in expected_hosts
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port is not None
        ):
            raise VoiceTranscriptionError("VOICE_TRANSCRIPT_DOWNLOAD_FAILED")

    def _read_transcript_response(
        self,
        client: httpx.Client,
        transcript_uri: str,
    ) -> bytes:
        with client.stream(
            "GET",
            transcript_uri,
            follow_redirects=False,
            timeout=httpx.Timeout(10.0),
        ) as response:
            if not 200 <= response.status_code < 300:
                raise VoiceTranscriptionError("VOICE_TRANSCRIPT_DOWNLOAD_FAILED")
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                    if declared_size < 0 or declared_size > self.transcript_max_bytes:
                        raise VoiceTranscriptionError(
                            "VOICE_TRANSCRIPT_DOWNLOAD_FAILED"
                        )
                except ValueError:
                    raise VoiceTranscriptionError(
                        "VOICE_TRANSCRIPT_DOWNLOAD_FAILED"
                    ) from None
            chunks: list[bytes] = []
            size_bytes = 0
            for chunk in response.iter_bytes(chunk_size=16 * 1024):
                size_bytes += len(chunk)
                if size_bytes > self.transcript_max_bytes:
                    raise VoiceTranscriptionError(
                        "VOICE_TRANSCRIPT_DOWNLOAD_FAILED"
                    )
                chunks.append(chunk)
        if size_bytes == 0:
            raise VoiceTranscriptionError("VOICE_TRANSCRIPT_INVALID")
        return b"".join(chunks)

    def _delete_job(self, job_name: str) -> None:
        try:
            self.client.delete_transcription_job(
                TranscriptionJobName=job_name
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in {"NotFoundException", "ResourceNotFoundException"}:
                return
            raise VoiceTranscriptionError(
                "VOICE_TRANSCRIPTION_CLEANUP_FAILED"
            ) from None
        except Exception:
            raise VoiceTranscriptionError(
                "VOICE_TRANSCRIPTION_CLEANUP_FAILED"
            ) from None
