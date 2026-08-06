from __future__ import annotations

import subprocess
from typing import Callable


FFMPEG_OGG_OPUS_ARGUMENTS = (
    "ffmpeg",
    "-hide_banner",
    "-loglevel",
    "error",
    "-nostdin",
    "-f",
    "mp3",
    "-i",
    "pipe:0",
    "-vn",
    "-c:a",
    "libopus",
    "-application",
    "voip",
    "-b:a",
    "24k",
    "-frame_duration",
    "20",
    "-ac",
    "1",
    "-ar",
    "16000",
    "-f",
    "ogg",
    "pipe:1",
)


class VoiceReplyConversionError(Exception):
    def __init__(self, error_code: str, *, retryable: bool) -> None:
        self.error_code = error_code
        self.retryable = retryable
        self.stage = "conversion"
        super().__init__("Voice reply audio conversion failed.")


class VoiceReplyAudioConverter:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_audio_bytes: int,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_audio_bytes = max_audio_bytes
        self.run = run

    def convert(self, source_audio: bytes) -> bytes:
        if not isinstance(source_audio, bytes) or not source_audio:
            raise VoiceReplyConversionError(
                "VOICE_REPLY_SOURCE_AUDIO_EMPTY",
                retryable=False,
            )
        try:
            completed = self.run(
                list(FFMPEG_OGG_OPUS_ARGUMENTS),
                input=source_audio,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except FileNotFoundError:
            raise VoiceReplyConversionError(
                "VOICE_REPLY_FFMPEG_NOT_FOUND",
                retryable=False,
            ) from None
        except subprocess.TimeoutExpired:
            raise VoiceReplyConversionError(
                "VOICE_REPLY_CONVERSION_TIMEOUT",
                retryable=True,
            ) from None
        except OSError:
            raise VoiceReplyConversionError(
                "VOICE_REPLY_CONVERSION_FAILED",
                retryable=True,
            ) from None

        if completed.returncode != 0:
            raise VoiceReplyConversionError(
                "VOICE_REPLY_CONVERSION_FAILED",
                retryable=False,
            )
        output = completed.stdout
        if not isinstance(output, bytes) or not output:
            raise VoiceReplyConversionError(
                "VOICE_REPLY_CONVERSION_EMPTY",
                retryable=False,
            )
        if len(output) > self.max_audio_bytes:
            raise VoiceReplyConversionError(
                "VOICE_REPLY_AUDIO_TOO_LARGE",
                retryable=False,
            )
        if not output.startswith(b"OggS") or b"OpusHead" not in output[:256]:
            raise VoiceReplyConversionError(
                "VOICE_REPLY_AUDIO_FORMAT_INVALID",
                retryable=False,
            )
        return output
