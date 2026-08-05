from __future__ import annotations

import ipaddress
import socket
import tempfile
import time
from dataclasses import dataclass
from typing import BinaryIO, Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

import httpx


VOICE_MEDIA_ERROR_MESSAGES = {
    "VOICE_MEDIA_URL_INVALID": "The voice media URL is invalid.",
    "VOICE_MEDIA_HOST_NOT_ALLOWED": "The voice media host is not allowed.",
    "VOICE_MEDIA_NETWORK_BLOCKED": "The voice media network destination is blocked.",
    "VOICE_MEDIA_DOWNLOAD_FAILED": "The voice media could not be downloaded.",
    "VOICE_MEDIA_TOO_LARGE": "The voice media exceeds the size limit.",
    "VOICE_MEDIA_EMPTY": "The voice media is empty.",
    "VOICE_MEDIA_FORMAT_UNSUPPORTED": "The voice media format is unsupported.",
}


class VoiceMediaError(Exception):
    def __init__(self, error_code: str):
        self.error_code = error_code
        self.retryable = error_code == "VOICE_MEDIA_DOWNLOAD_FAILED"
        super().__init__(VOICE_MEDIA_ERROR_MESSAGES[error_code])


@dataclass
class DownloadedVoiceMedia:
    file: BinaryIO
    media_format: str
    suffix: str
    content_type: str
    size_bytes: int

    def close(self) -> None:
        self.file.close()


def detect_voice_media_format(media_file: BinaryIO) -> tuple[str, str, str]:
    media_file.seek(0)
    header = media_file.read(4096)
    media_file.seek(0)
    if header.startswith(b"OggS") and b"OpusHead" in header:
        return "ogg", ".ogg", "audio/ogg"
    raise VoiceMediaError("VOICE_MEDIA_FORMAT_UNSUPPORTED")


def _resolve_host(hostname: str, port: int) -> Iterable[str]:
    return {
        result[4][0]
        for result in socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    }


class AgentfloMediaService:
    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str],
        max_media_bytes: int,
        timeout_seconds: float,
        client: httpx.Client | None = None,
        resolver: Callable[[str, int], Iterable[str]] = _resolve_host,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_media_bytes <= 0 or timeout_seconds <= 0:
            raise ValueError("Voice media limits must be positive")
        self.allowed_hosts = {host.strip().lower() for host in allowed_hosts if host.strip()}
        self.max_media_bytes = max_media_bytes
        self.timeout_seconds = timeout_seconds
        self.client = client
        self.resolver = resolver
        self.clock = clock

    def download(self, media_url: str) -> DownloadedVoiceMedia:
        started = self.clock()
        hostname = self._validated_hostname(media_url)
        validated_addresses = self._validate_resolved_addresses(hostname)

        if self.clock() - started >= self.timeout_seconds:
            raise VoiceMediaError("VOICE_MEDIA_DOWNLOAD_FAILED")

        selected_ip = validated_addresses[0]
        request_url = self._pinned_url(media_url, selected_ip)
        deadline = started + self.timeout_seconds

        temporary_file = tempfile.SpooledTemporaryFile(
            max_size=min(self.max_media_bytes, 1024 * 1024),
            mode="w+b",
        )
        try:
            if self.client is None:
                with httpx.Client(trust_env=False) as client:
                    size_bytes = self._stream_download(
                        client,
                        request_url,
                        hostname,
                        selected_ip,
                        temporary_file,
                        deadline,
                    )
            else:
                size_bytes = self._stream_download(
                    self.client,
                    request_url,
                    hostname,
                    selected_ip,
                    temporary_file,
                    deadline,
                )

            media_format, suffix, content_type = detect_voice_media_format(
                temporary_file
            )
            temporary_file.seek(0)
            return DownloadedVoiceMedia(
                file=temporary_file,
                media_format=media_format,
                suffix=suffix,
                content_type=content_type,
                size_bytes=size_bytes,
            )
        except Exception:
            temporary_file.close()
            raise

    def _validated_hostname(self, media_url: str) -> str:
        if not self.allowed_hosts:
            raise VoiceMediaError("VOICE_MEDIA_HOST_NOT_ALLOWED")
        try:
            parsed = urlsplit(media_url)
            hostname = parsed.hostname
            port = parsed.port
        except (TypeError, ValueError):
            raise VoiceMediaError("VOICE_MEDIA_URL_INVALID") from None
        if (
            parsed.scheme.lower() != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port is not None
        ):
            raise VoiceMediaError("VOICE_MEDIA_URL_INVALID")
        if hostname.endswith("."):
            raise VoiceMediaError("VOICE_MEDIA_URL_INVALID")
        normalized_host = hostname.lower()
        try:
            ipaddress.ip_address(normalized_host)
        except ValueError:
            pass
        else:
            raise VoiceMediaError("VOICE_MEDIA_URL_INVALID")
        if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
            raise VoiceMediaError("VOICE_MEDIA_URL_INVALID")
        if normalized_host not in self.allowed_hosts:
            raise VoiceMediaError("VOICE_MEDIA_HOST_NOT_ALLOWED")
        return normalized_host

    def _validate_resolved_addresses(
        self,
        hostname: str,
    ) -> tuple[str, ...]:
        try:
            addresses = list(self.resolver(hostname, 443))
        except (OSError, socket.gaierror):
            raise VoiceMediaError("VOICE_MEDIA_DOWNLOAD_FAILED") from None

        if not addresses:
            raise VoiceMediaError("VOICE_MEDIA_DOWNLOAD_FAILED")

        try:
            parsed_addresses = {
                ipaddress.ip_address(address)
                for address in addresses
            }
        except ValueError:
            raise VoiceMediaError("VOICE_MEDIA_NETWORK_BLOCKED") from None

        if any(not address.is_global for address in parsed_addresses):
            raise VoiceMediaError("VOICE_MEDIA_NETWORK_BLOCKED")

        ordered_addresses = sorted(
            parsed_addresses,
            key=lambda address: (address.version, int(address)),
        )
        return tuple(str(address) for address in ordered_addresses)

    @staticmethod
    def _pinned_url(media_url: str, selected_ip: str) -> str:
        parsed = urlsplit(media_url)
        authority = (
            f"[{selected_ip}]"
            if ":" in selected_ip
            else selected_ip
        )
        return urlunsplit(
            (
                "https",
                authority,
                parsed.path or "/",
                parsed.query,
                "",
            )
        )

    @staticmethod
    def _validate_connected_peer(
        response: httpx.Response,
        expected_ip: str,
    ) -> None:
        network_stream = response.extensions.get("network_stream")
        if network_stream is None:
            raise VoiceMediaError("VOICE_MEDIA_NETWORK_BLOCKED")

        try:
            server_address = network_stream.get_extra_info("server_addr")
        except Exception:
            raise VoiceMediaError("VOICE_MEDIA_NETWORK_BLOCKED") from None

        if (
            not isinstance(server_address, (tuple, list))
            or not server_address
            or not isinstance(server_address[0], str)
        ):
            raise VoiceMediaError("VOICE_MEDIA_NETWORK_BLOCKED")

        try:
            connected_ip = ipaddress.ip_address(server_address[0])
            pinned_ip = ipaddress.ip_address(expected_ip)
        except ValueError:
            raise VoiceMediaError("VOICE_MEDIA_NETWORK_BLOCKED") from None

        if connected_ip != pinned_ip or not connected_ip.is_global:
            raise VoiceMediaError("VOICE_MEDIA_NETWORK_BLOCKED")

    def _stream_download(
        self,
        client: httpx.Client,
        request_url: str,
        hostname: str,
        expected_ip: str,
        destination: BinaryIO,
        deadline: float,
    ) -> int:
        remaining_seconds = deadline - self.clock()
        if remaining_seconds <= 0:
            raise VoiceMediaError("VOICE_MEDIA_DOWNLOAD_FAILED")

        timeout = httpx.Timeout(remaining_seconds)

        try:
            with client.stream(
                "GET",
                request_url,
                headers={
                    "Accept": "audio/*,application/octet-stream",
                    "Host": hostname,
                },
                extensions={"sni_hostname": hostname},
                follow_redirects=False,
                timeout=timeout,
            ) as response:
                self._validate_connected_peer(response, expected_ip)

                if 300 <= response.status_code < 400:
                    raise VoiceMediaError("VOICE_MEDIA_DOWNLOAD_FAILED")
                if not 200 <= response.status_code < 300:
                    raise VoiceMediaError("VOICE_MEDIA_DOWNLOAD_FAILED")

                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError:
                        raise VoiceMediaError(
                            "VOICE_MEDIA_DOWNLOAD_FAILED"
                        ) from None

                    if declared_size > self.max_media_bytes:
                        raise VoiceMediaError("VOICE_MEDIA_TOO_LARGE")
                    if declared_size < 0:
                        raise VoiceMediaError(
                            "VOICE_MEDIA_DOWNLOAD_FAILED"
                        )

                size_bytes = 0
                for chunk in response.iter_bytes(
                    chunk_size=64 * 1024
                ):
                    if self.clock() >= deadline:
                        raise VoiceMediaError(
                            "VOICE_MEDIA_DOWNLOAD_FAILED"
                        )
                    if not chunk:
                        continue

                    size_bytes += len(chunk)
                    if size_bytes > self.max_media_bytes:
                        raise VoiceMediaError("VOICE_MEDIA_TOO_LARGE")

                    destination.write(chunk)

        except VoiceMediaError:
            raise
        except (
            httpx.TimeoutException,
            httpx.RequestError,
            OSError,
        ):
            raise VoiceMediaError("VOICE_MEDIA_DOWNLOAD_FAILED") from None

        if size_bytes == 0:
            raise VoiceMediaError("VOICE_MEDIA_EMPTY")

        destination.flush()
        destination.seek(0)
        return size_bytes

    @staticmethod
    def _detect_format(media_file: BinaryIO) -> tuple[str, str, str]:
        return detect_voice_media_format(media_file)
