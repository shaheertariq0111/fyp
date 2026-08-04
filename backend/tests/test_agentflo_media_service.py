import io

import httpx
import pytest

from src.services.agentflo_media_service import AgentfloMediaService, VoiceMediaError


MEDIA_URL = "https://media.example.test/voice"
OGG_OPUS = b"OggS" + (b"\x00" * 24) + b"OpusHead" + (b"\x00" * 32)


class FakeNetworkStream:
    def __init__(self, server_address):
        self.server_address = server_address

    def get_extra_info(self, name):
        if name == "server_addr":
            return self.server_address
        return None


def make_client(
    *,
    status=200,
    body=OGG_OPUS,
    headers=None,
    server_address=("93.184.216.34", 443),
    include_network_stream=True,
    captured=None,
):
    def handler(request):
        if captured is not None:
            captured["request"] = request

        extensions = {}
        if include_network_stream:
            extensions["network_stream"] = FakeNetworkStream(
                server_address
            )

        return httpx.Response(
            status,
            content=body,
            headers=headers,
            request=request,
            extensions=extensions,
        )

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )


def make_service(*, client=None, resolver=None, max_media_bytes=1024):
    return AgentfloMediaService(
        allowed_hosts=["media.example.test"],
        max_media_bytes=max_media_bytes,
        timeout_seconds=10,
        client=client or make_client(),
        resolver=resolver or (lambda _host, _port: ["93.184.216.34"]),
    )


@pytest.mark.parametrize(
    ("url", "error_code"),
    [
        ("http://media.example.test/voice", "VOICE_MEDIA_URL_INVALID"),
        ("https://user:pass@media.example.test/voice", "VOICE_MEDIA_URL_INVALID"),
        ("https://media.example.test:443/voice", "VOICE_MEDIA_URL_INVALID"),
        ("https://other.example.test/voice", "VOICE_MEDIA_HOST_NOT_ALLOWED"),
        ("https://sub.media.example.test/voice", "VOICE_MEDIA_HOST_NOT_ALLOWED"),
    ],
)
def test_media_url_requires_https_and_exact_allowlist(url, error_code):
    with pytest.raises(VoiceMediaError) as error:
        make_service().download(url)

    assert error.value.error_code == error_code


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.0.0.1"],
        ["169.254.169.254"],
        ["93.184.216.34", "192.168.1.5"],
    ],
)
def test_media_download_rejects_unsafe_and_mixed_dns_results(addresses):
    with pytest.raises(VoiceMediaError) as error:
        make_service(resolver=lambda _host, _port: addresses).download(MEDIA_URL)

    assert error.value.error_code == "VOICE_MEDIA_NETWORK_BLOCKED"


def test_media_download_rejects_redirects():
    with pytest.raises(VoiceMediaError) as error:
        make_service(
            client=make_client(status=302, headers={"Location": "https://other.test"})
        ).download(MEDIA_URL)

    assert error.value.error_code == "VOICE_MEDIA_DOWNLOAD_FAILED"


def test_media_download_sanitizes_network_failures():
    def handler(request):
        raise httpx.ReadTimeout("synthetic response body", request=request)

    secret_url = f"{MEDIA_URL}?token=synthetic-secret"
    with pytest.raises(VoiceMediaError) as error:
        make_service(
            client=httpx.Client(transport=httpx.MockTransport(handler))
        ).download(secret_url)

    assert error.value.error_code == "VOICE_MEDIA_DOWNLOAD_FAILED"
    assert secret_url not in str(error.value)
    assert "synthetic-secret" not in str(error.value)
    assert "synthetic response body" not in str(error.value)


def test_media_download_enforces_injected_overall_timeout():
    values = iter([0.0, 11.0])

    with pytest.raises(VoiceMediaError) as error:
        AgentfloMediaService(
            allowed_hosts=["media.example.test"],
            max_media_bytes=1024,
            timeout_seconds=10,
            client=make_client(),
            resolver=lambda _host, _port: ["93.184.216.34"],
            clock=lambda: next(values),
        ).download(MEDIA_URL)

    assert error.value.error_code == "VOICE_MEDIA_DOWNLOAD_FAILED"


def test_media_download_enforces_declared_and_streamed_size_limits():
    with pytest.raises(VoiceMediaError) as declared_error:
        make_service(
            client=make_client(headers={"Content-Length": "1025"}),
            max_media_bytes=1024,
        ).download(MEDIA_URL)
    with pytest.raises(VoiceMediaError) as streamed_error:
        make_service(
            client=make_client(body=b"OggS" + b"x" * 1024),
            max_media_bytes=1024,
        ).download(MEDIA_URL)

    assert declared_error.value.error_code == "VOICE_MEDIA_TOO_LARGE"
    assert streamed_error.value.error_code == "VOICE_MEDIA_TOO_LARGE"


def test_media_download_rejects_empty_and_unknown_content():
    with pytest.raises(VoiceMediaError) as empty_error:
        make_service(client=make_client(body=b"")).download(MEDIA_URL)
    with pytest.raises(VoiceMediaError) as format_error:
        make_service(
            client=make_client(body=b"not an audio container", headers={"Content-Type": "audio/ogg"})
        ).download("https://media.example.test/voice.ogg")

    assert empty_error.value.error_code == "VOICE_MEDIA_EMPTY"
    assert format_error.value.error_code == "VOICE_MEDIA_FORMAT_UNSUPPORTED"


def test_media_download_detects_ogg_opus_independent_of_headers_or_extension():
    media = make_service(
        client=make_client(
            body=OGG_OPUS,
            headers={"Content-Type": "text/plain"},
        )
    ).download("https://media.example.test/not-audio.txt")

    try:
        assert media.media_format == "ogg"
        assert media.suffix == ".ogg"
        assert media.content_type == "audio/ogg"
        assert media.size_bytes == len(OGG_OPUS)
    finally:
        media.close()


def test_media_download_closes_temporary_file_after_failure(monkeypatch):
    temporary_file = io.BytesIO()
    monkeypatch.setattr(
        "src.services.agentflo_media_service.tempfile.SpooledTemporaryFile",
        lambda **_kwargs: temporary_file,
    )

    with pytest.raises(VoiceMediaError):
        make_service(client=make_client(body=b"unknown")).download(MEDIA_URL)

    assert temporary_file.closed


def test_media_download_pins_validated_ip_and_preserves_hostname():
    captured = {}
    media = make_service(
        client=make_client(captured=captured),
    ).download(f"{MEDIA_URL}?token=synthetic")

    try:
        request = captured["request"]
        assert str(request.url) == (
            "https://93.184.216.34/voice?token=synthetic"
        )
        assert request.headers["Host"] == "media.example.test"
        assert (
            request.extensions["sni_hostname"]
            == "media.example.test"
        )
    finally:
        media.close()


@pytest.mark.parametrize(
    "client",
    [
        make_client(
            server_address=("93.184.216.35", 443),
        ),
        make_client(
            include_network_stream=False,
        ),
    ],
)
def test_media_download_rejects_unverified_or_mismatched_peer(client):
    with pytest.raises(VoiceMediaError) as error:
        make_service(client=client).download(MEDIA_URL)

    assert error.value.error_code == "VOICE_MEDIA_NETWORK_BLOCKED"


def test_media_download_default_client_ignores_proxy_environment(
    monkeypatch,
):
    captured = {}
    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        captured.update(kwargs)

        def handler(request):
            return httpx.Response(
                200,
                content=OGG_OPUS,
                request=request,
                extensions={
                    "network_stream": FakeNetworkStream(
                        ("93.184.216.34", 443)
                    )
                },
            )

        return real_client(
            transport=httpx.MockTransport(handler),
            trust_env=kwargs.get("trust_env", True),
        )

    monkeypatch.setattr(
        "src.services.agentflo_media_service.httpx.Client",
        client_factory,
    )

    media = AgentfloMediaService(
        allowed_hosts=["media.example.test"],
        max_media_bytes=1024,
        timeout_seconds=10,
        resolver=lambda _host, _port: ["93.184.216.34"],
    ).download(MEDIA_URL)

    try:
        assert captured["trust_env"] is False
    finally:
        media.close()
