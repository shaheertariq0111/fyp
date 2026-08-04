import io

from botocore.exceptions import ClientError

from src.services.agentflo_media_service import DownloadedVoiceMedia
from src.services.voice_media_storage_service import (
    StoredVoiceMedia,
    VoiceMediaStorageError,
    VoiceMediaStorageService,
)


class FakeS3Client:
    def __init__(self):
        self.uploads = []
        self.deletes = []
        self.delete_error = None

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs):
        self.uploads.append((fileobj, bucket, key, ExtraArgs))

    def delete_object(self, **kwargs):
        self.deletes.append(kwargs)
        if self.delete_error is not None:
            raise self.delete_error


def make_media():
    return DownloadedVoiceMedia(
        file=io.BytesIO(b"OggS synthetic OpusHead"),
        media_format="ogg",
        suffix=".ogg",
        content_type="audio/ogg",
        size_bytes=23,
    )


def test_voice_media_upload_uses_exact_bucket_prefix_and_safe_arguments():
    client = FakeS3Client()
    service = VoiceMediaStorageService(
        client=client,
        bucket_name="dedicated-voice-bucket",
        input_prefix="voice-input/",
    )
    stored = service.upload(make_media(), message_id="raw-message-customer-123")

    assert stored.bucket_name == "dedicated-voice-bucket"
    assert stored.object_key.startswith("voice-input/")
    assert stored.object_key.endswith(".ogg")
    assert "raw-message-customer-123" not in stored.object_key
    assert "customer" not in stored.object_key
    assert stored.s3_uri == (
        f"s3://dedicated-voice-bucket/{stored.object_key}"
    )
    _, bucket, key, extra_args = client.uploads[0]
    assert bucket == "dedicated-voice-bucket"
    assert key == stored.object_key
    assert extra_args == {
        "ServerSideEncryption": "AES256",
        "ContentType": "audio/ogg",
    }


def test_voice_media_key_is_deterministic_and_opaque():
    service = VoiceMediaStorageService(
        client=FakeS3Client(),
        bucket_name="voice-bucket",
        input_prefix="voice-input/",
    )

    first = service.object_key("message-123", ".ogg")
    second = service.object_key("message-123", ".ogg")

    assert first == second
    assert first != "voice-input/message-123.ogg"
    assert len(first.removeprefix("voice-input/").removesuffix(".ogg")) == 64


def test_voice_media_upload_rejects_unvalidated_content_type():
    service = VoiceMediaStorageService(
        client=FakeS3Client(),
        bucket_name="voice-bucket",
        input_prefix="voice-input/",
    )
    media = make_media()
    media.content_type = "text/html"

    try:
        service.upload(media, message_id="message-123")
    except VoiceMediaStorageError:
        pass
    else:
        raise AssertionError("Unvalidated content type should be rejected")


def test_voice_media_delete_uses_stored_bucket_and_key():
    client = FakeS3Client()
    service = VoiceMediaStorageService(
        client=client,
        bucket_name="voice-bucket",
        input_prefix="voice-input/",
    )
    stored = service.upload(make_media(), message_id="message-123")

    service.delete(stored)

    assert client.deletes == [
        {"Bucket": "voice-bucket", "Key": stored.object_key}
    ]


def test_voice_media_delete_treats_missing_object_as_idempotent():
    client = FakeS3Client()
    client.delete_error = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
        "DeleteObject",
    )
    service = VoiceMediaStorageService(
        client=client,
        bucket_name="voice-bucket",
        input_prefix="voice-input/",
    )
    stored = service.upload(make_media(), message_id="message-123")

    service.delete(stored)

    assert len(client.deletes) == 1


def test_voice_media_delete_rejects_arbitrary_object_key():
    service = VoiceMediaStorageService(
        client=FakeS3Client(),
        bucket_name="voice-bucket",
        input_prefix="voice-input/",
    )

    try:
        service.delete(
            StoredVoiceMedia(
                bucket_name="voice-bucket",
                object_key="voice-input/arbitrary.ogg",
                s3_uri="s3://voice-bucket/voice-input/arbitrary.ogg",
            )
        )
    except VoiceMediaStorageError:
        pass
    else:
        raise AssertionError("Arbitrary object key should be rejected")
