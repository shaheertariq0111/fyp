from __future__ import annotations

import logging
import subprocess

import pytest

from src.services.voice_reply_audio_converter import (
    FFMPEG_OGG_OPUS_ARGUMENTS,
    VoiceReplyAudioConverter,
    VoiceReplyConversionError,
)


OGG_OPUS = b"OggS" + (b"\x00" * 24) + b"OpusHead" + (b"\x00" * 32)


def test_converter_uses_exact_fixed_ffmpeg_pipe_contract_without_shell():
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=OGG_OPUS, stderr=b"")

    converter = VoiceReplyAudioConverter(
        timeout_seconds=7,
        max_audio_bytes=1024,
        run=run,
    )
    assert converter.convert(b"synthetic-mp3") == OGG_OPUS
    assert calls == [(
        list(FFMPEG_OGG_OPUS_ARGUMENTS),
        {
            "input": b"synthetic-mp3",
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "timeout": 7,
            "check": False,
            "shell": False,
        },
    )]
    arguments = calls[0][0]
    for sequence in (
        ["-c:a", "libopus"],
        ["-application", "voip"],
        ["-b:a", "24k"],
        ["-frame_duration", "20"],
        ["-ac", "1"],
        ["-ar", "16000"],
        ["-f", "ogg"],
    ):
        start = next(
            index
            for index in range(len(arguments))
            if arguments[index:index + len(sequence)] == sequence
        )
        assert start >= 0
    assert "pipe:0" in arguments and "pipe:1" in arguments


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (FileNotFoundError("private path"), "VOICE_REPLY_FFMPEG_NOT_FOUND"),
        (
            subprocess.TimeoutExpired("private command", 1, stderr=b"private stderr"),
            "VOICE_REPLY_CONVERSION_TIMEOUT",
        ),
    ],
)
def test_converter_maps_process_failures_without_leaking_details(
    failure,
    code,
    caplog,
):
    def run(*_args, **_kwargs):
        raise failure

    with caplog.at_level(logging.INFO), pytest.raises(
        VoiceReplyConversionError
    ) as error:
        VoiceReplyAudioConverter(
            timeout_seconds=1,
            max_audio_bytes=1024,
            run=run,
        ).convert(b"private-source")
    assert error.value.error_code == code
    assert "private" not in caplog.text
    assert "private" not in str(error.value)


@pytest.mark.parametrize(
    ("completed", "max_bytes", "code"),
    [
        (
            subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"private"),
            1024,
            "VOICE_REPLY_CONVERSION_FAILED",
        ),
        (
            subprocess.CompletedProcess([], 0, stdout=b"", stderr=b""),
            1024,
            "VOICE_REPLY_CONVERSION_EMPTY",
        ),
        (
            subprocess.CompletedProcess([], 0, stdout=OGG_OPUS, stderr=b""),
            4,
            "VOICE_REPLY_AUDIO_TOO_LARGE",
        ),
        (
            subprocess.CompletedProcess([], 0, stdout=b"not-ogg", stderr=b""),
            1024,
            "VOICE_REPLY_AUDIO_FORMAT_INVALID",
        ),
    ],
)
def test_converter_validates_exit_output_size_and_container(
    completed,
    max_bytes,
    code,
):
    converter = VoiceReplyAudioConverter(
        timeout_seconds=1,
        max_audio_bytes=max_bytes,
        run=lambda *_args, **_kwargs: completed,
    )
    with pytest.raises(VoiceReplyConversionError) as error:
        converter.convert(b"source")
    assert error.value.error_code == code


def test_converter_rejects_empty_source_without_process_or_temporary_files():
    called = False

    def run(*_args, **_kwargs):
        nonlocal called
        called = True

    with pytest.raises(VoiceReplyConversionError) as error:
        VoiceReplyAudioConverter(
            timeout_seconds=1,
            max_audio_bytes=1024,
            run=run,
        ).convert(b"")
    assert error.value.error_code == "VOICE_REPLY_SOURCE_AUDIO_EMPTY"
    assert called is False
