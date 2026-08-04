from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from botocore.exceptions import ClientError

from src.services.agentflo_media_service import DownloadedVoiceMedia


VOICE_OBJECT_KEY = re.compile(r"^voice-input/[0-9a-f]{64}\.ogg$")


class VoiceMediaStorageError(Exception):
    error_code = "VOICE_MEDIA_STORAGE_FAILED"

    def __init__(self) -> None:
        super().__init__("Temporary voice media storage failed.")


@dataclass(frozen=True)
class StoredVoiceMedia:
    bucket_name: str
    object_key: str
    s3_uri: str


class VoiceMediaStorageService:
    def __init__(self, *, client, bucket_name: str, input_prefix: str) -> None:
        if not bucket_name.strip():
            raise ValueError("VOICE_MEDIA_BUCKET_NAME is required")
        if input_prefix != "voice-input/":
            raise ValueError("VOICE_MEDIA_INPUT_PREFIX must be exactly voice-input/")
        self.client = client
        self.bucket_name = bucket_name
        self.input_prefix = input_prefix

    def upload(
        self,
        media: DownloadedVoiceMedia,
        *,
        message_id: str,
    ) -> StoredVoiceMedia:
        if (
            media.media_format != "ogg"
            or media.suffix != ".ogg"
            or media.content_type != "audio/ogg"
            or media.size_bytes <= 0
        ):
            raise VoiceMediaStorageError()
        object_key = self.object_key(message_id, media.suffix)
        try:
            media.file.seek(0)
            self.client.upload_fileobj(
                media.file,
                self.bucket_name,
                object_key,
                ExtraArgs={
                    "ServerSideEncryption": "AES256",
                    "ContentType": media.content_type,
                },
            )
        except Exception:
            raise VoiceMediaStorageError() from None
        return StoredVoiceMedia(
            bucket_name=self.bucket_name,
            object_key=object_key,
            s3_uri=f"s3://{self.bucket_name}/{object_key}",
        )

    def delete(self, stored_media: StoredVoiceMedia) -> None:
        if (
            stored_media.bucket_name != self.bucket_name
            or not VOICE_OBJECT_KEY.fullmatch(stored_media.object_key)
        ):
            raise VoiceMediaStorageError()
        try:
            self.client.delete_object(
                Bucket=self.bucket_name,
                Key=stored_media.object_key,
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in {"NoSuchKey", "NotFound", "404"}:
                return
            raise VoiceMediaStorageError() from None
        except Exception:
            raise VoiceMediaStorageError() from None

    def object_key(self, message_id: str, suffix: str) -> str:
        normalized_message_id = message_id.strip()
        if not normalized_message_id or suffix not in {".ogg"}:
            raise VoiceMediaStorageError()
        digest = hashlib.sha256(normalized_message_id.encode("utf-8")).hexdigest()
        return f"{self.input_prefix}{digest}{suffix}"
